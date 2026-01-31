"""PostgreSQL storage for ELLE Mobile Gateway.

This module provides persistent storage for:
- Paired devices and their status
- Elevation grants
- Pairing tokens

Storage backend: PostgreSQL via psycopg (schema ``mobile``).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import psycopg

from elle.mobile.config import MobileGatewayConfig, get_mobile_config
from elle.mobile.models import (
    DeviceStatus,
    Elevation,
    MobileRole,
    PairedDevice,
    PairingToken,
)
from elle.storage.engine import get_conn
from elle.storage.migrate import register_migration

logger = logging.getLogger(__name__)

PG_SCHEMA = "mobile"


# =============================================================================
# Migration registration
# =============================================================================


def _migrate_v1(conn: psycopg.Connection) -> None:
    """Create initial mobile gateway tables."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'mobile_readonly',
            status TEXT NOT NULL DEFAULT 'pending',
            cert_fingerprint TEXT NOT NULL,
            paired_at TIMESTAMPTZ,
            last_seen_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS elevations (
            id SERIAL PRIMARY KEY,
            device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
            elevated_role TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            granted_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_elevations_device ON elevations(device_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_elevations_expires ON elevations(expires_at)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS pairing_tokens (
            token TEXT PRIMARY KEY,
            expires_at TIMESTAMPTZ NOT NULL,
            role TEXT NOT NULL DEFAULT 'mobile_readonly',
            used BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tokens_expires ON pairing_tokens(expires_at)")


register_migration(PG_SCHEMA, 1, _migrate_v1)


class MobileStore:
    """PostgreSQL storage for mobile gateway data.

    Provides CRUD operations for devices, elevations, and pairing tokens.
    """

    def __init__(self, config: MobileGatewayConfig | None = None):
        """Initialize the store.

        Args:
            config: Mobile gateway configuration. Uses global config if None.
        """
        self.config = config or get_mobile_config()

    # =========================================================================
    # Device operations
    # =========================================================================

    def create_device(self, device: PairedDevice) -> PairedDevice:
        """Create a new device record.

        Args:
            device: Device to create.

        Returns:
            The created device.

        Raises:
            ValueError: If device_id already exists.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO devices (
                        device_id, name, role, status, cert_fingerprint,
                        paired_at, last_seen_at, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        device.device_id,
                        device.name,
                        device.role.value,
                        device.status.value,
                        device.cert_fingerprint,
                        device.paired_at.isoformat() if device.paired_at else None,
                        device.last_seen_at.isoformat() if device.last_seen_at else None,
                        device.created_at.isoformat(),
                    ),
                )
                return device
            except psycopg.errors.UniqueViolation as e:
                raise ValueError(f"Device {device.device_id} already exists") from e

    def get_device(self, device_id: str) -> PairedDevice | None:
        """Get a device by ID.

        Args:
            device_id: Device identifier.

        Returns:
            Device if found, None otherwise.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.execute("SELECT * FROM devices WHERE device_id = %s", (device_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_device(row)

    def get_device_by_fingerprint(self, fingerprint: str) -> PairedDevice | None:
        """Get a device by certificate fingerprint.

        Args:
            fingerprint: SHA-256 certificate fingerprint.

        Returns:
            Device if found, None otherwise.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.execute("SELECT * FROM devices WHERE cert_fingerprint = %s", (fingerprint,))
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_device(row)

    def list_devices(self, status: DeviceStatus | None = None) -> list[PairedDevice]:
        """List all devices, optionally filtered by status.

        Args:
            status: Optional status filter.

        Returns:
            List of devices.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            if status:
                cursor = conn.execute(
                    "SELECT * FROM devices WHERE status = %s ORDER BY created_at DESC",
                    (status.value,),
                )
            else:
                cursor = conn.execute("SELECT * FROM devices ORDER BY created_at DESC")
            return [self._row_to_device(row) for row in cursor.fetchall()]

    def update_device(
        self,
        device_id: str,
        *,
        name: str | None = None,
        role: MobileRole | None = None,
        status: DeviceStatus | None = None,
        paired_at: datetime | None = None,
        last_seen_at: datetime | None = None,
    ) -> PairedDevice | None:
        """Update a device's attributes.

        Args:
            device_id: Device to update.
            name: New name (optional).
            role: New role (optional).
            status: New status (optional).
            paired_at: Pairing timestamp (optional).
            last_seen_at: Last seen timestamp (optional).

        Returns:
            Updated device, or None if not found.
        """
        updates: list[str] = []
        params: list[str | None] = []

        if name is not None:
            updates.append("name = %s")
            params.append(name)
        if role is not None:
            updates.append("role = %s")
            params.append(role.value)
        if status is not None:
            updates.append("status = %s")
            params.append(status.value)
        if paired_at is not None:
            updates.append("paired_at = %s")
            params.append(paired_at.isoformat())
        if last_seen_at is not None:
            updates.append("last_seen_at = %s")
            params.append(last_seen_at.isoformat())

        if not updates:
            return self.get_device(device_id)

        params.append(device_id)

        with get_conn(schema=PG_SCHEMA) as conn:
            conn.execute(
                f"UPDATE devices SET {', '.join(updates)} WHERE device_id = %s",  # nosec B608
                params,
            )
            return self.get_device(device_id)

    def delete_device(self, device_id: str) -> bool:
        """Delete a device (cascades to elevations).

        Args:
            device_id: Device to delete.

        Returns:
            True if device was deleted, False if not found.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.execute("DELETE FROM devices WHERE device_id = %s", (device_id,))
            return cursor.rowcount > 0

    def count_devices(self, status: DeviceStatus | None = None) -> int:
        """Count devices, optionally filtered by status.

        Args:
            status: Optional status filter.

        Returns:
            Number of devices.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            if status:
                cursor = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM devices WHERE status = %s",
                    (status.value,),
                )
            else:
                cursor = conn.execute("SELECT COUNT(*) AS cnt FROM devices")
            row = cursor.fetchone()
            return int(row["cnt"]) if row else 0

    def _row_to_device(self, row: dict[str, Any]) -> PairedDevice:
        """Convert a database row to a PairedDevice."""
        paired_at = row["paired_at"]
        if isinstance(paired_at, str):
            paired_at = datetime.fromisoformat(paired_at)

        last_seen_at = row["last_seen_at"]
        if isinstance(last_seen_at, str):
            last_seen_at = datetime.fromisoformat(last_seen_at)

        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        return PairedDevice(
            device_id=row["device_id"],
            name=row["name"],
            role=MobileRole(row["role"]),
            status=DeviceStatus(row["status"]),
            cert_fingerprint=row["cert_fingerprint"],
            paired_at=paired_at,
            last_seen_at=last_seen_at,
            created_at=created_at,
        )

    # =========================================================================
    # Elevation operations
    # =========================================================================

    def create_elevation(self, elevation: Elevation) -> Elevation:
        """Create a new elevation grant.

        Args:
            elevation: Elevation to create.

        Returns:
            The created elevation.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            conn.execute(
                """
                INSERT INTO elevations (
                    device_id, elevated_role, expires_at, granted_by, created_at
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    elevation.device_id,
                    elevation.elevated_role.value,
                    elevation.expires_at.isoformat(),
                    elevation.granted_by,
                    elevation.created_at.isoformat(),
                ),
            )
            return elevation

    def get_active_elevation(self, device_id: str) -> Elevation | None:
        """Get the active elevation for a device.

        Args:
            device_id: Device identifier.

        Returns:
            Active elevation if one exists, None otherwise.
        """
        now = datetime.utcnow().isoformat()
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.execute(
                """
                SELECT * FROM elevations
                WHERE device_id = %s AND expires_at > %s
                ORDER BY expires_at DESC LIMIT 1
                """,
                (device_id, now),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_elevation(row)

    def list_active_elevations(self) -> list[Elevation]:
        """List all active (non-expired) elevations.

        Returns:
            List of active elevations.
        """
        now = datetime.utcnow().isoformat()
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.execute(
                "SELECT * FROM elevations WHERE expires_at > %s ORDER BY expires_at",
                (now,),
            )
            return [self._row_to_elevation(row) for row in cursor.fetchall()]

    def revoke_elevation(self, device_id: str) -> bool:
        """Revoke all elevations for a device.

        Args:
            device_id: Device whose elevations to revoke.

        Returns:
            True if any elevations were revoked.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.execute("DELETE FROM elevations WHERE device_id = %s", (device_id,))
            return cursor.rowcount > 0

    def cleanup_expired_elevations(self) -> int:
        """Remove expired elevation records.

        Returns:
            Number of records removed.
        """
        now = datetime.utcnow().isoformat()
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.execute("DELETE FROM elevations WHERE expires_at <= %s", (now,))
            return cursor.rowcount

    def _row_to_elevation(self, row: dict[str, Any]) -> Elevation:
        """Convert a database row to an Elevation."""
        expires_at = row["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)

        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        return Elevation(
            device_id=row["device_id"],
            elevated_role=MobileRole(row["elevated_role"]),
            expires_at=expires_at,
            granted_by=row["granted_by"],
            created_at=created_at,
        )

    # =========================================================================
    # Pairing token operations
    # =========================================================================

    def create_token(self, token: PairingToken) -> PairingToken:
        """Create a new pairing token.

        Args:
            token: Token to create.

        Returns:
            The created token.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            conn.execute(
                """
                INSERT INTO pairing_tokens (
                    token, expires_at, role, used, created_at
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    token.token,
                    token.expires_at.isoformat(),
                    token.role.value,
                    token.used,
                    token.created_at.isoformat(),
                ),
            )
            return token

    def get_token(self, token: str) -> PairingToken | None:
        """Get a pairing token.

        Args:
            token: Token string.

        Returns:
            Token if found, None otherwise.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.execute("SELECT * FROM pairing_tokens WHERE token = %s", (token,))
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_token(row)

    def mark_token_used(self, token: str) -> bool:
        """Mark a token as used.

        Args:
            token: Token to mark used.

        Returns:
            True if token was updated, False if not found.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.execute("UPDATE pairing_tokens SET used = TRUE WHERE token = %s", (token,))
            return cursor.rowcount > 0

    def cleanup_expired_tokens(self) -> int:
        """Remove expired pairing tokens.

        Returns:
            Number of tokens removed.
        """
        now = datetime.utcnow().isoformat()
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.execute("DELETE FROM pairing_tokens WHERE expires_at <= %s", (now,))
            return cursor.rowcount

    def _row_to_token(self, row: dict[str, Any]) -> PairingToken:
        """Convert a database row to a PairingToken."""
        expires_at = row["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)

        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        return PairingToken(
            token=row["token"],
            expires_at=expires_at,
            role=MobileRole(row["role"]),
            used=bool(row["used"]),
            created_at=created_at,
        )

    # =========================================================================
    # Maintenance operations
    # =========================================================================

    def cleanup(self) -> dict[str, int]:
        """Run all cleanup operations.

        Returns:
            Dictionary with counts of cleaned up records.
        """
        return {
            "expired_tokens": self.cleanup_expired_tokens(),
            "expired_elevations": self.cleanup_expired_elevations(),
        }
