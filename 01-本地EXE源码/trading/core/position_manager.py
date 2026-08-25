"""交易所仓位快照与重启恢复。"""
from __future__ import annotations


class PositionManager:
    def __init__(self) -> None:
        self.positions: dict[str, dict] = {}

    def sync(self, rows: list[dict]) -> None:
        self.positions = {
            str(row["symbol"]): row for row in rows if row.get("symbol")
        }

    def update(self, row: dict) -> None:
        symbol = str(row.get("symbol") or "")
        if symbol:
            self.positions[symbol] = row

    def active(self) -> list[dict]:
        return [row for row in self.positions.values() if float(row.get("size") or 0) > 0]

    def total_notional(self) -> float:
        return sum(abs(float(row.get("positionValue") or 0)) for row in self.active())
