"""小白涨跌日报：把行情翻译成普通人能看懂的报告。

输出包含：今日大盘一句话、涨跌榜 Top10、热门板块、资金动向、明日关注，
全部用通俗语言（避免术语堆砌）。
"""
from __future__ import annotations

import math
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pandas as pd

from backend.data import fetcher
from backend.analysis import market as market_an


def _f(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _pct_str(v) -> str:
    return f"{v:+.2f}%" if v is not None else "--"


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _is_weekend() -> bool:
    return datetime.now().weekday() >= 5


def gainers_losers(n: int = 10) -> dict:
    """涨跌幅 Top 榜：复用新浪并发全市场快照。"""
    cached = fetcher.mem_get("gainers_losers")
    if cached is not None:
        return cached

    out = {"gainers": [], "losers": [], "active": [], "degraded": False, "source": "sina-api"}
    try:
        spot = fetcher.market_spot()
        if spot is not None and not spot.empty:
            frame = spot.copy()
            frame["涨跌幅"] = pd.to_numeric(frame["涨跌幅"], errors="coerce")
            frame["成交额"] = pd.to_numeric(frame["成交额"], errors="coerce")
            frame = frame.dropna(subset=["涨跌幅"])
            for key, rows in (("gainers", frame.nlargest(n, "涨跌幅")), ("losers", frame.nsmallest(n, "涨跌幅"))):
                out[key] = [{
                    "代码": str(row["代码"]), "名称": str(row["名称"]),
                    "最新价": round(_f(row["最新价"]), 2), "涨跌幅": round(_f(row["涨跌幅"]), 2),
                    "成交额": round(_f(row["成交额"]) / 1e8, 2),
                } for row in rows[["代码", "名称", "最新价", "涨跌幅", "成交额"]].to_dict(orient="records")]
    except Exception:
        out["degraded"] = True
    fetcher.mem_set("gainers_losers", out)
    return out


def hot_boards() -> list[dict]:
    """热门板块：行业板块涨跌 Top。"""
    try:
        df = fetcher.board_industry_summary()
        if df is None or df.empty:
            return []
        df = df.copy()
        df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
        df = df.dropna(subset=["涨跌幅"])
        up = df.nlargest(8, "涨跌幅")
        down = df.nsmallest(5, "涨跌幅")
        rows = []
        for r in pd.concat([up, down]).to_dict(orient="records"):
            rows.append({
                "board": str(r.get("板块", "")),
                "pct": round(_f(r.get("涨跌幅")), 2),
                "amount": round(_f(r.get("总成交额")) / 1e8, 2),
            })
        return rows
    except Exception:
        return []


def _plain_market_line(ov: dict) -> str:
    """大盘一句话（大白话）。"""
    parts = []
    indices = ov.get("indices", [])
    if indices:
        sh = indices[0]
        updown = "涨了" if sh["change_pct"] >= 0 else "跌了"
        parts.append(f"今天上证指数{updown}，收在 {sh['close']} 点（{sh['change_pct']:+.2f}%）")
    b = ov.get("breadth", {})
    if b.get("total"):
        ratio = b.get("up_ratio", 0)
        if ratio > 0.6:
            mood = "市场挺热，大多数股票都在涨，赚钱效应不错"
        elif ratio > 0.4:
            mood = "涨跌差不多，市场比较纠结，分化明显"
        else:
            mood = "跌的比涨的多，市场情绪偏弱，要小心"
        parts.append(f"两市 {b['total']} 只股票里 {b['up']} 只上涨、{b['down']} 只下跌——{mood}")
    v = ov.get("volume", {})
    if v.get("amount_yi"):
        parts.append(f"两市成交 {v['amount_yi']:.0f} 亿元")
    return "，".join(parts) + "。"


def market_brief() -> dict:
    """日报首屏摘要；与榜单、板块、资金和涨停模块独立返回。"""
    cached = fetcher.mem_get("daily_market_brief")
    if cached is not None:
        return cached
    ov = market_an.overview()
    temp = ov.get("temperature", {})
    result = {
        "date": _today(),
        "market_line": _plain_market_line(ov),
        "temperature": temp,
        "conclusion": (
            f"整体来看，今天市场温度 {temp.get('temperature', '--')} 分"
            f"（{temp.get('label', '未知')}），适合「{temp.get('tone', '观望')}」的策略。"
        ),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    fetcher.mem_set("daily_market_brief", result)
    return result


def daily_report() -> dict:
    """小白涨跌日报主入口。结果 60 秒内存缓存。"""
    cached = fetcher.mem_get("daily_report")
    if cached is not None:
        return cached
    from backend.analysis import limitup

    with ThreadPoolExecutor(max_workers=5) as executor:
        ov_future = executor.submit(market_an.overview)
        gl_future = executor.submit(gainers_losers)
        boards_future = executor.submit(hot_boards)
        hsgt_future = executor.submit(fetcher.hsgt_summary)
        sentiment_future = executor.submit(limitup.sentiment_review)
        ov = ov_future.result()
        gl = gl_future.result()
        boards = boards_future.result()

    # 北向资金（可选，失败不阻塞）
    hsgt_note = ""
    try:
        hs = hsgt_future.result()
        if hs is not None and not hs.empty:
            total = hs[hs["类型"] == "北向"].get("成交净买额", pd.Series([0.0]))
            val = _f(total.sum()) / 1e8 if total.sum() else 0.0
            if abs(val) > 0:
                direction = "买入" if val > 0 else "卖出"
                hsgt_note = f"北向资金今日净{direction}约 {abs(val):.1f} 亿元。"
    except Exception:
        pass

    # 涨停速览
    zt_note = ""
    try:
        s = sentiment_future.result()
        if "limit_up" in s:
            zt_note = (
                f"今天有 {s['limit_up']} 只股票涨停（最多连板 {s['max_streak']} 板），"
                f"炸板 {s['zha_ban']} 只。当前情绪阶段：{s['phase']}。"
            )
    except Exception:
        pass

    # 通俗结论
    temp = ov.get("temperature", {})
    temp_label = temp.get("label", "未知")
    tone = temp.get("tone", "观望")
    conclusion = (
        f"整体来看，今天市场温度 {temp.get('temperature', '--')} 分（{temp_label}），"
        f"适合「{tone}」的策略。"
    )
    if temp.get("limit_down", 0) > 10:
        conclusion += "跌停数量偏多，注意回避弱势股。"
    if boards:
        top_b = boards[0]
        if top_b["pct"] >= 0:
            conclusion += f"今天最火的是{top_b['board']}板块（涨 {top_b['pct']:+.2f}%）。"
        else:
            conclusion += f"今天最强板块也才 {top_b['pct']:+.2f}%，整体偏弱。"

    result = {
        "date": _today(),
        "weekend": _is_weekend(),
        "market_line": _plain_market_line(ov),
        "temperature": temp,
        "breadth": ov.get("breadth", {}),
        "volume": ov.get("volume", {}),
        "gainers": gl["gainers"],
        "losers": gl["losers"],
        "active": gl["active"],
        "degraded": gl.get("degraded", False),
        "hot_boards": boards,
        "hsgt_note": hsgt_note,
        "zt_note": zt_note,
        "conclusion": conclusion,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    fetcher.mem_set("daily_report", result)
    return result


def archive_report() -> dict:
    """将当天完整日报保存到便携 data/reports 目录。"""
    from backend import config

    report = daily_report()
    path = config.REPORT_DIR / f"{report['date']}.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return report


def report_history(days: int = 30) -> list[dict]:
    from backend import config

    cutoff = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = []
    for path in sorted(config.REPORT_DIR.glob("????-??-??.json"), reverse=True):
        if path.stem < cutoff:
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8-sig"))
            rows.append({
                "date": report.get("date", path.stem),
                "market_line": report.get("market_line", ""),
                "temperature": report.get("temperature", {}),
            })
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def historical_report(report_date: str) -> dict | None:
    from backend import config

    try:
        datetime.strptime(report_date, "%Y-%m-%d")
    except ValueError:
        return None
    path = config.REPORT_DIR / f"{report_date}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
