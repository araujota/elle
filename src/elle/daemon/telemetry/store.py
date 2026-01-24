"""CRUD operations for telemetry event storage.

Provides efficient batch operations and queries for
telemetry events and probe results.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from elle.daemon.telemetry.models import ProbeResult, TelemetryEvent
from elle.daemon.telemetry.schema import ensure_schema, get_connection


def _serialize_datetime(dt: datetime) -> str:
    """Serialize datetime to ISO format."""
    return dt.isoformat()


def _parse_datetime(s: str) -> datetime:
    """Parse ISO format string to datetime."""
    return datetime.fromisoformat(s)


def _json_dumps(obj: Any) -> str:
    """Serialize object to JSON."""
    return json.dumps(obj, default=str)


def _json_loads(s: str | None) -> Any:
    """Parse JSON string."""
    if not s:
        return {}
    return json.loads(s)


# =============================================================================
# Event CRUD
# =============================================================================


def insert_event(
    event: TelemetryEvent,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Insert a single event.

    Args:
        event: The TelemetryEvent to insert.
        conn: SQLite connection.

    Returns:
        The inserted row ID.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO events (
                event_id, ts, source, severity, category,
                message, entity, fingerprint, raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                _serialize_datetime(event.ts),
                event.source,
                event.severity,
                event.category,
                event.message,
                event.entity,
                event.fingerprint,
                _json_dumps(event.raw),
            ),
        )
        conn.commit()
        return cursor.lastrowid or 0

    finally:
        if own_conn:
            conn.close()


def insert_events_batch(
    events: list[TelemetryEvent],
    conn: sqlite3.Connection | None = None,
) -> int:
    """Insert multiple events in a batch.

    More efficient than inserting one at a time.

    Args:
        events: List of TelemetryEvents to insert.
        conn: SQLite connection.

    Returns:
        Number of events inserted.
    """
    if not events:
        return 0

    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        rows = [
            (
                event.event_id,
                _serialize_datetime(event.ts),
                event.source,
                event.severity,
                event.category,
                event.message,
                event.entity,
                event.fingerprint,
                _json_dumps(event.raw),
            )
            for event in events
        ]

        cursor.executemany(
            """
            INSERT OR IGNORE INTO events (
                event_id, ts, source, severity, category,
                message, entity, fingerprint, raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        return cursor.rowcount

    finally:
        if own_conn:
            conn.close()


def get_event(
    event_id: str,
    conn: sqlite3.Connection | None = None,
) -> TelemetryEvent | None:
    """Get an event by its event_id.

    Args:
        event_id: The event UUID.
        conn: SQLite connection.

    Returns:
        TelemetryEvent if found, None otherwise.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM events WHERE event_id = ?", (event_id,))
        row = cursor.fetchone()

        if not row:
            return None

        return _row_to_event(row)

    finally:
        if own_conn:
            conn.close()


def get_event_by_id(
    row_id: int,
    conn: sqlite3.Connection | None = None,
) -> TelemetryEvent | None:
    """Get an event by its database row ID.

    Args:
        row_id: The database row ID.
        conn: SQLite connection.

    Returns:
        TelemetryEvent if found, None otherwise.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM events WHERE id = ?", (row_id,))
        row = cursor.fetchone()

        if not row:
            return None

        return _row_to_event(row)

    finally:
        if own_conn:
            conn.close()


def query_events(
    since: datetime | None = None,
    until: datetime | None = None,
    category: str | None = None,
    severity: str | None = None,
    entity: str | None = None,
    source: str | None = None,
    limit: int = 100,
    offset: int = 0,
    conn: sqlite3.Connection | None = None,
) -> list[TelemetryEvent]:
    """Query events with filters.

    Args:
        since: Filter events after this time.
        until: Filter events before this time.
        category: Filter by category.
        severity: Filter by severity.
        entity: Filter by entity.
        source: Filter by source type.
        limit: Maximum results.
        offset: Pagination offset.
        conn: SQLite connection.

    Returns:
        List of matching TelemetryEvents.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        query = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []

        if since:
            query += " AND ts >= ?"
            params.append(_serialize_datetime(since))
        if until:
            query += " AND ts <= ?"
            params.append(_serialize_datetime(until))
        if category:
            query += " AND category = ?"
            params.append(category)
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        if entity:
            query += " AND entity = ?"
            params.append(entity)
        if source:
            query += " AND source = ?"
            params.append(source)

        query += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = conn.cursor()
        cursor.execute(query, params)

        return [_row_to_event(row) for row in cursor.fetchall()]

    finally:
        if own_conn:
            conn.close()


def search_events(
    query_text: str,
    limit: int = 100,
    conn: sqlite3.Connection | None = None,
) -> list[TelemetryEvent]:
    """Full-text search events by message.

    Args:
        query_text: Search query.
        limit: Maximum results.
        conn: SQLite connection.

    Returns:
        List of matching TelemetryEvents.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT e.* FROM events e
            JOIN events_fts f ON e.id = f.rowid
            WHERE events_fts MATCH ?
            ORDER BY e.ts DESC
            LIMIT ?
            """,
            (query_text, limit),
        )

        return [_row_to_event(row) for row in cursor.fetchall()]

    finally:
        if own_conn:
            conn.close()


def count_events(
    since: datetime | None = None,
    category: str | None = None,
    severity: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Count events matching filters.

    Args:
        since: Count events after this time.
        category: Filter by category.
        severity: Filter by severity.
        conn: SQLite connection.

    Returns:
        Number of matching events.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        query = "SELECT COUNT(*) FROM events WHERE 1=1"
        params: list[Any] = []

        if since:
            query += " AND ts >= ?"
            params.append(_serialize_datetime(since))
        if category:
            query += " AND category = ?"
            params.append(category)
        if severity:
            query += " AND severity = ?"
            params.append(severity)

        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()[0]

    finally:
        if own_conn:
            conn.close()


def count_events_by_category(
    since: datetime | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, int]:
    """Count events grouped by category.

    Args:
        since: Count events after this time.
        conn: SQLite connection.

    Returns:
        Dict mapping category to count.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        query = "SELECT category, COUNT(*) FROM events"
        params: list[Any] = []

        if since:
            query += " WHERE ts >= ?"
            params.append(_serialize_datetime(since))

        query += " GROUP BY category"

        cursor = conn.cursor()
        cursor.execute(query, params)

        return {row[0]: row[1] for row in cursor.fetchall()}

    finally:
        if own_conn:
            conn.close()


def get_recent_fingerprints(
    window_sec: int = 60,
    conn: sqlite3.Connection | None = None,
) -> set[str]:
    """Get fingerprints of recent events for deduplication.

    Args:
        window_sec: Look back window in seconds.
        conn: SQLite connection.

    Returns:
        Set of fingerprints.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        since = datetime.now(UTC) - timedelta(seconds=window_sec)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT fingerprint FROM events
            WHERE ts >= ? AND fingerprint IS NOT NULL
            """,
            (_serialize_datetime(since),),
        )

        return {row[0] for row in cursor.fetchall()}

    finally:
        if own_conn:
            conn.close()


