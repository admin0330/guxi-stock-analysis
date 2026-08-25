"""本地文件缓存：按 key 缓存 DataFrame 到 parquet，带 TTL。"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import pandas as pd

from backend.config import CACHE_DIR

_WRITE_LOCK = threading.Lock()


def _safe_key(key: str) -> str:
    """把任意 key 变成安全的文件名（保留可读性）。"""
    h = hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
    return f"{h}_{key[:60]}" if len(key) <= 60 else f"{h}_{key[:40]}"


def _meta_path(p: Path) -> Path:
    return p.with_suffix(".json")


def get(key: str, ttl: int, allow_stale: bool = False) -> pd.DataFrame | None:
    """取缓存；过期或不存在返回 None。

    allow_stale=True 时：TTL 已过期但文件仍在，返回 (df, stale=True) 供调用方兜底。
    """
    p = CACHE_DIR / f"{_safe_key(key)}.parquet"
    if not p.exists():
        p = CACHE_DIR / _safe_key(key)  # 兼容 0.2.0 之前的无后缀缓存
        if not p.exists():
            return None
    meta = _meta_path(p)
    stale = False
    try:
        saved_at = p.stat().st_mtime
        if meta.exists():
            saved = json.loads(meta.read_text(encoding="utf-8"))
            saved_at = saved.get("ts", saved_at)
        if time.time() - saved_at > ttl:
            if not allow_stale:
                return None
            stale = True
    except Exception:
        pass
    try:
        df = pd.read_parquet(p)
        if allow_stale and stale:
            return _Stale(df)
        return df
    except Exception:
        return None


class _Stale:
    """包装过期数据，调用方可通过 .is_stale 判断。"""

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.is_stale = True

    def __len__(self):
        return len(self.df)

    def __getattr__(self, item):
        return getattr(self.df, item)


def set(key: str, df: pd.DataFrame) -> None:
    """写入缓存。"""
    p = CACHE_DIR / f"{_safe_key(key)}.parquet"
    data_tmp, meta_tmp = p.with_suffix(".tmp"), _meta_path(p).with_suffix(".json.tmp")
    try:
        with _WRITE_LOCK:
            df.to_parquet(data_tmp, index=False)
            meta_tmp.write_text(json.dumps({"ts": time.time(), "key": key}, ensure_ascii=False), encoding="utf-8")
            data_tmp.replace(p)
            meta_tmp.replace(_meta_path(p))
    except Exception:
        data_tmp.unlink(missing_ok=True)
        meta_tmp.unlink(missing_ok=True)


def clear(prefix: str = "") -> int:
    """清空缓存（可选按前缀），返回删除文件数。"""
    n = 0
    for p in CACHE_DIR.iterdir():
        if not p.is_file() or p.suffix in {".json", ".tmp"}:
            continue
        meta = _meta_path(p)
        key = ""
        try:
            key = json.loads(meta.read_text(encoding="utf-8")).get("key", "")
        except Exception:
            key = p.stem.split("_", 1)[-1]
        if not prefix or key.startswith(prefix):
            p.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
            n += 1
    return n
