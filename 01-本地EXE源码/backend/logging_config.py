"""应用日志：每天轮转并自动清理旧文件。"""
from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler

from backend import config


def configure_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_guxi_configured", False):
        return
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = TimedRotatingFileHandler(
        config.LOG_DIR / "guxi.log", when="midnight", backupCount=config.LOG_RETENTION_DAYS, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    if not getattr(sys, "frozen", False):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
    root._guxi_configured = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
