"""Database utilities for ELLE.

Provides initialization helpers that ensure all PostgreSQL schemas
are up to date.  Replaces the previous SQLite-based version.
"""

from __future__ import annotations

import logging

import psycopg

from elle.storage.engine import get_conn

logger = logging.getLogger(__name__)


def init_all_schemas() -> None:
    """Initialize all database schemas.

    Runs migrations for every ELLE schema:
    - telemetry (events, probes, forecasts)
    - incidents (incident vault)
    - manvault (documentation index)
    - autogen (generated capabilities)
    - reactive (reactive functions)
    - reboot (reboot intents)
    - mobile (mobile gateway)
    - docker (docker env values)
    - api (API keys)
    - deps (dependency preferences)

    Safe to call multiple times.  Requires that the connection pool
    has already been configured via ``configure_pool()``.
    """
    from elle.storage.config import SCHEMAS
    from elle.storage.migrate import run_migrations
    from elle.storage.provision import _import_schema_modules

    # Ensure all schema modules are imported so migrations are registered
    _import_schema_modules()

    for schema in SCHEMAS:
        try:
            with get_conn(schema=schema) as conn:
                run_migrations(conn, schema)
        except Exception as e:
            logger.error("Failed to initialize schema '%s': %s", schema, e)
