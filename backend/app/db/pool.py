"""Lazy Postgres connection pool.

The pool is opened on first use and never at import or in a startup hook. That is not a style
preference — the application is required to boot and serve its entire deterministic half with
no credentials and no database present, which `tests/security/test_api_key_handling.py::
test_deterministic_endpoints_work_with_no_key_anywhere` and the CI "App boots with no
credentials" step both assert. Connecting eagerly would make storage a boot dependency and
take the free tier down with the database.

Storage is therefore *optional infrastructure*: when `DATABASE_URL` is unset, every read and
write raises `StorageUnavailable`, and the endpoints that need it degrade while the rest of the
application carries on. Nothing here reads or holds an Anthropic credential.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cost is not worth paying at runtime
    import asyncpg

logger = logging.getLogger(__name__)

DATABASE_URL_ENV = "DATABASE_URL"

_pool: asyncpg.Pool | None = None
# Guards pool creation so a burst of concurrent first-requests opens exactly one pool.
_lock = asyncio.Lock()


class StorageUnavailable(RuntimeError):
    """Raised when persistence is required but not configured or not reachable.

    Callers should map this to a 503 with a message that distinguishes "this deployment has no
    database" from "your request was wrong" — they are very different problems for the user.
    """


def is_configured() -> bool:
    """True when a database URL is present. Cheap; safe to call per request."""
    return bool(os.environ.get(DATABASE_URL_ENV, "").strip())


async def get_pool() -> asyncpg.Pool:
    """Return the process-wide pool, opening it on first use.

    Raises `StorageUnavailable` when `DATABASE_URL` is unset or the database cannot be reached.
    """
    global _pool

    if _pool is not None:
        return _pool

    dsn = os.environ.get(DATABASE_URL_ENV, "").strip()
    if not dsn:
        raise StorageUnavailable(
            "No DATABASE_URL is configured, so this deployment cannot save or load data. "
            "The deterministic analysis and committee runs still work."
        )

    async with _lock:
        # Re-check: another coroutine may have opened it while we waited for the lock.
        if _pool is not None:
            return _pool

        try:
            import asyncpg
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency is declared
            raise StorageUnavailable("asyncpg is not installed.") from exc

        try:
            _pool = await asyncpg.create_pool(
                dsn=dsn,
                # min_size=0 so that even an accidental early call cannot open a socket
                # during startup and turn the database into a boot dependency.
                min_size=int(os.environ.get("AIFA_DB_POOL_MIN", "0")),
                max_size=int(os.environ.get("AIFA_DB_POOL_MAX", "10")),
                # Supabase's pooler drops idle server-side connections; recycling below that
                # horizon avoids handing out a socket the far end has already closed.
                max_inactive_connection_lifetime=180.0,
                command_timeout=30.0,
                # Required when connecting through Supabase's transaction-mode pooler
                # (Supavisor, port 6543): asyncpg's prepared-statement cache collides with
                # connection reuse there and fails with `prepared statement "__asyncpg_stmt_1__"
                # already exists`. Harmless on a direct connection, so it is on unconditionally
                # rather than depending on which port the deployment happens to use.
                statement_cache_size=0,
            )
        except Exception as exc:
            # The DSN carries a password. Never let the driver's message reach a caller.
            logger.warning("Database connection failed: %s", type(exc).__name__)
            raise StorageUnavailable("Could not connect to the database.") from exc

    logger.info("Database pool opened.")
    return _pool


async def close_pool() -> None:
    """Close the pool if one was ever opened. Safe to call when it was not."""
    global _pool
    if _pool is None:
        return
    await _pool.close()
    _pool = None
    logger.info("Database pool closed.")


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    """Check out a connection, translating connection failures into StorageUnavailable.

    The pool is created with `min_size=0`, so `create_pool()` returns without ever touching the
    network — that is what keeps the database off the boot path. The consequence is that an
    unreachable host or a bad password surfaces *here*, on first checkout, not at creation. The
    DSN carries a password and asyncpg puts the host and user in its message, so the driver's
    exception must not be allowed to propagate to a caller that might render it.
    """
    pool = await get_pool()
    try:
        conn = await pool.acquire()
    except Exception as exc:
        logger.warning("Database checkout failed: %s", type(exc).__name__)
        raise StorageUnavailable("Could not connect to the database.") from exc

    try:
        yield conn
    finally:
        await pool.release(conn)


async def fetch(query: str, *args: Any) -> list[asyncpg.Record]:
    async with acquire() as conn:
        return await conn.fetch(query, *args)


async def fetchrow(query: str, *args: Any) -> asyncpg.Record | None:
    async with acquire() as conn:
        return await conn.fetchrow(query, *args)


async def fetchval(query: str, *args: Any) -> Any:
    async with acquire() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args: Any) -> str:
    async with acquire() as conn:
        return await conn.execute(query, *args)
