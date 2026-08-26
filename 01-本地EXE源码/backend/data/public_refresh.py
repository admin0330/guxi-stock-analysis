"""服务运行期间持续预热固定页面依赖的公开行情缓存。"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from backend import config
from backend.analysis import crypto as crypto_an
from backend.analysis import daily as daily_an
from backend.analysis import daily_pick as daily_pick_an
from backend.analysis import limitup as limitup_an
from backend.analysis import market as market_an
from backend.data import crypto as crypto_data

logger = logging.getLogger(__name__)


def _warm_indices() -> None:
    for symbol in config.INDEX_SYMBOLS:
        market_an.index_detail(symbol)


_JOBS: tuple[tuple[str, Callable[[], object]], ...] = (
    ("大盘与日报", daily_an.daily_report),
    ("涨停复盘", limitup_an.full_review),
    ("主要指数 K 线", _warm_indices),
    ("BTC/ETH 行情", crypto_data.overview),
    ("BTC 技术分析", lambda: crypto_an.analyze("BTC")),
    ("ETH 技术分析", lambda: crypto_an.analyze("ETH")),
    ("每日关注池", daily_pick_an.get),
)


async def refresh_once() -> tuple[int, int]:
    """并发刷新一轮；单个公开数据源失败不影响其他缓存。"""
    results = await asyncio.gather(
        *(asyncio.to_thread(job) for _, job in _JOBS),
        return_exceptions=True,
    )
    failed = 0
    for (name, _), result in zip(_JOBS, results):
        if isinstance(result, Exception):
            failed += 1
            logger.warning("公共行情预热失败（%s）：%s", name, result)
    return len(_JOBS) - failed, failed


class PublicDataRefresher:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None

    async def start(self) -> None:
        if not config.PUBLIC_DATA_REFRESH_ENABLED or self._task:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="public-data-refresh")
        logger.info("公共行情后台预热已启动（每 %s 秒）", config.PUBLIC_DATA_REFRESH_SECONDS)

    async def stop(self) -> None:
        if not self._task:
            return
        assert self._stop is not None
        self._stop.set()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._stop = None
        logger.info("公共行情后台预热已停止")

    async def _run(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            succeeded, failed = await refresh_once()
            logger.debug("公共行情预热完成：成功 %s，失败 %s", succeeded, failed)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=config.PUBLIC_DATA_REFRESH_SECONDS)
            except asyncio.TimeoutError:
                pass


public_data_refresher = PublicDataRefresher()
