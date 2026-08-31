"""异步 Binance 账户查询引擎：行情 → 账户快照 → 本地只读审计。"""
from __future__ import annotations

import asyncio
import logging

from trading.core.exchange import BinanceExchange
from trading.core.market_data import MarketData
from trading.core.order_manager import OrderManager
from trading.core.position_manager import PositionManager
from trading.settings import TradingSettings
from trading.storage import TradingStore

logger = logging.getLogger(__name__)


class TradingEngine:
    def __init__(self, settings: TradingSettings, store: TradingStore | None = None) -> None:
        self.settings = settings
        self.store = store or TradingStore()
        self.exchange = BinanceExchange(settings)
        self.market = MarketData(settings, self.exchange)
        self.orders = OrderManager(self.store)
        self.positions = PositionManager()
        self.running = False
        self.account = {"equity": 0.0, "available_balance": 0.0, "wallet_balance": 0.0, "unrealised_pnl": 0.0, "balance_asset": "USDT"}
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        if self.running:
            return
        self.store.open()
        self.running = True
        self.store.event("system", f"Binance 只读查询启动：{self.settings.environment_name}")
        await self.market.start()
        self._tasks = [
            asyncio.create_task(self._event_loop(), name="trading-events"),
            asyncio.create_task(self._account_loop(), name="trading-account-sync"),
        ]

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.market.stop()
        self.store.event("system", "Binance 只读查询已停止")
        self.store.close()
        logger.info("Binance 只读查询已停止")

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

    async def _account_loop(self) -> None:
        while self.running:
            if self.settings.credentials_configured:
                try:
                    await self.refresh_account()
                except Exception as exc:
                    self.store.event("api", f"账户同步失败：{exc}", level="ERROR")
            # 只读查询不需要高频轮询；页面刷新或 WebSocket 事件会立即更新展示。
            await asyncio.sleep(30)

    async def refresh_account(self) -> dict:
        self.exchange.require_credentials()
        wallet = await self.exchange.wallet()
        positions = await self.exchange.positions()
        orders = await self.exchange.open_orders()
        executions = await self.exchange.executions()
        self.account = wallet
        self.positions.sync(positions)
        self.orders.sync(orders)
        for row in executions:
            self.store.add_trade(row)
        return self.account_snapshot()

    def account_snapshot(self) -> dict:
        return {
            **self.account,
            "positions": self.positions.active(),
            "total_position_notional": self.positions.total_notional(),
        }

    def status(self) -> dict:
        return {
            "running": self.running, "environment": self.settings.environment_name,
            "testnet": self.settings.testnet, "read_only": True,
            "credentials_configured": self.settings.credentials_configured,
            "api_status": self.exchange.api_status, "api_error": self.exchange.last_error,
            "public_ws": self.market.status, "private_ws": self.market.private_status,
            "capabilities": ["market_data", "account", "positions", "open_orders", "order_history", "executions"],
        }
