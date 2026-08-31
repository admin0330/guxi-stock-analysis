import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.main import app
from trading.core.engine import TradingEngine
from trading.core.exchange import BinanceExchange, ExchangeError, ReadOnlyModeError
from trading.core.market_data import MarketData
from trading.core.order_manager import OrderManager
from trading.core.position_manager import PositionManager
from trading.settings import TradingSettings, load_settings
from trading.storage import TradingStore


class TradingSettingsTests(unittest.TestCase):
    def test_loader_keeps_query_environment_and_removes_legacy_write_switches(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.yaml"
            path.write_text("allow_mainnet: false\nenable_auto_trade: false\n", encoding="utf-8")
            with patch("trading.settings.settings_path", return_value=path), patch.dict(
                os.environ, {"BINANCE_TESTNET": "false", "BINANCE_API_KEY": "", "BINANCE_API_SECRET": ""}, clear=False,
            ):
                settings = load_settings()
            self.assertFalse(hasattr(settings, "enable_auto_trade"))
            self.assertFalse(hasattr(settings, "allow_mainnet"))
            self.assertTrue(settings.public_dict()["read_only"])
            self.assertNotIn("api_key", settings.public_dict())
            self.assertNotIn("webhook_url", TradingSettings().public_dict())

    def test_binance_testnet_uses_futures_endpoint(self):
        self.assertEqual(BinanceExchange(TradingSettings()).base_url, "https://testnet.binancefuture.com")

    def test_public_settings_expose_query_capabilities_only(self):
        settings = TradingSettings(api_key="secret-key", api_secret="secret-value")
        public = settings.public_dict()
        self.assertTrue(public["read_only"])
        self.assertEqual(public["capabilities"], ["market_data", "account", "positions", "open_orders", "order_history", "executions"])
        self.assertNotIn("secret-key", str(public))


class QueryCoreTests(unittest.TestCase):
    def test_binance_ticker_and_kline_messages_update_local_cache(self):
        market = MarketData(TradingSettings(), object())
        market._public_callback({"data": {"e": "24hrTicker", "s": "BTCUSDT", "E": 1, "c": "64000", "P": "1", "h": "65000", "l": "62000", "q": "1000"}})
        market._public_callback({"data": {"e": "kline", "s": "BTCUSDT", "k": {"s": "BTCUSDT", "t": 1, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "3", "q": "4", "x": True}}})
        self.assertEqual(market.latest["BTCUSDT"]["price"], 64000)
        self.assertTrue(market.klines["BTCUSDT"][-1]["confirm"])

    def test_sqlite_audit_and_order_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            store = TradingStore(Path(folder) / "trading.sqlite3")
            store.event("api", "查询完成")
            OrderManager(store).update({
                "orderLinkId": "readonly-1", "orderId": "1", "symbol": "BTCUSDT", "side": "Buy",
                "orderType": "Market", "qty": "0.001", "orderStatus": "New",
            })
            self.assertEqual(store.rows("orders")[0]["status"], "New")
            self.assertEqual(store.rows("events")[0]["message"], "查询完成")
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
    def test_bootstrap_is_read_only_and_never_exposes_secrets(self):
        body = TestClient(app).get("/api/trading/bootstrap").json()
        self.assertTrue(body["read_only"])
        self.assertNotIn("write_token", body)
        self.assertNotIn("api_secret", str(body).lower())
        self.assertNotIn("api_key", str(body).lower())

    def test_legacy_write_routes_are_removed_and_analysis_stays_available(self):
        client = TestClient(app)
        for path in (
            "/api/trading/auto", "/api/trading/mainnet/unlock", "/api/trading/orders",
            "/api/trading/orders/cancel", "/api/trading/orders/amend", "/api/trading/positions/close",
            "/api/trading/emergency-stop",
        ):
            self.assertIn(client.post(path, json={}).status_code, {404, 405}, path)
        self.assertEqual(client.get("/api/health").status_code, 200)

    def test_read_only_order_and_trade_queries(self):
        client = TestClient(app)
        with patch("backend.api.trading_routes.trading_service.engine.store.rows") as rows:
            rows.return_value = [
                {"status": "New", "executed_at": TradingStore.now()},
                {"status": "Filled", "executed_at": "2020-01-01T00:00:00+00:00"},
            ]
            self.assertEqual(len(client.get("/api/trading/orders?open_only=true").json()["items"]), 1)
            self.assertEqual(len(client.get("/api/trading/trades?today=true").json()["items"]), 1)

    def test_invalid_exchange_key_failure_does_not_break_a_share_api(self):
        client = TestClient(app)
        with patch(
            "backend.api.trading_routes.trading_service.engine.refresh_account",
            new=AsyncMock(side_effect=ExchangeError("API key is invalid", 10003)),
        ):
            self.assertEqual(client.get("/api/trading/account").status_code, 502)
        self.assertEqual(client.get("/api/health").status_code, 200)


class TradingEngineSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_wallet_displays_funded_usdc_without_fabricating_usdt_balance(self):
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
            def json():
                return {"code": -2015, "msg": "Rejected secret-key"}

        with patch.object(exchange.http, "request", return_value=Response()):
            with self.assertRaisesRegex(ExchangeError, "与 Binance Testnet/Mainnet 环境不匹配") as raised:
                await exchange.wallet()
        self.assertNotIn("secret-key", str(raised.exception))

    async def test_exchange_rejects_all_account_mutations(self):
        exchange = BinanceExchange(TradingSettings(api_key="key", api_secret="secret"))
        exchange._request_sync = lambda *args, **kwargs: self.fail("只读模式不应触发 Binance 写请求")
        with self.assertRaises(ReadOnlyModeError):
            await exchange._call("POST", "/fapi/v1/order", params={"symbol": "BTCUSDT"})
        with self.assertRaises(ReadOnlyModeError):
            await exchange._call("DELETE", "/fapi/v1/allOpenOrders", params={"symbol": "BTCUSDT"})

    async def test_account_refresh_syncs_only_exchange_snapshots(self):
        class Exchange:
            def require_credentials(self):
                return None

            async def wallet(self):
                return {"equity": 1000, "available_balance": 800, "unrealised_pnl": 5}

            async def positions(self):
                return [{"symbol": "BTCUSDT", "side": "Buy", "size": "0.01", "positionValue": "500"}]

            async def open_orders(self):
                return [{"orderLinkId": "exchange-1", "orderId": "1", "symbol": "ETHUSDT", "side": "Buy", "orderType": "Limit", "qty": "0.01", "price": "1000", "orderStatus": "New"}]

            async def executions(self):
                return [{"execId": "exec-1", "orderId": "1", "symbol": "ETHUSDT", "side": "Buy", "execQty": "0.01", "execPrice": "1000", "execTime": "1787558400000"}]

        with tempfile.TemporaryDirectory() as folder:
            store = TradingStore(Path(folder) / "trading.sqlite3")
            engine = TradingEngine(TradingSettings(api_key="test", api_secret="test"), store)
            engine.exchange = Exchange()
            store.upsert_order({"orderLinkId": "unknown-1", "symbol": "BTCUSDT", "side": "Buy", "orderType": "Market", "qty": "0.001", "orderStatus": "SubmitUnknown"}, "test")
            snapshot = await engine.refresh_account()
            self.assertEqual(snapshot["equity"], 1000)
            self.assertEqual(engine.positions.active()[0]["symbol"], "BTCUSDT")
            statuses = {row["order_link_id"]: row["status"] for row in store.rows("orders")}
            self.assertEqual(statuses, {"exchange-1": "New", "unknown-1": "SubmitUnknown"})
            self.assertEqual(store.rows("trades")[0]["exec_id"], "exec-1")
            store.close()

    async def test_engine_has_no_order_or_automation_methods(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = TradingEngine(TradingSettings(), TradingStore(Path(folder) / "trading.sqlite3"))
            for name in ("manual_order", "execute_signal", "close_position", "cancel_order", "amend_order", "set_auto_trade", "unlock_mainnet", "emergency_stop"):
                self.assertFalse(hasattr(engine, name), name)
            self.assertTrue(engine.status()["read_only"])
            engine.store.close()


if __name__ == "__main__":
    unittest.main()
