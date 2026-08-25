"""SQLite 用户与服务端 Session；不保存明文密码或原始会话令牌。"""
from __future__ import annotations

import getpass
import hashlib
import logging
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from backend import config

logger = logging.getLogger(__name__)
_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class LoginLimited(Exception):
    pass


def _utc(value: int | None) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds") if value else None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(expected))
        )
        return secrets.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password("此密码永远不会用于登录-2026")


def validate_username(username: str) -> str:
    value = username.strip().lower()
    if not _USERNAME.fullmatch(value):
        raise ValueError("用户名须为 3～32 位字母、数字、点、下划线或连字符")
    return value


def validate_password(password: str) -> None:
    if len(password) < config.PASSWORD_MIN_LENGTH:
        raise ValueError(f"密码至少需要 {config.PASSWORD_MIN_LENGTH} 位")
    if len(password) > 128:
        raise ValueError("密码不能超过 128 位")
    if config.PASSWORD_REQUIRE_MIXED:
        groups = sum((any(c.islower() for c in password), any(c.isupper() for c in password), any(c.isdigit() for c in password), any(not c.isalnum() for c in password)))
        if groups < 3:
            raise ValueError("密码需包含大小写字母、数字、符号中的至少三类")


class AuthStore:
    def __init__(self, path: Path | None = None):
        self.path = path or config.AUTH_DB_FILE
        self._lock = threading.RLock()

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self) -> None:
        with self._lock, self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('admin','user')),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_login_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    ip_hash TEXT NOT NULL,
                    user_agent TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS login_attempts (
                    attempt_key TEXT PRIMARY KEY,
                    failures INTEGER NOT NULL,
                    window_started_at INTEGER NOT NULL,
                    locked_until INTEGER NOT NULL DEFAULT 0
                );
            """)

    def bootstrap_admin(self) -> None:
        self.initialize()
        with self._connect() as con:
            count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count:
            return
        username, password = os.getenv("ADMIN_USERNAME", "").strip(), os.getenv("ADMIN_PASSWORD", "")
        if bool(username) != bool(password):
            raise RuntimeError("首次启动必须同时设置 ADMIN_USERNAME 与 ADMIN_PASSWORD")
        if username and password:
            self.create_user(username, password, "admin")
            logger.info("已创建初始管理员：%s；请移除环境变量中的初始密码", username.lower())
        else:
            logger.warning("尚无网站用户；请设置 ADMIN_USERNAME/ADMIN_PASSWORD 后重启，或运行 python -m backend.auth create-admin")

    @staticmethod
    def _public(row: sqlite3.Row | dict) -> dict:
        return {
            "id": int(row["id"]), "username": row["username"], "role": row["role"], "enabled": bool(row["enabled"]),
            "created_at": _utc(row["created_at"]), "updated_at": _utc(row["updated_at"]), "last_login_at": _utc(row["last_login_at"]),
        }

    def create_user(self, username: str, password: str, role: str = "user") -> dict:
        username = validate_username(username)
        validate_password(password)
        if role not in {"admin", "user"}:
            raise ValueError("角色只能是管理员或普通用户")
        now = int(time.time())
        try:
            with self._lock, self._connect() as con:
                cur = con.execute(
                    "INSERT INTO users(username,password_hash,role,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (username, hash_password(password), role, 1, now, now),
                )
                row = con.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
            return self._public(row)
        except sqlite3.IntegrityError as exc:
            raise ValueError("用户名已被使用") from exc

    def list_users(self) -> list[dict]:
        with self._connect() as con:
            return [self._public(row) for row in con.execute("SELECT * FROM users ORDER BY id")]

    def _attempt_key(self, ip: str, username: str) -> str:
        return hashlib.sha256(f"{ip}\0{username}".encode()).hexdigest()

    def _check_limit(self, key: str, now: int) -> None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM login_attempts WHERE attempt_key=?", (key,)).fetchone()
        if row and row["locked_until"] > now:
            raise LoginLimited("登录尝试过于频繁，请稍后再试")

    def _fail(self, key: str, now: int) -> None:
        with self._lock, self._connect() as con:
            row = con.execute("SELECT * FROM login_attempts WHERE attempt_key=?", (key,)).fetchone()
            if not row or now - row["window_started_at"] > config.LOGIN_WINDOW_SECONDS:
                failures, started = 1, now
            else:
                failures, started = row["failures"] + 1, row["window_started_at"]
            locked = now + config.LOGIN_LOCK_SECONDS if failures >= config.LOGIN_MAX_FAILURES else 0
            con.execute(
                "INSERT INTO login_attempts(attempt_key,failures,window_started_at,locked_until) VALUES(?,?,?,?) "
                "ON CONFLICT(attempt_key) DO UPDATE SET failures=excluded.failures,window_started_at=excluded.window_started_at,locked_until=excluded.locked_until",
                (key, failures, started, locked),
            )

    def authenticate(self, username: str, password: str, ip: str) -> dict | None:
        normalized = username.strip().lower()[:64]
        key, now = self._attempt_key(ip, normalized), int(time.time())
        self._check_limit(key, now)
        with self._connect() as con:
            row = con.execute("SELECT * FROM users WHERE username=?", (normalized,)).fetchone()
        valid = verify_password(password, row["password_hash"] if row else _DUMMY_HASH)
        if not row or not valid or not row["enabled"]:
            self._fail(key, now)
            return None
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM login_attempts WHERE attempt_key=?", (key,))
            con.execute("UPDATE users SET last_login_at=?,updated_at=? WHERE id=?", (now, now, row["id"]))
            row = con.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
        return self._public(row)

    def create_session(self, user_id: int, ip: str, user_agent: str) -> tuple[str, dict]:
        token, csrf, now = secrets.token_urlsafe(48), secrets.token_urlsafe(32), int(time.time())
        expires = now + config.SESSION_MAX_AGE
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM sessions WHERE expires_at<=? OR last_seen_at<=?", (now, now - config.SESSION_IDLE_TIMEOUT))
            con.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)",
                (_token_hash(token), user_id, csrf, _token_hash(ip), user_agent[:255], now, now, expires),
            )
        session = self.session(token)
        return token, session

    def session(self, token: str | None) -> dict | None:
        if not token:
            return None
        now = int(time.time())
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT s.*,u.id,u.username,u.role,u.enabled,u.created_at,u.updated_at,u.last_login_at "
                "FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
                (_token_hash(token),),
            ).fetchone()
            if not row or not row["enabled"] or row["expires_at"] <= now or row["last_seen_at"] <= now - config.SESSION_IDLE_TIMEOUT:
                if row:
                    con.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))
                return None
            if row["last_seen_at"] < now - 300:
                con.execute("UPDATE sessions SET last_seen_at=? WHERE token_hash=?", (now, _token_hash(token)))
        return {**self._public(row), "csrf_token": row["csrf_token"], "session_expires_at": _utc(row["expires_at"])}

    def csrf_valid(self, session: dict, supplied: str | None) -> bool:
        return bool(supplied and secrets.compare_digest(session["csrf_token"], supplied))

    def logout(self, token: str | None) -> None:
        if token:
            with self._lock, self._connect() as con:
                con.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))

    def reset_password(self, user_id: int, password: str) -> dict:
        validate_password(password)
        now = int(time.time())
        with self._lock, self._connect() as con:
            row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise LookupError("用户不存在")
            con.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (hash_password(password), now, user_id))
            con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._public(row)

    def update_user(self, user_id: int, *, role: str | None = None, enabled: bool | None = None, actor_id: int | None = None) -> dict:
        if role is not None and role not in {"admin", "user"}:
            raise ValueError("角色只能是管理员或普通用户")
        with self._lock, self._connect() as con:
            row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise LookupError("用户不存在")
            next_role, next_enabled = role or row["role"], int(row["enabled"] if enabled is None else enabled)
            if actor_id == user_id and (next_role != "admin" or not next_enabled):
                raise ValueError("不能取消自己的管理员权限或禁用自己")
            if row["role"] == "admin" and row["enabled"] and (next_role != "admin" or not next_enabled):
                active_admins = con.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0]
                if active_admins <= 1:
                    raise ValueError("必须保留至少一个启用的管理员")
            now = int(time.time())
            con.execute("UPDATE users SET role=?,enabled=?,updated_at=? WHERE id=?", (next_role, next_enabled, now, user_id))
            if not next_enabled:
                con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._public(row)

    def delete_user(self, user_id: int, actor_id: int) -> None:
        if user_id == actor_id:
            raise ValueError("不能删除当前登录账号")
        with self._lock, self._connect() as con:
            row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise LookupError("用户不存在")
            if row["role"] == "admin" and row["enabled"]:
                active_admins = con.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0]
                if active_admins <= 1:
                    raise ValueError("不能删除最后一个启用的管理员")
            con.execute("DELETE FROM users WHERE id=?", (user_id,))


auth_store = AuthStore()


def client_ip(request) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer in config.TRUSTED_PROXIES:
        cloudflare = request.headers.get("CF-Connecting-IP", "").strip()
        if cloudflare:
            return cloudflare[:64]
        forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded[:64]
    return peer


def websocket_allowed(websocket) -> bool:
    if not config.AUTH_ENABLED:
        return True
    origin = websocket.headers.get("origin")
    if origin:
        host = (urlparse(origin).hostname or "").lower()
        if host not in config.ALLOWED_HOSTS:
            return False
    return auth_store.session(websocket.cookies.get(config.SESSION_COOKIE_NAME)) is not None


def password_policy() -> dict:
    return {"min_length": config.PASSWORD_MIN_LENGTH, "require_mixed": config.PASSWORD_REQUIRE_MIXED}


def _cli() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "create-admin":
        print("用法：python -m backend.auth create-admin [用户名]")
        return 2
    auth_store.initialize()
    username = sys.argv[2] if len(sys.argv) > 2 else input("管理员用户名：")
    password = getpass.getpass("管理员密码：")
    confirm = getpass.getpass("再次输入密码：")
    if password != confirm:
        print("两次密码不一致", file=sys.stderr)
        return 1
    try:
        user = auth_store.create_user(username, password, "admin")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"管理员已创建：{user['username']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
