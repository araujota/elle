"""Dependency Preference Store - PostgreSQL persistence for user preferences.

Stores user preferences for handling dependencies and installation history.

Storage backend: PostgreSQL via psycopg (schema ``deps``).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import cast

from elle.capabilities.dependencies.models import (
    DependencyPreference,
    InstallationResult,
    PreferenceChoice,
)
from elle.storage.engine import get_conn
from elle.storage.helpers import json_dumps, json_loads, parse_datetime, serialize_datetime

logger = logging.getLogger(__name__)

PG_SCHEMA = "deps"



# =============================================================================
# Store Class
# =============================================================================


class DependencyPreferenceStore:
    """PostgreSQL store for dependency preferences and installation history.

    Persists user preferences for handling dependencies across sessions.

    Example:
        store = DependencyPreferenceStore()

        # Set preference
        store.set_preference("augeas", "always_install")

        # Get preference
        pref = store.get_preference("augeas")
        if pref == "always_install":
            # Auto-install without prompting
            ...

        # Record installation
        store.record_installation(result)
    """

    def __init__(self) -> None:
        """Initialize the store."""

    def close(self) -> None:
        """Close the store (no-op for pooled connections)."""

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
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT preference FROM dependency_preferences WHERE dependency = %s",
                (dependency,),
            )

            row = cursor.fetchone()
            if row:
                pref = row["preference"]
                if pref in ("always_install", "always_skip", "ask"):
                    return cast(PreferenceChoice, pref)
            return "ask"

    def get_preference_full(self, dependency: str) -> DependencyPreference | None:
        """Get the full preference record for a dependency.

        Args:
            dependency: The dependency name.

        Returns:
            DependencyPreference if found, None otherwise.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT dependency, preference, created_at, updated_at
                FROM dependency_preferences
                WHERE dependency = %s
                """,
                (dependency,),
            )

            row = cursor.fetchone()
            if row:
                created_at = row["created_at"]
                if isinstance(created_at, str):
                    created_at = parse_datetime(created_at)
                updated_at = row["updated_at"]
                if isinstance(updated_at, str):
                    updated_at = parse_datetime(updated_at)
                return DependencyPreference(
                    dependency=row["dependency"],
                    preference=row["preference"],
                    created_at=created_at,
                    updated_at=updated_at,
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
        now = datetime.utcnow()

        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()

            # Check if exists
            cursor.execute(
                "SELECT created_at FROM dependency_preferences WHERE dependency = %s",
                (dependency,),
            )
            row = cursor.fetchone()

            if row:
                # Update existing
                cursor.execute(
                    """
                    UPDATE dependency_preferences
                    SET preference = %s, updated_at = %s
                    WHERE dependency = %s
                    """,
                    (preference, serialize_datetime(now), dependency),
                )
                created_at_val = row["created_at"]
                if isinstance(created_at_val, str):
                    created_at = parse_datetime(created_at_val)
                else:
                    created_at = created_at_val
            else:
                # Insert new
                cursor.execute(
                    """
                    INSERT INTO dependency_preferences
                        (dependency, preference, created_at, updated_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (dependency, preference, serialize_datetime(now), serialize_datetime(now)),
                )
                created_at = now

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
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM dependency_preferences WHERE dependency = %s",
                (dependency,),
            )
            deleted = cursor.rowcount > 0

        if deleted:
            logger.info(f"Deleted preference for '{dependency}'")

        return deleted

    def list_preferences(self) -> list[DependencyPreference]:
        """List all stored preferences.

        Returns:
            List of DependencyPreference objects.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
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
                created_at = row["created_at"]
                if isinstance(created_at, str):
                    created_at = parse_datetime(created_at)
                updated_at = row["updated_at"]
                if isinstance(updated_at, str):
                    updated_at = parse_datetime(updated_at)
                results.append(
                    DependencyPreference(
                        dependency=row["dependency"],
                        preference=row["preference"],
                        created_at=created_at,
                        updated_at=updated_at,
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
        record_id = str(uuid.uuid4())

        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO installation_history
                    (id, dependency, packages_json, success, error_message, installed_at, duration_sec)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record_id,
                    result.dependency,
                    json_dumps(list(result.installed_packages)),
                    result.success,
                    result.error_message,
                    serialize_datetime(result.installed_at),
                    result.duration_sec,
                ),
            )

        logger.info(f"Recorded installation for '{result.dependency}': {'success' if result.success else 'failed'}")

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
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()

            if dependency:
                cursor.execute(
                    """
                    SELECT id, dependency, packages_json, success, error_message, installed_at, duration_sec
                    FROM installation_history
                    WHERE dependency = %s
                    ORDER BY installed_at DESC
                    LIMIT %s
                    """,
                    (dependency, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, dependency, packages_json, success, error_message, installed_at, duration_sec
                    FROM installation_history
                    ORDER BY installed_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )

            results: list[InstallationResult] = []
            for row in cursor.fetchall():
                packages = json_loads(row["packages_json"])
                installed_at = row["installed_at"]
                if isinstance(installed_at, str):
                    installed_at = parse_datetime(installed_at)
                results.append(
                    InstallationResult(
                        success=bool(row["success"]),
                        dependency=row["dependency"],
                        installed_packages=tuple(packages) if isinstance(packages, list) else (),
                        error_message=row["error_message"],
                        duration_sec=row["duration_sec"] or 0.0,
                        installed_at=installed_at,
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
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(hours=within_hours)

        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 1 FROM installation_history
                WHERE dependency = %s
                  AND success = TRUE
                  AND installed_at > %s
                LIMIT 1
                """,
                (dependency, serialize_datetime(cutoff)),
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
