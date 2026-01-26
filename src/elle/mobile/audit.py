"""Audit logging for ELLE Mobile Gateway.

This module provides persistent audit logging for all mobile gateway
operations. All security-relevant events are logged for review.

Audit log location: /var/lib/elle/mobile_audit.db
"""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from elle.mobile.config import MobileGatewayConfig, get_mobile_config
from elle.mobile.models import MobileAuditAction, MobileAuditEntry

logger = logging.getLogger(__name__)


AUDIT_SCHEMA = """
-- Audit log entries
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    device_id TEXT,
    device_name TEXT,
    action TEXT NOT NULL,
    endpoint TEXT,
    execution_mode TEXT,
    success INTEGER NOT NULL,
    error TEXT,
    ip_address TEXT NOT NULL,
    extra_data TEXT
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_device ON audit_log(device_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
"""


class MobileAuditStore:
    """SQLite storage for mobile gateway audit logs.

    Provides append-only logging and query capabilities.
    """

    def __init__(self, config: MobileGatewayConfig | None = None):
        """Initialize the audit store.

        Args:
            config: Mobile gateway configuration.
        """
        self.config = config or get_mobile_config()
        self.db_path = self.config.audit_db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._connection() as conn:
            conn.executescript(AUDIT_SCHEMA)
            conn.commit()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Context manager for database connections."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
        finally:
            conn.close()

    def log(self, entry: MobileAuditEntry) -> None:
        """Log an audit entry.

        Args:
            entry: Audit entry to log.
        """
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO audit_log (
                    timestamp, device_id, device_name, action,
                    endpoint, execution_mode, success, error, ip_address
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.timestamp.isoformat(),
                    entry.device_id,
                    entry.device_name,
                    entry.action.value,
                    entry.endpoint,
                    entry.execution_mode,
                    1 if entry.success else 0,
                    entry.error,
                    entry.ip_address,
                ),
            )
            conn.commit()

    def log_pair(
        self,
        device_id: str,
        device_name: str,
        ip_address: str,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Log a pairing event.

        Args:
            device_id: Device that paired.
            device_name: Device name.
            ip_address: Client IP.
            success: Whether pairing succeeded.
            error: Error message if failed.
        """
        self.log(
            MobileAuditEntry(
                device_id=device_id,
                device_name=device_name,
                action=MobileAuditAction.PAIR,
                success=success,
                error=error,
                ip_address=ip_address,
            )
        )

    def log_request(
        self,
        device_id: str,
        device_name: str,
        endpoint: str,
        execution_mode: str,
        ip_address: str,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Log an API request.

        Args:
            device_id: Device making request.
            device_name: Device name.
            endpoint: API endpoint accessed.
            execution_mode: Execution mode (readonly/execute).
            ip_address: Client IP.
            success: Whether request succeeded.
            error: Error message if failed.
        """
        self.log(
            MobileAuditEntry(
                device_id=device_id,
                device_name=device_name,
                action=MobileAuditAction.REQUEST,
                endpoint=endpoint,
                execution_mode=execution_mode,
                success=success,
                error=error,
                ip_address=ip_address,
            )
        )

    def log_elevate(
        self,
        device_id: str,
        device_name: str,
        ip_address: str,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Log an elevation event.

        Args:
            device_id: Device being elevated.
            device_name: Device name.
            ip_address: IP that initiated elevation.
            success: Whether elevation succeeded.
            error: Error message if failed.
        """
        self.log(
            MobileAuditEntry(
                device_id=device_id,
                device_name=device_name,
                action=MobileAuditAction.ELEVATE,
                success=success,
                error=error,
                ip_address=ip_address,
            )
        )

    def log_revoke(
        self,
        device_id: str,
        device_name: str,
        ip_address: str,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Log a device revocation.

        Args:
            device_id: Device being revoked.
            device_name: Device name.
            ip_address: IP that initiated revocation.
            success: Whether revocation succeeded.
            error: Error message if failed.
        """
        self.log(
            MobileAuditEntry(
                device_id=device_id,
                device_name=device_name,
                action=MobileAuditAction.REVOKE,
                success=success,
                error=error,
                ip_address=ip_address,
            )
        )

    def log_gateway_start(self, ip_address: str) -> None:
        """Log gateway startup.

        Args:
            ip_address: Bind address.
        """
        self.log(
            MobileAuditEntry(
                action=MobileAuditAction.GATEWAY_START,
                success=True,
                ip_address=ip_address,
            )
        )

    def log_gateway_stop(self, ip_address: str) -> None:
        """Log gateway shutdown.

        Args:
            ip_address: Bind address.
        """
        self.log(
            MobileAuditEntry(
                action=MobileAuditAction.GATEWAY_STOP,
                success=True,
                ip_address=ip_address,
            )
        )

    def query(
        self,
        device_id: str | None = None,
        action: MobileAuditAction | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        success_only: bool = False,
        limit: int = 100,
    ) -> list[MobileAuditEntry]:
        """Query audit log entries.

        Args:
            device_id: Filter by device ID.
            action: Filter by action type.
            since: Filter entries after this time.
            until: Filter entries before this time.
            success_only: Only return successful actions.
            limit: Maximum entries to return.

        Returns:
            List of matching audit entries.
        """
        conditions = []
        params = []

        if device_id:
            conditions.append("device_id = ?")
            params.append(device_id)

        if action:
            conditions.append("action = ?")
            params.append(action.value)

        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())

        if until:
            conditions.append("timestamp <= ?")
            params.append(until.isoformat())

        if success_only:
            conditions.append("success = 1")

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        with self._connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT * FROM audit_log
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                params,
            )
            return [self._row_to_entry(row) for row in cursor.fetchall()]

    def get_recent(self, hours: int = 24, limit: int = 100) -> list[MobileAuditEntry]:
        """Get recent audit entries.

        Args:
            hours: How many hours back to query.
            limit: Maximum entries.

        Returns:
            List of recent audit entries.
        """
        since = datetime.utcnow() - timedelta(hours=hours)
        return self.query(since=since, limit=limit)

    def get_device_history(self, device_id: str, limit: int = 50) -> list[MobileAuditEntry]:
        """Get audit history for a specific device.

        Args:
            device_id: Device to query.
            limit: Maximum entries.

        Returns:
            List of audit entries for the device.
        """
        return self.query(device_id=device_id, limit=limit)

    def get_failed_attempts(self, hours: int = 24, limit: int = 100) -> list[MobileAuditEntry]:
        """Get failed actions in recent time period.

        Args:
            hours: How many hours back to query.
            limit: Maximum entries.

        Returns:
            List of failed audit entries.
        """
        since = datetime.utcnow() - timedelta(hours=hours)

        with self._connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM audit_log
                WHERE timestamp >= ? AND success = 0
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (since.isoformat(), limit),
            )
            return [self._row_to_entry(row) for row in cursor.fetchall()]

    def cleanup_old_entries(self, days: int = 30) -> int:
        """Remove audit entries older than specified days.

        Args:
            days: Delete entries older than this many days.

        Returns:
            Number of entries deleted.
        """
        cutoff = datetime.utcnow() - timedelta(days=days)

        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM audit_log WHERE timestamp < ?",
                (cutoff.isoformat(),),
            )
            conn.commit()
            return cursor.rowcount

    def _row_to_entry(self, row: sqlite3.Row) -> MobileAuditEntry:
        """Convert database row to MobileAuditEntry."""
        return MobileAuditEntry(
            timestamp=datetime.fromisoformat(row["timestamp"]),
            device_id=row["device_id"],
            device_name=row["device_name"],
            action=MobileAuditAction(row["action"]),
            endpoint=row["endpoint"],
            execution_mode=row["execution_mode"],
            success=bool(row["success"]),
            error=row["error"],
            ip_address=row["ip_address"],
        )
