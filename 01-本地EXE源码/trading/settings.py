"""Binance 只读查询配置。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from backend import config as app_config


class TradingSettings(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    market_type: Literal["linear", "spot"] = "linear"
    kline_interval: Literal["1", "3", "5", "15", "30", "60", "120", "240", "D"] = "5"
    kline_limit: int = Field(240, ge=60, le=1000)
    testnet: bool = True
    api_key: str = ""
    api_secret: str = ""
    config_path: Path | None = None

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(str(v).strip().upper() for v in values))
        if not cleaned or any(not v.isalnum() or not v.endswith("USDT") for v in cleaned):
            raise ValueError("交易对必须是 USDT 计价的大写字母数字组合")
        return cleaned

    @property
    def credentials_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def environment_name(self) -> str:
        return "Binance Futures Testnet 只读查询" if self.testnet else "Binance Futures 只读查询"

    def public_dict(self) -> dict:
        data = self.model_dump(exclude={
            "api_key", "api_secret", "telegram_bot_token", "telegram_chat_id", "webhook_url", "config_path",
        })
        data["credentials_configured"] = self.credentials_configured
        data["environment"] = self.environment_name
        data["read_only"] = True
        data["capabilities"] = ["market_data", "account", "positions", "open_orders", "order_history", "executions"]
        return data


def settings_path() -> Path:
    configured = os.getenv("TRADING_SETTINGS_PATH", "").strip()
    if configured:
        return Path(configured)
    external = app_config.APP_DIR / "trading" / "config" / "settings.yaml"
    return external if external.exists() else Path(__file__).resolve().parent / "config" / "settings.yaml"


def load_settings() -> TradingSettings:
    path = settings_path()
    raw = yaml.safe_load(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    raw = raw or {}
    requested_testnet = os.getenv("BINANCE_TESTNET", "true").strip().lower() not in {"0", "false", "no", "off"}
    # 交易能力已移除；BINANCE_TESTNET 仅用于选择只读行情/账户查询环境。
    raw["testnet"] = requested_testnet
    raw["api_key"] = os.getenv("BINANCE_API_KEY", "").strip()
    raw["api_secret"] = os.getenv("BINANCE_API_SECRET", "").strip()
    raw["config_path"] = path
    return TradingSettings.model_validate(raw)
