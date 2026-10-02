"""
One store for everything: agent memory, sessions, souls, skills, settings and
the trader's trade log and engine settings.

Backend is chosen at runtime:
  * NEON_DATABASE_URL set  -> Postgres (Neon) via asyncpg, pooled
  * otherwise              -> local SQLite via aiosqlite

Both backends expose the same tiny async API (`execute`, `fetchall`,
`fetchone`, `run`) so callers never branch on the backend. Placeholders are
written once as `?` and translated to `%s` for Postgres.

Anything written here survives a restart, so the agent keeps its memory and
the trader keeps its history even when the process moves to another machine
(GitHub Actions, a VPS, a new deploy).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional, Sequence

from config import Config

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_DB = BASE_DIR / "data" / "store.db"
LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)

_pool: Any = None
_sqlite: Any = None
_backend: str = ""
_ready: bool = False


def neon_url() -> str:
    return (Config.NEON_DATABASE_URL or "").strip()


def backend() -> str:
    """'postgres' or 'sqlite' — resolved once, then cached."""
    global _backend
    if not _backend:
        _backend = "postgres" if neon_url() else "sqlite"
    return _backend


def _fix(sql: str) -> str:
    """Rewrite SQLite-flavoured SQL for Postgres."""
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    sql = re.sub(r"\bAUTOINCREMENT\b", "", sql, flags=re.I)
    sql = re.sub(r"\?", "%s", sql)
    return sql


async def connect() -> None:
    """Open the pool/connection once. Safe to call repeatedly."""
    global _pool, _sqlite, _backend, _ready
    if _ready:
        return
    url = neon_url()
    if url:
        try:
            import asyncpg
            _pool = await asyncpg.create_pool(
                url,
                min_size=1,
                max_size=5,
                command_timeout=30,
                init=_init_conn,
            )
            _backend = "postgres"
        except Exception as e:  # noqa: BLE001
            # A bad URL must not brick the bot: say why, then run locally.
            print(f"[store] postgres unavailable ({e.__class__.__name__}: {e}) "
                  f"- falling back to SQLite at {LOCAL_DB}")
            _pool = None
            _backend = "sqlite"
    if _backend == "sqlite":
        import aiosqlite
        _sqlite = await aiosqlite.connect(LOCAL_DB)
        _sqlite.row_factory = aiosqlite.Row
    _ready = True
    print(f"[store] connected — backend: {_backend}")
    # Always ensure tables exist right after connecting
    await init_schema()


async def _init_conn(conn: Any) -> None:
    """Per-connection setup for asyncpg."""
    for stmt in ("SET TIME ZONE 'UTC'",):
        await conn.execute(stmt)


async def close() -> None:
    global _pool, _sqlite, _ready
    if _pool is not None:
        await _pool.close()
        _pool = None
    if _sqlite is not None:
        await _sqlite.close()
        _sqlite = None
    _ready = False


async def _conn() -> Any:
    await connect()
    if _backend == "postgres":
        return _pool.acquire()
    return _sqlite


async def execute(sql: str, params: Sequence[Any] = ()) -> None:
    await connect()
    if _backend == "postgres":
        async with _pool.acquire() as conn:
            await conn.execute(_fix(sql), *params)
    else:
        await _sqlite.execute(_fix(sql), tuple(params))
        await _sqlite.commit()


async def fetchall(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    await connect()
    if _backend == "postgres":
        async with _pool.acquire() as conn:
            rows = await conn.fetch(_fix(sql), *params)
        return [dict(r) for r in rows]
    cur = await _sqlite.execute(_fix(sql), tuple(params))
    return [dict(r) for r in await cur.fetchall()]


async def fetchone(sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
    rows = await fetchall(sql, params)
    return rows[0] if rows else None


async def run(sql: str, params: Sequence[Any] = ()) -> int:
    """INSERT/UPDATE/DELETE; returns affected rowcount."""
    await connect()
    if _backend == "postgres":
        async with _pool.acquire() as conn:
            status = await conn.execute(_fix(sql), *params)
            try:
                return int(str(status).rsplit(" ", 1)[-1])
            except ValueError:
                return 0
    cur = await _sqlite.execute(_fix(sql), tuple(params))
    await _sqlite.commit()
    return cur.rowcount


# ── Schema ──────────────────────────────────────────────────────────

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS kv (
        key    TEXT PRIMARY KEY,
        value  TEXT NOT NULL,
        updated_at BIGINT NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS memory (
        id         BIGSERIAL PRIMARY KEY,
        user_id    BIGINT NOT NULL,
        fact       TEXT NOT NULL,
        kind       TEXT NOT NULL DEFAULT 'fact',
        source     TEXT NOT NULL DEFAULT 'chat',
        weight     REAL NOT NULL DEFAULT 1.0,
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS memory_profile (
        user_id    BIGINT PRIMARY KEY,
        data       TEXT NOT NULL DEFAULT '{}',
        updated_at BIGINT NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        id         TEXT PRIMARY KEY,
        user_id    BIGINT NOT NULL,
        title      TEXT NOT NULL DEFAULT 'session',
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS session_msgs (
        id         BIGSERIAL PRIMARY KEY,
        session_id TEXT NOT NULL,
        role       TEXT NOT NULL,
        content    TEXT NOT NULL,
        created_at BIGINT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS souls (
        name       TEXT PRIMARY KEY,
        body       TEXT NOT NULL,
        updated_at BIGINT NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS skills (
        name       TEXT PRIMARY KEY,
        body       TEXT NOT NULL,
        meta       TEXT NOT NULL DEFAULT '{}',
        updated_at BIGINT NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS trades (
        id          BIGSERIAL PRIMARY KEY,
        user_id     BIGINT NOT NULL DEFAULT 0,
        symbol      TEXT NOT NULL,
        side        TEXT NOT NULL,
        qty         TEXT,
        entry_price TEXT,
        exit_price  TEXT,
        pnl         TEXT,
        roi_pct     REAL,
        strategy    TEXT,
        status      TEXT NOT NULL DEFAULT 'open',
        order_id    TEXT,
        position_id TEXT,
        paper       INTEGER NOT NULL DEFAULT 1,
        created_at  BIGINT NOT NULL,
        closed_at   BIGINT
    )""",
]


