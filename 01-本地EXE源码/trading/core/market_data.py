"""Binance 公共/用户 WebSocket、ticker 与 K线缓存。"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone

import pandas as pd
import websocket

from trading.core.exchange import BinanceExchange
from trading.settings import TradingSettings

logger = logging.getLogger(__name__)
BACKOFF = (1, 2, 5, 10, 20, 30)
WS_COOLDOWN = 300


class _Socket:
    def __init__(self, url: str, callback) -> None:
        self.connected = threading.Event()
        self.app = websocket.WebSocketApp(
            url, on_open=lambda _: self.connected.set(),
            on_message=lambda _, raw: callback(json.loads(raw)),
            on_close=lambda *_: self.connected.clear(),
            on_error=lambda _, error: logger.debug("Binance WS: %s", error),
        )
        self.thread = threading.Thread(target=self.app.run_forever, kwargs={"ping_interval": 20, "ping_timeout": 10}, daemon=True)
        self.thread.start()
        if not self.connected.wait(8):
            self.exit()
            raise ConnectionError("Binance WebSocket 连接超时")

    def is_connected(self) -> bool:
        return self.connected.is_set() and self.app.sock is not None and self.app.sock.connected

    def exit(self) -> None:
        self.connected.clear()
        self.app.close()


class MarketData:
    def __init__(self, settings: TradingSettings, exchange: BinanceExchange) -> None:
        self.settings, self.exchange = settings, exchange
        self.status = "disconnected"
        self.private_status = "未配置" if not settings.credentials_configured else "disconnected"
        self.latest: dict[str, dict] = {}
        self.klines = {symbol: deque(maxlen=settings.kline_limit) for symbol in settings.symbols}
        self.events: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._public: _Socket | None = None
        self._private: _Socket | None = None
        self._supervisor: asyncio.Task | None = None
        self._seed_task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None
        self._connect_lock = threading.Lock()
        self._listen_key = ""
        self._last_keepalive = 0.0

    async def start(self) -> None:
        if self._supervisor:
            return
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._supervisor = asyncio.create_task(self._run(), name="binance-ws-supervisor")
        self._seed_task = asyncio.create_task(self._seed_klines(), name="binance-kline-seed")

    async def stop(self) -> None:
        if self._stop:
            self._stop.set()
        for task in (self._supervisor, self._seed_task):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (self._supervisor, self._seed_task) if task), return_exceptions=True)
        self._supervisor = self._seed_task = None
        await asyncio.to_thread(self._close_sockets)
        self.status = "disconnected"
        self.private_status = "disconnected" if self.settings.credentials_configured else "未配置"
        self._publish({"type": "connection", "public": self.status, "private": self.private_status})

    async def _seed_klines(self) -> None:
        for symbol in self.settings.symbols:
            try:
                self.klines[symbol].extend(await self.exchange.historical_klines(symbol, self.settings.kline_interval, self.settings.kline_limit))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("%s 历史K线预载失败：%s", symbol, exc)

    async def _run(self) -> None:
        failures = 0
        assert self._stop is not None
        while not self._stop.is_set():
            try:
                public_ok = bool(self._public and self._public.is_connected())
                private_ok = not self.settings.credentials_configured or bool(self._private and self._private.is_connected())
                if not public_ok or not private_ok:
                    self.status = "connecting" if failures == 0 else "reconnecting"
                    if self.settings.credentials_configured and not private_ok:
                        self.private_status = self.status
                    self._publish({"type": "connection", "public": self.status, "private": self.private_status})
                    if self.settings.credentials_configured:
                        self._listen_key = await self.exchange.create_listen_key()
                        self._last_keepalive = time.monotonic()
                    await asyncio.to_thread(self._connect)
                    failures = 0
                    self.status = "connected"
                    self.private_status = "connected" if self.settings.credentials_configured else "未配置"
                    self._publish({"type": "connection", "public": self.status, "private": self.private_status})
                if self.settings.credentials_configured and time.monotonic() - self._last_keepalive > 25 * 60:
                    await self.exchange.keepalive_listen_key()
                    self._last_keepalive = time.monotonic()
                await asyncio.wait_for(self._stop.wait(), timeout=2)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                self.status = "reconnecting"
                if self.settings.credentials_configured:
                    self.private_status = "reconnecting"
                delay = WS_COOLDOWN if failures >= len(BACKOFF) else BACKOFF[failures - 1]
                if failures <= 3 or delay == WS_COOLDOWN:
                    logger.warning("Binance WebSocket 暂不可用，%s 秒后重试：%s", delay, type(exc).__name__)
                if delay == WS_COOLDOWN:
                    failures = 0
                self._publish({"type": "connection", "public": self.status, "private": self.private_status})
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

    def _connect(self) -> None:
        with self._connect_lock:
            self._close_sockets()
            interval = {"60": "1h", "120": "2h", "240": "4h", "D": "1d"}.get(self.settings.kline_interval, self.settings.kline_interval + "m")
            streams = []
            for symbol in self.settings.symbols:
                name = symbol.lower()
                streams.extend((f"{name}@ticker", f"{name}@markPrice@1s", f"{name}@kline_{interval}"))
            self._public = _Socket(f"{self.exchange.stream_url}/stream?streams={'/'.join(streams)}", self._public_callback)
            if self.settings.credentials_configured:
                self._private = _Socket(f"{self.exchange.stream_url}/ws/{self._listen_key}", self._private_callback)

    def _close_sockets(self) -> None:
        for socket in (self._private, self._public):
            if socket:
                try:
                    socket.exit()
                except Exception:
                    pass
        self._public = self._private = None

    def _public_callback(self, message: dict) -> None:
        data = message.get("data") or message
        kind = data.get("e")
        if kind == "24hrTicker":
            self._ticker(data)
        elif kind == "markPriceUpdate":
            self._funding(data)
        elif kind == "kline":
            self._kline(data)

    def _ticker(self, data: dict) -> None:
        symbol = str(data.get("s") or "")
        previous = self.latest.get(symbol, {})
        ticker = {
            "symbol": symbol, "asset": symbol.removesuffix("USDT"), "price": float(data.get("c") or 0),
            "change_24h": float(data.get("P") or 0), "high_24h": float(data.get("h") or 0),
            "low_24h": float(data.get("l") or 0), "quote_volume_24h": float(data.get("q") or 0),
            "funding_rate": float(previous.get("funding_rate") or 0),
            "next_funding_time": previous.get("next_funding_time"), "source": "Binance WebSocket",
            "event_time": int(data.get("E") or time.time() * 1000), "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.latest[symbol] = {**previous, **ticker}
        self._threadsafe_publish({"type": "ticker", "data": ticker})

    def _funding(self, data: dict) -> None:
        symbol = str(data.get("s") or "")
        previous = self.latest.get(symbol, {})
        previous.update(funding_rate=float(data.get("r") or 0), next_funding_time=data.get("T"), markPrice=data.get("p"))
        self.latest[symbol] = previous
        if previous.get("price"):
            self._threadsafe_publish({"type": "ticker", "data": {key: value for key, value in previous.items() if key in self._ticker_keys()}})

    def _kline(self, data: dict) -> None:
        row = data.get("k") or {}
        symbol = str(row.get("s") or data.get("s") or "")
        candle = {"time": int(row.get("t") or 0), "open": float(row.get("o") or 0), "high": float(row.get("h") or 0), "low": float(row.get("l") or 0), "close": float(row.get("c") or 0), "volume": float(row.get("v") or 0), "turnover": float(row.get("q") or 0), "confirm": bool(row.get("x"))}
        cache = self.klines.setdefault(symbol, deque(maxlen=self.settings.kline_limit))
        if cache and cache[-1]["time"] == candle["time"]:
            cache[-1] = candle
        else:
            cache.append(candle)
        self._threadsafe_publish({"type": "kline", "symbol": symbol, "data": candle})

    def _private_callback(self, message: dict) -> None:
        kind = message.get("e")
        if kind == "ORDER_TRADE_UPDATE":
            row = message.get("o") or {}
            order = self.exchange._order({**row, "clientOrderId": row.get("c"), "orderId": row.get("i"), "side": row.get("S"), "type": row.get("o"), "origQty": row.get("q"), "price": row.get("p"), "status": row.get("X"), "reduceOnly": row.get("R"), "updateTime": message.get("E")})
            self._threadsafe_publish({"type": "order", "data": [order]})
            if row.get("x") == "TRADE":
                trade = {"execId": str(row.get("t") or ""), "orderId": str(row.get("i") or ""), "symbol": row.get("s"), "side": self.exchange._side(str(row.get("S") or "")), "execQty": row.get("l") or 0, "execPrice": row.get("L") or 0, "execFee": row.get("n") or 0, "closedPnl": row.get("rp") or 0, "execTime": message.get("E") or 0}
                self._threadsafe_publish({"type": "execution", "data": [trade]})
        elif kind == "ACCOUNT_UPDATE":
            now = message.get("E") or int(time.time() * 1000)
            positions = []
            for row in (message.get("a") or {}).get("P", []):
                amount = float(row.get("pa") or 0)
                positions.append({"symbol": row.get("s"), "size": abs(amount), "side": "Buy" if amount > 0 else "Sell", "positionValue": abs(amount * float(self.latest.get(row.get("s"), {}).get("price") or 0)), "createdTime": now, "updatedTime": now})
            self._threadsafe_publish({"type": "position", "data": positions})
        elif kind == "listenKeyExpired":
            if self._private:
                self._private.exit()

    @staticmethod
    def _ticker_keys() -> set[str]:
        return {"symbol", "asset", "price", "change_24h", "high_24h", "low_24h", "quote_volume_24h", "funding_rate", "next_funding_time", "source", "event_time", "updated_at"}

    def _threadsafe_publish(self, message: dict) -> None:
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._publish, message)

    def _publish(self, message: dict) -> None:
        if not self.events.full():
            self.events.put_nowait(message)
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(message)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._subscribers.add(queue)
        queue.put_nowait(self.snapshot())
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def frame(self, symbol: str, confirmed_only: bool = True) -> pd.DataFrame:
        rows = list(self.klines.get(symbol, []))
        return pd.DataFrame([row for row in rows if row.get("confirm")]) if confirmed_only else pd.DataFrame(rows)

    def snapshot(self) -> dict:
        return {"type": "snapshot", "public_ws": self.status, "private_ws": self.private_status, "tickers": [{key: value for key, value in row.items() if key in self._ticker_keys()} for row in self.latest.values()], "subscriber_count": len(self._subscribers)}
