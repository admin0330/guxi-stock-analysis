"""FastAPI 与只读 Binance 查询引擎之间的单例服务。"""
from __future__ import annotations

from trading.core.engine import TradingEngine
from trading.settings import load_settings


class TradingService:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.engine = TradingEngine(self.settings)

    async def start(self) -> None:
        await self.engine.start()

    async def stop(self) -> None:
        await self.engine.stop()


trading_service = TradingService()
