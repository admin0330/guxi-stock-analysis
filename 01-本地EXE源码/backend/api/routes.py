"""REST API 路由。"""
from __future__ import annotations

import math
from datetime import datetime

import pandas as pd
from fastapi import APIRouter, Body, HTTPException, Query, Request

from backend.analysis import daily as daily_an
from backend.analysis import daily_pick as daily_pick_an
from backend.analysis import crypto as crypto_an
from backend.analysis import limitup as limitup_an
from backend.analysis import market as market_an
from backend.analysis import stock as stock_an
from backend.data import cache, crypto as crypto_data, fetcher, http, user_store

router = APIRouter(prefix="/api")


def _clean(v):
    """递归清洗 NaN/Infinity，保证 JSON 可序列化。"""
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def _ok(data) -> dict:
    """包装响应并清洗。"""
    return _clean(data)


@router.get("/health")
def health():
    last_success = http.last_success_at()
    return _ok({
        "status": "ok",
        "version": fetcher.config.APP_VERSION,
        "last_data_success": datetime.fromtimestamp(last_success).isoformat(timespec="seconds") if last_success else None,
    })


# ---------------------------------------------------------------- 本地用户状态

def _user_id(request: Request) -> int | None:
    session = getattr(request.state, "auth_session", None)
    return session["id"] if session else None


@router.get("/user/state")
def user_state(request: Request):
    return _ok(user_store.load(_user_id(request)))


@router.patch("/user/state")
def user_state_update(request: Request, changes: dict = Body(...)):
    return _ok(user_store.update(changes, _user_id(request)))


@router.get("/watchlist")
def watchlist(request: Request):
    symbols = user_store.load(_user_id(request))["watchlist"]
    if not symbols:
        return _ok({"items": []})
    try:
        spot = fetcher.market_spot()
        indexed = {str(row["代码"]): row for row in spot.to_dict(orient="records")} if spot is not None else {}
    except Exception:
        indexed = {}
    items = []
    for symbol in symbols:
        code = symbol[-6:]
        row = indexed.get(code, {})
        items.append({
            "symbol": symbol,
            "code": code,
            "name": str(row.get("名称") or code),
            "price": row.get("最新价"),
            "pct": row.get("涨跌幅"),
            "cached": not bool(row),
        })
    return _ok({"items": items})


@router.post("/watchlist/{symbol}")
def watchlist_add(symbol: str, request: Request):
    try:
        return _ok(user_store.add_watch(fetcher.normalize_symbol(symbol), _user_id(request)))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/watchlist/{symbol}")
def watchlist_remove(symbol: str, request: Request):
    return _ok(user_store.remove_watch(fetcher.normalize_symbol(symbol), _user_id(request)))


# ---------------------------------------------------------------- 大盘

@router.get("/market/overview")
def market_overview():
    """大盘总览：指数、涨跌家数、量能、市场温度。"""
    try:
        return _ok(market_an.overview())
    except Exception as e:
        raise HTTPException(500, f"大盘数据获取失败: {e}") from e


@router.get("/market/indices")
def market_indices():
    """主要指数表现。"""
    try:
        rows = market_an.indices_overview()
        return _ok({"indices": rows, "stale": any(row.get("stale", False) for row in rows)})
    except Exception as e:
        raise HTTPException(500, f"指数数据获取失败: {e}") from e


@router.get("/market/breadth")
def market_breadth():
    """涨跌家数分布（独立轻量接口，供首页渐进加载）。"""
    try:
        return _ok(market_an.market_breadth())
    except Exception as e:
        raise HTTPException(500, f"涨跌分布获取失败: {e}") from e


@router.get("/market/volume")
def market_volume():
    """成交额（独立轻量接口，供首页渐进加载）。"""
    try:
        return _ok(market_an.market_volume())
    except Exception as e:
        raise HTTPException(500, f"成交额获取失败: {e}") from e


@router.get("/market/index/{symbol}")
def market_index_detail(symbol: str):
    """单指数详情与K线。"""
    sym = symbol.lower()
    if sym not in fetcher.config.INDEX_SYMBOLS:
        raise HTTPException(404, f"不支持的指数: {symbol}")
    return _ok(market_an.index_detail(sym))


@router.get("/market/temperature")
def market_temperature():
    """市场温度评分。"""
    try:
        return _ok(market_an.market_temperature())
    except Exception as e:
        raise HTTPException(500, f"市场温度获取失败: {e}") from e


