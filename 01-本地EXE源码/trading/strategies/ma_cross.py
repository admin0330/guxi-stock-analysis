from __future__ import annotations

import pandas as pd

from trading.core.strategy_base import Signal, SignalAction, Strategy


class MaCrossStrategy(Strategy):
    name = "ma_cross"

    def __init__(self, fast: int = 9, slow: int = 21) -> None:
        self.fast, self.slow = fast, slow

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        timestamp = str(candles.iloc[-1].get("time", "")) if not candles.empty else ""
        if len(candles) < self.slow + 2:
            return Signal(SignalAction.HOLD, 0, "K线数量不足", self.name, timestamp)
        close = candles["close"].astype(float)
        fast = close.rolling(self.fast).mean()
        slow = close.rolling(self.slow).mean()
        previous = fast.iloc[-2] - slow.iloc[-2]
        current = fast.iloc[-1] - slow.iloc[-1]
        scale = max(abs(float(slow.iloc[-1])), 1e-9)
        strength = min(1.0, abs(float(current)) / scale * 100)
        if previous <= 0 < current:
            return Signal(SignalAction.BUY, strength, f"MA{self.fast} 上穿 MA{self.slow}", self.name, timestamp)
        if previous >= 0 > current:
            return Signal(SignalAction.SELL, strength, f"MA{self.fast} 下穿 MA{self.slow}", self.name, timestamp)
        return Signal(SignalAction.HOLD, strength, "均线未形成新的交叉", self.name, timestamp)
