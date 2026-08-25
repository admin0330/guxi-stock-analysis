"""登录、登出与管理员用户管理 API。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend import config
from backend.auth import LoginLimited, auth_store, client_ip, password_policy
from backend.data import user_store

logger = logging.getLogger(__name__)
auth_router = APIRouter(prefix="/api/auth", tags=["网站登录"])
admin_router = APIRouter(prefix="/api/admin", tags=["用户管理"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserCreateBody(BaseModel):
    username: str
    password: str
    role: str = "user"


class UserUpdateBody(BaseModel):
    role: str | None = None
    enabled: bool | None = None


class PasswordResetBody(BaseModel):
    password: str


def _session(request: Request) -> dict:
    session = getattr(request.state, "auth_session", None)
    if not config.AUTH_ENABLED:
        return {"id": 0, "username": "本地用户", "role": "admin", "enabled": True, "created_at": None, "updated_at": None, "last_login_at": None, "csrf_token": "", "session_expires_at": None}
    if not session:
        raise HTTPException(401, "请先登录")
    return session


def _admin(request: Request) -> dict:
    session = _session(request)
    if session["role"] != "admin":
        raise HTTPException(403, "仅管理员可以执行此操作")
    return session


@auth_router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    try:
        user = auth_store.authenticate(body.username, body.password, client_ip(request))
    except LoginLimited as exc:
        logger.warning("登录请求被限流")
        raise HTTPException(429, str(exc)) from exc
    if not user:
        logger.warning("网站登录失败")
        raise HTTPException(401, "用户名或密码错误")
    token, session = auth_store.create_session(user["id"], client_ip(request), request.headers.get("User-Agent", ""))
    response.set_cookie(
        config.SESSION_COOKIE_NAME, token, max_age=config.SESSION_MAX_AGE, path="/", httponly=True,
        secure=config.SESSION_COOKIE_SECURE, samesite="lax",
    )
    logger.info("网站用户登录成功：user_id=%s", user["id"])
    return {"user": user, "csrf_token": session["csrf_token"]}


@auth_router.get("/me")
def me(request: Request):
    session = _session(request)
    return {"user": {key: session[key] for key in ("id", "username", "role", "enabled", "created_at", "updated_at", "last_login_at")}, "csrf_token": session["csrf_token"], "session_expires_at": session["session_expires_at"]}


@auth_router.post("/logout")
def logout(request: Request, response: Response):
    session = _session(request)
    auth_store.logout(request.cookies.get(config.SESSION_COOKIE_NAME))
    response.delete_cookie(config.SESSION_COOKIE_NAME, path="/", secure=config.SESSION_COOKIE_SECURE, httponly=True, samesite="lax")
    logger.info("网站用户退出：user_id=%s", session["id"])
    return {"message": "已安全退出"}


@admin_router.get("/users")
def users(request: Request):
    _admin(request)
    return {"items": auth_store.list_users(), "password_policy": password_policy()}


@admin_router.post("/users", status_code=201)
def create_user(body: UserCreateBody, request: Request):
    actor = _admin(request)
    try:
        user = auth_store.create_user(body.username, body.password, body.role)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    logger.info("管理员创建用户：actor_id=%s target_id=%s role=%s", actor["id"], user["id"], user["role"])
    return user


@admin_router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdateBody, request: Request):
    actor = _admin(request)
    if body.role is None and body.enabled is None:
        raise HTTPException(400, "没有需要修改的内容")
    try:
        user = auth_store.update_user(user_id, role=body.role, enabled=body.enabled, actor_id=actor["id"])
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    logger.info("管理员更新用户：actor_id=%s target_id=%s", actor["id"], user_id)
    return user


@admin_router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, body: PasswordResetBody, request: Request):
    actor = _admin(request)
    try:
        user = auth_store.reset_password(user_id, body.password)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    logger.info("管理员重置用户密码并清除会话：actor_id=%s target_id=%s", actor["id"], user_id)
    return user


@admin_router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, request: Request):
    actor = _admin(request)
    try:
        auth_store.delete_user(user_id, actor["id"])
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    user_store.delete_user_state(user_id)
    logger.info("管理员删除用户：actor_id=%s target_id=%s", actor["id"], user_id)
    return Response(status_code=204)
