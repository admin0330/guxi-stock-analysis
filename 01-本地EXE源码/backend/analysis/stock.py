"""个股深度分析：技术面 + 基本面 + 资金面综合评分与报告。"""
from __future__ import annotations

import math

import pandas as pd

from backend.data import fetcher


def _f(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _pct(v) -> float | None:
    """把 '12.34%' / '12.34' / NaN 统一成 float（% 值），无法解析返回 None。"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        s = str(v).strip().replace("%", "").replace(",", "")
        if s in ("", "--", "None", "nan"):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ 技术面

def _rsi(close: pd.Series, period: int = 14) -> float | None:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    rs = pd.to_numeric(rs, errors="coerce")  # 确保数值类型
    rsi = 100 - 100 / (1 + rs)
    v = rsi.iloc[-1]
    return round(float(v), 2) if not pd.isna(v) else None


def _macd(close: pd.Series):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea) * 2
    return (
        round(float(dif.iloc[-1]), 4),
        round(float(dea.iloc[-1]), 4),
        round(float(hist.iloc[-1]), 4),
    )


def _kdj(df: pd.DataFrame, n: int = 9):
    low = pd.to_numeric(df["low"], errors="coerce").rolling(n).min()
    high = pd.to_numeric(df["high"], errors="coerce").rolling(n).max()
    denom = (high - low).replace(0, pd.NA)
    rsv = (pd.to_numeric(df["close"], errors="coerce") - low) / denom * 100
    rsv = pd.to_numeric(rsv, errors="coerce")  # 确保数值类型（pandas3 下 replace 可能变 object）
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d
    return (
        round(float(k.iloc[-1]), 2) if not pd.isna(k.iloc[-1]) else None,
        round(float(d.iloc[-1]), 2) if not pd.isna(d.iloc[-1]) else None,
        round(float(j.iloc[-1]), 2) if not pd.isna(j.iloc[-1]) else None,
    )


def technical_analysis(symbol: str) -> dict:
    """技术面分析：趋势、均线、动量指标、量能。"""
    df = fetcher.stock_daily(symbol)
    if df is None or df.empty:
        return {"error": "无日线数据"}

    close = pd.to_numeric(df["close"], errors="coerce")
    vol = pd.to_numeric(df["volume"], errors="coerce")
    price = _f(close.iloc[-1])

    ma = {}
    for n in (5, 10, 20, 60):
        m = close.rolling(n).mean().iloc[-1]
        ma[f"ma{n}"] = round(_f(m), 2) if not pd.isna(m) else None

    # 趋势判定：均线多头排列
    ma5, ma10, ma20 = ma["ma5"], ma["ma10"], ma["ma20"]
    bullish = bool(
        ma5 is not None and ma10 is not None and ma20 is not None
        and ma5 > ma10 > ma20 and price > ma5
    )
    bearish = bool(
        ma5 is not None and ma10 is not None and ma20 is not None
        and ma5 < ma10 < ma20 and price < ma5
    )

    dif, dea, hist = _macd(close)
    k, d, j = _kdj(df)
    rsi14 = _rsi(close)

    # 量比：今日量 vs 前5日均量
    vol_ratio = None
    if len(vol) > 6 and _f(vol.iloc[-6:].mean()) > 0:
        vol_ratio = round(_f(vol.iloc[-1]) / _f(vol.iloc[-6:-1].mean()), 2)

    # 20日涨跌幅
    chg20 = None
    if len(close) > 21 and _f(close.iloc[-21]) != 0:
        chg20 = round((price / _f(close.iloc[-21]) - 1) * 100, 2)

    # 年内高低点位置
    year_high = close.tail(250).max()
    year_low = close.tail(250).min()
    pos_in_year = None
    if not pd.isna(year_high) and year_high != year_low:
        pos_in_year = round((price - _f(year_low)) / (_f(year_high) - _f(year_low)) * 100, 1)

    return {
        "price": price,
        "ma": ma,
        "trend": "多头" if bullish else ("空头" if bearish else "震荡"),
        "macd": {"dif": dif, "dea": dea, "hist": hist, "golden_cross": bool(dif > dea)},
        "kdj": {"k": k, "d": d, "j": j},
        "rsi14": rsi14,
        "vol_ratio": vol_ratio,
        "chg20": chg20,
        "pos_in_year": pos_in_year,
        "stale": bool(df.attrs.get("stale", False)),
    }


# ------------------------------------------------------------------ 基本面

def fundamental_analysis(symbol: str) -> dict:
    """基本面分析：估值、盈利能力、成长性、财务健康。"""
    code = symbol[-6:] if symbol.lower().startswith(("sh", "sz", "bj")) else symbol
    out: dict = {}

    # 公司资料（stock-open-api / 东方财富）：静态缓存，失败不影响财务分析。
    profile = fetcher.stock_open_api_company_profile(symbol)
    if profile:
        out["company_profile"] = profile

    # 财务指标（新浪）
    try:
        ind = fetcher.stock_financial_indicator(code)
        if ind is not None and not ind.empty:
            row = ind.iloc[0]
            out.update({
                "eps": _pct(row.get("摊薄每股收益(元)")),
                "roe": _pct(row.get("净资产收益率(%)")),
                "gross_margin": _pct(row.get("主营业务利润率(%)")),
                "revenue_yoy": _pct(row.get("主营业务收入增长率(%)")),
                "profit_yoy": _pct(row.get("净利润增长率(%)")),
                "debt_ratio": _pct(row.get("资产负债率(%)")),
                "bps": _pct(row.get("每股净资产(元)")),
            })
    except Exception:
        pass

    # 财务摘要（新浪，多期对比）
    try:
        ab = fetcher.stock_financial_abstract(code)
        if ab is not None and not ab.empty:
            periods = [c for c in ab.columns if str(c).startswith("20")]
            if periods:
                latest = periods[0]
                prev = periods[1] if len(periods) > 1 else latest
                out["report_period"] = latest
                out["report_prev"] = prev
                out["abstract_latest"] = ab[["指标", latest]].to_dict(orient="records")[:25]
    except Exception:
        pass

    # 估值：复用单股日线最新价近似 PB，避免为一只股票拉取全市场快照。
    try:
        daily = fetcher.stock_daily(fetcher.normalize_symbol(symbol))
        if daily is not None and not daily.empty:
            price = _f(daily.iloc[-1].get("close"))
            out["spot"] = {"最新价": price}
            bps = out.get("bps")
            if bps and price:
                out["pb_approx"] = round(price / bps, 2)
    except Exception:
        pass

    return out


# ------------------------------------------------------------------ 资金面

def fund_analysis(symbol: str) -> dict:
    """资金面分析：东方财富单股资金流 API，不再下载全市场排行。"""
    try:
        row = fetcher.stock_fund_flow(symbol)
        if not row:
            return {"note": "资金流接口暂不可用，已跳过该维度"}
        return {
            "name": str(row.get("name", "")),
            "inflow": _f(row.get("inflow")),
            "outflow": _f(row.get("outflow")),
            "net": round(_f(row.get("net")), 2),
            "turnover": _pct(row.get("turnover")),
            "price": _f(row.get("price")),
            "source": row.get("source", "eastmoney-api"),
        }
    except Exception as e:
        return {"error": f"资金流获取失败: {e}"}


# ------------------------------------------------------------------ 综合评分

def _score_technical(t: dict) -> tuple[int, list[str], list[str]]:
    """技术面评分 0-40。"""
    score = 20
    pos, neg = [], []
    if t.get("trend") == "多头":
        score += 10
        pos.append("均线多头排列，趋势向上")
    elif t.get("trend") == "空头":
        score -= 10
        neg.append("均线空头排列，趋势向下")

    macd = t.get("macd", {})
    if macd.get("golden_cross"):
        score += 4
        pos.append("MACD 金叉状态")
    else:
        score -= 4
        neg.append("MACD 死叉状态")

    rsi = t.get("rsi14")
    if rsi is not None:
        if rsi > 70:
            score -= 3
            neg.append(f"RSI={rsi} 超买")
        elif rsi < 30:
            score += 2
            pos.append(f"RSI={rsi} 超卖，或有反弹")
        else:
            score += 2

    chg20 = t.get("chg20")
    if chg20 is not None:
        if chg20 > 15:
            score -= 2
            neg.append(f"20日已涨 {chg20:.1f}%，短期涨幅较大")
        elif chg20 < -10:
            score += 3
            pos.append(f"20日回撤 {chg20:.1f}%，位置偏低")

    return max(0, min(40, score)), pos, neg


def _score_fundamental(f: dict) -> tuple[int, list[str], list[str]]:
    """基本面评分 0-35。"""
    score = 18
    pos, neg = [], []

    roe = f.get("roe")
    if roe is not None:
        if roe >= 15:
            score += 6
            pos.append(f"ROE {roe:.1f}% 优秀")
        elif roe >= 8:
            score += 3
            pos.append(f"ROE {roe:.1f}% 良好")
        elif roe <= 0:
            score -= 6
            neg.append(f"ROE {roe:.1f}% 亏损状态")

    rev = f.get("revenue_yoy")
    prof = f.get("profit_yoy")
    if prof is not None:
        if prof >= 20:
            score += 5
            pos.append(f"净利润增速 {prof:.1f}% 高成长")
        elif prof < 0:
            score -= 5
            neg.append(f"净利润同比下滑 {prof:.1f}%")
    if rev is not None and rev < 0:
        score -= 2
        neg.append(f"营收同比下滑 {rev:.1f}%")

    debt = f.get("debt_ratio")
    if debt is not None:
        if debt > 70:
            score -= 3
            neg.append(f"资产负债率 {debt:.1f}% 偏高")
        elif debt < 40:
            score += 2
            pos.append(f"资产负债率 {debt:.1f}% 稳健")

    eps = f.get("eps")
    if eps is not None and eps < 0:
        score -= 4
        neg.append("每股收益为负")

    return max(0, min(35, score)), pos, neg


def _score_fund(fa: dict) -> tuple[int, list[str], list[str]]:
    """资金面评分 0-25。"""
    score = 12
    pos, neg = [], []
    if fa.get("error") or fa.get("note"):
        return 12, [], []
    net = fa.get("net", 0)
    if net > 0:
        score += 8
        pos.append(f"当日主力净流入 {net / 1e8:.2f} 亿元")
    elif net < 0:
        score -= 8
        neg.append(f"当日主力净流出 {abs(net) / 1e8:.2f} 亿元")
    to = fa.get("turnover")
    if to is not None:
        if to > 10:
            score += 3
            pos.append(f"换手率 {to:.1f}% 交投活跃")
        elif to < 1:
            score -= 2
            neg.append(f"换手率 {to:.1f}% 交投清淡")
    return max(0, min(25, score)), pos, neg


def technical_report(symbol: str) -> dict:
    """独立技术面报告，供前端按模块渐进渲染。"""
    sym = fetcher.normalize_symbol(symbol)
    data = technical_analysis(sym)
    score, positives, risks = _score_technical(data)
    return {
        "dimension": "technical", "symbol": sym, "code": sym[-6:],
        "score": score, "max_score": 40, "data": data,
        "positives": positives, "risks": risks,
        "stale": bool(data.get("stale", False)),
    }


def fundamental_report(symbol: str) -> dict:
    """独立基本面报告，供前端按模块渐进渲染。"""
    sym = fetcher.normalize_symbol(symbol)
    data = fundamental_analysis(sym)
    score, positives, risks = _score_fundamental(data)
    profile = data.get("company_profile", {})
    return {
        "dimension": "fundamental", "symbol": sym, "code": sym[-6:],
        "name": profile.get("short_name") or profile.get("company_name") or "",
        "score": score, "max_score": 35, "data": data,
        "positives": positives, "risks": risks,
    }


def fund_report(symbol: str) -> dict:
    """独立资金面报告，供前端按模块渐进渲染。"""
    sym = fetcher.normalize_symbol(symbol)
    data = fund_analysis(sym)
    score, positives, risks = _score_fund(data)
    return {
        "dimension": "fund", "symbol": sym, "code": sym[-6:],
        "name": data.get("name", ""),
        "score": score, "max_score": 25, "data": data,
        "positives": positives, "risks": risks,
    }


def analyze(symbol: str, name: str = "") -> dict:
    """个股综合分析入口：返回评分、维度明细、优缺点与一句话总结。

    三个维度（技术/基本面/资金面）数据源独立，并行拉取。
    """
    sym = fetcher.normalize_symbol(symbol)
    code = sym[-6:]

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_t = ex.submit(technical_report, sym)
        f_f = ex.submit(fundamental_report, sym)
        f_fa = ex.submit(fund_report, sym)
        tr, fr, far = f_t.result(), f_f.result(), f_fa.result()

    t, f, fa = tr["data"], fr["data"], far["data"]
    ts, fs, fas = tr["score"], fr["score"], far["score"]
    t_pos, t_neg = tr["positives"], tr["risks"]
    f_pos, f_neg = fr["positives"], fr["risks"]
    fa_pos, fa_neg = far["positives"], far["risks"]

    total = ts + fs + fas
    if total >= 75:
        label, confidence = "强势", "较高"
    elif total >= 60:
        label, confidence = "偏强", "中等"
    elif total >= 45:
        label, confidence = "中性", "中等"
    elif total >= 30:
        label, confidence = "偏弱", "中等"
    else:
        label, confidence = "弱势", "较高"

    positives = t_pos + f_pos + fa_pos
    risks = t_neg + f_neg + fa_neg

    # 名称兜底：优先已随基本面返回的公司资料，避免额外拉取全市场快照。
    if not name:
        profile = f.get("company_profile", {})
        name = profile.get("short_name") or profile.get("company_name") or ""
    # 其次使用资金流排行（含股票简称），不再拉取全市场快照。
    if not name:
        name = far.get("name", "")
    if not name:
        name = code

    summary = (
        f"{name}（{code}）综合评分 {total} 分（技术{ts}/基本面{fs}/资金{fas}），"
        f"当前判断为「{label}」。"
    )
    if positives:
        summary += " 亮点：" + "；".join(positives[:3]) + "。"
    if risks:
        summary += " 风险：" + "；".join(risks[:3]) + "。"

    return {
        "code": code,
        "symbol": sym,
        "name": name,
        "score": total,
        "label": label,
        "confidence": confidence,
        "breakdown": {"technical": ts, "fundamental": fs, "fund": fas},
        "technical": t,
        "fundamental": f,
        "fund": fa,
        "positives": positives,
        "risks": risks,
        "summary": summary,
    }
