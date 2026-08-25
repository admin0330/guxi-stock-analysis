"""Binance 模拟交易台 API；所有写操作均要求本地会话令牌。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from backend.auth import websocket_allowed
from trading.core.exchange import CredentialsMissing, ExchangeError
from trading.service import trading_service

router = APIRouter(prefix="/api/trading", tags=["Binance 模拟交易台"])


class AutoTradeRequest(BaseModel):
    enabled: bool


class MainnetUnlockRequest(BaseModel):
    phrase: str


class ManualOrderRequest(BaseModel):
    symbol: str
    side: Literal["Buy", "Sell"]
    order_type: Literal["Market", "Limit"] = "Market"
    qty: float = Field(gt=0)
    price: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    mainnet_phrase: str = ""


class CancelRequest(BaseModel):
    symbol: str
    order_id: str = ""
    order_link_id: str = ""


class AmendRequest(CancelRequest):
    qty: float | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, gt=0)


class CloseRequest(BaseModel):
    symbol: str
    mainnet_phrase: str = ""


class EmergencyRequest(BaseModel):
    close_positions: bool = False


def authorize(token: str | None) -> None:
    try:
        trading_service.verify_token(token)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


def trading_error(exc: Exception) -> HTTPException:
    trading_service.engine.store.event("api_error", str(exc), level="ERROR")
    if isinstance(exc, CredentialsMissing):
        return HTTPException(428, str(exc))
    if isinstance(exc, (PermissionError, ValueError)):
        return HTTPException(400, str(exc))
    if isinstance(exc, ExchangeError):
        return HTTPException(502, str(exc))
    return HTTPException(500, f"交易服务暂不可用：{exc}")


def require_mainnet_phrase(value: str) -> None:
    if not trading_service.settings.testnet and value != "确认主网实盘下单":
        raise HTTPException(400, "请输入主网实盘确认短语")


@router.get("/bootstrap")
def bootstrap():
    return {"write_token": trading_service.write_token, "settings": trading_service.settings.public_dict()}


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
    return {"items": rows}


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


@router.get("/risk")
def risk():
    return trading_service.engine.risk.summary()


@router.get("/settings")
def settings():
    return trading_service.settings.public_dict()


@router.patch("/settings")
async def settings_update(changes: dict, x_trade_token: str | None = Header(default=None)):
    authorize(x_trade_token)
    try:
        return await trading_service.update_settings(changes)
    except Exception as exc:
        raise trading_error(exc) from exc


@router.post("/auto")
async def auto_trade(body: AutoTradeRequest, x_trade_token: str | None = Header(default=None)):
    authorize(x_trade_token)
    try:
        return await trading_service.engine.set_auto_trade(body.enabled)
    except Exception as exc:
        raise trading_error(exc) from exc


@router.post("/mainnet/unlock")
def unlock_mainnet(body: MainnetUnlockRequest, x_trade_token: str | None = Header(default=None)):
    authorize(x_trade_token)
    try:
        return trading_service.engine.unlock_mainnet(body.phrase)
    except Exception as exc:
        raise trading_error(exc) from exc


@router.post("/orders")
async def place_order(body: ManualOrderRequest, x_trade_token: str | None = Header(default=None)):
    authorize(x_trade_token)
    require_mainnet_phrase(body.mainnet_phrase)
    try:
        return await trading_service.engine.manual_order(
            symbol=body.symbol, side=body.side, order_type=body.order_type, qty=body.qty,
            price=body.price, take_profit=body.take_profit, stop_loss=body.stop_loss,
        )
    except Exception as exc:
        raise trading_error(exc) from exc


@router.post("/orders/cancel")
async def cancel_order(body: CancelRequest, x_trade_token: str | None = Header(default=None)):
    authorize(x_trade_token)
    try:
        return await trading_service.engine.cancel_order(body.symbol, body.order_id, body.order_link_id)
    except Exception as exc:
        raise trading_error(exc) from exc


@router.post("/orders/amend")
async def amend_order(body: AmendRequest, x_trade_token: str | None = Header(default=None)):
    authorize(x_trade_token)
    try:
        return await trading_service.engine.amend_order(
            body.symbol, order_id=body.order_id, order_link_id=body.order_link_id,
            qty=body.qty, price=body.price,
        )
    except Exception as exc:
        raise trading_error(exc) from exc


@router.post("/positions/close")
async def close_position(body: CloseRequest, x_trade_token: str | None = Header(default=None)):
    authorize(x_trade_token)
    require_mainnet_phrase(body.mainnet_phrase)
    try:
        return {"order": await trading_service.engine.close_position(body.symbol)}
    except Exception as exc:
        raise trading_error(exc) from exc


@router.post("/emergency-stop")
async def emergency_stop(body: EmergencyRequest, x_trade_token: str | None = Header(default=None)):
    authorize(x_trade_token)
    try:
        return await trading_service.engine.emergency_stop(body.close_positions)
    except Exception as exc:
        raise trading_error(exc) from exc


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
