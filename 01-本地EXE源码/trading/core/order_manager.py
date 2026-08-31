"""Binance 历史订单的只读同步。"""
from __future__ import annotations

from trading.storage import TradingStore


class OrderManager:
    def __init__(self, store: TradingStore) -> None:
        self.store = store

    def update(self, row: dict, source: str = "exchange") -> None:
        self.store.upsert_order(row, source)

    def sync(self, rows: list[dict]) -> None:
        for row in rows:
            self.update(row, "recovery")
