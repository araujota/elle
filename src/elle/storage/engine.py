"""Connection pool management for ELLE's PostgreSQL storage.

Provides module-level singletons for sync and async connection pools.
All connections use ``dict_row`` by default (matching the previous
``sqlite3.Row`` access pattern) and register the pgvector extension.

Usage:
    # One-time setup (daemon startup or CLI init):
    from elle.storage.config import PostgresConfig
    from elle.storage.engine import configure_pool

    pool = configure_pool(PostgresConfig())

    # Per-operation (sync):
    from elle.storage.engine import get_conn
    with get_conn(schema="incidents") as conn:
        rows = conn.execute("SELECT id FROM incidents").fetchall()

    # Per-operation (async, in daemon):
    from elle.storage.engine import get_async_conn
    async with get_async_conn(schema="incidents") as conn:
        await conn.execute(...)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool

from elle.storage.config import PostgresConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level pool singletons
# ---------------------------------------------------------------------------
_pool: ConnectionPool | None = None
_async_pool: AsyncConnectionPool | None = None
_config: PostgresConfig | None = None


def _on_connect(conn: psycopg.Connection) -> None:
    """Callback executed on every new physical connection.

    Registers pgvector types so that ``vector`` columns are
    transparently converted to/from Python lists.
    """
    try:
        from pgvector.psycopg import register_vector

        register_vector(conn)
    except Exception:
        logger.debug("pgvector registration skipped (extension may not be available)")


async def _on_connect_async(conn: psycopg.AsyncConnection) -> None:
    """Async variant of ``_on_connect``."""
    try:
        from pgvector.psycopg import register_vector_async

        await register_vector_async(conn)
    except Exception:
        logger.debug("pgvector async registration skipped")


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------


def configure_pool(config: PostgresConfig | None = None) -> ConnectionPool:
    """Create (or return existing) synchronous connection pool.

    Args:
        config: Connection parameters.  Uses defaults if *None*.

    Returns:
        The active :class:`ConnectionPool`.
    """
    global _pool, _config
    if _pool is not None:
        return _pool

    cfg = config or PostgresConfig()
    _config = cfg

    _pool = ConnectionPool(
        conninfo=cfg.conninfo,
        min_size=cfg.min_pool_size,
        max_size=cfg.max_pool_size,
        max_idle=cfg.max_idle_sec,
        kwargs={"row_factory": dict_row, "autocommit": False},
        configure=_on_connect,
    )
    logger.info("PostgreSQL sync pool configured (%s)", cfg.conninfo)
    return _pool


def configure_async_pool(config: PostgresConfig | None = None) -> AsyncConnectionPool:
    """Create (or return existing) asynchronous connection pool.

    Args:
        config: Connection parameters.  Uses defaults if *None*.

    Returns:
        The active :class:`AsyncConnectionPool`.
    """
    global _async_pool, _config
    if _async_pool is not None:
        return _async_pool

    cfg = config or _config or PostgresConfig()
    _config = cfg

    _async_pool = AsyncConnectionPool(
        conninfo=cfg.conninfo,
        min_size=cfg.min_pool_size,
        max_size=cfg.max_pool_size,
        max_idle=cfg.max_idle_sec,
        kwargs={"row_factory": dict_row, "autocommit": False},
        configure=_on_connect_async,
    )
    logger.info("PostgreSQL async pool configured (%s)", cfg.conninfo)
    return _async_pool


def get_pool() -> ConnectionPool:
    """Return the active synchronous pool.

    Raises:
        RuntimeError: If :func:`configure_pool` has not been called.
    """
    if _pool is None:
        raise RuntimeError("PostgreSQL sync pool not configured. Call configure_pool() during startup.")
    return _pool


def get_async_pool() -> AsyncConnectionPool:
    """Return the active asynchronous pool.

    Raises:
        RuntimeError: If :func:`configure_async_pool` has not been called.
    """
    if _async_pool is None:
        raise RuntimeError("PostgreSQL async pool not configured. Call configure_async_pool() during startup.")
    return _async_pool


def close_pools() -> None:
    """Close both pools (call during shutdown)."""
    global _pool, _async_pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:
            logger.debug("Error closing sync pool", exc_info=True)
        _pool = None
    if _async_pool is not None:
        try:
            _async_pool.close()  # type: ignore[unused-coroutine]
        except Exception:
            logger.debug("Error closing async pool", exc_info=True)
        _async_pool = None
    logger.info("PostgreSQL pools closed")


def reset_pools() -> None:
    """Reset pool singletons without closing (for testing)."""
    global _pool, _async_pool, _config
    _pool = None
    _async_pool = None
    _config = None


# ---------------------------------------------------------------------------
# Connection context managers
# ---------------------------------------------------------------------------


@contextmanager
def get_conn(schema: str = "public") -> Iterator[psycopg.Connection]:
    """Get a sync connection with ``search_path`` set to *schema*.

    The connection is returned to the pool when the block exits.
    On normal exit the transaction is committed; on exception it
    is rolled back.

    Args:
        schema: PostgreSQL schema name (e.g. ``"incidents"``).

    Yields:
        A :class:`psycopg.Connection` with ``dict_row`` factory.
    """
    pool = get_pool()
    with pool.connection() as conn:
        conn.execute("SET search_path TO %s, public", (schema,))
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


@asynccontextmanager
async def get_async_conn(schema: str = "public") -> AsyncIterator[psycopg.AsyncConnection]:
    """Get an async connection with ``search_path`` set to *schema*.

    Args:
        schema: PostgreSQL schema name.

    Yields:
        A :class:`psycopg.AsyncConnection` with ``dict_row`` factory.
    """
    pool = get_async_pool()
    async with pool.connection() as conn:
        await conn.execute("SET search_path TO %s, public", (schema,))
        try:
            yield conn
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
