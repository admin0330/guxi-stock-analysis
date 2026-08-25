"""便携用户状态：自选、最近搜索和必要 UI 偏好。"""
from __future__ import annotations

import json
import re
import threading
from copy import deepcopy

from backend import config

_LOCK = threading.RLock()
_SYMBOL = re.compile(r"^(?:sh|sz|bj)?\d{6}$", re.I)
_PAGES = {"overview", "stock", "limitup", "picks", "daily", "crypto"}
_FREQUENCIES = {1, 60, 300, 1200}
_DEFAULT = {
    "watchlist": [],
    "recent_searches": [],
    "last_page": "overview",
    "crypto_refresh_seconds": 60,
    "welcomed": False,
}


def _path(user_id: int | None = None):
    return config.USER_STATE_FILE if user_id is None else config.USER_STATE_DIR / f"{int(user_id)}.json"


def _read(user_id: int | None = None) -> dict:
    path = _path(user_id)
    if not path.exists():
        # 仅首位管理员承接旧版单用户状态，避免普通用户看到旧自选。
        if user_id == 1 and config.USER_STATE_FILE.exists():
            path = config.USER_STATE_FILE
        else:
            return deepcopy(_DEFAULT)
    if not path.exists():
        return deepcopy(_DEFAULT)
    try:
        stored = json.loads(path.read_text(encoding="utf-8-sig"))
        return {**deepcopy(_DEFAULT), **stored} if isinstance(stored, dict) else deepcopy(_DEFAULT)
    except (OSError, json.JSONDecodeError):
        return deepcopy(_DEFAULT)


def _write(state: dict, user_id: int | None = None) -> None:
    path = _path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def load(user_id: int | None = None) -> dict:
    with _LOCK:
        return _read(user_id)


def update(changes: dict, user_id: int | None = None) -> dict:
    """只接受已知字段，避免前端把任意内容写进本地文件。"""
    with _LOCK:
        state = _read(user_id)
        if changes.get("last_page") in _PAGES:
            state["last_page"] = changes["last_page"]
        if changes.get("crypto_refresh_seconds") in _FREQUENCIES:
            state["crypto_refresh_seconds"] = changes["crypto_refresh_seconds"]
        if isinstance(changes.get("welcomed"), bool):
            state["welcomed"] = changes["welcomed"]
        if isinstance(changes.get("watchlist"), list):
            symbols = [str(item).lower() for item in changes["watchlist"] if _SYMBOL.fullmatch(str(item))]
            state["watchlist"] = list(dict.fromkeys(symbols))[:100]
        if isinstance(changes.get("recent_searches"), list):
            rows = []
            for item in changes["recent_searches"]:
                if not isinstance(item, dict) or not _SYMBOL.fullmatch(str(item.get("symbol", ""))):
                    continue
                rows.append({
                    "symbol": str(item["symbol"]).lower(),
                    "code": str(item.get("code", ""))[:12],
                    "name": str(item.get("name", ""))[:40],
                })
            state["recent_searches"] = rows[:10]
        _write(state, user_id)
        return state


def add_watch(symbol: str, user_id: int | None = None) -> dict:
    state = load(user_id)
    normalized = symbol.lower()
    if not _SYMBOL.fullmatch(normalized):
        raise ValueError("股票代码格式不正确")
    state["watchlist"] = [normalized, *[item for item in state["watchlist"] if item != normalized]][:100]
    return update({"watchlist": state["watchlist"]}, user_id)


def remove_watch(symbol: str, user_id: int | None = None) -> dict:
    state = load(user_id)
    normalized = symbol.lower()
    state["watchlist"] = [item for item in state["watchlist"] if item != normalized]
    return update({"watchlist": state["watchlist"]}, user_id)


def delete_user_state(user_id: int) -> None:
    path = _path(user_id)
    if path.exists():
        path.unlink()
