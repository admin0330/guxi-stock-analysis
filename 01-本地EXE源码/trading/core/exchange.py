"""Binance USDⓈ-M Futures REST 封装；账户与行情查询专用。"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import requests

from trading.settings import TradingSettings


class ExchangeError(RuntimeError):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class CredentialsMissing(ExchangeError):
    pass


class ReadOnlyModeError(ExchangeError):
    """任何可能改变 Binance 状态的请求都会被拒绝。"""


class BinanceExchange:
    def __init__(self, settings: TradingSettings) -> None:
        self.settings = settings
        self.base_url = "https://testnet.binancefuture.com" if settings.testnet else "https://fapi.binance.com"
        self.stream_url = "wss://fstream.binancefuture.com" if settings.testnet else "wss://fstream.binance.com"
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": "Guxi/1.0"})
        if settings.api_key:
            self.http.headers["X-MBX-APIKEY"] = settings.api_key
        # ponytail: 单会话串行签名请求；出现真实吞吐瓶颈时再拆连接池。
        self._http_lock = asyncio.Lock()
        self._time_offset_ms = 0
        self.api_status = "未配置" if not settings.credentials_configured else "待连接"
        self.last_error = ""

    def require_credentials(self) -> None:
        if not self.settings.credentials_configured:
            raise CredentialsMissing("尚未配置 Binance API Key；请在 EXE 同目录 .env 中填写")

    def _request_sync(self, method: str, path: str, params: dict | None, signed: bool) -> Any:
        values = {key: value for key, value in (params or {}).items() if value is not None}
        if signed:
            self.require_credentials()
            values.update(timestamp=int(time.time() * 1000) + self._time_offset_ms, recvWindow=10000)
            values["signature"] = hmac.new(
                self.settings.api_secret.encode(), urlencode(values).encode(), hashlib.sha256,
            ).hexdigest()
        response = self.http.request(method, self.base_url + path, params=values, timeout=(3.05, 8))
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExchangeError(f"Binance 返回无效响应（HTTP {response.status_code}）", response.status_code) from exc
        if response.ok:
            return payload
        code = int(payload.get("code") or response.status_code)
        message = str(payload.get("msg") or f"HTTP {response.status_code}")
        if code in {-2014, -2015}:
            message = "API Key 无效、权限不足，或与 Binance Testnet/Mainnet 环境不匹配"
        raise ExchangeError(message, code)

    async def _call(self, method: str, path: str, *, params: dict | None = None, private: bool = True, retries: int = 0) -> Any:
        if method.upper() != "GET" and path != "/fapi/v1/listenKey":
            raise ReadOnlyModeError("当前 Binance 连接为只读查询模式，不支持交易写操作")
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                async with self._http_lock:
                    result = await asyncio.to_thread(self._request_sync, method, path, params, private)
                if private:
                    self.api_status, self.last_error = "已连接", ""
                return result
            except CredentialsMissing:
                raise
            except ExchangeError as exc:
                last, self.last_error = exc, str(exc)
                if exc.code == -1021 and attempt < retries:
                    await self.sync_time()
                elif exc.code not in {-1001, -1003, -1021} or attempt >= retries:
                    if private:
                        self.api_status = "异常"
                    raise
            except requests.RequestException as exc:
                last, self.last_error = exc, type(exc).__name__
                if attempt >= retries:
                    if private:
                        self.api_status = "异常"
                    raise ExchangeError(f"Binance 网络请求失败：{type(exc).__name__}") from exc
            await asyncio.sleep(min(2 ** attempt, 3))
        raise ExchangeError("Binance 请求失败") from last

    async def sync_time(self) -> None:
        payload = await self._call("GET", "/fapi/v1/time", private=False)
        self._time_offset_ms = int(payload["serverTime"]) - int(time.time() * 1000)

    @staticmethod
    def _side(side: str) -> str:
        return "Buy" if side.upper() == "BUY" else "Sell"

    @classmethod
    def _order(cls, row: dict) -> dict:
        statuses = {"NEW": "New", "PARTIALLY_FILLED": "PartiallyFilled", "FILLED": "Filled", "CANCELED": "Cancelled", "REJECTED": "Rejected", "EXPIRED": "Cancelled"}
        return {
            **row, "orderId": str(row.get("orderId") or ""),
            "orderLinkId": str(row.get("clientOrderId") or row.get("origClientOrderId") or ""),
            "side": cls._side(str(row.get("side") or "")),
            "orderType": str(row.get("type") or "").title().replace("_", ""),
            "qty": row.get("origQty") or row.get("quantity") or 0, "price": row.get("price") or 0,
            "orderStatus": statuses.get(str(row.get("status") or ""), str(row.get("status") or "")),
            "reduceOnly": bool(row.get("reduceOnly")),
            "createdTime": row.get("time") or row.get("updateTime") or int(time.time() * 1000),
            "updatedTime": row.get("updateTime") or row.get("time") or int(time.time() * 1000),
        }

    async def public_tickers(self, symbol: str | None = None) -> list[dict]:
        params = {"symbol": symbol} if symbol else None
        payload, premium = await asyncio.gather(
            self._call("GET", "/fapi/v1/ticker/24hr", private=False, retries=1, params=params),
            self._call("GET", "/fapi/v1/premiumIndex", private=False, retries=1, params=params),
        )
        rows = payload if isinstance(payload, list) else [payload]
        funding = {row["symbol"]: row for row in (premium if isinstance(premium, list) else [premium])}
        return [{
            **row, "price24hPcnt": float(row.get("priceChangePercent") or 0) / 100,
            "highPrice24h": row.get("highPrice"), "lowPrice24h": row.get("lowPrice"),
            "turnover24h": row.get("quoteVolume"),
            "fundingRate": funding.get(row.get("symbol"), {}).get("lastFundingRate", 0),
            "nextFundingTime": funding.get(row.get("symbol"), {}).get("nextFundingTime"),
        } for row in rows]

    async def historical_klines(self, symbol: str, interval: str, limit: int) -> list[dict]:
        interval = {"1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m", "60": "1h", "120": "2h", "240": "4h", "D": "1d"}[interval]
        rows = await self._call("GET", "/fapi/v1/klines", private=False, retries=1, params={"symbol": symbol, "interval": interval, "limit": limit})
        return [{"time": int(row[0]), "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]), "turnover": float(row[7]), "confirm": True} for row in rows]

    async def wallet(self) -> dict:
        account = await self._call("GET", "/fapi/v2/account", retries=1)
        stable = [row for row in account.get("assets", []) if row.get("asset") in {"USDT", "USDC", "FDUSD", "BNFCR"}]
        usdt = next((row for row in stable if row.get("asset") == "USDT"), {})
        funded = [row for row in stable if any(float(row.get(key) or 0) for key in ("walletBalance", "marginBalance", "availableBalance"))]
        display = usdt if any(float(usdt.get(key) or 0) for key in ("walletBalance", "marginBalance", "availableBalance")) else max(funded, key=lambda row: float(row.get("marginBalance") or row.get("walletBalance") or 0), default=usdt)
        return {
            "equity": float(display.get("marginBalance") or 0),
            "available_balance": float(display.get("availableBalance") or 0),
            "wallet_balance": float(display.get("walletBalance") or 0),
            "unrealised_pnl": float(display.get("unrealizedProfit") or 0),
            "balance_asset": display.get("asset") or "USDT",
            "trading_equity": float(usdt.get("marginBalance") or 0),
            "trading_available_balance": float(usdt.get("availableBalance") or 0),
        }

    async def positions(self, symbol: str | None = None) -> list[dict]:
        rows = await self._call("GET", "/fapi/v2/positionRisk", params={"symbol": symbol} if symbol else None, retries=1)
        now = int(time.time() * 1000)
        return [{**row, "size": abs(float(row.get("positionAmt") or 0)), "side": "Buy" if float(row.get("positionAmt") or 0) > 0 else "Sell", "positionValue": abs(float(row.get("positionAmt") or 0) * float(row.get("markPrice") or 0)), "createdTime": row.get("updateTime") or now, "updatedTime": row.get("updateTime") or now} for row in rows]

    async def open_orders(self, symbol: str | None = None) -> list[dict]:
        return [self._order(row) for row in await self._call("GET", "/fapi/v1/openOrders", params={"symbol": symbol} if symbol else None, retries=1)]

    async def order_history(self, symbol: str, order_link_id: str) -> list[dict]:
        try:
            return [self._order(await self._call("GET", "/fapi/v1/order", params={"symbol": symbol, "origClientOrderId": order_link_id}, retries=1))]
        except ExchangeError as exc:
            if exc.code == -2013:
                return []
            raise

    async def executions(self, limit: int = 50) -> list[dict]:
        result = []
        for symbol in self.settings.symbols:
            rows = await self._call("GET", "/fapi/v1/userTrades", params={"symbol": symbol, "limit": max(1, min(100, limit))}, retries=1)
            result.extend({**row, "execId": str(row.get("id") or ""), "orderId": str(row.get("orderId") or ""), "side": "Buy" if row.get("buyer") else "Sell", "execQty": row.get("qty") or 0, "execPrice": row.get("price") or 0, "execFee": row.get("commission") or 0, "closedPnl": row.get("realizedPnl") or 0, "execTime": row.get("time") or 0} for row in rows)
        return result

    async def create_listen_key(self) -> str:
        self.require_credentials()
        return str((await self._call("POST", "/fapi/v1/listenKey", private=False))["listenKey"])

    async def keepalive_listen_key(self) -> None:
        self.require_credentials()
        await self._call("PUT", "/fapi/v1/listenKey", private=False)
