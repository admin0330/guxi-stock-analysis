"""A股分析系统 FastAPI 入口。

启动:  uvicorn backend.main:app --reload --port 8765
"""
from __future__ import annotations

import logging
from urllib.parse import quote
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.auth import auth_store, websocket_allowed
from backend.api.auth_routes import admin_router, auth_router
from backend.api.routes import router
from backend.api.trading_routes import router as trading_router
from backend.data import http
from backend.data.crypto_stream import crypto_stream
from backend.data.public_refresh import public_data_refresher
from trading.service import trading_service
from backend.logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动实时行情；退出时关闭上游连接与 HTTP 连接池。"""
    if config.AUTH_ENABLED:
        auth_store.bootstrap_admin()
    logger.info("股析 %s 启动", config.APP_VERSION)
    await crypto_stream.start()
    await trading_service.start()
    await public_data_refresher.start()
    try:
        yield
    finally:
        await public_data_refresher.stop()
        await trading_service.stop()
        await crypto_stream.stop()
        http.close()
        logger.info("股析服务停止")


app = FastAPI(
    title=config.APP_TITLE,
    version=config.APP_VERSION,
    description="本地市场分析系统：A股大盘 / 个股深度 / 涨停复盘 / 小白日报 / BTC与ETH",
    lifespan=lifespan,
)

app.include_router(router)
app.include_router(trading_router)
app.include_router(auth_router)
app.include_router(admin_router)


def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:"
    if config.SESSION_COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = "max-age=15552000; includeSubDomains"
    return response


@app.middleware("http")
async def authenticate_request(request: Request, call_next):
    """业务页面/API 服务端鉴权；静态资源和登录接口保持匿名可用。"""
    path = request.url.path
    host = (request.url.hostname or "").lower()
    if config.AUTH_ENABLED and host not in config.ALLOWED_HOSTS:
        return _security_headers(JSONResponse(status_code=400, content={"detail": "请求域名不受信任"}))

    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    session = auth_store.session(token) if config.AUTH_ENABLED else None
    request.state.auth_session = session
    if path == "/login" and session:
        return _security_headers(RedirectResponse("/stock", status_code=303))
    public = path == "/login" or path == "/api/auth/login" or path == "/api/health" or path.startswith("/static/")
    if path in {"/static/index.html", "/static/admin.html"}:
        public = False

    if config.AUTH_ENABLED and not public and not session:
        if path.startswith("/api/"):
            return _security_headers(JSONResponse(status_code=401, content={"detail": "请先登录"}))
        target = path + (f"?{request.url.query}" if request.url.query else "")
        return _security_headers(RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303))

    if config.AUTH_ENABLED and path.startswith("/admin") and session and session["role"] != "admin":
        if path.startswith("/api/"):
            return _security_headers(JSONResponse(status_code=403, content={"detail": "仅管理员可以访问"}))
        return _security_headers(HTMLResponse("<h1>403</h1><p>仅管理员可以访问此页面。</p>", status_code=403))

    if config.AUTH_ENABLED and session and request.method in {"POST", "PUT", "PATCH", "DELETE"} and path.startswith("/api/") and path != "/api/auth/login":
        if not auth_store.csrf_valid(session, request.headers.get("X-CSRF-Token")):
            return _security_headers(JSONResponse(status_code=403, content={"detail": "安全校验失败，请刷新页面后重试"}))

    response = _security_headers(await call_next(request))
    if path.startswith("/api/") or path in {"/login", "/admin"}:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.websocket("/ws/crypto")
async def crypto_websocket(websocket: WebSocket):
    """把单一上游行情连接转发给本机浏览器。"""
    if not websocket_allowed(websocket):
        await websocket.close(code=4401, reason="请先登录")
        return
    await websocket.accept()
    queue = crypto_stream.subscribe()
    try:
        while True:
            await websocket.send_json(await queue.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        crypto_stream.unsubscribe(queue)


@app.get("/api/crypto/stream/status")
def crypto_stream_status():
    """供界面诊断实时链路状态，不触发外部请求。"""
    return crypto_stream.snapshot()


@app.exception_handler(RequestValidationError)
async def validation_error(_request, exc: RequestValidationError):
    logger.warning("请求参数错误: %s", exc)
    return JSONResponse(status_code=422, content={"detail": "请求参数格式不正确，请检查后重试"})


@app.exception_handler(Exception)
async def unexpected_error(_request, exc: Exception):
    logger.exception("未处理异常", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "服务暂时不可用，请稍后重试或查看日志"})

# 前端静态文件
FRONTEND = config.FRONTEND_DIR
if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


@app.get("/")
def index():
    """返回前端入口页面。"""
    idx = FRONTEND / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return {"app": config.APP_TITLE, "version": config.APP_VERSION, "docs": "/docs"}


@app.get("/stock")
def stock_page():
    """业务入口别名；供服务入口页与移动端直接打开。"""
    page = FRONTEND / "index.html"
    return FileResponse(str(page)) if page.exists() else HTMLResponse("<h1>业务页面缺失</h1>", status_code=500)


@app.get("/login")
def login_page():
    page = FRONTEND / "login.html"
    return FileResponse(str(page)) if page.exists() else HTMLResponse("<h1>登录页面缺失</h1>", status_code=500)


@app.get("/admin")
def admin_page():
    page = FRONTEND / "admin.html"
    return FileResponse(str(page)) if page.exists() else HTMLResponse("<h1>管理页面缺失</h1>", status_code=500)