def delete_old_events(
    older_than_days: int = 30,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Delete events older than specified days.

    Args:
        older_than_days: Delete events older than this.
        conn: SQLite connection.

    Returns:
        Number of deleted events.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM events WHERE ts < ?",
            (_serialize_datetime(cutoff),),
        )
        conn.commit()
        return cursor.rowcount

    finally:
        if own_conn:
            conn.close()


def _row_to_event(row: sqlite3.Row) -> TelemetryEvent:
    """Convert a database row to TelemetryEvent."""
    return TelemetryEvent(
        event_id=row["event_id"],
        ts=_parse_datetime(row["ts"]),
        source=row["source"],
        severity=row["severity"],
        category=row["category"],
        message=row["message"],
        entity=row["entity"],
        fingerprint=row["fingerprint"],
        raw=_json_loads(row["raw"]),
    )


# =============================================================================
# Probe Results CRUD
# =============================================================================


def insert_probe_result(
    result: ProbeResult,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Insert a probe result.

    Args:
        result: The ProbeResult to insert.
        conn: SQLite connection.

    Returns:
        The inserted row ID.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO probe_results (
                probe_name, ts, success, data, error
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                result.probe_name,
                _serialize_datetime(result.ts),
                1 if result.success else 0,
                _json_dumps(result.data),
                result.error,
            ),
        )
        conn.commit()
        return cursor.lastrowid or 0

    finally:
        if own_conn:
            conn.close()


def get_latest_probe_result(
    probe_name: str,
    conn: sqlite3.Connection | None = None,
) -> ProbeResult | None:
    """Get the most recent result for a probe.

    Args:
        probe_name: Name of the probe.
        conn: SQLite connection.

    Returns:
        Latest ProbeResult if found, None otherwise.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM probe_results
            WHERE probe_name = ?
            ORDER BY ts DESC
            LIMIT 1
            """,
            (probe_name,),
        )
        row = cursor.fetchone()

        if not row:
            return None

        return ProbeResult(
            probe_name=row["probe_name"],
            ts=_parse_datetime(row["ts"]),
            success=bool(row["success"]),
            data=_json_loads(row["data"]),
            error=row["error"],
        )

    finally:
        if own_conn:
            conn.close()


def delete_old_probe_results(
    older_than_days: int = 7,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Delete probe results older than specified days.

    Args:
        older_than_days: Delete results older than this.
        conn: SQLite connection.

    Returns:
        Number of deleted results.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM probe_results WHERE ts < ?",
            (_serialize_datetime(cutoff),),
        )
        conn.commit()
        return cursor.rowcount

    finally:
        if own_conn:
            conn.close()