@router.get("/market/hsgt")
def market_hsgt():
    """北向资金速览（轻量接口）。"""
    try:
        from backend.data import fetcher as _f

        hs = _f.hsgt_summary()
        if hs is None or hs.empty:
            return _ok({"note": ""})
        total = hs[hs["类型"] == "北向"].get("成交净买额", pd.Series([0.0]))
        val = float(total.sum()) / 1e8 if total.sum() else 0.0
        if abs(val) < 0.01:
            return _ok({"note": ""})
        direction = "买入" if val > 0 else "卖出"
        return _ok({"note": f"北向资金今日净{direction}约 {abs(val):.1f} 亿元。"})
    except Exception:
        return _ok({"note": ""})


# ---------------------------------------------------------------- 个股

@router.get("/stock/search")
def stock_search(q: str = Query(..., min_length=1)):
    """按代码或名称搜索股票，返回匹配列表。"""
    try:
        spot = fetcher.market_spot()
        if spot is None or spot.empty:
            return {"results": []}
        df = spot.copy()
        q = q.strip().lower()
        mask = df["代码"].astype(str).str.contains(q, case=False) | df["名称"].astype(str).str.contains(q, case=False)
        hits = df[mask].head(20)
        results = [
            {
                "code": str(r["代码"]),
                "symbol": fetcher.normalize_symbol(str(r["代码"])),
                "name": str(r["名称"]),
                "price": round(float(r["最新价"]), 2) if r["最新价"] == r["最新价"] else None,
                "pct": round(float(r["涨跌幅"]), 2) if r["涨跌幅"] == r["涨跌幅"] else None,
            }
            for r in hits.to_dict(orient="records")
        ]
        return _ok({"results": results})
    except Exception as e:
        raise HTTPException(500, f"搜索失败: {e}") from e


@router.get("/stock/{symbol}/profile")
def stock_profile(symbol: str):
    """公司基础资料；独立轻量接口，便于前端渐进加载。"""
    sym = fetcher.normalize_symbol(symbol)
    profile = fetcher.stock_open_api_company_profile(sym)
    return _ok({
        "symbol": sym,
        "available": bool(profile),
        "source": "stock-open-api/eastmoney",
        "profile": profile or None,
    })


@router.get("/stock/{symbol}/technical")
def stock_technical(symbol: str):
    """技术面独立报告；与其他维度并行请求。"""
    try:
        result = stock_an.technical_report(symbol)
        if result.get("data", {}).get("error"):
            raise HTTPException(404, f"个股 {symbol} 无日线数据，请检查代码")
        return _ok(result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"技术面分析失败: {e}") from e


@router.get("/stock/{symbol}/fundamental")
def stock_fundamental(symbol: str):
    """基本面独立报告；与其他维度并行请求。"""
    try:
        return _ok(stock_an.fundamental_report(symbol))
    except Exception as e:
        raise HTTPException(500, f"基本面分析失败: {e}") from e


@router.get("/stock/{symbol}/fund")
def stock_fund(symbol: str):
    """资金面独立报告；与其他维度并行请求。"""
    try:
        return _ok(stock_an.fund_report(symbol))
    except Exception as e:
        raise HTTPException(500, f"资金面分析失败: {e}") from e


@router.get("/stock/{symbol}")
def stock_analyze(symbol: str):
    """个股综合分析报告。symbol 可为 '600519' 或 'sh600519'。"""
    try:
        result = stock_an.analyze(symbol)
        if result.get("technical", {}).get("error"):
            raise HTTPException(404, f"个股 {symbol} 无日线数据，请检查代码")
        return _ok(result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"个股分析失败: {e}") from e


