import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pandas as pd
from fastapi.testclient import TestClient

from backend.main import app
from trading.core.order_manager import OrderManager
from trading.core.position_manager import PositionManager
from trading.core.engine import TradingEngine
from trading.core.exchange import BinanceExchange, CredentialsMissing, ExchangeError
from trading.core.market_data import MarketData
from trading.core.risk_manager import RiskContext, RiskManager
from trading.core.strategy_base import Signal, SignalAction
from trading.settings import TradingSettings, load_settings, save_public_settings
from trading.service import trading_service
from trading.storage import TradingStore
from trading.strategies.ma_cross import MaCrossStrategy


class TradingSettingsTests(unittest.TestCase):
    def test_loader_forces_testnet_without_both_mainnet_switches(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.yaml"
            path.write_text("allow_mainnet: false\nenable_auto_trade: false\n", encoding="utf-8")
            with patch("trading.settings.settings_path", return_value=path), patch.dict(
                os.environ, {"BINANCE_TESTNET": "false", "BINANCE_API_KEY": "", "BINANCE_API_SECRET": ""}, clear=False,
            ):
                settings = load_settings()
            self.assertTrue(settings.testnet)
            self.assertFalse(settings.enable_auto_trade)
            self.assertNotIn("api_key", settings.public_dict())
            self.assertNotIn("webhook_url", TradingSettings(webhook_url="https://example.invalid/secret").public_dict())

    def test_invalid_strategy_and_risk_relationships_are_rejected(self):
        with self.assertRaises(ValueError):
            TradingSettings(ma_fast=30, ma_slow=20)
        with self.assertRaises(ValueError):
            TradingSettings(max_position_pct=0.2, max_total_position_pct=0.1)

    def test_binance_testnet_uses_futures_endpoint(self):
        exchange = BinanceExchange(TradingSettings())
        self.assertEqual(exchange.base_url, "https://testnet.binancefuture.com")

    def test_mainnet_requires_both_switches_and_session_phrase(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.yaml"
            path.write_text("allow_mainnet: true\nenable_auto_trade: false\n", encoding="utf-8")
            with patch("trading.settings.settings_path", return_value=path), patch.dict(
                os.environ, {"BINANCE_TESTNET": "false", "BINANCE_API_KEY": "", "BINANCE_API_SECRET": ""}, clear=False,
            ):
                settings = load_settings()
        self.assertFalse(settings.testnet)
        engine = TradingEngine(settings)
        with self.assertRaises(PermissionError):
            engine.unlock_mainnet("错误短语")
        self.assertTrue(engine.unlock_mainnet("我确认使用Binance主网实盘")["mainnet_unlocked"])
        engine.store.close()

    def test_public_settings_persist_without_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.yaml"
            path.write_text("strategy: ma_cross\nma_fast: 9\nma_slow: 21\n", encoding="utf-8")
            settings = TradingSettings(api_key="secret-key", api_secret="secret-value", config_path=path)
            with patch("trading.settings.settings_path", return_value=path), patch.dict(
                os.environ, {"BINANCE_API_KEY": "", "BINANCE_API_SECRET": ""}, clear=False,
            ):
                updated = save_public_settings(settings, {"ma_fast": 10, "ma_slow": 30})
            text = path.read_text(encoding="utf-8")
            self.assertEqual((updated.ma_fast, updated.ma_slow), (10, 30))
            self.assertNotIn("secret", text)


class TradingLogicTests(unittest.TestCase):
    def test_ma_cross_outputs_buy_on_fresh_cross(self):
        closes = [10] * 21 + [9, 8, 8, 12]
        frame = pd.DataFrame({"close": closes, "time": list(range(len(closes)))})
        signal = MaCrossStrategy(2, 3).evaluate(frame)
        self.assertEqual(signal.action, SignalAction.BUY)

    def test_risk_rejects_oversize_and_always_allows_reduce_only(self):
        manager = RiskManager(TradingSettings())
        rejected = manager.check(RiskContext(
            equity=1000, available_balance=1000, current_price=100, order_qty=20, leverage=3,
        ))
        self.assertFalse(rejected.allowed)
        manager.stop("测试熔断")
        closing = manager.check(RiskContext(
            equity=0, available_balance=0, current_price=100, order_qty=1, leverage=3, reduce_only=True,
        ))
        self.assertTrue(closing.allowed)

    def test_daily_loss_and_consecutive_loss_trip_circuit(self):
        manager = RiskManager(TradingSettings(max_daily_loss_pct=0.05, max_consecutive_losses=3))
        decision = manager.check(RiskContext(
            equity=1000, available_balance=500, current_price=100, order_qty=0.1,
            leverage=3, daily_closed_pnl=-50, consecutive_losses=3,
        ))
        self.assertFalse(decision.allowed)
        self.assertTrue(manager.emergency_stopped)

    def test_binance_ticker_and_kline_messages_update_local_cache(self):
        settings = TradingSettings()
        market = MarketData(settings, object())
        market._public_callback({"data": {"e": "24hrTicker", "s": "BTCUSDT", "E": 1, "c": "64000", "P": "1", "h": "65000", "l": "62000", "q": "1000"}})
        market._public_callback({"data": {"e": "kline", "s": "BTCUSDT", "k": {"s": "BTCUSDT", "t": 1, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "3", "q": "4", "x": True}}})
        self.assertEqual(market.latest["BTCUSDT"]["price"], 64000)
        self.assertTrue(market.klines["BTCUSDT"][-1]["confirm"])


class TradingStoreTests(unittest.TestCase):
    def test_sqlite_audit_and_order_idempotency(self):
        with tempfile.TemporaryDirectory() as folder:
            store = TradingStore(Path(folder) / "trading.sqlite3")
            store.event("risk", "拒绝原因", level="WARNING")
            manager = OrderManager(store, 60)
            link_id = manager.link_id("BTCUSDT", "Buy", "strategy", "one-candle")
            self.assertTrue(manager.reserve(link_id))
            self.assertFalse(manager.reserve(link_id))
            manager.update({
                "orderLinkId": link_id, "orderId": "1", "symbol": "BTCUSDT", "side": "Buy",
                "orderType": "Market", "qty": "0.001", "orderStatus": "New",
            })
            self.assertEqual(store.rows("orders")[0]["status"], "New")
            self.assertEqual(store.rows("events")[0]["message"], "拒绝原因")
            store.close()

    def test_exchange_timestamps_and_empty_position_sync_are_recoverable(self):
        with tempfile.TemporaryDirectory() as folder:
            store = TradingStore(Path(folder) / "trading.sqlite3")
            store.add_trade({
                "execId": "e1", "symbol": "BTCUSDT", "side": "Buy", "execQty": "0.01",
                "execPrice": "50000", "closedPnl": "-2", "execTime": "1787558400000",
            })
            self.assertTrue(store.rows("trades")[0]["executed_at"].startswith("2026-"))
            positions = PositionManager()
            positions.sync([{"symbol": "BTCUSDT", "size": "0.01"}])
            self.assertEqual(len(positions.active()), 1)
            positions.sync([])
            self.assertEqual(positions.active(), [])
            store.close()


class TradingApiSafetyTests(unittest.TestCase):
    def test_emergency_stop_needs_second_click_not_typed_phrase(self):
        client = TestClient(app)
        token = client.get("/api/trading/bootstrap").json()["write_token"]
        with patch("backend.api.trading_routes.trading_service.engine.emergency_stop", new=AsyncMock(return_value={"stopped": True})):
            response = client.post("/api/trading/emergency-stop", json={"close_positions": False}, headers={"X-Trade-Token": token})
        self.assertEqual(response.status_code, 200)

    def test_bootstrap_exposes_session_token_but_never_secrets(self):
        client = TestClient(app)
        response = client.get("/api/trading/bootstrap")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["write_token"])
        text = response.text.lower()
        self.assertNotIn("api_secret", text)
        self.assertNotIn("api_key", text)

    def test_write_requires_token_and_missing_key_keeps_analysis_available(self):
        client = TestClient(app)
        self.assertEqual(client.post("/api/trading/auto", json={"enabled": True}).status_code, 403)
        token = client.get("/api/trading/bootstrap").json()["write_token"]
        with patch.object(
            trading_service.engine.exchange, "require_credentials",
            side_effect=CredentialsMissing("尚未配置 Binance API Key"),
        ):
            response = client.post("/api/trading/auto", json={"enabled": True}, headers={"X-Trade-Token": token})
        self.assertEqual(response.status_code, 428)
        self.assertEqual(client.get("/api/health").status_code, 200)

    def test_open_orders_today_trades_and_amend_contract(self):
        client = TestClient(app)
        with patch("backend.api.trading_routes.trading_service.engine.store.rows") as rows:
            rows.return_value = [
                {"status": "New", "executed_at": TradingStore.now()},
                {"status": "Filled", "executed_at": "2020-01-01T00:00:00+00:00"},
            ]
            self.assertEqual(len(client.get("/api/trading/orders?open_only=true").json()["items"]), 1)
            self.assertEqual(len(client.get("/api/trading/trades?today=true").json()["items"]), 1)
        token = client.get("/api/trading/bootstrap").json()["write_token"]
        with patch("backend.api.trading_routes.trading_service.engine.amend_order", new=AsyncMock(return_value={"orderId": "1"})):
            response = client.post(
                "/api/trading/orders/amend", json={"symbol": "BTCUSDT", "order_id": "1", "price": 50000},
                headers={"X-Trade-Token": token},
            )
        self.assertEqual(response.status_code, 200)

    def test_invalid_exchange_key_failure_does_not_break_a_share_api(self):
        client = TestClient(app)
        with patch(
            "backend.api.trading_routes.trading_service.engine.refresh_account",
            new=AsyncMock(side_effect=ExchangeError("API key is invalid", 10003)),
        ):
            self.assertEqual(client.get("/api/trading/account").status_code, 502)
        self.assertEqual(client.get("/api/health").status_code, 200)


class TradingEngineSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_wallet_displays_funded_usdc_but_keeps_usdt_risk_balance_zero(self):
        exchange = BinanceExchange(TradingSettings(api_key="key", api_secret="secret"))
        exchange._call = AsyncMock(return_value={"assets": [
            {"asset": "USDT", "walletBalance": "0", "marginBalance": "0", "availableBalance": "0"},
            {"asset": "USDC", "walletBalance": "5000", "marginBalance": "5000", "availableBalance": "5000", "unrealizedProfit": "0"},
        ]})
        wallet = await exchange.wallet()
        self.assertEqual((wallet["available_balance"], wallet["balance_asset"]), (5000, "USDC"))
        self.assertEqual(wallet["trading_available_balance"], 0)

    async def test_invalid_key_reports_environment_mismatch_without_secret(self):
        exchange = BinanceExchange(TradingSettings(api_key="secret-key", api_secret="secret-value"))
        class Response:
            ok, status_code = False, 401
            @staticmethod
            def json(): return {"code": -2015, "msg": "Rejected secret-key"}
        with patch.object(exchange.http, "request", return_value=Response()):
            with self.assertRaisesRegex(ExchangeError, "与 Binance Testnet/Mainnet 环境不匹配") as raised:
                await exchange.wallet()
        self.assertNotIn("secret-key", str(raised.exception))

    async def test_limit_entry_is_cancelled_if_protection_order_fails(self):
        exchange = BinanceExchange(TradingSettings(api_key="key", api_secret="secret"))
        exchange.normalize_order = AsyncMock(return_value=("0.001", "50000"))
        exchange._call = AsyncMock(side_effect=[
            {"orderId": 7, "clientOrderId": "entry", "side": "BUY", "type": "LIMIT", "origQty": "0.001", "price": "50000", "status": "NEW"},
            ExchangeError("保护单拒绝", -2021),
            {"orderId": 7, "status": "CANCELED"},
        ])
        with self.assertRaisesRegex(ExchangeError, "已安全回退"):
            await exchange.place_order(symbol="BTCUSDT", side="Buy", order_type="Limit", qty=.001, price=50000, order_link_id="entry", stop_loss=49000)
        self.assertEqual(exchange._call.await_args_list[-1].args[:2], ("DELETE", "/fapi/v1/order"))

    async def test_order_runs_through_risk_and_emergency_disables_auto(self):
        class Exchange:
            def __init__(self): self.orders, self.closed, self.amended = [], [], []
            def require_credentials(self): return None
            async def set_leverage(self, symbol, leverage): return None
            async def place_order(self, **kwargs):
                row = {**kwargs, "orderId": "1", "orderStatus": "Submitted"}
                self.orders.append(row)
                return row
            async def cancel_all(self): return [{"success": True}]
            async def close_all_positions(self): return []
            async def close_position(self, symbol):
                self.closed.append(symbol)
                return {"orderLinkId": "close-1", "orderId": "2", "symbol": symbol, "side": "Sell", "orderType": "Market", "qty": "0.001", "orderStatus": "Submitted", "reduceOnly": True}
            async def amend_order(self, symbol, **kwargs):
                self.amended.append((symbol, kwargs))
                return {"orderId": kwargs.get("order_id"), "symbol": symbol}

        class Notifier:
            async def send(self, message): return None
            async def close(self): return None

        with tempfile.TemporaryDirectory() as folder:
            settings = TradingSettings(api_key="test", api_secret="test")
            store = TradingStore(Path(folder) / "trading.sqlite3")
            engine = TradingEngine(settings, store)
            engine.exchange = Exchange()
            engine.notifier = Notifier()
            engine.account.update(equity=1000, available_balance=1000)
            engine.market.latest["BTCUSDT"] = {"price": 50000}
            result = await engine.manual_order(symbol="BTCUSDT", side="Buy", order_type="Market", qty=.001)
            self.assertEqual(result["orderStatus"], "Submitted")
            engine.auto_trade = True
            stopped = await engine.emergency_stop()
            self.assertFalse(engine.auto_trade)
            self.assertTrue(engine.risk.emergency_stopped)
            self.assertTrue(stopped["cancelled"])
            store.close()

    async def test_recovered_position_blocks_duplicate_and_close_signal_only_reduces(self):
        class Exchange:
            def __init__(self): self.placed, self.closed = 0, []
            async def close_position(self, symbol):
                self.closed.append(symbol)
                return {"orderLinkId": "close", "symbol": symbol, "side": "Sell", "orderType": "Market", "qty": "0.01", "orderStatus": "Submitted", "reduceOnly": True}

        class Notifier:
            async def send(self, message): return None
            async def close(self): return None

        with tempfile.TemporaryDirectory() as folder:
            engine = TradingEngine(TradingSettings(), TradingStore(Path(folder) / "trading.sqlite3"))
            engine.exchange, engine.notifier = Exchange(), Notifier()
            engine.positions.sync([{"symbol": "BTCUSDT", "side": "Buy", "size": "0.01", "positionValue": "500"}])
            same = await engine.execute_signal("BTCUSDT", Signal(SignalAction.BUY, 1, "仍看多", "test", "1"))
            closed = await engine.execute_signal("BTCUSDT", Signal(SignalAction.CLOSE, 1, "退出", "test", "2"))
            self.assertIsNone(same)
            self.assertEqual(engine.exchange.closed, ["BTCUSDT"])
            self.assertTrue(closed["reduceOnly"])
            engine.store.close()

    async def test_amend_order_is_audited(self):
        class Exchange:
            async def amend_order(self, symbol, **kwargs): return {"symbol": symbol, **kwargs}

        with tempfile.TemporaryDirectory() as folder:
            store = TradingStore(Path(folder) / "trading.sqlite3")
            engine = TradingEngine(TradingSettings(), store)
            engine.exchange = Exchange()
            result = await engine.amend_order("BTCUSDT", order_id="1", qty=.002, price=50000)
            self.assertEqual(result["qty"], .002)
            self.assertEqual(store.rows("events")[0]["message"], "改单请求已提交")
            store.close()

    async def test_emergency_close_is_attempted_even_when_cancel_fails(self):
        class Exchange:
            async def cancel_all(self): raise RuntimeError("撤单链路异常")
            async def close_all_positions(self): return [{"symbol": "BTCUSDT", "orderId": "close"}]

        class Notifier:
            async def send(self, message): return None
            async def close(self): return None

        with tempfile.TemporaryDirectory() as folder:
            store = TradingStore(Path(folder) / "trading.sqlite3")
            engine = TradingEngine(TradingSettings(api_key="test", api_secret="test"), store)
            engine.exchange, engine.notifier = Exchange(), Notifier()
            result = await engine.emergency_stop(close_positions=True)
            self.assertTrue(result["closed"])
            self.assertIn("撤单失败", result["errors"][0])
            self.assertTrue(engine.risk.emergency_stopped)
            store.close()

    async def test_account_refresh_restores_positions_orders_and_executions(self):
        class Exchange:
            def require_credentials(self): return None
            async def wallet(self): return {"equity": 1000, "available_balance": 800, "unrealised_pnl": 5}
            async def positions(self): return [{"symbol": "BTCUSDT", "side": "Buy", "size": "0.01", "positionValue": "500"}]
            async def open_orders(self): return [{"orderLinkId": "recover-1", "orderId": "1", "symbol": "ETHUSDT", "side": "Buy", "orderType": "Limit", "qty": "0.01", "price": "1000", "orderStatus": "New"}]
            async def executions(self): return [{"execId": "exec-1", "orderId": "1", "symbol": "ETHUSDT", "side": "Buy", "execQty": "0.01", "execPrice": "1000", "execTime": "1787558400000"}]
            async def order_history(self, symbol, order_link_id): return [{"orderLinkId": order_link_id, "orderId": "2", "symbol": symbol, "side": "Buy", "orderType": "Market", "qty": "0.001", "orderStatus": "Filled"}]

        with tempfile.TemporaryDirectory() as folder:
            store = TradingStore(Path(folder) / "trading.sqlite3")
            engine = TradingEngine(TradingSettings(api_key="test", api_secret="test"), store)
            engine.exchange = Exchange()
            store.upsert_order({"orderLinkId": "unknown-1", "symbol": "BTCUSDT", "side": "Buy", "orderType": "Market", "qty": "0.001", "orderStatus": "SubmitUnknown"}, "test")
            snapshot = await engine.refresh_account()
            self.assertEqual(snapshot["equity"], 1000)
            self.assertEqual(engine.positions.active()[0]["symbol"], "BTCUSDT")
            statuses = {row["order_link_id"]: row["status"] for row in store.rows("orders")}
            self.assertEqual(statuses, {"recover-1": "New", "unknown-1": "Filled"})
            self.assertEqual(store.rows("trades")[0]["exec_id"], "exec-1")
            store.close()

    async def test_unknown_submit_result_keeps_idempotency_guard(self):
        class Exchange:
            async def set_leverage(self, symbol, leverage): return None
            async def place_order(self, **kwargs): raise RuntimeError("响应超时")

        class Notifier:
            async def send(self, message): return None
            async def close(self): return None

        with tempfile.TemporaryDirectory() as folder:
            store = TradingStore(Path(folder) / "trading.sqlite3")
            engine = TradingEngine(TradingSettings(), store)
            engine.exchange, engine.notifier = Exchange(), Notifier()
            engine.account.update(equity=1000, available_balance=1000)
            engine.market.latest["BTCUSDT"] = {"price": 50000}
            kwargs = dict(symbol="BTCUSDT", side="Buy", order_type="Market", qty=.001, source="strategy:test", signal_time="same-candle")
            with self.assertRaises(RuntimeError):
                await engine.manual_order(**kwargs)
            self.assertEqual(store.rows("orders")[0]["status"], "SubmitUnknown")
            with self.assertRaises(PermissionError):
                await engine.manual_order(**kwargs)
            store.close()

    async def test_exchange_rejection_is_not_marked_as_unknown(self):
        class Exchange:
            async def set_leverage(self, symbol, leverage): return None
            async def place_order(self, **kwargs): raise ExchangeError("监管限制", 10024)

        with tempfile.TemporaryDirectory() as folder:
            store = TradingStore(Path(folder) / "trading.sqlite3")
            engine = TradingEngine(TradingSettings(), store)
            engine.exchange = Exchange()
            engine.account.update(equity=1000, available_balance=1000)
            engine.market.latest["BTCUSDT"] = {"price": 50000}
            with self.assertRaises(ExchangeError):
                await engine.manual_order(symbol="BTCUSDT", side="Buy", order_type="Market", qty=.001)
            self.assertEqual(store.rows("orders")[0]["status"], "Rejected")
            store.close()

    async def test_auto_switch_strictly_controls_closed_candle_execution(self):
        class Strategy:
            name = "test"
            def evaluate(self, candles): return Signal(SignalAction.BUY, 1, "测试信号", "test", "1")

        with tempfile.TemporaryDirectory() as folder:
            store = TradingStore(Path(folder) / "trading.sqlite3")
            engine = TradingEngine(TradingSettings(), store)
            engine.strategy = Strategy()
            engine.market.frame = lambda symbol: pd.DataFrame({"close": [1]})
            engine.execute_signal = AsyncMock(return_value={"orderId": "1"})
            await engine._on_closed_candle("BTCUSDT", {"time": 1})
            engine.execute_signal.assert_not_awaited()
            engine.auto_trade = True
            await engine._on_closed_candle("BTCUSDT", {"time": 2})
            engine.execute_signal.assert_awaited_once()
            store.close()


if __name__ == "__main__":
    unittest.main()
