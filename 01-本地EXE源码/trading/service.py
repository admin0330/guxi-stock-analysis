"""FastAPI 与交易引擎之间的单例服务。"""
from __future__ import annotations

import secrets

from trading.core.engine import TradingEngine, build_strategy
from trading.settings import load_settings, save_public_settings


class TradingService:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.engine = TradingEngine(self.settings)
        self.write_token = secrets.token_urlsafe(32)

    async def start(self) -> None:
        await self.engine.start()

    async def stop(self) -> None:
        await self.engine.stop()

    def verify_token(self, value: str | None) -> None:
        if not value or not secrets.compare_digest(value, self.write_token):
            raise PermissionError("交易会话令牌无效，请刷新交易台")

    async def update_settings(self, changes: dict) -> dict:
        updated = save_public_settings(self.settings, changes)
        self.settings = updated
        # 热更新只替换策略与风控参数；环境和连接参数必须重启。
        self.engine.settings = updated
        self.engine.strategy = build_strategy(updated)
        self.engine.risk.settings = updated
        self.engine.orders.cooldown_seconds = updated.order_cooldown_sec
        return updated.public_dict()


trading_service = TradingService()