@router.get("/stock/{symbol}/kline")
def stock_kline(symbol: str, limit: int = Query(120, ge=30, le=500)):
    """个股K线（前复权）。"""
    try:
        sym = fetcher.normalize_symbol(symbol)
        df = fetcher.stock_daily(sym)
        if df is None or df.empty:
            raise HTTPException(404, f"个股 {symbol} 无日线数据")
        kline = df.tail(limit).copy()
        kline["date"] = kline["date"].astype(str)
        return _ok({
            "symbol": sym,
            "kline": kline[["date", "open", "high", "low", "close", "volume"]].to_dict(orient="records"),
            "stale": bool(df.attrs.get("stale", False)),
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"K线获取失败: {e}") from e


# ---------------------------------------------------------------- 加密货币

@router.get("/crypto/overview")
def crypto_overview(refresh: bool = False):
    """BTC / ETH 最新行情；Binance 主源，OKX 回退。"""
    try:
        return _ok(crypto_data.overview(force=refresh))
    except Exception as e:
        raise HTTPException(503, f"加密货币行情暂不可用: {e}") from e


@router.get("/crypto/{asset}/analysis")
def crypto_analysis(
    asset: str,
    interval: str = Query("1h", pattern="^(1m|5m|15m|1h|4h|1d)$"),
    limit: int = Query(240, ge=60, le=500),
    refresh: bool = False,
):
    """单资产 K 线、MA/MACD/RSI/BOLL 与技术评分。"""
    if asset.upper() not in crypto_data.ASSETS:
        raise HTTPException(404, f"暂不支持的加密资产: {asset}")
    try:
        return _ok(crypto_an.analyze(asset, interval, limit, refresh))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(503, f"{asset.upper()} 技术分析暂不可用: {e}") from e


# ---------------------------------------------------------------- 涨停复盘

@router.get("/limitup/review")
def limitup_review(trade_date: str | None = None):
    """涨停复盘完整报告。"""
    try:
        return _ok(limitup_an.full_review(trade_date))
    except Exception as e:
        raise HTTPException(500, f"涨停复盘失败: {e}") from e


@router.get("/limitup/sentiment")
def limitup_sentiment(trade_date: str | None = None):
    """情绪指标：涨停数、炸板率、连板高度、晋级率、情绪温度。"""
    try:
        return _ok(limitup_an.sentiment_review(trade_date))
    except Exception as e:
        raise HTTPException(500, f"情绪分析失败: {e}") from e


@router.get("/limitup/pool")
def limitup_pool(trade_date: str | None = None):
    """涨停梯队、行业与龙头榜；与情绪指标并行加载。"""
    try:
        return _ok(limitup_an.limit_up_review(trade_date))
    except Exception as e:
        raise HTTPException(500, f"涨停池分析失败: {e}") from e


# ---------------------------------------------------------------- 小白日报

@router.get("/daily-picks")
def daily_picks(date: str | None = None, refresh: bool = False):
    """每日透明规则关注池；当日优先读取本地 Parquet。"""
    try:
        return _ok(daily_pick_an.get(date, refresh))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:
        raise HTTPException(503, f"每日关注池暂不可用: {e}") from e


@router.get("/daily-picks/history")
def daily_picks_history(days: int = Query(10, ge=1, le=30)):
    return _ok({"items": daily_pick_an.history(days)})


@router.get("/daily-picks/rules")
def daily_picks_rules():
    return _ok(daily_pick_an.rules())


@router.get("/daily/market")
def daily_market():
    """日报市场摘要；与榜单、板块等模块独立加载。"""
    try:
        return _ok(daily_an.market_brief())
    except Exception as e:
        raise HTTPException(500, f"日报市场摘要失败: {e}") from e


@router.get("/daily/ranks")
def daily_ranks():
    """日报涨跌榜。"""
    try:
        return _ok(daily_an.gainers_losers())
    except Exception as e:
        raise HTTPException(500, f"日报涨跌榜失败: {e}") from e


@router.get("/daily/boards")
def daily_boards():
    """日报热门板块。"""
    try:
        return _ok({"boards": daily_an.hot_boards()})
    except Exception as e:
        raise HTTPException(500, f"日报热门板块失败: {e}") from e


@router.get("/daily/report")
def daily_report():
    """小白涨跌日报。"""
    try:
        return _ok(daily_an.daily_report())
    except Exception as e:
        raise HTTPException(500, f"日报生成失败: {e}") from e


@router.post("/daily/archive")
def daily_archive():
    """在其他模块完成后异步式归档当天日报，不阻塞首屏。"""
    try:
        report = daily_an.archive_report()
        return _ok({"saved": True, "date": report.get("date")})
    except Exception as e:
        raise HTTPException(500, f"日报归档失败: {e}") from e


@router.get("/daily/history")
def daily_history(days: int = Query(30, ge=7, le=30)):
    return _ok({"items": daily_an.report_history(days)})


@router.get("/daily/history/{report_date}")
def daily_history_detail(report_date: str):
    report = daily_an.historical_report(report_date)
    if report is None:
        raise HTTPException(404, "未找到该日期的本地日报")
    return _ok(report)


# ---------------------------------------------------------------- 辅助

@router.post("/cache/clear")
def cache_clear():
    """清空数据缓存。"""
    n = cache.clear()
    return _ok({"cleared": n, "memory_cleared": fetcher.mem_clear()})
