"""涨停/短线情绪复盘：连板梯队、炸板率、封板质量、情绪周期判断。"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd

from backend.data import fetcher


def _f(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _today_str() -> str:
    return date.today().strftime("%Y%m%d")


def _last_trade_dates(n: int = 7) -> list[str]:
    """粗略取最近 n 个工作日（含今天），用于涨停池查询。"""
    out, d, added = [], date.today(), 0
    while added < n:
        if d.weekday() < 5:  # 周一~周五
            out.append(d.strftime("%Y%m%d"))
            added += 1
        d -= timedelta(days=1)
    return out


def limit_up_review(trade_date: str | None = None) -> dict:
    """涨停复盘主函数。trade_date 缺省用今天（无数据则回退最近交易日）。"""
    target = trade_date or _today_str()
    try:
        zt = fetcher.zt_pool(target)
    except Exception:
        zt = pd.DataFrame()

    # 若当天无涨停数据（非交易日/未开盘），回退到最近有数据的日期
    used_date = target
    if zt is None or zt.empty:
        for d in _last_trade_dates(6):
            if d == target:
                continue
            try:
                zt = fetcher.zt_pool(d)
                if zt is not None and not zt.empty:
                    used_date = d
                    break
            except Exception:
                continue
    if zt is None or zt.empty:
        return {"error": "近期无涨停数据（可能非交易日）", "trade_date": target}

    return _build_review(zt, used_date)


def _build_review(zt: pd.DataFrame, trade_date: str) -> dict:
    zt = zt.copy()
    # 列名兼容
    rename = {
        "代码": "code", "名称": "name", "涨跌幅": "pct", "最新价": "price",
        "成交额": "amount", "流通市值": "float_mv", "总市值": "total_mv",
        "换手率": "turnover", "封板资金": "seal_money", "首次封板时间": "first_seal",
        "最后封板时间": "last_seal", "炸板次数": "open_count", "连板数": "streak",
        "所属行业": "industry", "涨停统计": "zt_stats",
    }
    zt = zt.rename(columns={k: v for k, v in rename.items() if k in zt.columns})

    total = len(zt)

    # 连板梯队
    streak = pd.to_numeric(zt["streak"], errors="coerce").fillna(1).astype(int) if "streak" in zt else pd.Series([1] * total)
    max_streak = int(streak.max()) if total else 0
    ladder = {}
    for level in range(1, max_streak + 1):
        ladder[str(level)] = int((streak == level).sum())
    # 高标（>=3板）
    high_streak = zt[streak >= 3] if total else zt

    # 行业分布
    industry_dist = {}
    if "industry" in zt:
        industry_dist = zt["industry"].value_counts().head(10).to_dict()
        industry_dist = {str(k): int(v) for k, v in industry_dist.items()}

    # 封板质量：封单/流通市值 与 炸板次数
    seal_money = pd.to_numeric(zt["seal_money"], errors="coerce") if "seal_money" in zt else pd.Series([0.0] * total)
    float_mv = pd.to_numeric(zt["float_mv"], errors="coerce") if "float_mv" in zt else pd.Series([1.0] * total)
    seal_ratio = (seal_money / float_mv.replace(0, pd.NA) * 100)
    first_seal_time = zt["first_seal"] if "first_seal" in zt else None
    early_seal = 0
    if first_seal_time is not None:
        ts = pd.to_numeric(first_seal_time, errors="coerce")
        early_seal = int((ts <= 93000).sum())  # 09:30:00 前封板 = 一字/秒板

    # 榜单排序：连板数降序 + 封单额降序
    rank_df = zt.copy()
    rank_df["_streak"] = streak
    rank_df["_seal"] = seal_money
    rank_df["_seal_ratio"] = seal_ratio
    rank_df = rank_df.sort_values(["_streak", "_seal"], ascending=False).head(20)

    leaders = []
    for _, r in rank_df.iterrows():
        leaders.append({
            "code": str(r.get("code", "")),
            "name": str(r.get("name", "")),
            "streak": int(r.get("_streak", 1)),
            "price": round(_f(r.get("price")), 2),
            "pct": round(_f(r.get("pct")), 2),
            "seal_money_yi": round(_f(r.get("_seal")) / 1e8, 2),
            "seal_ratio": round(_f(r.get("_seal_ratio")), 2) if not pd.isna(r.get("_seal_ratio")) else None,
            "open_count": int(_f(r.get("open_count"))),
            "first_seal": str(r.get("first_seal", "")),
            "industry": str(r.get("industry", "")),
            "turnover": round(_f(r.get("turnover")), 2),
        })

    return {
        "trade_date": trade_date,
        "stale": bool(zt.attrs.get("stale", False)),
        "total": total,
        "max_streak": max_streak,
        "ladder": ladder,
        "high_streak_count": int(len(high_streak)),
        "industry_dist": industry_dist,
        "early_seal_count": early_seal,
        "leaders": leaders,
        "seal_quality": {
            "avg_seal_ratio": round(float(seal_ratio.mean()), 2) if total else None,
            "avg_open_count": round(float(pd.to_numeric(zt["open_count"], errors="coerce").mean()), 2) if "open_count" in zt else None,
        },
    }


def sentiment_review(trade_date: str | None = None) -> dict:
    """情绪综合判断：涨停数、连板高度、炸板率、晋级率、情绪温度。"""
    target = trade_date or _today_str()
    with ThreadPoolExecutor(max_workers=2) as executor:
        zt_future = executor.submit(fetcher.zt_pool, target)
        zb_future = executor.submit(fetcher.zb_pool, target)
        try:
            zt = zt_future.result()
        except Exception:
            zt = pd.DataFrame()
        try:
            zb = zb_future.result()
        except Exception:
            zb = pd.DataFrame()

    used_date = target
    if (zt is None or zt.empty) and (zb is None or zb.empty):
        for d in _last_trade_dates(6):
            if d == target:
                continue
            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    zt, zb = list(executor.map(lambda fn: fn(d), (fetcher.zt_pool, fetcher.zb_pool)))
                if (zt is not None and not zt.empty) or (zb is not None and not zb.empty):
                    used_date = d
                    break
            except Exception:
                continue

    zt_n = len(zt) if zt is not None else 0
    zb_n = len(zb) if zb is not None else 0

    # 炸板率 = 炸板 / (涨停 + 炸板)
    zha_rate = round(zb_n / (zt_n + zb_n) * 100, 1) if (zt_n + zb_n) else None

    # 连板高度
    max_streak = 0
    if zt is not None and not zt.empty and "连板数" in zt.columns:
        max_streak = int(pd.to_numeric(zt["连板数"], errors="coerce").fillna(1).max())

    # 晋级率：昨日涨停今日继续涨停比例
    promo = None
    try:
        prev_date = _last_trade_dates(5)[-1]
        prev = fetcher.zt_pool(prev_date)
        if prev is not None and not prev.empty:
            prev_codes = set(prev["代码"].astype(str))
            today_codes = set(zt["代码"].astype(str)) if zt is not None and not zt.empty else set()
            promo = round(len(prev_codes & today_codes) / len(prev_codes) * 100, 1)
    except Exception:
        pass

    # 情绪温度 0-100
    if zt_n == 0 and zb_n == 0:
        temp, phase = 0, "冰点（无交易数据）"
    else:
        s_zt = min(50, zt_n * 0.6)            # 涨停数
        s_streak = min(30, max_streak * 6)    # 高度
        s_zha = 20 - (zha_rate or 0) * 0.25   # 炸板率越低越好
        temp = max(0, min(100, s_zt + s_streak + s_zha))
        temp = round(temp, 1)
        if zha_rate is not None and zha_rate > 40:
            phase = "退潮" if temp < 50 else "分歧加剧"
        elif max_streak >= 5 and zt_n >= 60:
            phase = "主升/亢奋"
        elif zt_n >= 40 and zha_rate is not None and zha_rate < 30:
            phase = "加强"
        elif zt_n >= 20:
            phase = "修复/活跃"
        else:
            phase = "冰点/酝酿"

    return {
        "trade_date": used_date,
        "stale": bool(getattr(zt, "attrs", {}).get("stale", False) or getattr(zb, "attrs", {}).get("stale", False)),
        "limit_up": zt_n,
        "zha_ban": zb_n,
        "zha_rate": zha_rate,
        "max_streak": max_streak,
        "promotion_rate": promo,
        "temperature": temp,
        "phase": phase,
    }


def full_review(trade_date: str | None = None) -> dict:
    """完整复盘：榜单 + 情绪 + 简明结论。结果 60 秒内存缓存。"""
    target = trade_date or _today_str()
    key = f"limitup_full_{target}"
    cached = fetcher.mem_get(key)
    if cached is not None:
        return cached
    with ThreadPoolExecutor(max_workers=2) as executor:
        review_future = executor.submit(limit_up_review, target)
        senti_future = executor.submit(sentiment_review, target)
        review, senti = review_future.result(), senti_future.result()

    if "error" in review:
        return review

    # 结论文本
    phase = senti["phase"]
    conclusion = (
        f"{senti['trade_date']} 涨停 {senti['limit_up']} 家、炸板 {senti['zha_ban']} 家"
        f"（炸板率 {senti['zha_rate']}%），最高连板 {senti['max_streak']} 板，"
        f"情绪温度 {senti['temperature']} 分，当前阶段：{phase}。"
    )
    if senti["zha_rate"] is not None:
        if senti["zha_rate"] > 40:
            conclusion += "炸板率高企，追高需谨慎，注意分歧兑现。"
        elif senti["zha_rate"] < 20 and senti["limit_up"] >= 40:
            conclusion += "封板质量好，情绪处于活跃期，可关注主线龙头。"
    if review["industry_dist"]:
        top_ind = next(iter(review["industry_dist"].items()))
        conclusion += f"涨停最集中的行业为「{top_ind[0]}」（{top_ind[1]} 家）。"

    result = {
        **senti,
        "ladder": review["ladder"],
        "industry_dist": review["industry_dist"],
        "leaders": review["leaders"],
        "seal_quality": review["seal_quality"],
        "early_seal_count": review["early_seal_count"],
        "conclusion": conclusion,
    }
    fetcher.mem_set(key, result)
    return result
