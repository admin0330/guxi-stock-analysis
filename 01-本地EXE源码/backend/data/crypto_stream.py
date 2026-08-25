"""BTC / ETH 实时行情：单一 Binance 上游连接，本地 WebSocket 广播。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from websockets.asyncio.client import connect

from backend import config
from backend.data import crypto

logger = logging.getLogger(__name__)

STREAM_URL = (
    "wss://stream.binance.com:9443/stream?"
    "streams=btcusdt@miniTicker/ethusdt@miniTicker"
)
BACKOFF_SECONDS = (1, 2, 5, 10, 20, 30)


def parse_binance_message(raw: str) -> dict:
    """把 Binance combined miniTicker 消息压缩为前端需要的字段。"""
    payload = json.loads(raw)
    data = payload.get("data", payload)
    symbol = str(data.get("s", ""))
    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise ValueError("非目标交易对")
    price, opened = float(data["c"]), float(data["o"])
    event_ms = int(data.get("E") or time.time() * 1000)
    return {
        "asset": symbol.removesuffix("USDT"),
        "price": price,
        "change_24h": (price / opened - 1) * 100 if opened else 0.0,
        "high_24h": float(data.get("h") or 0),
        "low_24h": float(data.get("l") or 0),
        "volume_24h": float(data.get("v") or 0),
        "quote_volume_24h": float(data.get("q") or 0),
        "source": "Binance WebSocket",
        "updated_at": datetime.fromtimestamp(event_ms / 1000, timezone.utc).isoformat(),
        "event_time": event_ms,
        "stale": False,
    }


class CryptoStream:
    def __init__(self) -> None:
        self.status = "disconnected"
        self.latest: dict[str, dict] = {}
        self.last_ws_event = 0.0
        self._subscribers: set[asyncio.Queue] = set()
        self._tasks: list[asyncio.Task] = []
        self._stop: asyncio.Event | None = None

    async def start(self) -> None:
        if self._tasks:
            return
        self._stop = asyncio.Event()
        self._set_status("connecting")
        self._tasks = [
            asyncio.create_task(self._ws_loop(), name="crypto-binance-ws"),
            asyncio.create_task(self._fallback_loop(), name="crypto-rest-fallback"),
        ]

    async def stop(self) -> None:
        if not self._tasks:
            return
        assert self._stop is not None
        self._stop.set()
        for task in self._tasks:
            if task.get_name() == "crypto-binance-ws":
                task.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), timeout=4)
        except asyncio.TimeoutError:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._set_status("disconnected")
        self._subscribers.clear()
        logger.info("币圈实时行情连接已关闭")

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._subscribers.add(queue)
        queue.put_nowait(self.snapshot())
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "status": self.status,
            "assets": list(self.latest.values()),
            "subscriber_count": len(self._subscribers),
        }

    def _publish(self, message: dict) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(message)

    def _set_status(self, status: str, error: str | None = None) -> None:
        if status == self.status and not error:
            return
        old, self.status = self.status, status
        logger.info("币圈实时行情状态：%s -> %s%s", old, status, f" ({error})" if error else "")
        self._publish({"type": "status", "status": status, "error": error})

    async def _ws_loop(self) -> None:
        failures = 0
        assert self._stop is not None
        while not self._stop.is_set():
            try:
                if failures == 0:
                    self._set_status("connecting")
                async with connect(
                    STREAM_URL,
                    open_timeout=config.REQUEST_TIMEOUT,
                    close_timeout=1,
                    ping_interval=20,
                    ping_timeout=10,
                    max_queue=16,
                ) as socket:
                    failures = 0
                    self._set_status("connected")
                    while not self._stop.is_set():
                        raw = await asyncio.wait_for(socket.recv(), timeout=35)
                        ticker = parse_binance_message(raw)
                        self.last_ws_event = time.monotonic()
                        self.latest[ticker["asset"]] = ticker
                        self._publish({"type": "ticker", "status": "connected", "data": ticker})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                delay = BACKOFF_SECONDS[min(failures - 1, len(BACKOFF_SECONDS) - 1)]
                self._set_status("fallback", type(exc).__name__)
                logger.warning("Binance WebSocket 中断，%s 秒后重连：%s", delay, exc)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

    async def _fallback_loop(self) -> None:
        assert self._stop is not None
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=2)
            return
        except asyncio.TimeoutError:
            pass
        while not self._stop.is_set():
            stale = not self.last_ws_event or time.monotonic() - self.last_ws_event > 5
            if self.status != "connected" or stale:
                try:
                    rows = await asyncio.to_thread(crypto.realtime_snapshot)
                    if self.status != "connected" or stale:
                        self._set_status("fallback")
                        for ticker in rows:
                            ticker = {**ticker, "source": f'{ticker["source"]} REST', "fallback": True}
                            self.latest[ticker["asset"]] = ticker
                            self._publish({"type": "ticker", "status": "fallback", "data": ticker})
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._set_status("disconnected", type(exc).__name__)
                    logger.warning("币圈 REST 降级行情失败：%s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass


crypto_stream = CryptoStream()
