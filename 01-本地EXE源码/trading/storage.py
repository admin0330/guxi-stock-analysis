"""轻量 SQLite 审计存储。"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from backend import config

logger = logging.getLogger("trading.audit")


class TradingStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.DATA_DIR / "data" / "trading.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    def open(self) -> None:
        with self._lock:
            if self._connection is not None:
                return
            self._connection = sqlite3.connect(self.path, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    level TEXT NOT NULL,
                    symbol TEXT,
                    message TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS orders (
                    order_link_id TEXT PRIMARY KEY,
                    order_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    qty REAL NOT NULL,
                    price REAL,
                    status TEXT NOT NULL,
                    reduce_only INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS trades (
                    exec_id TEXT PRIMARY KEY,
                    order_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty REAL NOT NULL,
                    price REAL NOT NULL,
                    fee REAL NOT NULL DEFAULT 0,
                    closed_pnl REAL NOT NULL DEFAULT 0,
                    executed_at TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(executed_at DESC);
            """)
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    @property
    def db(self) -> sqlite3.Connection:
        self.open()
        assert self._connection is not None
        return self._connection

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def timestamp(value) -> str:
        if value in (None, ""):
            return TradingStore.now()
        try:
            number = float(value)
            return datetime.fromtimestamp(number / 1000 if number > 10_000_000_000 else number, timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            return str(value)

    def event(self, kind: str, message: str, *, level: str = "INFO", symbol: str = "", payload: dict | None = None) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO events(created_at,kind,level,symbol,message,payload) VALUES(?,?,?,?,?,?)",
                (self.now(), kind, level, symbol, message, json.dumps(payload or {}, ensure_ascii=False, default=str)),
            )
            self.db.commit()
        logger.log(getattr(logging, level.upper(), logging.INFO), "%s%s：%s", f"{symbol} " if symbol else "", kind, message)

    def upsert_order(self, row: dict, source: str = "exchange") -> None:
        link_id = str(row.get("orderLinkId") or row.get("order_link_id") or row.get("orderId") or "")
        if not link_id:
            return
        now = self.now()
        values = (
            link_id, str(row.get("orderId") or ""), str(row.get("symbol") or ""), str(row.get("side") or ""),
            str(row.get("orderType") or ""), float(row.get("qty") or 0), float(row.get("price") or 0) or None,
            str(row.get("orderStatus") or row.get("status") or "Unknown"), int(bool(row.get("reduceOnly"))), source,
            self.timestamp(row.get("createdTime") or now), self.timestamp(row.get("updatedTime") or now),
            json.dumps(row, ensure_ascii=False, default=str),
        )
        with self._lock:
            self.db.execute("""
                INSERT INTO orders(order_link_id,order_id,symbol,side,order_type,qty,price,status,reduce_only,source,created_at,updated_at,payload)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(order_link_id) DO UPDATE SET
                  order_id=excluded.order_id,symbol=excluded.symbol,side=excluded.side,
                  order_type=excluded.order_type,qty=excluded.qty,price=excluded.price,
                  status=excluded.status,reduce_only=excluded.reduce_only,source=excluded.source,
                  updated_at=excluded.updated_at,payload=excluded.payload
            """, values)
            self.db.commit()

    def add_trade(self, row: dict) -> None:
        exec_id = str(row.get("execId") or row.get("exec_id") or "")
        if not exec_id:
            return
        with self._lock:
            self.db.execute("""
                INSERT OR IGNORE INTO trades(exec_id,order_id,symbol,side,qty,price,fee,closed_pnl,executed_at,payload)
                VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (
                exec_id, str(row.get("orderId") or ""), str(row.get("symbol") or ""), str(row.get("side") or ""),
                float(row.get("execQty") or row.get("qty") or 0), float(row.get("execPrice") or row.get("price") or 0),
                float(row.get("execFee") or 0), float(row.get("closedPnl") or 0),
                self.timestamp(row.get("execTime")), json.dumps(row, ensure_ascii=False, default=str),
            ))
            self.db.commit()

    def rows(self, table: str, limit: int = 100) -> list[dict]:
        if table not in {"events", "orders", "trades"}:
            raise ValueError("不支持的数据表")
        order = "created_at" if table != "trades" else "executed_at"
        with self._lock:
            return [dict(row) for row in self.db.execute(
                f"SELECT * FROM {table} ORDER BY {order} DESC LIMIT ?", (max(1, min(500, limit)),)
            ).fetchall()]

    def daily_closed_pnl(self, date_prefix: str) -> float:
        with self._lock:
            row = self.db.execute(
                "SELECT COALESCE(SUM(closed_pnl),0) AS pnl FROM trades WHERE executed_at LIKE ?", (date_prefix + "%",)
            ).fetchone()
        return float(row["pnl"] or 0)

    def consecutive_losses(self) -> int:
        with self._lock:
            rows = self.db.execute(
                "SELECT closed_pnl FROM trades WHERE closed_pnl != 0 ORDER BY executed_at DESC LIMIT 50"
            ).fetchall()
        count = 0
        for row in rows:
            if float(row["closed_pnl"]) >= 0:
                break
            count += 1
        return count
