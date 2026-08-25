"""异步交易引擎：行情 → 信号 → 风控 → 执行 → 审计。"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from trading.core.exchange import BinanceExchange, CredentialsMissing, ExchangeError
from trading.core.market_data import MarketData
from trading.core.notifier import Notifier
from trading.core.order_manager import OrderManager
from trading.core.position_manager import PositionManager
from trading.core.risk_manager import RiskContext, RiskManager
from trading.core.strategy_base import Signal, SignalAction, Strategy
from trading.settings import TradingSettings
from trading.storage import TradingStore
from trading.strategies import BollBreakStrategy, MaCrossStrategy, RsiBasicStrategy

logger = logging.getLogger(__name__)


def build_strategy(settings: TradingSettings) -> Strategy:
    if settings.strategy == "rsi_basic":
        return RsiBasicStrategy(settings.rsi_period, settings.rsi_buy, settings.rsi_sell)
    if settings.strategy == "boll_break":
        return BollBreakStrategy(settings.boll_window, settings.boll_std)
    return MaCrossStrategy(settings.ma_fast, settings.ma_slow)


class TradingEngine:
    def __init__(self, settings: TradingSettings, store: TradingStore | None = None) -> None:
        self.settings = settings
        self.store = store or TradingStore()
        self.exchange = BinanceExchange(settings)
        self.market = MarketData(settings, self.exchange)
        self.risk = RiskManager(settings)
        self.orders = OrderManager(self.store, settings.order_cooldown_sec)
        self.positions = PositionManager()
        self.notifier = Notifier(settings)
        self.strategy = build_strategy(settings)
        self.auto_trade = bool(settings.enable_auto_trade and settings.testnet and settings.credentials_configured)
        self.mainnet_unlocked = settings.testnet
        self.running = False
        self.last_signal: dict[str, dict] = {}
        self.account = {"equity": 0.0, "available_balance": 0.0, "wallet_balance": 0.0, "unrealised_pnl": 0.0, "balance_asset": "USDT"}
        self._tasks: list[asyncio.Task] = []
        self._last_candle: dict[str, int] = {}

    async def start(self) -> None:
        if self.running:
            return
        self.store.open()
        self.running = True
        self.store.event("system", f"交易引擎启动：{self.settings.environment_name}")
        await self.market.start()
        self._tasks = [
            asyncio.create_task(self._event_loop(), name="trading-events"),
            asyncio.create_task(self._account_loop(), name="trading-account-sync"),
            asyncio.create_task(self._position_watchdog(), name="trading-position-watchdog"),
        ]

    async def stop(self) -> None:
        if not self.running:
            return
        self.auto_trade = False
        self.running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.market.stop()
        await self.notifier.close()
        self.store.event("system", "交易引擎已停止")
        self.store.close()
        logger.info("Binance 交易引擎已停止")

    async def _event_loop(self) -> None:
        while self.running:
            message = await self.market.events.get()
            kind = message.get("type")
            if kind == "order":
                for row in message.get("data", []):
                    self.orders.update(row, "private_ws")
            elif kind == "execution":
                for row in message.get("data", []):
                    self.store.add_trade(row)
            elif kind == "position":
                for row in message.get("data", []):
                    self.positions.update(row)
            elif kind == "kline" and message.get("data", {}).get("confirm"):
                await self._on_closed_candle(str(message.get("symbol") or ""), message["data"])

    async def _account_loop(self) -> None:
        while self.running:
            if self.settings.credentials_configured:
                try:
                    await self.refresh_account()
                except Exception as exc:
                    self.store.event("api", f"账户同步失败：{exc}", level="ERROR")
            await asyncio.sleep(5)

    async def _position_watchdog(self) -> None:
        while self.running:
            if self.settings.max_holding_minutes > 0 and self.settings.credentials_configured:
                now_ms = time.time() * 1000
                for row in self.positions.active():
                    created = float(row.get("createdTime") or row.get("updatedTime") or now_ms)
                    if now_ms - created > self.settings.max_holding_minutes * 60_000:
                        symbol = str(row.get("symbol") or "")
                        self.store.event("risk", "达到最大持仓时间，执行平仓", level="WARNING", symbol=symbol)
                        try:
                            await self.close_position(symbol, source="max_holding_time")
                        except Exception as exc:
                            self.store.event("error", f"超时平仓失败：{exc}", level="ERROR", symbol=symbol)
            await asyncio.sleep(30)

    async def _on_closed_candle(self, symbol: str, candle: dict) -> None:
        candle_time = int(candle.get("time") or 0)
        if not symbol or self._last_candle.get(symbol) == candle_time:
            return
        self._last_candle[symbol] = candle_time
        signal = self.strategy.evaluate(self.market.frame(symbol))
        self.last_signal[symbol] = signal.as_dict()
        self.store.event("signal", signal.reason, symbol=symbol, payload=signal.as_dict())
        if self.auto_trade and signal.action != SignalAction.HOLD:
            try:
                await self.execute_signal(symbol, signal)
            except Exception as exc:
                self.store.event("error", f"自动交易执行失败：{exc}", level="ERROR", symbol=symbol)
                await self.notifier.send(f"【股析】{symbol} 自动交易失败：{exc}")

    async def refresh_account(self) -> dict:
        self.exchange.require_credentials()
        wallet = await self.exchange.wallet()
        positions = await self.exchange.positions()
        orders = await self.exchange.open_orders()
        executions = await self.exchange.executions()
        self.account = wallet
        self.positions.sync(positions)
        self.orders.sync(orders)
        for row in self.orders.uncertain():
            try:
                history = await self.exchange.order_history(row["symbol"], row["order_link_id"])
                self.orders.sync(history)
            except Exception as exc:
                self.store.event("recovery", f"待确认订单暂未恢复：{exc}", level="WARNING", symbol=row["symbol"])
        for row in executions:
            self.store.add_trade(row)
        return self.account_snapshot()

    def account_snapshot(self) -> dict:
        return {
            **self.account,
            "positions": self.positions.active(),
            "total_position_notional": self.positions.total_notional(),
        }

    def _risk_context(self, price: float, qty: float, leverage: int, reduce_only: bool) -> RiskContext:
        today = datetime.now(timezone.utc).date().isoformat()
        return RiskContext(
            equity=float(self.account.get("trading_equity", self.account.get("equity")) or 0),
            available_balance=float(self.account.get("trading_available_balance", self.account.get("available_balance")) or 0),
            current_price=price, order_qty=qty, leverage=leverage,
            total_position_notional=self.positions.total_notional(),
            daily_closed_pnl=self.store.daily_closed_pnl(today),
            consecutive_losses=self.store.consecutive_losses(), reduce_only=reduce_only,
        )

    async def manual_order(
        self, *, symbol: str, side: str, order_type: str, qty: float, price: float | None = None,
        take_profit: float | None = None, stop_loss: float | None = None, source: str = "manual",
        signal_time: str = "",
    ) -> dict:
        if symbol not in self.settings.symbols:
            raise ValueError("交易对不在允许列表中")
        if not self.settings.testnet and not self.mainnet_unlocked:
            raise PermissionError("Mainnet 实盘尚未解锁")
        if not self.account.get("equity"):
            await self.refresh_account()
        ticker = self.market.latest.get(symbol, {})
        current_price = float(price or ticker.get("price") or ticker.get("lastPrice") or 0)
        leverage = self.settings.default_leverage
        decision = self.risk.check(self._risk_context(current_price, qty, leverage, False))
        self.store.event("risk", decision.reason, level="INFO" if decision.allowed else "WARNING", symbol=symbol)
        if not decision.allowed:
            await self.notifier.send(f"【股析风控】拒绝 {symbol} {side}：{decision.reason}")
            raise PermissionError(decision.reason)
        link_id = self.orders.link_id(symbol, side, source, signal_time or str(time.time_ns()))
        if not self.orders.reserve(link_id):
            raise PermissionError("检测到重复信号或订单冷却中")
        pending = {
            "orderLinkId": link_id, "symbol": symbol, "side": side, "orderType": order_type,
            "qty": qty, "price": price or 0, "orderStatus": "PendingSubmit", "reduceOnly": False,
        }
        self.orders.update(pending, source)
        try:
            await self.exchange.set_leverage(symbol, leverage)
            if stop_loss is None:
                stop_loss = current_price * (1 - self.settings.hard_stop_loss_pct if side == "Buy" else 1 + self.settings.hard_stop_loss_pct)
            if take_profit is None:
                take_profit = current_price * (1 + self.settings.take_profit_pct if side == "Buy" else 1 - self.settings.take_profit_pct)
            order = await self.exchange.place_order(
                symbol=symbol, side=side, order_type=order_type, qty=qty, price=price,
                order_link_id=link_id, take_profit=round(take_profit, 8), stop_loss=round(stop_loss, 8),
            )
            self.orders.update(order, source)
            self.store.event("order", f"{source} {side} 订单已提交", symbol=symbol, payload=order)
            await self.notifier.send(f"【股析】{self.settings.environment_name} {symbol} {side} 订单已提交")
            return order
        except ExchangeError as exc:
            uncertain = exc.code is None and exc.__cause__ is not None
            status = "SubmitUnknown" if uncertain else "Rejected"
            self.orders.update({**pending, "orderStatus": status}, source)
            message = "订单提交结果待确认" if uncertain else "订单被交易所拒绝"
            self.store.event("error", f"{message}：{exc}", level="ERROR", symbol=symbol)
            raise
        except Exception as exc:
            self.orders.update({**pending, "orderStatus": "SubmitUnknown"}, source)
            self.store.event("error", f"订单提交结果待确认：{exc}", level="ERROR", symbol=symbol)
            raise

    async def execute_signal(self, symbol: str, signal: Signal) -> dict | None:
        active = next((row for row in self.positions.active() if row.get("symbol") == symbol), None)
        if signal.action == SignalAction.CLOSE:
            return await self.close_position(symbol, source=f"strategy:{signal.strategy}") if active else None
        desired_side = "Buy" if signal.action == SignalAction.BUY else "Sell"
        if active:
            if active.get("side") == desired_side:
                return None
            return await self.close_position(symbol, source=f"strategy:{signal.strategy}")
        price = float(self.market.latest.get(symbol, {}).get("price") or 0)
        if not price:
            raise ExchangeError("实时价格不可用")
        if not self.account.get("equity"):
            await self.refresh_account()
        notional = float(self.account.get("trading_equity", self.account["equity"])) * self.settings.max_position_pct * self.settings.default_leverage
        qty = notional / price
        return await self.manual_order(
            symbol=symbol, side=desired_side, order_type="Market", qty=qty,
            source=f"strategy:{signal.strategy}", signal_time=signal.timestamp,
        )

    async def close_position(self, symbol: str, source: str = "manual") -> dict | None:
        if not self.settings.testnet and not self.mainnet_unlocked:
            raise PermissionError("Mainnet 实盘尚未解锁")
        result = await self.exchange.close_position(symbol)
        if result:
            self.orders.update(result, source)
            self.store.event("order", f"{source} 平仓订单已提交", symbol=symbol, payload=result)
        return result

    async def cancel_order(self, symbol: str, order_id: str = "", order_link_id: str = "") -> dict:
        result = await self.exchange.cancel_order(symbol, order_id, order_link_id)
        self.store.event("order", "撤单请求已提交", symbol=symbol, payload=result)
        return result

    async def amend_order(
        self, symbol: str, *, order_id: str = "", order_link_id: str = "",
        qty: float | None = None, price: float | None = None,
    ) -> dict:
        result = await self.exchange.amend_order(
            symbol, order_id=order_id, order_link_id=order_link_id, qty=qty, price=price,
        )
        self.store.event("order", "改单请求已提交", symbol=symbol, payload=result)
        return result

    async def set_auto_trade(self, enabled: bool) -> dict:
        if enabled:
            self.exchange.require_credentials()
            if not self.settings.testnet and not self.mainnet_unlocked:
                raise PermissionError("Mainnet 实盘尚未解锁")
            if self.risk.emergency_stopped:
                raise PermissionError("紧急停止状态下不能启用自动交易")
        self.auto_trade = enabled
        self.store.event("control", "自动交易已开启" if enabled else "自动交易已关闭", level="WARNING" if enabled else "INFO")
        return self.status()

    def unlock_mainnet(self, phrase: str) -> dict:
        if self.settings.testnet:
            raise ValueError("当前配置为 Testnet，无需解锁主网")
        if not self.settings.allow_mainnet:
            raise PermissionError("settings.yaml 未允许 Mainnet")
        if phrase != "我确认使用Binance主网实盘":
            raise PermissionError("主网确认短语不正确")
        self.mainnet_unlocked = True
        self.store.event("security", "Mainnet 实盘已在当前会话解锁", level="WARNING")
        return self.status()

    async def emergency_stop(self, close_positions: bool = False) -> dict:
        self.auto_trade = False
        self.risk.stop("用户触发紧急停止")
        cancelled, closed, errors = [], [], []
        if self.settings.credentials_configured:
            try:
                cancelled = await self.exchange.cancel_all()
            except Exception as exc:
                errors.append(f"撤单失败：{exc}")
            if close_positions:
                try:
                    closed = await self.exchange.close_all_positions()
                except Exception as exc:
                    errors.append(f"平仓失败：{exc}")
        self.store.event(
            "emergency", "紧急停止：已禁止新开仓并撤销挂单" + ("，已提交全部平仓" if close_positions else ""),
            level="ERROR", payload={"cancelled": cancelled, "closed": closed, "errors": errors},
        )
        await self.notifier.send("【股析】紧急停止已触发" + ("；" + "；".join(errors) if errors else ""))
        return {"stopped": True, "cancelled": cancelled, "closed": closed, "errors": errors}

    def status(self) -> dict:
        return {
            "running": self.running, "environment": self.settings.environment_name,
            "testnet": self.settings.testnet, "mainnet_unlocked": self.mainnet_unlocked,
            "credentials_configured": self.settings.credentials_configured,
            "api_status": self.exchange.api_status, "api_error": self.exchange.last_error,
            "public_ws": self.market.status, "private_ws": self.market.private_status,
            "auto_trade": self.auto_trade, "strategy": self.strategy.name,
            "last_signal": self.last_signal, "risk": self.risk.summary(),
        }