async def init_schema() -> None:
    await connect()
    for stmt in SCHEMA:
        try:
            await execute(stmt)
        except Exception as e:  # noqa: BLE001
            print(f"[store] schema failed on {stmt.split()[5][:24]!r}: {e}")
            raise


# ── Key/value (settings, engine config, arbitrary state) ───────────


async def kv_get(key: str, default: str = "") -> str:
    row = await fetchone("SELECT value FROM kv WHERE key=?", (key,))
    return row["value"] if row else default


async def kv_set(key: str, value: str) -> None:
    now = int(time.time())
    if backend() == "postgres":
        await execute(
            """INSERT INTO kv(key,value,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,
                                             updated_at=EXCLUDED.updated_at""",
            (key, value, now),
        )
    else:
        await execute(
            """INSERT INTO kv(key,value,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                             updated_at=excluded.updated_at""",
            (key, value, now),
        )


async def kv_all(prefix: str = "") -> dict[str, str]:
    if prefix:
        rows = await fetchall(
            "SELECT key,value FROM kv WHERE key LIKE ? ORDER BY key", (prefix + "%",)
        )
    else:
        rows = await fetchall("SELECT key,value FROM kv ORDER BY key")
    return {r["key"]: r["value"] for r in rows}


async def kv_delete(key: str) -> None:
    await execute("DELETE FROM kv WHERE key=?", (key,))


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(raw: Any, default: Any = None) -> Any:
    if raw in (None, ""):
        return default if default is not None else {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default if default is not None else {}
