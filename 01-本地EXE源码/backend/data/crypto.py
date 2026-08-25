"""BTC / ETH 公开行情数据源：Binance 主源、OKX 回退、CoinGecko 补充。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd

from backend import config
from backend.data import cache
from backend.data.http import get_json

ASSETS = {"BTC": "bitcoin", "ETH": "ethereum"}
INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
OKX_INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}


def _asset(value: str) -> str:
    asset = value.upper()
    if asset not in ASSETS:
        raise ValueError(f"暂不支持的加密资产：{value}")
    return asset


def _row(cached) -> dict:
    frame = cached.df if getattr(cached, "is_stale", False) else cached
    if frame is None or frame.empty:
        return {}
    result = frame.iloc[0].to_dict()
    result["stale"] = bool(getattr(cached, "is_stale", False))
    return result


def _binance_ticker(asset: str, quick: bool = False) -> dict:
    data = get_json(
        "https://data-api.binance.vision/api/v3/ticker/24hr",
        {"symbol": f"{asset}USDT"},
        timeout=1.5 if quick else None,
        retries=0 if quick else None,
    )
    return {
        "asset": asset,
        "price": float(data["lastPrice"]),
        "change_24h": float(data["priceChangePercent"]),
        "volume_24h": float(data["volume"]),
        "quote_volume_24h": float(data["quoteVolume"]),
        "high_24h": float(data["highPrice"]),
        "low_24h": float(data["lowPrice"]),
        "source": "Binance",
    }


def _okx_ticker(asset: str, quick: bool = False) -> dict:
    payload = get_json(
        "https://www.okx.com/api/v5/market/ticker",
        {"instId": f"{asset}-USDT"},
        timeout=1.5 if quick else None,
        retries=0 if quick else None,
    )
    data = payload.get("data", [])[0]
    last, opened = float(data["last"]), float(data["open24h"])
    return {
        "asset": asset,
        "price": last,
        "change_24h": (last / opened - 1) * 100 if opened else 0.0,
        "volume_24h": float(data.get("vol24h") or 0),
        "quote_volume_24h": float(data.get("volCcy24h") or 0),
        "high_24h": float(data.get("high24h") or 0),
        "low_24h": float(data.get("low24h") or 0),
        "source": "OKX",
    }


def ticker(asset: str, force: bool = False, quick: bool = False) -> dict:
    asset = _asset(asset)
    key = f"crypto_ticker_{asset.lower()}"
    cached = cache.get(key, config.CRYPTO_TTL_TICKER, allow_stale=True)
    if cached is not None and not force and not getattr(cached, "is_stale", False):
        return _row(cached)
    sources = [_binance_ticker, _okx_ticker]
    if config.CRYPTO_PRIMARY_SOURCE == "okx":
        sources.reverse()
    for source in sources:
        try:
            result = source(asset, quick)
            result["updated_at"] = datetime.now(timezone.utc).isoformat()
            result["stale"] = False
            cache.set(key, pd.DataFrame([result]))
            return result
        except Exception:
            continue
    if cached is not None:
        return _row(cached)
    raise RuntimeError(f"{asset} 行情源暂不可用")


def _coingecko(force: bool = False) -> dict[str, dict]:
    key = "crypto_coingecko_overview"
    cached = cache.get(key, config.CRYPTO_TTL_TICKER, allow_stale=True)
    if cached is not None and not force and not getattr(cached, "is_stale", False):
        frame = cached.df if getattr(cached, "is_stale", False) else cached
        return {str(row["asset"]): row for row in frame.to_dict(orient="records")}
    try:
        payload = get_json("https://api.coingecko.com/api/v3/simple/price", {
            "ids": ",".join(ASSETS.values()), "vs_currencies": "usd",
            "include_market_cap": "true", "include_24hr_vol": "true", "include_24hr_change": "true",
        })
        rows = []
        for asset, coin_id in ASSETS.items():
            item = payload.get(coin_id, {})
            rows.append({
                "asset": asset, "market_cap": item.get("usd_market_cap"),
                "cg_volume_24h": item.get("usd_24h_vol"), "cg_price": item.get("usd"),
                "cg_change_24h": item.get("usd_24h_change"),
            })
        frame = pd.DataFrame(rows)
        cache.set(key, frame)
        return {str(row["asset"]): row for row in rows}
    except Exception:
        if cached is None:
            return {}
        frame = cached.df if getattr(cached, "is_stale", False) else cached
        return {str(row["asset"]): row for row in frame.to_dict(orient="records")}


def overview(force: bool = False) -> dict:
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {asset: executor.submit(ticker, asset, force) for asset in ASSETS}
        cg_future = executor.submit(_coingecko, force)
        results = {asset: future.result() for asset, future in futures.items()}
        enrichment = cg_future.result()
    for asset, result in results.items():
        extra = enrichment.get(asset, {})
        result["market_cap"] = extra.get("market_cap")
        if not result.get("quote_volume_24h"):
            result["quote_volume_24h"] = extra.get("cg_volume_24h")
    return {"assets": list(results.values()), "refresh_seconds": config.CRYPTO_REFRESH_SECONDS}


def realtime_snapshot() -> list[dict]:
    """WebSocket 不可用时的轻量 BTC / ETH 快照，不请求 CoinGecko 或 K 线。"""
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(ticker, asset, True, True) for asset in ASSETS]
        rows = []
        for future in futures:
            try:
                rows.append(future.result())
            except Exception:
                continue
    if not rows:
        raise RuntimeError("BTC / ETH 轻量行情源均不可用")
    return rows


def _binance_kline(asset: str, interval: str, limit: int) -> pd.DataFrame:
    rows = get_json("https://data-api.binance.vision/api/v3/klines", {
        "symbol": f"{asset}USDT", "interval": interval, "limit": limit,
    })
    return pd.DataFrame([{
        "time": datetime.fromtimestamp(row[0] / 1000, timezone.utc).isoformat(),
        "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
        "close": float(row[4]), "volume": float(row[5]), "source": "Binance",
    } for row in rows])


def _okx_kline(asset: str, interval: str, limit: int) -> pd.DataFrame:
    payload = get_json("https://www.okx.com/api/v5/market/candles", {
        "instId": f"{asset}-USDT", "bar": OKX_INTERVALS[interval], "limit": limit,
    })
    rows = payload.get("data", [])
    return pd.DataFrame([{
        "time": datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc).isoformat(),
        "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
        "close": float(row[4]), "volume": float(row[5]), "source": "OKX",
    } for row in reversed(rows)])


def kline(asset: str, interval: str = "1h", limit: int = 240, force: bool = False) -> pd.DataFrame:
    asset = _asset(asset)
    if interval not in INTERVALS:
        raise ValueError(f"不支持的周期：{interval}")
    limit = max(60, min(500, int(limit)))
    key = f"crypto_kline_{asset.lower()}_{interval}_{limit}"
    cached = cache.get(key, config.CRYPTO_TTL_KLINE, allow_stale=True)
    if cached is not None and not force and not getattr(cached, "is_stale", False):
        return cached
    sources = [_binance_kline, _okx_kline]
    if config.CRYPTO_PRIMARY_SOURCE == "okx":
        sources.reverse()
    for source in sources:
        try:
            frame = source(asset, interval, limit)
            if not frame.empty:
                frame.attrs["stale"] = False
                cache.set(key, frame)
                return frame
        except Exception:
            continue
    if cached is not None:
        frame = cached.df if getattr(cached, "is_stale", False) else cached
        frame.attrs["stale"] = True
        return frame
    raise RuntimeError(f"{asset} K线数据源暂不可用")
