"""Binance 只读查询 API。

这里故意只暴露 GET 与行情 WebSocket；订单、撤单、改单、平仓、自动交易和
紧急停止均不属于本系统能力。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from backend.auth import websocket_allowed
from trading.core.exchange import CredentialsMissing, ExchangeError
from trading.service import trading_service

router = APIRouter(prefix="/api/trading", tags=["Binance 只读查询"])


def trading_error(exc: Exception) -> HTTPException:
    trading_service.engine.store.event("api_error", str(exc), level="ERROR")
    if isinstance(exc, CredentialsMissing):
        return HTTPException(428, str(exc))
    if isinstance(exc, (PermissionError, ValueError)):
        return HTTPException(400, str(exc))
    if isinstance(exc, ExchangeError):
        return HTTPException(502, str(exc))
    return HTTPException(500, f"交易服务暂不可用：{exc}")


@router.get("/bootstrap")
def bootstrap():
    return {
        "read_only": True,
        "capabilities": ["market_data", "account", "positions", "open_orders", "order_history", "executions"],
        "settings": trading_service.settings.public_dict(),
    }


@router.get("/status")
def status():
    return trading_service.engine.status()


@router.get("/account")
async def account():
    try:
        return await trading_service.engine.refresh_account()
    except Exception as exc:
        raise trading_error(exc) from exc


@router.get("/positions")
def positions():
    return {"items": trading_service.engine.positions.active()}


@router.get("/orders")
def orders(limit: int = 100, open_only: bool = False):
    rows = trading_service.engine.store.rows("orders", limit)
    if open_only:
        active = {"New", "Created", "Untriggered", "PartiallyFilled", "Submitted", "PendingSubmit", "SubmitUnknown"}
        rows = [row for row in rows if row.get("status") in active]
    return {"items": rows, "read_only": True}


@router.get("/trades")
def trades(limit: int = 100, today: bool = False):
    rows = trading_service.engine.store.rows("trades", limit)
    if today:
        prefix = datetime.now(timezone.utc).date().isoformat()
        rows = [row for row in rows if str(row.get("executed_at") or "").startswith(prefix)]
    return {"items": rows}


@router.get("/logs")
def logs(limit: int = 100):
    return {"items": trading_service.engine.store.rows("events", limit)}


@router.get("/settings")
def settings():
    return trading_service.settings.public_dict()


@router.websocket("/ws")
async def websocket_stream(websocket: WebSocket):
    if not websocket_allowed(websocket):
        await websocket.close(code=4401, reason="请先登录")
        return
    await websocket.accept()
    queue = trading_service.engine.market.subscribe()
    try:
        while True:
            await websocket.send_json(await queue.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        trading_service.engine.market.unsubscribe(queue)
