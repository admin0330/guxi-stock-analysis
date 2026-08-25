"""交易配置加载、校验与安全覆盖。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from backend import config as app_config


class TradingSettings(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    market_type: Literal["linear", "spot"] = "linear"
    kline_interval: Literal["1", "3", "5", "15", "30", "60", "120", "240", "D"] = "5"
    kline_limit: int = Field(240, ge=60, le=1000)
    default_leverage: int = Field(3, ge=1, le=20)
    max_leverage: int = Field(5, ge=1, le=20)
    max_position_pct: float = Field(0.10, gt=0, le=0.25)
    max_total_position_pct: float = Field(0.25, gt=0, le=0.75)
    max_daily_loss_pct: float = Field(0.05, gt=0, le=0.25)
    max_consecutive_losses: int = Field(3, ge=1, le=20)
    hard_stop_loss_pct: float = Field(0.02, gt=0, le=0.20)
    take_profit_pct: float = Field(0.04, gt=0, le=0.50)
    max_holding_minutes: int = Field(1440, ge=0, le=43200)
    order_cooldown_sec: int = Field(300, ge=1, le=86400)
    strategy: Literal["ma_cross", "rsi_basic", "boll_break"] = "ma_cross"
    ma_fast: int = Field(9, ge=2, le=200)
    ma_slow: int = Field(21, ge=3, le=500)
    rsi_period: int = Field(14, ge=2, le=100)
    rsi_buy: float = Field(35, ge=1, le=49)
    rsi_sell: float = Field(65, ge=51, le=99)
    boll_window: int = Field(20, ge=5, le=200)
    boll_std: float = Field(2.0, ge=0.5, le=5)
    enable_auto_trade: bool = False
    allow_mainnet: bool = False
    notify_enabled: bool = False
    webhook_url: str = ""
    maintenance_windows: list[str] = Field(default_factory=list)
    testnet: bool = True
    api_key: str = ""
    api_secret: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    config_path: Path | None = None

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(str(v).strip().upper() for v in values))
        if not cleaned or any(not v.isalnum() or not v.endswith("USDT") for v in cleaned):
            raise ValueError("交易对必须是 USDT 计价的大写字母数字组合")
        return cleaned

    @model_validator(mode="after")
    def validate_risk_relationships(self):
        if self.ma_fast >= self.ma_slow:
            raise ValueError("ma_fast 必须小于 ma_slow")
        if self.default_leverage > self.max_leverage:
            raise ValueError("default_leverage 不能超过 max_leverage")
        if self.max_position_pct > self.max_total_position_pct:
            raise ValueError("单笔仓位上限不能超过总仓位上限")
        return self

    @property
    def credentials_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def environment_name(self) -> str:
        return "Binance Futures Testnet 模拟盘" if self.testnet else "Binance Futures Mainnet 实盘"

    def public_dict(self) -> dict:
        data = self.model_dump(exclude={
            "api_key", "api_secret", "telegram_bot_token", "telegram_chat_id", "webhook_url", "config_path",
        })
        data["credentials_configured"] = self.credentials_configured
        data["environment"] = self.environment_name
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
    # Mainnet 需要配置文件和环境变量同时明确允许；否则强制退回 Testnet。
    raw["testnet"] = requested_testnet or not bool(raw.get("allow_mainnet", False))
    raw["api_key"] = os.getenv("BINANCE_API_KEY", "").strip()
    raw["api_secret"] = os.getenv("BINANCE_API_SECRET", "").strip()
    raw["telegram_bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    raw["telegram_chat_id"] = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    raw["config_path"] = path
    return TradingSettings.model_validate(raw)


def save_public_settings(settings: TradingSettings, changes: dict) -> TradingSettings:
    allowed = {
        "default_leverage", "max_leverage", "max_position_pct",
        "max_total_position_pct", "max_daily_loss_pct", "max_consecutive_losses",
        "hard_stop_loss_pct", "take_profit_pct", "max_holding_minutes",
        "order_cooldown_sec", "strategy", "ma_fast", "ma_slow", "rsi_period",
        "rsi_buy", "rsi_sell", "boll_window", "boll_std", "notify_enabled",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"不允许修改的配置：{', '.join(sorted(unknown))}")
    candidate = TradingSettings.model_validate({**settings.model_dump(), **changes})
    path = settings.config_path or settings_path()
    persisted = yaml.safe_load(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    persisted = persisted or {}
    persisted.update({key: getattr(candidate, key) for key in changes})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".yaml.tmp")
    temporary.write_text(yaml.safe_dump(persisted, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temporary.replace(path)
    return load_settings()
