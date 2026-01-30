"""PostgreSQL storage layer for ELLE.

Provides connection pooling, schema migration, and shared utilities
for all ELLE database operations. Replaces the previous per-module
SQLite connection management.

Usage:
    from elle.storage.engine import get_conn, get_async_conn
    from elle.storage.config import PostgresConfig

    # Sync (CLI):
    with get_conn(schema="incidents") as conn:
        conn.execute("SELECT * FROM incidents WHERE id = %s", (id,))

    # Async (daemon):
    async with get_async_conn(schema="incidents") as conn:
        await conn.execute(...)
"""

from __future__ import annotations

from elle.storage.config import PostgresConfig
from elle.storage.engine import (
    close_pools,
    configure_async_pool,
    configure_pool,
    get_async_conn,
    get_async_pool,
    get_conn,
    get_pool,
)

__all__ = [
    "PostgresConfig",
    "close_pools",
    "configure_async_pool",
    "configure_pool",
    "get_async_conn",
    "get_async_pool",
    "get_conn",
    "get_pool",
]
