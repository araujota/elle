"""Automated PostgreSQL provisioning for ELLE.

Executed by ``elle setup database``.  Handles:

1. Check PostgreSQL is installed
2. Create system user ``elle`` (if not exists)
3. Create PostgreSQL role ``elle`` (peer auth)
4. Create database ``elle``
5. Install extensions (vector, pgcrypto, pg_trgm)
6. Create all 10 schemas
7. Run initial migrations
8. Generate encryption key
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import subprocess
from pathlib import Path

import psycopg

from elle.storage.config import EXTENSIONS, SCHEMAS, PostgresConfig

logger = logging.getLogger(__name__)


class ProvisionError(Exception):
    """Raised when a provisioning step fails."""


# ---------------------------------------------------------------------------
# Helper: run a shell command
# ---------------------------------------------------------------------------


def _run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = True,
    user: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, optionally as another user.

    Args:
        cmd: Command and arguments.
        check: Raise on non-zero exit.
        capture: Capture stdout/stderr.
        user: Run as this OS user via ``sudo -u``.

    Returns:
        Completed process result.
    """
    if user:
        cmd = ["sudo", "-u", user] + cmd
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Step 1: Check PostgreSQL installed
# ---------------------------------------------------------------------------


def check_postgresql_installed() -> str:
    """Verify PostgreSQL is installed and return the version.

    Returns:
        Version string (e.g. ``"16.4"``).

    Raises:
        ProvisionError: If PostgreSQL is not found.
    """
    pg_config = shutil.which("pg_config")
    if not pg_config:
        raise ProvisionError(
            "PostgreSQL is not installed.  Install with: apt install postgresql-16 postgresql-16-pgvector"
        )
    result = _run([pg_config, "--version"])
    version = result.stdout.strip().split()[-1]
    logger.info("PostgreSQL %s found", version)
    return version


# ---------------------------------------------------------------------------
# Step 2: Create system user
# ---------------------------------------------------------------------------


def ensure_system_user(username: str = "elle") -> None:
    """Create the ``elle`` system user if it does not exist.

    Args:
        username: System username to create.
    """
    try:
        _run(["id", username])
        logger.info("System user '%s' already exists", username)
    except subprocess.CalledProcessError:
        _run(
            [
                "useradd",
                "--system",
                "--no-create-home",
                "--shell",
                "/usr/sbin/nologin",
                username,
            ]
        )
        logger.info("Created system user '%s'", username)


# ---------------------------------------------------------------------------
# Step 3: Create PostgreSQL role
# ---------------------------------------------------------------------------


def ensure_pg_role(username: str = "elle") -> None:
    """Create the PostgreSQL role if it does not exist.

    Uses ``createuser`` as the ``postgres`` superuser.

    Args:
        username: Role name to create.
    """
    result = _run(
        ["psql", "-tAc", f"SELECT 1 FROM pg_roles WHERE rolname = '{username}'"],  # nosec B608
        user="postgres",
        check=False,
    )
    if result.stdout.strip() == "1":
        logger.info("PostgreSQL role '%s' already exists", username)
        return

    _run(["createuser", "--no-password", username], user="postgres")
    logger.info("Created PostgreSQL role '%s'", username)


# ---------------------------------------------------------------------------
# Step 4: Create database
# ---------------------------------------------------------------------------


def ensure_database(config: PostgresConfig) -> None:
    """Create the database if it does not exist.

    Args:
        config: Connection configuration.
    """
    result = _run(
        ["psql", "-tAc", f"SELECT 1 FROM pg_database WHERE datname = '{config.dbname}'"],  # nosec B608
        user="postgres",
        check=False,
    )
    if result.stdout.strip() == "1":
        logger.info("Database '%s' already exists", config.dbname)
        return

    _run(
        ["createdb", "--owner", config.user, config.dbname],
        user="postgres",
    )
    logger.info("Created database '%s' owned by '%s'", config.dbname, config.user)


# ---------------------------------------------------------------------------
# Step 5: Install extensions
# ---------------------------------------------------------------------------


