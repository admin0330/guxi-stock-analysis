"""共享 HTTP 连接池：统一超时、重试、限流退避与日志。"""
from __future__ import annotations

import logging
import threading
import time

import httpx

from backend import config

logger = logging.getLogger(__name__)
_client: httpx.Client | None = None
_client_lock = threading.Lock()
_last_success_at: float | None = None


def _shared_client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.Client(
                timeout=config.REQUEST_TIMEOUT,
                follow_redirects=True,
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
                headers={"Accept": "application/json,text/plain,*/*", "User-Agent": "Guxi/1.0"},
            )
        return _client


def get_json(url: str, params: dict | None = None, *, timeout: float | None = None, retries: int | None = None):
    return _get(url, params, timeout=timeout, retries=retries, as_json=True)


def get_text(url: str, params: dict | None = None, *, timeout: float | None = None, retries: int | None = None) -> str:
    return _get(url, params, timeout=timeout, retries=retries, as_json=False)


def _get(url: str, params: dict | None, *, timeout: float | None, retries: int | None, as_json: bool):
    global _last_success_at
    attempts = config.MAX_RETRIES if retries is None else max(0, retries)
    last_error: Exception | None = None
    for attempt in range(attempts + 1):
        try:
            response = _shared_client().get(url, params=params, timeout=timeout or config.REQUEST_TIMEOUT)
            response.raise_for_status()
            _last_success_at = time.time()
            return response.json() if as_json else response.text
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            if status is not None and status not in {418, 429, 500, 502, 503, 504}:
                break
            if attempt < attempts:
                delay = config.RETRY_BACKOFF * (attempt + 1)
                logger.warning("行情 API 失败，%.1f 秒后重试：%s", delay, url)
                time.sleep(delay)
    raise RuntimeError(f"外部行情服务暂不可用：{type(last_error).__name__}") from last_error


def last_success_at() -> float | None:
    return _last_success_at


def close() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None
