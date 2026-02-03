"""Docker Environment Variable Store.

PostgreSQL-based persistence for remembering environment variable values
per image family across sessions.

Storage backend: PostgreSQL via psycopg (schema ``docker``).
"""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg

from elle.cli.docker.env_models import EnvVarValue, SavedEnvVar
from elle.cli.docker.env_profiles import extract_image_family
from elle.storage.engine import get_conn
from elle.storage.migrate import register_migration

PG_SCHEMA = "docker"


# =============================================================================
# Migration registration
# =============================================================================


def _migrate_v1(conn: psycopg.Connection) -> None:
    """Create initial docker env tables."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS docker_env_values (
            image_family TEXT NOT NULL,
            var_name TEXT NOT NULL,
            var_value TEXT NOT NULL,
            is_sensitive BOOLEAN DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (image_family, var_name)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_env_values_family ON docker_env_values(image_family)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_env_values_updated ON docker_env_values(updated_at)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)


register_migration(PG_SCHEMA, 1, _migrate_v1)


# =============================================================================
# Store Operations
# =============================================================================


class DockerEnvStore:
    """PostgreSQL store for Docker environment variable values.

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

    def __init__(self) -> None:
        """Initialize the store."""

    def close(self) -> None:
        """Close the store (no-op for pooled connections)."""

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
        now = datetime.now(timezone.utc).isoformat()

        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            for value in values:
                cursor.execute(
                    """
                    INSERT INTO docker_env_values
                        (image_family, var_name, var_value, is_sensitive, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (image_family, var_name) DO UPDATE
                    SET var_value = EXCLUDED.var_value,
                        is_sensitive = EXCLUDED.is_sensitive,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (family, value.name, value.value, value.sensitive, now),
                )

    def get_saved_values(self, image: str) -> tuple[SavedEnvVar, ...]:
        """Get saved environment variable values for an image family.

        Args:
            image: Docker image name (family will be extracted).

        Returns:
            Tuple of SavedEnvVar with previously stored values.
        """
        family = extract_image_family(image)

        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT image_family, var_name, var_value, is_sensitive, updated_at
                FROM docker_env_values
                WHERE image_family = %s
                ORDER BY var_name
                """,
                (family,),
            )

            results: list[SavedEnvVar] = []
            for row in cursor.fetchall():
                updated_at = row["updated_at"]
                if isinstance(updated_at, str):
                    updated_at = datetime.fromisoformat(updated_at)
                results.append(
                    SavedEnvVar(
                        image_family=row["image_family"],
                        name=row["var_name"],
                        value=row["var_value"],
                        sensitive=bool(row["is_sensitive"]),
                        updated_at=updated_at,
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

        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT image_family, var_name, var_value, is_sensitive, updated_at
                FROM docker_env_values
                WHERE image_family = %s AND var_name = %s
                """,
                (family, var_name),
            )

            row = cursor.fetchone()
            if row:
                updated_at = row["updated_at"]
                if isinstance(updated_at, str):
                    updated_at = datetime.fromisoformat(updated_at)
                return SavedEnvVar(
                    image_family=row["image_family"],
                    name=row["var_name"],
                    value=row["var_value"],
                    sensitive=bool(row["is_sensitive"]),
                    updated_at=updated_at,
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

        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM docker_env_values WHERE image_family = %s",
                (family,),
            )
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

        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM docker_env_values WHERE image_family = %s AND var_name = %s",
                (family, var_name),
            )
            return cursor.rowcount > 0

    def list_families(self) -> tuple[str, ...]:
        """List all image families with saved values.

        Returns:
            Tuple of image family names.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT image_family FROM docker_env_values ORDER BY image_family")
            return tuple(row["image_family"] for row in cursor.fetchall())

    def clear_all(self) -> int:
        """Clear all saved values.

        Returns:
            Number of values deleted.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM docker_env_values")
            return cursor.rowcount

    def has_saved_values(self, image: str) -> bool:
        """Check if there are any saved values for an image family.

        Args:
            image: Docker image name.

        Returns:
            True if saved values exist.
        """
        family = extract_image_family(image)

        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM docker_env_values WHERE image_family = %s LIMIT 1",
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
