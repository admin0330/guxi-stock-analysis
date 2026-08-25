"""Binance Futures Testnet 真实订单验收；必须显式开启且账户需无 BTC/ETH 仓位与挂单。"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from decimal import Decimal, ROUND_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from backend.main import app
from trading.core.exchange import BinanceExchange
from trading.settings import load_settings

GATE = "RUN_LIVE_TESTNET"


async def market_inputs(settings) -> tuple[float, float]:
    exchange = BinanceExchange(settings)
    ticker = (await exchange.public_tickers("BTCUSDT"))[0]
    info = await exchange.instrument("BTCUSDT")
    price = float(ticker["lastPrice"])
    lot = info["lotSizeFilter"]
    step = Decimal(str(lot["qtyStep"])); minimum = Decimal(str(lot["minOrderQty"]))
    min_notional = Decimal(str(lot.get("minNotionalValue") or "5"))
    qty = max(minimum, (min_notional / Decimal(str(price)) / step).to_integral_value(rounding=ROUND_UP) * step)
    return price, float(qty)


def wait_for(client: TestClient, predicate, timeout: float = 20) -> dict:
    deadline, last = time.monotonic() + timeout, {}
    while time.monotonic() < deadline:
        response = client.get("/api/trading/account"); response.raise_for_status(); last = response.json()
        if predicate(last):
            return last
        time.sleep(.5)
    raise TimeoutError(f"等待交易所状态更新超时；最后快照：{last}")


def main() -> int:
    settings = load_settings()
    if os.getenv("BINANCE_TESTNET_ACCEPTANCE") != GATE:
        print(f"未执行：请设置 BINANCE_TESTNET_ACCEPTANCE={GATE}"); return 2
    if not settings.testnet:
        print("拒绝执行：验收脚本只允许 Binance Futures Testnet"); return 2
    if not settings.credentials_configured:
        print("未执行：.env 尚未配置 Binance Futures Testnet Key"); return 2
    price, qty = asyncio.run(market_inputs(settings))
    with TestClient(app) as client:
        token = client.get("/api/trading/bootstrap").json()["write_token"]
        headers = {"X-Trade-Token": token}
        account = client.get("/api/trading/account").json()
        open_orders = client.get("/api/trading/orders?open_only=true").json()["items"]
        if any(float(row.get("size") or 0) > 0 for row in account.get("positions", [])) or open_orders:
            print("拒绝执行：请先清空 BTC/ETH 仓位与挂单"); return 2
        if float(account.get("available_balance") or 0) <= 0:
            print("未执行：Binance Testnet 没有可用 USDT，请先领取测试资金"); return 2
        try:
            response = client.post("/api/trading/orders", headers=headers, json={"symbol": "BTCUSDT", "side": "Buy", "order_type": "Limit", "qty": qty, "price": round(price * .70, 2), "take_profit": round(price * 1.10, 2), "stop_loss": round(price * .60, 2)})
            response.raise_for_status(); order = response.json()
            client.post("/api/trading/orders/amend", headers=headers, json={"symbol": "BTCUSDT", "order_id": order.get("orderId", ""), "order_link_id": order.get("orderLinkId", ""), "price": round(price * .69, 2)}).raise_for_status()
            client.post("/api/trading/orders/cancel", headers=headers, json={"symbol": "BTCUSDT", "order_id": order.get("orderId", ""), "order_link_id": order.get("orderLinkId", "")}).raise_for_status()
            client.post("/api/trading/orders", headers=headers, json={"symbol": "BTCUSDT", "side": "Buy", "order_type": "Market", "qty": qty, "take_profit": round(price * 1.04, 2), "stop_loss": round(price * .98, 2)}).raise_for_status()
            wait_for(client, lambda row: any(float(p.get("size") or 0) > 0 for p in row.get("positions", [])))
            client.post("/api/trading/positions/close", headers=headers, json={"symbol": "BTCUSDT"}).raise_for_status()
            wait_for(client, lambda row: not any(float(p.get("size") or 0) > 0 for p in row.get("positions", [])))
            print("PASS：Binance Testnet 限价、改单、撤单、市价开仓与平仓均确认"); return 0
        finally:
            client.post("/api/trading/emergency", headers=headers, json={"close_positions": True})


if __name__ == "__main__":
    sys.exit(main())
