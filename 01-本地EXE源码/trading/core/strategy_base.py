"""策略统一信号协议。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from enum import StrEnum

import pandas as pd


class SignalAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Signal:
    action: SignalAction
    strength: float
    reason: str
    strategy: str
    timestamp: str

    def as_dict(self) -> dict:
        data = asdict(self)
        data["action"] = self.action.value
        return data


class Strategy(ABC):
    name: str

    @abstractmethod
    def evaluate(self, candles: pd.DataFrame) -> Signal:
        raise NotImplementedError
