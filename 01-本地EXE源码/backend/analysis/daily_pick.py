"""每日关注池：公开行情、透明规则、可复盘缓存。"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from backend import config
from backend.data import fetcher

STRATEGY = "daily_score_v1"
DISCLAIMER = "仅供学习研究，不构成投资建议，不保证收益；过往与回测不代表未来，股市有风险，决策自负。"
WEIGHTS = {"trend": 40, "momentum": 25, "liquidity": 20, "risk": 15}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COLUMNS = [
    "trade_date", "generated_at", "strategy", "code", "symbol", "name", "price", "pct", "amount",
    "score", "trend_score", "momentum_score", "liquidity_score", "risk_score", "reasons", "risk_tags",
]


def rules() -> dict:
    return {
        "strategy": STRATEGY,
        "weights": WEIGHTS,
        "top_k": config.DAILY_PICK_TOP_K,
        "pool_size": config.DAILY_PICK_POOL_SIZE,
        "filters": [
            "排除 ST、*ST、退市整理标的", "排除停牌、无有效价格或成交额不足标的",
            f"排除可用日线少于 {config.DAILY_PICK_MIN_LISTING_DAYS} 个交易日的次新股",
            "排除日线数据严重缺失、无法完整评分的标的",
            *( ["排除创业板与科创板"] if config.DAILY_PICK_EXCLUDE_GROWTH_BOARDS else [] ),
        ],
        "dimensions": [
            "趋势 40：价格与 MA20/MA60 位置、MA20 斜率、中期方向",
            "动量 25：近 5/20 日涨幅适中得分，极端追高降权",
            "流动性 20：按当日成交额相对最低门槛评分",
            "风险 15：20 日波动、连续上涨和接近涨停会降低分数",
        ],
        "min_amount": config.DAILY_PICK_MIN_AMOUNT,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "disclaimer": DISCLAIMER,
    }


def _path(trade_date: str) -> Path:
    return config.DAILY_PICK_DIR / f"{trade_date}.parquet"


def _decode(frame: pd.DataFrame) -> list[dict]:
    rows = []
    for row in frame.to_dict(orient="records"):
        for key in ("reasons", "risk_tags"):
            try:
                row[key] = json.loads(row.get(key) or "[]")
            except (TypeError, json.JSONDecodeError):
                row[key] = []
        rows.append(row)
    return rows


def _load(trade_date: str) -> list[dict] | None:
    path = _path(trade_date)
    if not path.exists():
        return None
    try:
        return _decode(pd.read_parquet(path))
    except Exception:
        return None


def _save(trade_date: str, rows: list[dict]) -> None:
    path = _path(trade_date)
    temporary = path.with_suffix(".tmp")
    frame = pd.DataFrame([{**row, "reasons": json.dumps(row["reasons"], ensure_ascii=False), "risk_tags": json.dumps(row["risk_tags"], ensure_ascii=False)} for row in rows], columns=_COLUMNS)
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def _latest_trade_date() -> str:
    try:
        frame = fetcher.index_daily("sh000001")
        if frame is not None and not frame.empty:
            return str(frame.iloc[-1]["date"])[:10]
    except Exception:
        pass
    today = date.today()
    while today.weekday() >= 5:
        today = today.fromordinal(today.toordinal() - 1)
    return today.isoformat()


def _score(candidate: dict) -> dict | None:
    symbol = fetcher.normalize_symbol(str(candidate["代码"]))
    try:
        frame = fetcher.stock_daily(symbol).dropna(subset=["close"]).tail(max(120, config.DAILY_PICK_MIN_LISTING_DAYS)).copy()
    except Exception:
        return None
    if len(frame) < config.DAILY_PICK_MIN_LISTING_DAYS:
        return None
    close = frame["close"].astype(float)
    returns = close.pct_change().dropna()
    if len(returns) < 60:
        return None
    price, ma20, ma60 = float(close.iloc[-1]), float(close.tail(20).mean()), float(close.tail(60).mean())
    ma20_prev = float(close.iloc[-25:-5].mean())
    r5 = (price / float(close.iloc[-6]) - 1) * 100
    r20 = (price / float(close.iloc[-21]) - 1) * 100
    vol = float(returns.tail(20).std() * math.sqrt(252) * 100)
    recent = returns.tail(8)
    streak = 0
    for value in reversed(recent.tolist()):
        if value <= 0:
            break
        streak += 1

    trend = 0
    reasons = []
    if price > ma20: trend += 10
    if ma20 > ma60: trend += 12
    if ma20 > ma20_prev: trend += 8
    if r20 > 0: trend += 10
    if trend >= 30:
        reasons.append("中期均线向上，价格保持在主要均线上方")

    momentum = 0
    if -2 <= r5 <= 8: momentum += 10
    elif -5 <= r5 <= 12: momentum += 5
    if 2 <= r20 <= 20: momentum += 10
    elif -3 <= r20 <= 28: momentum += 5
    drawdown = (price / float(close.tail(20).max()) - 1) * 100
    if drawdown >= -8: momentum += 5
    if momentum >= 18:
        reasons.append("近 5/20 日动量适中，未出现极端追高")

    amount = float(candidate.get("成交额") or 0)
    ratio = max(1.0, amount / config.DAILY_PICK_MIN_AMOUNT)
    liquidity = min(20, round(8 + math.log10(ratio) * 8, 1))
    if liquidity >= 14:
        reasons.append("成交额达标，流动性处于活跃区间")

    risk = 15
    tags = []
    if vol > 80: risk -= 10; tags.append("高波动")
    elif vol > 55: risk -= 5; tags.append("波动偏高")
    if streak >= 4: risk -= 4; tags.append("连续上涨")
    pct = float(candidate.get("涨跌幅") or 0)
    if pct >= 9.5: risk -= 5; tags.append("接近涨停")
    if r20 > 35: risk -= 5; tags.append("阶段涨幅偏大")
    risk = max(0, risk)
    score = round(trend + momentum + liquidity + risk, 1)
    if not reasons:
        reasons = ["流动性达标，综合指标处于候选区间"]
    return {
        "code": str(candidate["代码"]).zfill(6), "symbol": symbol, "name": str(candidate["名称"]),
        "price": round(price, 2), "pct": round(pct, 2), "amount": round(amount / 1e8, 2),
        "score": score, "trend_score": trend, "momentum_score": momentum,
        "liquidity_score": liquidity, "risk_score": risk, "reasons": reasons[:3], "risk_tags": tags,
    }


def _candidates(spot: pd.DataFrame) -> list[dict]:
    frame = spot.copy()
    names = frame["名称"].astype(str)
    codes = frame["代码"].astype(str).str.zfill(6)
    mask = (
        ~names.str.upper().str.contains(r"ST|退", regex=True, na=True)
        & pd.to_numeric(frame["最新价"], errors="coerce").gt(0)
        & pd.to_numeric(frame["成交额"], errors="coerce").ge(config.DAILY_PICK_MIN_AMOUNT)
        & pd.to_numeric(frame["成交量"], errors="coerce").gt(0)
    )
    if config.DAILY_PICK_EXCLUDE_GROWTH_BOARDS:
        mask &= ~codes.str.startswith(("300", "301", "688"))
    return frame.loc[mask].sort_values("成交额", ascending=False).head(config.DAILY_PICK_POOL_SIZE).to_dict(orient="records")


def generate(refresh: bool = False) -> dict:
    trade_date = _latest_trade_date()
    cached = None if refresh else _load(trade_date)
    if cached is not None:
        return _response(trade_date, cached, True)
    spot = fetcher.market_spot()
    if spot is None or spot.empty:
        raise RuntimeError("全市场行情暂不可用")
    rows = [row for row in fetcher.parallel_map(_score, _candidates(spot)) if row]
    rows = sorted(rows, key=lambda row: (-row["score"], -row["amount"]))[:config.DAILY_PICK_TOP_K]
    generated_at = datetime.now().isoformat(timespec="seconds")
    rows = [{"trade_date": trade_date, "generated_at": generated_at, "strategy": STRATEGY, **row} for row in rows]
    _save(trade_date, rows)
    return _response(trade_date, rows, False)


def _response(trade_date: str, rows: list[dict], cached: bool) -> dict:
    return {
        "trade_date": trade_date, "generated_at": rows[0]["generated_at"] if rows else None,
        "strategy": STRATEGY, "items": rows, "cached": cached,
        "non_trading_day": trade_date != date.today().isoformat(),
        "notice": "非交易日，显示最近交易日结果" if trade_date != date.today().isoformat() else "",
        "disclaimer": DISCLAIMER,
    }


def get(trade_date: str | None = None, refresh: bool = False) -> dict:
    if not trade_date:
        return generate(refresh)
    if not _DATE.fullmatch(trade_date):
        raise ValueError("日期格式应为 YYYY-MM-DD")
    rows = _load(trade_date)
    if rows is None:
        raise FileNotFoundError("未找到该交易日的本地推荐记录")
    return _response(trade_date, rows, True)


def history(days: int = 10) -> list[dict]:
    items = []
    for path in sorted(config.DAILY_PICK_DIR.glob("????-??-??.parquet"), reverse=True)[:days]:
        rows = _load(path.stem) or []
        items.append({"date": path.stem, "count": len(rows), "strategy": STRATEGY})
    return items
