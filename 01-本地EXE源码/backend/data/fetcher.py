"""A 股数据层：直接 HTTP API 优先，并发、短超时、Parquet TTL 与过期回退。

高频路径不经过 AKShare：腾讯日线、新浪全市场快照、东方财富资金流/涨停池/
北向资金。AKShare 仅保留在低频财务数据路径，且结果使用 24 小时缓存。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import wraps
from typing import Callable, TypeVar

import akshare as ak
import pandas as pd

from backend import config
from backend.data import cache
from backend.data.http import get_json

T = TypeVar("T")

# 全局线程池：并行拉取独立数据源
_pool = ThreadPoolExecutor(max_workers=16)
# akshare 全局锁：py_mini_racer（V8）在多线程并发初始化会崩溃，
# 所有直接调用 akshare 的入口必须持锁串行执行。
_ak_lock = threading.RLock()
# stock-open-api 内部没有默认超时；调用期间临时注入超时，因此必须串行保护补丁。
_stock_open_api_lock = threading.Lock()
# 进程内结果缓存：{key: (timestamp, value)}，避免同一秒内重复计算
_mem_cache: dict[str, tuple[float, object]] = {}
_mem_lock = threading.Lock()
_key_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
MEM_TTL = 30.0  # 进程内聚合结果缓存 30 秒（比磁盘缓存更细粒度）


def mem_get(key: str) -> object | None:
    with _mem_lock:
        hit = _mem_cache.get(key)
        if hit and time.time() - hit[0] < MEM_TTL:
            return hit[1]
        return None


def mem_set(key: str, value: object) -> None:
    with _mem_lock:
        _mem_cache[key] = (time.time(), value)


def mem_clear() -> int:
    with _mem_lock:
        count = len(_mem_cache)
        _mem_cache.clear()
        return count


def parallel_map(fn: Callable, items: list) -> list:
    """并行执行 fn(item)，保持顺序。"""
    if len(items) <= 1:
        return [fn(i) for i in items]
    return list(_pool.map(fn, items))


def _cached_frame(key: str, ttl: int, loader: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    """缓存优先并合并同 key 的并发请求；失败时返回过期缓存。"""
    cached = cache.get(key, ttl, allow_stale=True)
    if cached is not None and not getattr(cached, "is_stale", False):
        return cached
    with _key_locks[key]:
        fresh = cache.get(key, ttl, allow_stale=True)
        if fresh is not None and not getattr(fresh, "is_stale", False):
            return fresh
        try:
            frame = loader()
            cache.set(key, frame)
            return frame
        except Exception:
            fallback = fresh if fresh is not None else cached
            if fallback is not None:
                frame = fallback.df if getattr(fallback, "is_stale", False) else fallback
                if getattr(fallback, "is_stale", False):
                    frame.attrs["stale"] = True
                return frame
            raise

# 常见中文字段名冲突修复：东财接口返回的列有时混有字节前缀
def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df.columns = [str(c).replace("\u0000", "").strip() for c in df.columns]
    return df


def with_retry(fn: Callable[..., T]) -> Callable[..., T]:
    """简单重试装饰器：处理瞬时网络错误。持全局锁，避免 akshare V8 并发崩溃。"""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        last_err: Exception | None = None
        with _ak_lock:  # 串行化所有 akshare 调用
            for attempt in range(config.MAX_RETRIES + 1):
                try:
                    df = fn(*args, **kwargs)
                    return _clean_columns(df)
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if attempt < config.MAX_RETRIES:
                        time.sleep(config.RETRY_BACKOFF * (attempt + 1))
        raise last_err  # type: ignore[misc]

    return wrapper


# ---------------------------------------------------------------- 指数与大盘

def index_daily(symbol: str = "sh000001") -> pd.DataFrame:
    """指数日线：腾讯 JSON API，AKShare 不参与高频路径。"""
    return _cached_frame(
        f"index_daily_api_{symbol}", config.CACHE_TTL_SHORT,
        lambda: _tencent_daily(symbol, "", 320),
    )


def _tencent_daily(symbol: str, adjust: str, limit: int) -> pd.DataFrame:
    mode = "qfq" if adjust == "qfq" else ""
    payload = get_json(
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        {"param": f"{symbol},day,,,{limit},{mode}"},
    )
    block = payload.get("data", {}).get(symbol, {})
    rows = block.get("qfqday") if adjust == "qfq" else block.get("day")
    rows = rows or block.get("day") or []
    if not rows:
        raise RuntimeError(f"{symbol} 日线 API 暂无数据")
    frame = pd.DataFrame([row[:6] for row in rows], columns=["date", "open", "close", "high", "low", "volume"])
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    for column in ("open", "close", "high", "low", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["date", "close"]).reset_index(drop=True)


def _sina_market_spot() -> pd.DataFrame:
    base = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    count = int(get_json(base + "Market_Center.getHQNodeStockCount", {"node": "hs_a"}, retries=1))
    pages = list(range(1, (count + 99) // 100 + 1))

    def page(number: int) -> list[dict]:
        try:
            return get_json(base + "Market_Center.getHQNodeData", {
                "page": number, "num": 100, "sort": "symbol", "asc": 1,
                "node": "hs_a", "symbol": "", "_s_r_a": "page",
            }, retries=1)
        except Exception:
            return []

    rows = [row for batch in parallel_map(page, pages) for row in batch]
    if len(rows) < 1000:
        raise RuntimeError("新浪全市场 API 返回数据不完整")
    frame = pd.DataFrame(rows).rename(columns={
        "code": "代码", "name": "名称", "trade": "最新价", "pricechange": "涨跌额",
        "changepercent": "涨跌幅", "settlement": "昨收", "open": "今开", "high": "最高",
        "low": "最低", "buy": "买入", "sell": "卖出", "volume": "成交量",
        "amount": "成交额", "ticktime": "时间戳",
    })
    for column in ("最新价", "涨跌额", "涨跌幅", "昨收", "今开", "最高", "最低", "成交量", "成交额"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame[[column for column in config.SPOT_COLUMNS if column in frame.columns]]


def market_spot() -> pd.DataFrame:
    """全市场快照：新浪 JSON API 约 56 页并发拉取，30 秒缓存。"""
    return _cached_frame("market_spot_api", config.CACHE_TTL_SHORT, _sina_market_spot)


def index_realtime() -> pd.DataFrame:
    """主要指数实时快照：用日线最后一行近似（收盘后准确；盘中为最近收盘）。

    并行拉取 + 结果缓存，避免每次全量下载历史。
    """
    cached = mem_get("index_realtime")
    if cached is not None:
        return cached

    def _one(item):
        sym, name = item
        try:
            df = index_daily(sym)
            if df.empty:
                return None
            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else last
            chg = (last["close"] - prev["close"]) / prev["close"] * 100 if prev["close"] else 0.0
            return {
                "code": sym,
                "name": name,
                "close": round(float(last["close"]), 2),
                "change_pct": round(float(chg), 2),
                "date": str(last["date"])[:10],
                "volume": float(last["volume"]),
                "stale": bool(df.attrs.get("stale", False)),
            }
        except Exception:
            return None

    rows = parallel_map(_one, list(config.INDEX_SYMBOLS.items()))
    rows = [r for r in rows if r is not None]
    df = pd.DataFrame(rows)
    mem_set("index_realtime", df)
    return df


# ---------------------------------------------------------------- 个股

def stock_daily(symbol: str, adjust: str = "qfq") -> pd.DataFrame:
    """个股日线：腾讯 JSON API；技术面与 K 线并发时只访问上游一次。"""
    return _cached_frame(
        f"stock_daily_api_{symbol}_{adjust}", config.CACHE_TTL_SHORT,
        lambda: _tencent_daily(symbol, adjust, 320),
    )


def stock_financial_abstract(symbol: str) -> pd.DataFrame:
    """个股财务摘要（新浪）。symbol 形如 '600519'（不带前缀）。"""
    code = symbol[-6:] if symbol.lower().startswith(("sh", "sz", "bj")) else symbol
    cached = cache.get(f"fin_abstract_{code}", config.CACHE_TTL_STATIC)
    if cached is not None:
        return cached
    df = with_retry(ak.stock_financial_abstract)(symbol=code)
    cache.set(f"fin_abstract_{code}", df)
    return df


def stock_financial_indicator(symbol: str) -> pd.DataFrame:
    """个股财务指标（新浪）。"""
    code = symbol[-6:] if symbol.lower().startswith(("sh", "sz", "bj")) else symbol
    cached = cache.get(f"fin_indicator_{code}", config.CACHE_TTL_STATIC)
    if cached is not None:
        return cached
    df = with_retry(ak.stock_financial_analysis_indicator)(symbol=code)
    cache.set(f"fin_indicator_{code}", df)
    return df


_COMPANY_PROFILE_FIELDS = {
    "公司名称": "company_name",
    "英文名称": "english_name",
    "A股简称": "short_name",
    "证券类别": "security_category",
    "上市交易所": "exchange",
    "所属东财行业": "eastmoney_industry",
    "所属证监会行业": "csrc_industry",
    "总经理": "general_manager",
    "法人代表": "legal_representative",
    "董秘": "board_secretary",
    "董事长": "chairman",
    "联系电话": "phone",
    "电子信箱": "email",
    "公司网址": "website",
    "办公地址": "office_address",
    "注册地址": "registered_address",
    "区域": "region",
    "注册资本(元)": "registered_capital",
    "雇员人数": "employees",
    "公司介绍": "company_intro",
    "经营范围": "business_scope",
    "成立日期": "founded_date",
    "上市日期": "listing_date",
    "发行市盈率(倍)": "issue_pe",
    "每股发行价(元)": "issue_price",
}


def _plain_scalar(value):
    """把 pandas/numpy 标量转成可 JSON 序列化的 Python 值。"""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        value = value.item()
    return value


def _profile_from_cache(cached) -> dict:
    if cached is None:
        return {}
    df = cached.df if getattr(cached, "is_stale", False) else cached
    if df is None or df.empty:
        return {}
    return {key: _plain_scalar(value) for key, value in df.iloc[0].to_dict().items()}


def stock_open_api_company_profile(symbol: str) -> dict:
    """通过 stock-open-api 获取公司资料；失败时回退过期缓存，不影响主分析。"""
    if not config.STOCK_OPEN_API_ENABLED:
        return {}

    sym = normalize_symbol(symbol)
    key = f"stock_open_api_company_{sym}"
    cached = cache.get(key, config.CACHE_TTL_STATIC, allow_stale=True)
    if cached is not None and not getattr(cached, "is_stale", False):
        return _profile_from_cache(cached)

    try:
        from stock_open_api.api.eastmoney import company
        from stock_open_api.utils import request_util
    except ImportError:
        return _profile_from_cache(cached)

    api_code = sym.upper()
    for attempt in range(config.MAX_RETRIES + 1):
        try:
            with _stock_open_api_lock:
                original_get = request_util.get

                def bounded_get(url, params=None, **kwargs):
                    kwargs.setdefault("timeout", config.REQUEST_TIMEOUT)
                    return original_get(url, params=params, **kwargs)

                request_util.get = bounded_get
                try:
                    raw = company.get_company_info(api_code)
                finally:
                    request_util.get = original_get

            profile = {
                "symbol": sym,
                "source": "stock-open-api/eastmoney",
                **{
                    target: _plain_scalar(raw.get(source))
                    for source, target in _COMPANY_PROFILE_FIELDS.items()
                },
            }
            cache.set(key, pd.DataFrame([profile]))
            return profile
        except Exception:  # 第三方源失败必须静默降级
            if attempt < config.MAX_RETRIES:
                time.sleep(config.RETRY_BACKOFF * (attempt + 1))

    return _profile_from_cache(cached)


def stock_fund_flow(symbol: str) -> dict:
    """单股资金流：东方财富 JSON API；失败在 3 秒内回退缓存。"""
    sym = normalize_symbol(symbol)
    key = f"stock_fund_api_{sym}"

    def load() -> pd.DataFrame:
        market = 1 if sym.startswith("sh") else 0
        payload = get_json(
            "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
            {
                "lmt": 1, "klt": 101, "secid": f"{market}.{sym[-6:]}",
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                "ut": "b2884a393a59ad64002292a3e90d46a5",
            },
            timeout=3,
            retries=0,
        )
        data = payload.get("data") or {}
        rows = data.get("klines") or []
        if not rows:
            raise RuntimeError("东方财富单股资金流暂无数据")
        values = rows[-1].split(",")
        net = float(values[1])
        return pd.DataFrame([{
            "name": data.get("name") or "",
            "net": net,
            "inflow": max(net, 0.0),
            "outflow": max(-net, 0.0),
            "turnover": None,
            "price": float(values[11]) if len(values) > 11 else None,
            "source": "eastmoney-api",
        }])

    try:
        frame = _cached_frame(key, config.CACHE_TTL_SHORT, load)
        return frame.iloc[0].to_dict() if frame is not None and not frame.empty else {}
    except Exception:
        return {}


# ---------------------------------------------------------------- 涨停池

_POOL_ENDPOINTS = {
    "zt": ("getTopicZTPool", "fbt:asc"),
    "strong": ("getTopicQSPool", "zdp:desc"),
    "subnew": ("getTopicCXPooll", "ods:asc"),
    "dt": ("getTopicDTPool", "fund:asc"),
    "zb": ("getTopicZBPool", "fbt:asc"),
    "previous": ("getYesterdayZTPool", "zs:desc"),
}


def _pool_frame(kind: str, trade_date: str) -> pd.DataFrame:
    endpoint, order = _POOL_ENDPOINTS[kind]
    payload = get_json(f"https://push2ex.eastmoney.com/{endpoint}", {
        "ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
        "Pageindex": 0, "pagesize": 10000, "sort": order, "date": trade_date,
    })
    rows = (payload.get("data") or {}).get("pool") or []
    normalized = []
    for index, row in enumerate(rows, 1):
        stats = row.get("zttj") or {}
        normalized.append({
            "序号": index,
            "代码": str(row.get("c") or "").zfill(6),
            "名称": row.get("n") or "",
            "涨跌幅": row.get("zdp"),
            "最新价": (row.get("p") or 0) / 1000,
            "涨停价": (row.get("ztp") or 0) / 1000 if row.get("ztp") is not None else None,
            "成交额": row.get("amount"),
            "流通市值": row.get("ltsz"),
            "总市值": row.get("tshare"),
            "换手率": row.get("hs"),
            "封板资金": row.get("fund"),
            "首次封板时间": str(row.get("fbt") or "").zfill(6),
            "最后封板时间": str(row.get("lbt") or "").zfill(6),
            "炸板次数": row.get("zbc") or 0,
            "涨停统计": f"{stats.get('days', 0)}/{stats.get('ct', 0)}",
            "连板数": row.get("lbc") or stats.get("ct") or 1,
            "所属行业": row.get("hybk") or "",
            "振幅": row.get("zf"),
        })
    return pd.DataFrame(normalized)


def _limit_pool(kind: str, trade_date: str) -> pd.DataFrame:
    ttl = config.CACHE_TTL_SHORT if trade_date == date.today().strftime("%Y%m%d") else config.CACHE_TTL_DAILY
    return _cached_frame(f"{kind}_pool_api_{trade_date}", ttl, lambda: _pool_frame(kind, trade_date))


def zt_pool(date: str) -> pd.DataFrame:
    return _limit_pool("zt", date)


def zt_pool_strong(date: str) -> pd.DataFrame:
    return _limit_pool("strong", date)


def zt_pool_sub_new(date: str) -> pd.DataFrame:
    return _limit_pool("subnew", date)


def dt_pool(date: str) -> pd.DataFrame:
    return _limit_pool("dt", date)


def zb_pool(date: str) -> pd.DataFrame:
    return _limit_pool("zb", date)


def zt_pool_previous(date: str) -> pd.DataFrame:
    return _limit_pool("previous", date)


# ---------------------------------------------------------------- 板块

def board_industry_summary() -> pd.DataFrame:
    """行业板块行情：东方财富 JSON API，失败只回退缓存。"""
    def load() -> pd.DataFrame:
        payload = get_json("https://push2.eastmoney.com/api/qt/clist/get", {
            "pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f3", "fs": "m:90+t:2+f:!50", "fields": "f12,f14,f2,f3,f6",
        }, retries=0)
        rows = (payload.get("data") or {}).get("diff") or []
        return pd.DataFrame([{
            "板块": row.get("f14") or "", "涨跌幅": row.get("f3"),
            "最新价": row.get("f2"), "总成交额": row.get("f6"),
        } for row in rows])

    return _cached_frame("board_industry_api", config.CACHE_TTL_SHORT, load)


# ---------------------------------------------------------------- 资金与情绪

def hsgt_summary() -> pd.DataFrame:
    """北向资金汇总：东方财富数据中心 JSON API。"""
    def load() -> pd.DataFrame:
        payload = get_json("https://datacenter-web.eastmoney.com/api/data/v1/get", {
            "reportName": "RPT_MUTUAL_QUOTA",
            "columns": "TRADE_DATE,MUTUAL_TYPE,BOARD_TYPE,MUTUAL_TYPE_NAME,FUNDS_DIRECTION,INDEX_CODE,INDEX_NAME,BOARD_CODE",
            "quoteColumns": "status~07~BOARD_CODE,dayNetAmtIn~07~BOARD_CODE,dayAmtRemain~07~BOARD_CODE,dayAmtThreshold~07~BOARD_CODE,f104~07~BOARD_CODE,f105~07~BOARD_CODE,f106~07~BOARD_CODE,f3~03~INDEX_CODE~INDEX_f3,netBuyAmt~07~BOARD_CODE",
            "quoteType": 0, "pageNumber": 1, "pageSize": 2000,
            "sortTypes": 1, "sortColumns": "MUTUAL_TYPE", "source": "WEB", "client": "WEB",
        })
        rows = (payload.get("result") or {}).get("data") or []
        return pd.DataFrame([{
            "交易日": str(row.get("TRADE_DATE") or "")[:10],
            "类型": row.get("FUNDS_DIRECTION") or "",
            "板块": row.get("BOARD_TYPE") or "",
            "资金方向": row.get("FUNDS_DIRECTION") or "",
            "交易状态": row.get("status"),
            "成交净买额": float(row.get("netBuyAmt") or 0) / 10000,
            "资金净流入": float(row.get("dayNetAmtIn") or 0) / 10000,
            "当日资金余额": float(row.get("dayAmtRemain") or 0) / 10000,
            "上涨数": row.get("f104"), "持平数": row.get("f106"), "下跌数": row.get("f105"),
            "相关指数": row.get("INDEX_NAME") or "", "指数涨跌幅": row.get("INDEX_f3"),
        } for row in rows])

    return _cached_frame("hsgt_summary_api", config.CACHE_TTL_SHORT, load)


def stock_name_to_symbol(name: str) -> str | None:
    """根据名称反查代码（用于搜索）；返回带前缀 symbol。"""
    try:
        spot = market_spot()
        hit = spot[spot["名称"] == name]
        if hit.empty:
            return None
        code = str(hit.iloc[0]["代码"])
        return normalize_symbol(code)
    except Exception:
        return None


def normalize_symbol(code: str) -> str:
    """把 '600519' 或 'sh600519' 归一化为 'sh600519' 形式。"""
    code = code.strip().lower()
    if code.startswith(("sh", "sz", "bj")):
        return code
    if code.startswith(("6", "9", "5")):
        return f"sh{code}"
    if code.startswith(("0", "3", "2")):
        return f"sz{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sh{code}"
