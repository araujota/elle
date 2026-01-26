"""Docker Environment Variable Store.

SQLite-based persistence for remembering environment variable values
per image family across sessions.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from elle.cli.docker.env_models import EnvVarValue, SavedEnvVar
from elle.cli.docker.env_profiles import extract_image_family

if TYPE_CHECKING:
    pass


# Schema version for migrations
SCHEMA_VERSION = 1

# Default database path
DEFAULT_DB_PATH = Path("/var/lib/elle/docker_env.db")


# =============================================================================
# Schema
# =============================================================================

ENV_VALUES_TABLE = """
CREATE TABLE IF NOT EXISTS docker_env_values (
    image_family TEXT NOT NULL,
    var_name TEXT NOT NULL,
    var_value TEXT NOT NULL,
    is_sensitive INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (image_family, var_name)
)
"""

META_TABLE = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_env_values_family ON docker_env_values(image_family)",
    "CREATE INDEX IF NOT EXISTS idx_env_values_updated ON docker_env_values(updated_at)",
]


# =============================================================================
# Database Connection
# =============================================================================


def get_db_path() -> Path:
    """Get the Docker environment store database path.

    Returns:
        Path to the database file.
    """
    return DEFAULT_DB_PATH


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a connection to the environment store database.

    Creates the database directory if it doesn't exist.

    Args:
        db_path: Override database path (for testing).

    Returns:
        SQLite connection with row factory set.
    """
    path = db_path or get_db_path()

    # For non-system paths, ensure directory exists
    if not str(path).startswith("/var/lib"):
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Initialize the database schema.

    Creates all tables and indexes if they don't exist.
    Safe to call multiple times.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Create tables
    cursor.execute(ENV_VALUES_TABLE)
    cursor.execute(META_TABLE)

    # Create indexes
    for index_sql in INDEXES:
        cursor.execute(index_sql)

    # Set schema version
    cursor.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )

    conn.commit()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure the schema is initialized.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM meta WHERE key = 'schema_version'")
        row = cursor.fetchone()
        if not row:
            init_schema(conn)
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        init_schema(conn)


# =============================================================================
# Store Operations
# =============================================================================


class DockerEnvStore:
    """SQLite store for Docker environment variable values.

    Persists environment variable values per image family for reuse
    across sessions. Sensitive values are stored but flagged.

    Example:
        store = DockerEnvStore()

        # Save values for postgres
        store.save_values("postgres", [
            EnvVarValue(name="POSTGRES_PASSWORD", value="secret", sensitive=True),
            EnvVarValue(name="POSTGRES_USER", value="myuser"),
        ])

        # Retrieve saved values
        saved = store.get_saved_values("postgres")
    """

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialize the store.

        Args:
            db_path: Override database path (for testing).
        """
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create the database connection."""
        if self._conn is None:
            self._conn = get_connection(self._db_path)
            ensure_schema(self._conn)
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def save_values(
        self,
        image: str,
        values: list[EnvVarValue] | tuple[EnvVarValue, ...],
    ) -> None:
        """Save environment variable values for an image family.

        Args:
            image: Docker image name (family will be extracted).
            values: List of EnvVarValue to save.
        """
        family = extract_image_family(image)
        conn = self._get_conn()
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()

        for value in values:
            cursor.execute(
                """
                INSERT OR REPLACE INTO docker_env_values
                    (image_family, var_name, var_value, is_sensitive, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (family, value.name, value.value, 1 if value.sensitive else 0, now),
            )

        conn.commit()

    def get_saved_values(self, image: str) -> tuple[SavedEnvVar, ...]:
        """Get saved environment variable values for an image family.

        Args:
            image: Docker image name (family will be extracted).

        Returns:
            Tuple of SavedEnvVar with previously stored values.
        """
        family = extract_image_family(image)
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT image_family, var_name, var_value, is_sensitive, updated_at
            FROM docker_env_values
            WHERE image_family = ?
            ORDER BY var_name
            """,
            (family,),
        )

        results: list[SavedEnvVar] = []
        for row in cursor.fetchall():
            results.append(
                SavedEnvVar(
                    image_family=row["image_family"],
                    name=row["var_name"],
                    value=row["var_value"],
                    sensitive=bool(row["is_sensitive"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )
            )

        return tuple(results)

    def get_saved_value(self, image: str, var_name: str) -> SavedEnvVar | None:
        """Get a single saved environment variable value.

        Args:
            image: Docker image name.
            var_name: Environment variable name.

        Returns:
            SavedEnvVar if found, None otherwise.
        """
        family = extract_image_family(image)
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT image_family, var_name, var_value, is_sensitive, updated_at
            FROM docker_env_values
            WHERE image_family = ? AND var_name = ?
            """,
            (family, var_name),
        )

        row = cursor.fetchone()
        if row:
            return SavedEnvVar(
                image_family=row["image_family"],
                name=row["var_name"],
                value=row["var_value"],
                sensitive=bool(row["is_sensitive"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
        return None

    def clear_values(self, image: str) -> int:
        """Clear all saved values for an image family.

        Args:
            image: Docker image name.

        Returns:
            Number of values deleted.
        """
        family = extract_image_family(image)
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM docker_env_values WHERE image_family = ?",
            (family,),
        )
        conn.commit()

        return cursor.rowcount

    def clear_value(self, image: str, var_name: str) -> bool:
        """Clear a specific saved value.

        Args:
            image: Docker image name.
            var_name: Environment variable name.

        Returns:
            True if a value was deleted, False otherwise.
        """
        family = extract_image_family(image)
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM docker_env_values WHERE image_family = ? AND var_name = ?",
            (family, var_name),
        )
        conn.commit()

        return cursor.rowcount > 0

    def list_families(self) -> tuple[str, ...]:
        """List all image families with saved values.

        Returns:
            Tuple of image family names.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT DISTINCT image_family FROM docker_env_values ORDER BY image_family")

        return tuple(row["image_family"] for row in cursor.fetchall())

    def clear_all(self) -> int:
        """Clear all saved values.

        Returns:
            Number of values deleted.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM docker_env_values")
        conn.commit()

        return cursor.rowcount

    def has_saved_values(self, image: str) -> bool:
        """Check if there are any saved values for an image family.

        Args:
            image: Docker image name.

        Returns:
            True if saved values exist.
        """
        family = extract_image_family(image)
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT 1 FROM docker_env_values WHERE image_family = ? LIMIT 1",
            (family,),
        )

        return cursor.fetchone() is not None


# =============================================================================
# Module-level Convenience Functions
# =============================================================================

# Global store instance
_store: DockerEnvStore | None = None


def get_store() -> DockerEnvStore:
    """Get the global DockerEnvStore instance.

    Returns:
        The global store instance.
    """
    global _store
    if _store is None:
        _store = DockerEnvStore()
    return _store


def save_env_values(
    image: str,
    values: list[EnvVarValue] | tuple[EnvVarValue, ...],
) -> None:
    """Save environment variable values for an image family.

    Args:
        image: Docker image name.
        values: Values to save.
    """
    get_store().save_values(image, values)


def get_saved_env_values(image: str) -> tuple[SavedEnvVar, ...]:
    """Get saved environment variable values for an image family.

    Args:
        image: Docker image name.

    Returns:
        Tuple of saved values.
    """
    return get_store().get_saved_values(image)


def clear_saved_env_values(image: str) -> int:
    """Clear saved values for an image family.

    Args:
        image: Docker image name.

    Returns:
        Number of values deleted.
    """
    return get_store().clear_values(image)
