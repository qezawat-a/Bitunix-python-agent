"""
One store for everything: agent memory, lessons, settings and the trader's
trade log — all in Neon (Postgres) via asyncpg. No SQLite, no local DB files.

Set NEON_DATABASE_URL (postgresql://user:pass@host/db?sslmode=require).
If it is missing or unreachable the bot stops with a clear error instead of
silently running on a different database.

Callers write portable SQL with `?` placeholders; they are translated to
asyncpg's numbered `$1, $2, ...` here (never `%s` — Postgres parses that as
"modulo column s" and raises `column "s" does not exist`).
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from config import Config

_pool: Any = None
_lock = asyncio.Lock()


def neon_url() -> str:
    return (Config.NEON_DATABASE_URL or "").strip()


def backend() -> str:
    return "postgres"


def _clean_dsn(url: str) -> tuple[str, bool]:
    """Drop libpq-only params asyncpg rejects (channel_binding); report pooler."""
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k != "channel_binding"]
    if not any(k == "sslmode" for k, _ in q):
        q.append(("sslmode", "require"))
    clean = urlunsplit(parts._replace(query=urlencode(q)))
    return clean, "-pooler" in (parts.hostname or "")


def _fix(sql: str) -> str:
    """`?` -> `$1, $2, ...` (ignoring `?` inside single-quoted literals)."""
    out: list[str] = []
    n = 0
    in_str = False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
        if ch == "?" and not in_str:
            n += 1
            out.append(f"${n}")
        else:
            out.append(ch)
    return "".join(out)


async def _init_conn(conn: Any) -> None:
    await conn.execute("SET TIME ZONE 'UTC'")


async def connect() -> None:
    """Open the Neon pool once and ensure the schema exists. Safe to repeat."""
    global _pool
    if _pool is not None:
        return
    async with _lock:
        if _pool is not None:
            return
        url = neon_url()
        if not url:
            raise RuntimeError(
                "NEON_DATABASE_URL is not set. Add your Neon connection string "
                "to .env (or the GitHub Actions secret NEON_DATABASE_URL)."
            )
        import asyncpg

        dsn, pooled = _clean_dsn(url)
        last: Exception | None = None
        for attempt in range(1, 4):  # Neon may be waking from scale-to-zero
            try:
                _pool = await asyncpg.create_pool(
                    dsn,
                    min_size=1,
                    max_size=5,
                    timeout=30,
                    command_timeout=30,
                    init=_init_conn,
                    # PgBouncer (-pooler host) can't use prepared statements
                    statement_cache_size=0 if pooled else 100,
                )
                break
            except Exception as e:  # noqa: BLE001
                last = e
                print(f"[store] neon connect attempt {attempt}/3 failed: "
                      f"{e.__class__.__name__}: {e}")
                await asyncio.sleep(2 * attempt)
        if _pool is None:
            raise RuntimeError(f"Cannot connect to Neon: {last}")
        print("[store] connected — backend: neon (postgres)")
    await init_schema()


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def execute(sql: str, params: Sequence[Any] = ()) -> None:
    await connect()
    async with _pool.acquire() as conn:
        await conn.execute(_fix(sql), *params)


async def fetchall(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    await connect()
    async with _pool.acquire() as conn:
        rows = await conn.fetch(_fix(sql), *params)
    return [dict(r) for r in rows]


async def fetchone(sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
    rows = await fetchall(sql, params)
    return rows[0] if rows else None


async def run(sql: str, params: Sequence[Any] = ()) -> int:
    """INSERT/UPDATE/DELETE; returns affected rowcount."""
    await connect()
    async with _pool.acquire() as conn:
        status = await conn.execute(_fix(sql), *params)
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except ValueError:
        return 0


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
    """CREATE TABLE IF NOT EXISTS lessons (
        id         BIGSERIAL PRIMARY KEY,
        user_id    BIGINT NOT NULL,
        lesson     TEXT NOT NULL,
        hits       INTEGER NOT NULL DEFAULT 0,
        created_at BIGINT NOT NULL
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
    global _pool
    if _pool is None:
        await connect()  # connect() calls init_schema() itself
        return
    async with _pool.acquire() as conn:
        for stmt in SCHEMA:
            try:
                await conn.execute(stmt)
            except Exception as e:  # noqa: BLE001
                print(f"[store] schema failed: {e}")
                raise


# ── Trade log ───────────────────────────────────────────────────────

async def log_paper_trade(symbol: str, side: str, qty: str, price: float,
                          strategy: str = "", user_id: int = 0) -> None:
    await execute(
        """INSERT INTO trades
           (user_id, symbol, side, qty, entry_price, status, strategy, paper, created_at)
           VALUES (?, ?, ?, ?, ?, 'open', ?, 1, ?)""",
        (user_id, symbol, side, str(qty), str(price), strategy, int(time.time())),
    )


# ── Key/value (settings, engine config, arbitrary state) ───────────

async def kv_get(key: str, default: str = "") -> str:
    row = await fetchone("SELECT value FROM kv WHERE key=?", (key,))
    return row["value"] if row else default


async def kv_set(key: str, value: str) -> None:
    await execute(
        """INSERT INTO kv(key,value,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,
                                         updated_at=EXCLUDED.updated_at""",
        (key, value, int(time.time())),
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
