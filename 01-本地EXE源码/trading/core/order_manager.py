"""订单幂等、冷却与状态同步。"""
from __future__ import annotations

import hashlib
import threading
import time

from trading.storage import TradingStore


class OrderManager:
    def __init__(self, store: TradingStore, cooldown_seconds: int) -> None:
        self.store = store
        self.cooldown_seconds = cooldown_seconds
        self._reserved: dict[str, float] = {}
        self._lock = threading.Lock()

    def link_id(self, symbol: str, action: str, source: str, signal_time: str = "") -> str:
        raw = f"{symbol}:{action}:{source}:{signal_time or int(time.time() // self.cooldown_seconds)}"
        return "guxi-" + hashlib.sha256(raw.encode()).hexdigest()[:24]

    def reserve(self, link_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._reserved = {key: value for key, value in self._reserved.items() if now - value < self.cooldown_seconds}
            if link_id in self._reserved:
                return False
            existing = {row["order_link_id"] for row in self.store.rows("orders", 500)}
            if link_id in existing:
                return False
            self._reserved[link_id] = now
            return True

    def release(self, link_id: str) -> None:
        with self._lock:
            self._reserved.pop(link_id, None)

    def update(self, row: dict, source: str = "exchange") -> None:
        self.store.upsert_order(row, source)

    def sync(self, rows: list[dict]) -> None:
        for row in rows:
            self.update(row, "recovery")

    def uncertain(self) -> list[dict]:
        return [row for row in self.store.rows("orders", 500) if row.get("status") in {"PendingSubmit", "SubmitUnknown"}]
