"""SQLite database layer using aiosqlite."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from config import Config

_DB_PATH = Config.DATABASE_PATH


async def get_db() -> aiosqlite.Connection:
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(_DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db() -> None:
    """Create all tables on first run."""
    async with await get_db() as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                name        TEXT NOT NULL,
                created_at  INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL,
                messages    TEXT NOT NULL DEFAULT '[]',
                soul        TEXT,
                model       TEXT,
                active      INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS memory (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                key         TEXT NOT NULL,
                value       TEXT NOT NULL,
                tags        TEXT NOT NULL DEFAULT '[]',
                created_at  INTEGER NOT NULL,
                UNIQUE(user_id, key)
            );

            CREATE TABLE IF NOT EXISTS learning (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                input       TEXT NOT NULL,
                output      TEXT NOT NULL,
                feedback    TEXT,
                rating      INTEGER,
                created_at  INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skills (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                description TEXT,
                prompt      TEXT NOT NULL,
                created_at  INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS souls (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                prompt      TEXT NOT NULL,
                is_default  INTEGER NOT NULL DEFAULT 0,
                created_at  INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mcp_servers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                url         TEXT NOT NULL,
                enabled     INTEGER NOT NULL DEFAULT 1,
                config      TEXT NOT NULL DEFAULT '{}',
                created_at  INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trade_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                symbol      TEXT NOT NULL,
                side        TEXT NOT NULL,
                qty         TEXT NOT NULL,
                entry_price TEXT,
                exit_price  TEXT,
                pnl         TEXT,
                strategy    TEXT,
                status      TEXT NOT NULL DEFAULT 'open',
                order_id    TEXT,
                position_id TEXT,
                paper       INTEGER NOT NULL DEFAULT 1,
                created_at  INTEGER NOT NULL,
                closed_at   INTEGER
            );

            CREATE TABLE IF NOT EXISTS settings (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL
            );
        """)
        await db.commit()


# ── Session helpers ───────────────────────────────────────────────────────────

async def create_session(user_id: int, name: str, soul: Optional[str] = None, model: Optional[str] = None) -> int:
    now = int(time.time())
    async with await get_db() as db:
        # Deactivate all existing sessions for this user
        await db.execute("UPDATE sessions SET active=0 WHERE user_id=?", (user_id,))
        cur = await db.execute(
            "INSERT INTO sessions(user_id,name,created_at,updated_at,messages,soul,model) VALUES(?,?,?,?,?,?,?)",
            (user_id, name, now, now, "[]", soul, model),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def get_active_session(user_id: int) -> Optional[Dict[str, Any]]:
    async with await get_db() as db:
        cur = await db.execute(
            "SELECT * FROM sessions WHERE user_id=? AND active=1 ORDER BY updated_at DESC LIMIT 1",
            (user_id,),
        )
        row = await cur.fetchone()
        if row:
            d = dict(row)
            d["messages"] = json.loads(d["messages"])
            return d
        return None


async def list_sessions(user_id: int) -> List[Dict[str, Any]]:
    async with await get_db() as db:
        cur = await db.execute(
            "SELECT id,name,created_at,updated_at,active,soul,model FROM sessions WHERE user_id=? ORDER BY updated_at DESC",
            (user_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def save_session_messages(session_id: int, messages: List[Dict]) -> None:
    now = int(time.time())
    async with await get_db() as db:
        await db.execute(
            "UPDATE sessions SET messages=?, updated_at=? WHERE id=?",
            (json.dumps(messages), now, session_id),
        )
        await db.commit()


async def resume_session(user_id: int, session_id: int) -> bool:
    async with await get_db() as db:
        await db.execute("UPDATE sessions SET active=0 WHERE user_id=?", (user_id,))
        cur = await db.execute(
            "UPDATE sessions SET active=1,updated_at=? WHERE id=? AND user_id=?",
            (int(time.time()), session_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0  # type: ignore[return-value]


# ── Memory helpers ────────────────────────────────────────────────────────────

async def set_memory(user_id: int, key: str, value: str, tags: List[str] | None = None) -> None:
    now = int(time.time())
    async with await get_db() as db:
        await db.execute(
            "INSERT INTO memory(user_id,key,value,tags,created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value,tags=excluded.tags",
            (user_id, key, value, json.dumps(tags or []), now),
        )
        await db.commit()


async def get_memory(user_id: int, key: str) -> Optional[str]:
    async with await get_db() as db:
        cur = await db.execute("SELECT value FROM memory WHERE user_id=? AND key=?", (user_id, key))
        row = await cur.fetchone()
        return row["value"] if row else None


async def list_memory(user_id: int) -> List[Dict[str, Any]]:
    async with await get_db() as db:
        cur = await db.execute(
            "SELECT key,value,tags,created_at FROM memory WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def delete_memory(user_id: int, key: str) -> bool:
    async with await get_db() as db:
        cur = await db.execute("DELETE FROM memory WHERE user_id=? AND key=?", (user_id, key))
        await db.commit()
        return cur.rowcount > 0  # type: ignore[return-value]


# ── Learning helpers ──────────────────────────────────────────────────────────

async def log_learning(user_id: int, inp: str, out: str) -> int:
    now = int(time.time())
    async with await get_db() as db:
        cur = await db.execute(
            "INSERT INTO learning(user_id,input,output,created_at) VALUES(?,?,?,?)",
            (user_id, inp, out, now),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def rate_learning(entry_id: int, rating: int, feedback: str = "") -> None:
    async with await get_db() as db:
        await db.execute(
            "UPDATE learning SET rating=?,feedback=? WHERE id=?",
            (rating, feedback, entry_id),
        )
        await db.commit()


# ── Settings helpers ──────────────────────────────────────────────────────────

async def set_setting(key: str, value: str) -> None:
    async with await get_db() as db:
        await db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()


async def get_setting(key: str, default: str = "") -> str:
    async with await get_db() as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else default
