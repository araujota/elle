"""PostgreSQL connection configuration for ELLE.

Provides a frozen dataclass that holds all connection parameters.
Supports Unix socket (peer auth) by default, with optional TCP.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# All schemas managed in the single 'elle' database
SCHEMAS = (
    "telemetry",
    "incidents",
    "manvault",
    "autogen",
    "reactive",
    "reboot",
    "mobile",
    "docker",
    "api",
    "deps",
)

# Extensions required in the database
EXTENSIONS = ("vector", "pgcrypto", "pg_trgm")


@dataclass(frozen=True)
class PostgresConfig:
    """PostgreSQL connection configuration.

    Defaults to Unix socket with peer authentication,
    which requires no password when the OS user matches
    the PostgreSQL role.

    Attributes:
        host: Hostname or IP.  Empty string = Unix socket.
        port: TCP port (ignored for Unix sockets).
        dbname: Database name.
        user: PostgreSQL role name.
        password: Password (empty for peer auth).
        socket_dir: Directory containing the Unix socket.
        min_pool_size: Minimum connections in the pool.
        max_pool_size: Maximum connections in the pool.
        max_idle_sec: Seconds before idle connections are reaped.
        sslmode: SSL mode for TCP connections.
        encryption_key_path: Path to the pgcrypto symmetric key file.
    """

    host: str = ""
    port: int = 5432
    dbname: str = "elle"
    user: str = "elle"
    password: str = ""
    socket_dir: str = "/var/run/postgresql"
    min_pool_size: int = 2
    max_pool_size: int = 10
    max_idle_sec: float = 300.0
    sslmode: str = "prefer"
    connect_timeout: int = 10
    encryption_key_path: str = field(default_factory=lambda: os.environ.get("ELLE_DB_KEY", "/etc/elle/db.key"))

    @property
    def conninfo(self) -> str:
        """Build a libpq connection string.

        Returns:
            A connection string suitable for psycopg.connect().
        """
        if self.host:
            parts = [
                f"host={self.host}",
                f"port={self.port}",
                f"dbname={self.dbname}",
                f"user={self.user}",
                f"sslmode={self.sslmode}",
                f"connect_timeout={self.connect_timeout}",
            ]
            if self.password:
                parts.append(f"password={self.password}")
        else:
            parts = [
                f"host={self.socket_dir}",
                f"dbname={self.dbname}",
                f"user={self.user}",
                f"connect_timeout={self.connect_timeout}",
            ]
        return " ".join(parts)

    @property
    def conninfo_safe(self) -> str:
        """Build a connection string with the password redacted for logging."""
        import re

        info = self.conninfo
        return re.sub(r"password=\S+", "password=***", info)

    def __repr__(self) -> str:
        """Repr with password redacted to prevent leakage in tracebacks."""
        return f"PostgresConfig(conninfo={self.conninfo_safe!r})"
