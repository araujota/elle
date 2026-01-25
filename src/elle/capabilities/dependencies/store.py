"""Dependency Preference Store - SQLite persistence for user preferences.

Stores user preferences for handling dependencies and installation history.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from elle.capabilities.dependencies.models import (
    DependencyPreference,
    InstallationResult,
    PreferenceChoice,
)
from elle.capabilities.dependencies.schema import (
    ensure_schema,
    get_connection,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================


def _serialize_datetime(dt: datetime) -> str:
    """Serialize datetime to ISO format string."""
    return dt.isoformat()


def _parse_datetime(s: str) -> datetime:
    """Parse ISO format string to datetime."""
    return datetime.fromisoformat(s)


def _json_dumps(obj: object) -> str:
    """Serialize object to JSON string."""
    return json.dumps(obj, default=str)


def _json_loads(s: str | None) -> object:
    """Parse JSON string to object."""
    if not s:
        return {}
    return json.loads(s)


# =============================================================================
# Store Class
# =============================================================================


class DependencyPreferenceStore:
    """SQLite store for dependency preferences and installation history.

    Persists user preferences for handling dependencies across sessions.

    Example:
        store = DependencyPreferenceStore()

        # Set preference
        store.set_preference("atspi", "always_install")

        # Get preference
        pref = store.get_preference("atspi")
        if pref == "always_install":
            # Auto-install without prompting
            ...

        # Record installation
        store.record_installation(result)
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

    # -------------------------------------------------------------------------
    # Preferences
    # -------------------------------------------------------------------------

    def get_preference(self, dependency: str) -> PreferenceChoice:
        """Get the user preference for a dependency.

        Args:
            dependency: The dependency name.

        Returns:
            The preference (defaults to 'ask' if not set).
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT preference FROM dependency_preferences WHERE dependency = ?",
            (dependency,),
        )

        row = cursor.fetchone()
        if row:
            return row["preference"]
        return "ask"

    def get_preference_full(self, dependency: str) -> DependencyPreference | None:
        """Get the full preference record for a dependency.

        Args:
            dependency: The dependency name.

        Returns:
            DependencyPreference if found, None otherwise.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT dependency, preference, created_at, updated_at
            FROM dependency_preferences
            WHERE dependency = ?
            """,
            (dependency,),
        )

        row = cursor.fetchone()
        if row:
            return DependencyPreference(
                dependency=row["dependency"],
                preference=row["preference"],
                created_at=_parse_datetime(row["created_at"]),
                updated_at=_parse_datetime(row["updated_at"]),
            )
        return None

    def set_preference(self, dependency: str, preference: PreferenceChoice) -> DependencyPreference:
        """Set the user preference for a dependency.

        Args:
            dependency: The dependency name.
            preference: The preference to set.

        Returns:
            The created/updated DependencyPreference.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        now = datetime.utcnow()

        # Check if exists
        cursor.execute(
            "SELECT created_at FROM dependency_preferences WHERE dependency = ?",
            (dependency,),
        )
        row = cursor.fetchone()

        if row:
            # Update existing
            cursor.execute(
                """
                UPDATE dependency_preferences
                SET preference = ?, updated_at = ?
                WHERE dependency = ?
                """,
                (preference, _serialize_datetime(now), dependency),
            )
            created_at = _parse_datetime(row["created_at"])
        else:
            # Insert new
            cursor.execute(
                """
                INSERT INTO dependency_preferences
                    (dependency, preference, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (dependency, preference, _serialize_datetime(now), _serialize_datetime(now)),
            )
            created_at = now

        conn.commit()

        logger.info(f"Set preference for '{dependency}': {preference}")

        return DependencyPreference(
            dependency=dependency,
            preference=preference,
            created_at=created_at,
            updated_at=now,
        )

    def delete_preference(self, dependency: str) -> bool:
        """Delete the preference for a dependency.

        Args:
            dependency: The dependency name.

        Returns:
            True if preference was deleted, False if not found.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM dependency_preferences WHERE dependency = ?",
            (dependency,),
        )
        conn.commit()

        deleted = cursor.rowcount > 0
        if deleted:
            logger.info(f"Deleted preference for '{dependency}'")

        return deleted

    def list_preferences(self) -> list[DependencyPreference]:
        """List all stored preferences.

        Returns:
            List of DependencyPreference objects.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT dependency, preference, created_at, updated_at
            FROM dependency_preferences
            ORDER BY dependency
            """
        )

        results: list[DependencyPreference] = []
        for row in cursor.fetchall():
            results.append(
                DependencyPreference(
                    dependency=row["dependency"],
                    preference=row["preference"],
                    created_at=_parse_datetime(row["created_at"]),
                    updated_at=_parse_datetime(row["updated_at"]),
                )
            )

        return results

    # -------------------------------------------------------------------------
    # Installation History
    # -------------------------------------------------------------------------

    def record_installation(self, result: InstallationResult) -> str:
        """Record an installation attempt in history.

        Args:
            result: The installation result to record.

        Returns:
            The generated record ID.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        record_id = str(uuid.uuid4())

        cursor.execute(
            """
            INSERT INTO installation_history
                (id, dependency, packages_json, success, error_message, installed_at, duration_sec)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                result.dependency,
                _json_dumps(list(result.installed_packages)),
                1 if result.success else 0,
                result.error_message,
                _serialize_datetime(result.installed_at),
                result.duration_sec,
            ),
        )
        conn.commit()

        logger.info(
            f"Recorded installation for '{result.dependency}': "
            f"{'success' if result.success else 'failed'}"
        )

        return record_id

    def get_installation_history(
        self,
        dependency: str | None = None,
        limit: int = 50,
    ) -> list[InstallationResult]:
        """Get installation history.

        Args:
            dependency: Filter by dependency name (optional).
            limit: Maximum records to return.

        Returns:
            List of InstallationResult objects, newest first.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        if dependency:
            cursor.execute(
                """
                SELECT id, dependency, packages_json, success, error_message, installed_at, duration_sec
                FROM installation_history
                WHERE dependency = ?
                ORDER BY installed_at DESC
                LIMIT ?
                """,
                (dependency, limit),
            )
        else:
            cursor.execute(
                """
                SELECT id, dependency, packages_json, success, error_message, installed_at, duration_sec
                FROM installation_history
                ORDER BY installed_at DESC
                LIMIT ?
                """,
                (limit,),
            )

        results: list[InstallationResult] = []
        for row in cursor.fetchall():
            packages = _json_loads(row["packages_json"])
            results.append(
                InstallationResult(
                    success=bool(row["success"]),
                    dependency=row["dependency"],
                    installed_packages=tuple(packages) if isinstance(packages, list) else (),
                    error_message=row["error_message"],
                    duration_sec=row["duration_sec"] or 0.0,
                    installed_at=_parse_datetime(row["installed_at"]),
                )
            )

        return results

    def was_recently_installed(
        self,
        dependency: str,
        within_hours: float = 24.0,
    ) -> bool:
        """Check if a dependency was recently installed successfully.

        Args:
            dependency: The dependency name.
            within_hours: Hours to look back.

        Returns:
            True if successfully installed within the time window.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        cutoff = datetime.utcnow()
        # Calculate cutoff time (simple approach, not handling DST)
        from datetime import timedelta

        cutoff = cutoff - timedelta(hours=within_hours)

        cursor.execute(
            """
            SELECT 1 FROM installation_history
            WHERE dependency = ?
              AND success = 1
              AND installed_at > ?
            LIMIT 1
            """,
            (dependency, _serialize_datetime(cutoff)),
        )

        return cursor.fetchone() is not None


# =============================================================================
# Module-level Convenience Functions
# =============================================================================

_store: DependencyPreferenceStore | None = None


def get_store() -> DependencyPreferenceStore:
    """Get the global DependencyPreferenceStore instance.

    Returns:
        The global store instance.
    """
    global _store
    if _store is None:
        _store = DependencyPreferenceStore()
    return _store


def reset_store() -> None:
    """Reset the global store.

    Useful for testing.
    """
    global _store
    if _store is not None:
        _store.close()
    _store = None


def get_preference(dependency: str) -> PreferenceChoice:
    """Get the user preference for a dependency.

    Convenience function using the global store.

    Args:
        dependency: The dependency name.

    Returns:
        The preference.
    """
    return get_store().get_preference(dependency)


def set_preference(dependency: str, preference: PreferenceChoice) -> DependencyPreference:
    """Set the user preference for a dependency.

    Convenience function using the global store.

    Args:
        dependency: The dependency name.
        preference: The preference to set.

    Returns:
        The DependencyPreference.
    """
    return get_store().set_preference(dependency, preference)
