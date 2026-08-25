from __future__ import annotations

import pandas as pd
from ta.volatility import BollingerBands

from trading.core.strategy_base import Signal, SignalAction, Strategy


class BollBreakStrategy(Strategy):
    name = "boll_break"

    def __init__(self, window: int = 20, std: float = 2.0) -> None:
        self.window, self.std = window, std

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        timestamp = str(candles.iloc[-1].get("time", "")) if not candles.empty else ""
        if len(candles) < self.window + 2:
            return Signal(SignalAction.HOLD, 0, "K线数量不足", self.name, timestamp)
        close = candles["close"].astype(float)
        bands = BollingerBands(close, self.window, self.std)
        upper, lower = bands.bollinger_hband(), bands.bollinger_lband()
        if close.iloc[-2] <= upper.iloc[-2] and close.iloc[-1] > upper.iloc[-1]:
            return Signal(SignalAction.BUY, 0.7, "价格向上突破布林带上轨", self.name, timestamp)
        if close.iloc[-2] >= lower.iloc[-2] and close.iloc[-1] < lower.iloc[-1]:
            return Signal(SignalAction.SELL, 0.7, "价格向下突破布林带下轨", self.name, timestamp)
        return Signal(SignalAction.HOLD, 0, "价格仍在布林带区间内", self.name, timestamp)
