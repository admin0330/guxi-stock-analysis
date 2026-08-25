"""BTC / ETH 技术指标、评分与通俗解读。"""
from __future__ import annotations

import pandas as pd

from backend.data import crypto as crypto_data


def _number(value, digits: int = 4):
    return None if pd.isna(value) else round(float(value), digits)


def analyze(asset: str, interval: str = "1h", limit: int = 240, force: bool = False) -> dict:
    frame = crypto_data.kline(asset, interval, limit, force)
    close = pd.to_numeric(frame["close"], errors="coerce")
    for period in (5, 10, 20):
        frame[f"ma{period}"] = close.rolling(period).mean()

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea) * 2

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean().replace(0, pd.NA)
    rsi = 100 - 100 / (1 + gain / loss)

    boll_mid = close.rolling(20).mean()
    boll_std = close.rolling(20).std()
    boll_upper, boll_lower = boll_mid + 2 * boll_std, boll_mid - 2 * boll_std

    latest = frame.iloc[-1]
    ma5, ma10, ma20 = (_number(frame[f"ma{period}"].iloc[-1], 2) for period in (5, 10, 20))
    price = _number(latest["close"], 2)
    rsi14 = _number(rsi.iloc[-1], 2)
    score, positives, risks = 50, [], []
    if ma20 is not None and price is not None:
        if price > ma20:
            score += 12
            positives.append("价格位于 MA20 上方")
        else:
            score -= 12
            risks.append("价格位于 MA20 下方")
    if None not in (ma5, ma10, ma20):
        if ma5 > ma10 > ma20:
            score += 12
            positives.append("短中期均线呈多头排列")
        elif ma5 < ma10 < ma20:
            score -= 12
            risks.append("短中期均线呈空头排列")
    if _number(hist.iloc[-1]) is not None:
        if hist.iloc[-1] > 0:
            score += 10
            positives.append("MACD 动能为正")
        else:
            score -= 10
            risks.append("MACD 动能为负")
    if rsi14 is not None:
        if rsi14 > 70:
            score -= 8
            risks.append(f"RSI {rsi14:.1f}，短线偏热")
        elif rsi14 < 30:
            score += 5
            positives.append(f"RSI {rsi14:.1f}，处于超卖区")
    score = max(0, min(100, score))
    label = "偏强" if score >= 60 else "偏弱" if score < 40 else "中性"
    interpretation = f"{asset.upper()} 在 {interval} 周期的技术评分为 {score} 分，当前状态为「{label}」。"
    if positives:
        interpretation += " 积极信号：" + "；".join(positives[:2]) + "。"
    if risks:
        interpretation += " 风险信号：" + "；".join(risks[:2]) + "。"

    records = frame[["time", "open", "high", "low", "close", "volume", "ma5", "ma10", "ma20"]].copy()
    records = records.rename(columns={"time": "date"}).where(pd.notna(records), None)
    return {
        "asset": asset.upper(), "interval": interval, "score": score, "label": label,
        "source": str(latest.get("source", "")), "stale": bool(frame.attrs.get("stale", False)),
        "indicators": {
            "ma5": ma5, "ma10": ma10, "ma20": ma20,
            "macd": {"dif": _number(dif.iloc[-1]), "dea": _number(dea.iloc[-1]), "hist": _number(hist.iloc[-1])},
            "rsi14": rsi14,
            "boll": {"upper": _number(boll_upper.iloc[-1], 2), "mid": _number(boll_mid.iloc[-1], 2), "lower": _number(boll_lower.iloc[-1], 2)},
        },
        "positives": positives, "risks": risks, "interpretation": interpretation,
        "kline": records.to_dict(orient="records"),
    }