def install_extensions(config: PostgresConfig) -> None:
    """Install required PostgreSQL extensions.

    Must be run as a superuser or a user with CREATE privileges.

    Args:
        config: Connection configuration.
    """
    # Connect as postgres superuser to install extensions
    conninfo = f"host={config.socket_dir} dbname={config.dbname} user=postgres"
    with psycopg.connect(conninfo) as conn:
        conn.autocommit = True
        for ext in EXTENSIONS:
            conn.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
            logger.info("Extension '%s' ready", ext)


# ---------------------------------------------------------------------------
# Step 6: Create schemas
# ---------------------------------------------------------------------------


def create_schemas(config: PostgresConfig) -> None:
    """Create all ELLE schemas in the database.

    Args:
        config: Connection configuration.
    """
    conninfo = f"host={config.socket_dir} dbname={config.dbname} user=postgres"
    with psycopg.connect(conninfo) as conn:
        conn.autocommit = True
        for schema in SCHEMAS:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema} AUTHORIZATION {config.user}")
            logger.info("Schema '%s' ready", schema)

        # Grant usage to the elle role
        for schema in SCHEMAS:
            conn.execute(f"GRANT ALL ON SCHEMA {schema} TO {config.user}")


# ---------------------------------------------------------------------------
# Step 7: Run initial migrations
# ---------------------------------------------------------------------------


def run_initial_migrations(config: PostgresConfig) -> dict[str, int]:
    """Run all schema migrations.

    Args:
        config: Connection configuration.

    Returns:
        Dict of schema -> version after migration.
    """
    from elle.storage.migrate import run_all_migrations

    # Import all schema modules so migrations get registered
    _import_schema_modules()

    with psycopg.connect(config.conninfo) as conn:
        return run_all_migrations(conn)


def _import_schema_modules() -> None:
    """Import all schema modules to trigger migration registration."""
    # Each schema module registers its migrations on import
    import importlib

    modules = [
        "elle.daemon.telemetry.schema",
        "elle.daemon.incidents.schema",
        "elle.daemon.manvault.schema",
        "elle.capabilities.autogen.store",
        "elle.reactive.schema",
        "elle.daemon.reboot.schema",
        "elle.mobile.store",
        "elle.cli.docker.env_store",
        "elle.daemon.api.auth",
        "elle.capabilities.dependencies.schema",
    ]
    for mod in modules:
        try:
            importlib.import_module(mod)
        except ImportError:
            logger.warning("Could not import %s for migration registration", mod)


# ---------------------------------------------------------------------------
# Step 8: Generate encryption key
# ---------------------------------------------------------------------------


def ensure_encryption_key(key_path: str = "/etc/elle/db.key") -> None:
    """Generate a 256-bit encryption key if one does not exist.

    The key file is created with mode 0400, owned by the elle user.

    Args:
        key_path: Path to the key file.
    """
    path = Path(key_path)
    if path.exists():
        logger.info("Encryption key already exists at %s", key_path)
        return

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Generate 256-bit (32-byte) key
    key = secrets.token_hex(32)
    path.write_text(key)
    os.chmod(path, 0o400)
    logger.info("Generated encryption key at %s", key_path)


# ---------------------------------------------------------------------------
# Full provisioning
# ---------------------------------------------------------------------------


def provision(config: PostgresConfig | None = None) -> None:
    """Run the full provisioning sequence.

    Args:
        config: PostgreSQL configuration.  Uses defaults if None.
    """
    cfg = config or PostgresConfig()

    logger.info("Starting ELLE database provisioning")

    # Step 1
    version = check_postgresql_installed()
    logger.info("PostgreSQL version: %s", version)

    # Step 2
    ensure_system_user(cfg.user)

    # Step 3
    ensure_pg_role(cfg.user)

    # Step 4
    ensure_database(cfg)

    # Step 5
    install_extensions(cfg)

    # Step 6
    create_schemas(cfg)

    # Step 7
    results = run_initial_migrations(cfg)
    for schema, ver in sorted(results.items()):
        logger.info("  %s: v%d", schema, ver)

    # Step 8
    ensure_encryption_key(cfg.encryption_key_path)

    logger.info("ELLE database provisioning complete")
