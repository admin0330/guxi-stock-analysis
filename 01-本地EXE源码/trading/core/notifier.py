"""Telegram 与通用 webhook 通知。"""
from __future__ import annotations

import logging

import httpx

from trading.settings import TradingSettings

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings: TradingSettings) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=5)

    async def send(self, message: str) -> None:
        if not self.settings.notify_enabled:
            return
        try:
            if self.settings.telegram_bot_token and self.settings.telegram_chat_id:
                response = await self.client.post(
                    f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage",
                    json={"chat_id": self.settings.telegram_chat_id, "text": message},
                )
            elif self.settings.webhook_url:
                response = await self.client.post(self.settings.webhook_url, json={"text": message, "content": message})
            else:
                return
            response.raise_for_status()
        except Exception as exc:
            logger.warning("交易通知发送失败：%s", type(exc).__name__)

    async def close(self) -> None:
        await self.client.aclose()
