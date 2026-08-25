"""大盘/指数分析：指数表现、量价关系、涨跌家数、市场温度。"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pandas as pd

from backend.data import fetcher

UP = "up"
DOWN = "down"
FLAT = "flat"


def _safe_float(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def market_breadth() -> dict:
    """新浪全市场 JSON 与东方财富涨跌停池并发汇总。"""
    cached = fetcher.mem_get("market_breadth")
    if cached is not None:
        return cached

    out = {}
    # 高频关键数据并发直连：新浪全市场 JSON + 东财涨跌停池。
    today = date.today().strftime("%Y%m%d")
    with ThreadPoolExecutor(max_workers=3) as executor:
        spot_future = executor.submit(fetcher.market_spot)
        zt_future = executor.submit(fetcher.zt_pool, today)
        dt_future = executor.submit(fetcher.dt_pool, today)
        try:
            spot = spot_future.result()
            pct = pd.to_numeric(spot["涨跌幅"], errors="coerce").fillna(0.0)
            up, down, flat = int((pct > 0).sum()), int((pct < 0).sum()), int((pct == 0).sum())
            total = int(len(spot))
            out = {
                "total": total, "up": up, "down": down, "flat": flat,
                "limit_up": int((pct >= 9.8).sum()), "limit_down": int((pct <= -9.8).sum()),
                "up_ratio": round(up / total, 4) if total else 0.0, "source": "sina-api",
                "stale": bool(spot.attrs.get("stale", False)),
            }
        except Exception:
            spot = pd.DataFrame()
        try:
            zt_fast = zt_future.result()
        except Exception:
            zt_fast = pd.DataFrame()
        try:
            dt_fast = dt_future.result()
        except Exception:
            dt_fast = pd.DataFrame()

    # 涨跌停数用东财池精确补充（快速）
    if out:
        try:
            zt, dt = zt_fast, dt_fast
            if zt is not None and not zt.empty:
                out["limit_up"] = int(len(zt))
            if dt is not None and not dt.empty:
                out["limit_down"] = int(len(dt))
        except Exception:
            pass

    if not out:
        out = {"total": 0, "up": 0, "down": 0, "flat": 0, "limit_up": 0, "limit_down": 0, "up_ratio": 0.0}
    fetcher.mem_set("market_breadth", out)
    return out


def market_volume() -> dict:
    """两市成交额；与涨跌分布复用并发快照缓存。"""
    cached = fetcher.mem_get("market_volume")
    if cached is not None:
        return cached

    # 与 market_breadth 共用同一个并发快照请求；同 key 锁会自动合并请求。
    try:
        spot = fetcher.market_spot()
        if spot is not None and not spot.empty:
            amount = pd.to_numeric(spot["成交额"], errors="coerce").fillna(0.0).sum()
            out = {"amount": round(float(amount), 2), "amount_yi": round(float(amount) / 1e8, 2), "stale": bool(spot.attrs.get("stale", False))}
            fetcher.mem_set("market_volume", out)
            return out
    except Exception:
        pass

    return {"amount": 0.0, "amount_yi": 0.0}


def indices_overview() -> list[dict]:
    """主要指数表现。"""
    df = fetcher.index_realtime()
    if df is None or df.empty:
        return []
    return df.to_dict(orient="records")


def _compute_technicals(df: pd.DataFrame) -> dict:
    """基于指数日线计算均线/量能等。"""
    close = pd.to_numeric(df["close"], errors="coerce")
    vol = pd.to_numeric(df["volume"], errors="coerce")
    out = {}
    for n in (5, 10, 20, 60):
        ma = close.rolling(n).mean()
        out[f"ma{n}"] = round(float(ma.iloc[-1]), 2) if not pd.isna(ma.iloc[-1]) else None
    out["ma5_above"] = bool(close.iloc[-1] > out.get("ma5", 0)) if out.get("ma5") else None
    # 量能：近5日均量 vs 近20日均量
    v5 = vol.tail(5).mean()
    v20 = vol.tail(20).mean()
    out["vol_ratio_5_20"] = round(float(v5 / v20), 2) if v20 else None
    out["trend_20d"] = round(float((close.iloc[-1] / close.iloc[-21] - 1) * 100), 2) if len(close) > 21 else None
    return out


def index_detail(symbol: str) -> dict:
    """单指数详情：日线技术指标 + 近60日K线（供前端画图）。"""
    try:
        df = fetcher.index_daily(symbol)
    except Exception:
        return {"symbol": symbol, "error": "数据获取失败"}
    if df is None or df.empty:
        return {"symbol": symbol, "error": "无数据"}

    tech = _compute_technicals(df)
    kline = df.tail(90).copy()  # 指数默认只返回近 90 根，避免K线过密
    kline["date"] = kline["date"].astype(str)
    return {
        "symbol": symbol,
        "name": fetcher.config.INDEX_SYMBOLS.get(symbol, symbol),
        "technicals": tech,
        "stale": bool(df.attrs.get("stale", False)),
        "kline": kline[["date", "open", "high", "low", "close", "volume"]].to_dict(orient="records"),
    }


def market_temperature() -> dict:
    """市场温度：综合涨跌比、涨停数、量能给出 0-100 温度分与定性。结果30秒缓存。"""
    cached = fetcher.mem_get("market_temperature")
    if cached is not None:
        return cached
    breadth = market_breadth()
    up_ratio = breadth.get("up_ratio", 0.0)
    limit_up = int(breadth.get("limit_up", 0))
    limit_down = int(breadth.get("limit_down", 0))

    # 涨跌比得分（0-40）
    score_ratio = min(40.0, up_ratio * 80.0)
    # 涨停得分（0-30）：>80 极热，30-80 活跃，<30 偏冷
    score_zt = min(30.0, limit_up * 0.4)
    # 跌停惩罚（0-20 倒扣）
    penalty = min(20.0, limit_down * 1.5)
    # 量能得分（0-10）
    vol = market_volume()
    amount_yi = vol.get("amount_yi", 0.0)
    score_vol = min(10.0, amount_yi / 200.0) if amount_yi else 5.0  # 2万亿以上满分

    temp = max(0.0, min(100.0, score_ratio + score_zt + score_vol - penalty))
    temp = round(temp, 1)

    if temp >= 70:
        label, tone = "火热", "进攻"
    elif temp >= 50:
        label, tone = "偏暖", "均衡"
    elif temp >= 30:
        label, tone = "偏冷", "防守"
    else:
        label, tone = "冰点", "观望"

    result = {
        "temperature": temp,
        "label": label,
        "tone": tone,
        "up_ratio": round(up_ratio, 4),
        "limit_up": limit_up,
        "limit_down": limit_down,
        "amount_yi": round(amount_yi, 2),
        "volume_partial": vol.get("partial", False),
    }
    fetcher.mem_set("market_temperature", result)
    return result


def overview() -> dict:
    """大盘总览（一次调用返回全部大盘指标）。结果 30 秒内存缓存，避免重复计算。"""
    cached = fetcher.mem_get("market_overview")
    if cached is not None:
        return cached

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_breadth = ex.submit(market_breadth)
        f_volume = ex.submit(market_volume)
        f_indices = ex.submit(indices_overview)
        f_temp = ex.submit(market_temperature)
        breadth = f_breadth.result()
        volume = f_volume.result()
        indices = f_indices.result()
        temp = f_temp.result()

    summary = []
    if indices:
        top = indices[0]  # 上证指数
        summary.append(f"上证指数收于 {top['close']} 点，涨跌幅 {top['change_pct']:+.2f}%。")
    summary.append(f"两市共 {breadth['total']} 只股票，上涨 {breadth['up']} 家、下跌 {breadth['down']} 家，"
                   f"涨跌比 {breadth['up_ratio']:.1%}，市场温度 {temp['temperature']} 分（{temp['label']}）。")
    if volume["amount_yi"]:
        if volume.get("partial") == "deep":
            summary.append(f"深市成交 {volume['amount_yi']:.0f} 亿元（两市合计数据加载中）。")
        else:
            summary.append(f"两市合计成交 {volume['amount_yi']:.0f} 亿元，"
                           f"{'放量' if volume['amount_yi'] >= 10000 else '缩量'}运行。")

    out = {
        "indices": indices,
        "breadth": breadth,
        "volume": volume,
        "temperature": temp,
        "summary": " ".join(summary),
        "as_of": _now_str(),
    }
    fetcher.mem_set("market_overview", out)
    return out


def _now_str() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
