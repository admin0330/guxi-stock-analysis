from __future__ import annotations

import pandas as pd
from ta.momentum import RSIIndicator

from trading.core.strategy_base import Signal, SignalAction, Strategy


class RsiBasicStrategy(Strategy):
    name = "rsi_basic"

    def __init__(self, period: int = 14, buy: float = 35, sell: float = 65, trend: int = 50) -> None:
        self.period, self.buy, self.sell, self.trend = period, buy, sell, trend

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        timestamp = str(candles.iloc[-1].get("time", "")) if not candles.empty else ""
        if len(candles) < max(self.period + 2, self.trend):
            return Signal(SignalAction.HOLD, 0, "K线数量不足", self.name, timestamp)
        close = candles["close"].astype(float)
        rsi = float(RSIIndicator(close, self.period).rsi().iloc[-1])
        trend = float(close.rolling(self.trend).mean().iloc[-1])
        price = float(close.iloc[-1])
        if rsi <= self.buy and price >= trend:
            return Signal(SignalAction.BUY, min(1, (self.buy - rsi + 1) / self.buy), f"RSI {rsi:.1f} 超卖且价格位于趋势线上方", self.name, timestamp)
        if rsi >= self.sell and price <= trend:
            return Signal(SignalAction.SELL, min(1, (rsi - self.sell + 1) / (100 - self.sell)), f"RSI {rsi:.1f} 超买且价格位于趋势线下方", self.name, timestamp)
        return Signal(SignalAction.HOLD, 0, f"RSI {rsi:.1f} 未满足趋势过滤", self.name, timestamp)
