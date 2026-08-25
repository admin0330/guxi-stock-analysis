"""所有开仓请求必须经过的统一风控。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from trading.settings import TradingSettings


@dataclass
class RiskContext:
    equity: float
    available_balance: float
    current_price: float
    order_qty: float
    leverage: int
    total_position_notional: float = 0
    daily_closed_pnl: float = 0
    consecutive_losses: int = 0
    reduce_only: bool = False


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    order_notional: float = 0


class RiskManager:
    def __init__(self, settings: TradingSettings) -> None:
        self.settings = settings
        self.emergency_stopped = False
        self.circuit_reason = ""

    def reset_emergency(self) -> None:
        self.emergency_stopped = False
        self.circuit_reason = ""

    def stop(self, reason: str) -> None:
        self.emergency_stopped = True
        self.circuit_reason = reason

    def check(self, context: RiskContext) -> RiskDecision:
        notional = context.current_price * context.order_qty
        if context.reduce_only:
            return RiskDecision(True, "减仓/平仓订单允许执行", notional)
        if self.emergency_stopped:
            return RiskDecision(False, self.circuit_reason or "紧急停止已触发", notional)
        if context.equity <= 0 or context.current_price <= 0 or context.order_qty <= 0:
            return RiskDecision(False, "账户权益、价格或数量无效", notional)
        if context.leverage > self.settings.max_leverage:
            return RiskDecision(False, f"杠杆 {context.leverage}x 超过上限 {self.settings.max_leverage}x", notional)
        if notional > context.equity * self.settings.max_position_pct * context.leverage:
            return RiskDecision(False, "单笔名义仓位超过风控上限", notional)
        if context.total_position_notional + notional > context.equity * self.settings.max_total_position_pct * context.leverage:
            return RiskDecision(False, "总名义仓位超过风控上限", notional)
        if context.daily_closed_pnl <= -(context.equity * self.settings.max_daily_loss_pct):
            self.stop("达到每日最大亏损，已熔断")
            return RiskDecision(False, self.circuit_reason, notional)
        if context.consecutive_losses >= self.settings.max_consecutive_losses:
            self.stop("达到连续亏损次数，已熔断")
            return RiskDecision(False, self.circuit_reason, notional)
        if self._in_maintenance():
            return RiskDecision(False, "当前处于配置的维护暂停时段", notional)
        return RiskDecision(True, "风控校验通过", notional)

    def _in_maintenance(self) -> bool:
        current = datetime.now(timezone.utc).strftime("%H:%M")
        for window in self.settings.maintenance_windows:
            try:
                start, end = window.split("-", 1)
                if start <= end and start <= current <= end:
                    return True
                if start > end and (current >= start or current <= end):
                    return True
            except ValueError:
                continue
        return False

    def summary(self) -> dict:
        return {
            "max_position_pct": self.settings.max_position_pct,
            "max_total_position_pct": self.settings.max_total_position_pct,
            "max_leverage": self.settings.max_leverage,
            "hard_stop_loss_pct": self.settings.hard_stop_loss_pct,
            "max_daily_loss_pct": self.settings.max_daily_loss_pct,
            "max_consecutive_losses": self.settings.max_consecutive_losses,
            "max_holding_minutes": self.settings.max_holding_minutes,
            "emergency_stopped": self.emergency_stopped,
            "circuit_reason": self.circuit_reason,
        }
