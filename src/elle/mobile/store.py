"""SQLite storage for ELLE Mobile Gateway.

This module provides persistent storage for:
- Paired devices and their status
- Elevation grants
- Pairing tokens

Database location: /var/lib/elle/mobile.db
"""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from elle.mobile.config import MobileGatewayConfig, get_mobile_config
from elle.mobile.models import (
    DeviceStatus,
    Elevation,
    MobileRole,
    PairedDevice,
    PairingToken,
)

logger = logging.getLogger(__name__)


# Schema version for migrations
SCHEMA_VERSION = 1


SCHEMA = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

-- Paired devices table
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'mobile_readonly',
    status TEXT NOT NULL DEFAULT 'pending',
    cert_fingerprint TEXT NOT NULL,
    paired_at TEXT,
    last_seen_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Index for status queries
CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);

-- Elevations table
CREATE TABLE IF NOT EXISTS elevations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    elevated_role TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

-- Index for device lookup
CREATE INDEX IF NOT EXISTS idx_elevations_device ON elevations(device_id);

-- Index for expiration cleanup
CREATE INDEX IF NOT EXISTS idx_elevations_expires ON elevations(expires_at);

-- Pairing tokens table
CREATE TABLE IF NOT EXISTS pairing_tokens (
    token TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'mobile_readonly',
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Index for cleanup of expired tokens
CREATE INDEX IF NOT EXISTS idx_tokens_expires ON pairing_tokens(expires_at);
"""


class MobileStore:
    """SQLite storage for mobile gateway data.

    Provides CRUD operations for devices, elevations, and pairing tokens.
    Thread-safe with connection pooling via context managers.
    """

    def __init__(self, config: MobileGatewayConfig | None = None):
        """Initialize the store.

        Args:
            config: Mobile gateway configuration. Uses global config if None.
        """
        self.config = config or get_mobile_config()
        self.db_path = self.config.db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._connection() as conn:
            conn.executescript(SCHEMA)
            # Check/set schema version
            cursor = conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            conn.commit()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Context manager for database connections."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
        finally:
            conn.close()

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
        with self._connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO devices (
                        device_id, name, role, status, cert_fingerprint,
                        paired_at, last_seen_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                conn.commit()
                return device
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Device {device.device_id} already exists") from e

    def get_device(self, device_id: str) -> PairedDevice | None:
        """Get a device by ID.

        Args:
            device_id: Device identifier.

        Returns:
            Device if found, None otherwise.
        """
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM devices WHERE device_id = ?", (device_id,)
            )
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
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM devices WHERE cert_fingerprint = ?", (fingerprint,)
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_device(row)

    def list_devices(
        self, status: DeviceStatus | None = None
    ) -> list[PairedDevice]:
        """List all devices, optionally filtered by status.

        Args:
            status: Optional status filter.

        Returns:
            List of devices.
        """
        with self._connection() as conn:
            if status:
                cursor = conn.execute(
                    "SELECT * FROM devices WHERE status = ? ORDER BY created_at DESC",
                    (status.value,),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM devices ORDER BY created_at DESC"
                )
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
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if role is not None:
            updates.append("role = ?")
            params.append(role.value)
        if status is not None:
            updates.append("status = ?")
            params.append(status.value)
        if paired_at is not None:
            updates.append("paired_at = ?")
            params.append(paired_at.isoformat())
        if last_seen_at is not None:
            updates.append("last_seen_at = ?")
            params.append(last_seen_at.isoformat())

        if not updates:
            return self.get_device(device_id)

        params.append(device_id)

        with self._connection() as conn:
            conn.execute(
                f"UPDATE devices SET {', '.join(updates)} WHERE device_id = ?",
                params,
            )
            conn.commit()
            return self.get_device(device_id)

    def delete_device(self, device_id: str) -> bool:
        """Delete a device (cascades to elevations).

        Args:
            device_id: Device to delete.

        Returns:
            True if device was deleted, False if not found.
        """
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM devices WHERE device_id = ?", (device_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def count_devices(self, status: DeviceStatus | None = None) -> int:
        """Count devices, optionally filtered by status.

        Args:
            status: Optional status filter.

        Returns:
            Number of devices.
        """
        with self._connection() as conn:
            if status:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM devices WHERE status = ?",
                    (status.value,),
                )
            else:
                cursor = conn.execute("SELECT COUNT(*) FROM devices")
            return cursor.fetchone()[0]

    def _row_to_device(self, row: sqlite3.Row) -> PairedDevice:
        """Convert a database row to a PairedDevice."""
        return PairedDevice(
            device_id=row["device_id"],
            name=row["name"],
            role=MobileRole(row["role"]),
            status=DeviceStatus(row["status"]),
            cert_fingerprint=row["cert_fingerprint"],
            paired_at=(
                datetime.fromisoformat(row["paired_at"])
                if row["paired_at"]
                else None
            ),
            last_seen_at=(
                datetime.fromisoformat(row["last_seen_at"])
                if row["last_seen_at"]
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
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
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO elevations (
                    device_id, elevated_role, expires_at, granted_by, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    elevation.device_id,
                    elevation.elevated_role.value,
                    elevation.expires_at.isoformat(),
                    elevation.granted_by,
                    elevation.created_at.isoformat(),
                ),
            )
            conn.commit()
            return elevation

    def get_active_elevation(self, device_id: str) -> Elevation | None:
        """Get the active elevation for a device.

        Args:
            device_id: Device identifier.

        Returns:
            Active elevation if one exists, None otherwise.
        """
        now = datetime.utcnow().isoformat()
        with self._connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM elevations
                WHERE device_id = ? AND expires_at > ?
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
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM elevations WHERE expires_at > ? ORDER BY expires_at",
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
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM elevations WHERE device_id = ?", (device_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def cleanup_expired_elevations(self) -> int:
        """Remove expired elevation records.

        Returns:
            Number of records removed.
        """
        now = datetime.utcnow().isoformat()
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM elevations WHERE expires_at <= ?", (now,)
            )
            conn.commit()
            return cursor.rowcount

    def _row_to_elevation(self, row: sqlite3.Row) -> Elevation:
        """Convert a database row to an Elevation."""
        return Elevation(
            device_id=row["device_id"],
            elevated_role=MobileRole(row["elevated_role"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            granted_by=row["granted_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
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
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO pairing_tokens (
                    token, expires_at, role, used, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    token.token,
                    token.expires_at.isoformat(),
                    token.role.value,
                    1 if token.used else 0,
                    token.created_at.isoformat(),
                ),
            )
            conn.commit()
            return token

    def get_token(self, token: str) -> PairingToken | None:
        """Get a pairing token.

        Args:
            token: Token string.

        Returns:
            Token if found, None otherwise.
        """
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM pairing_tokens WHERE token = ?", (token,)
            )
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
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE pairing_tokens SET used = 1 WHERE token = ?", (token,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def cleanup_expired_tokens(self) -> int:
        """Remove expired pairing tokens.

        Returns:
            Number of tokens removed.
        """
        now = datetime.utcnow().isoformat()
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM pairing_tokens WHERE expires_at <= ?", (now,)
            )
            conn.commit()
            return cursor.rowcount

    def _row_to_token(self, row: sqlite3.Row) -> PairingToken:
        """Convert a database row to a PairingToken."""
        return PairingToken(
            token=row["token"],
            expires_at=datetime.fromisoformat(row["expires_at"]),
            role=MobileRole(row["role"]),
            used=bool(row["used"]),
            created_at=datetime.fromisoformat(row["created_at"]),
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
