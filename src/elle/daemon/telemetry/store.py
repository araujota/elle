"""CRUD operations for telemetry event storage.

Provides efficient batch operations and queries for
telemetry events and probe results.

Storage backend: PostgreSQL via psycopg (schema ``telemetry``).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from elle.daemon.telemetry.models import ProbeResult, TelemetryEvent
from elle.storage.engine import get_conn

PG_SCHEMA = "telemetry"


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
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> int:
    """Insert a single event.

    Args:
        event: The TelemetryEvent to insert.
        conn: psycopg connection.

    Returns:
        The inserted row ID.
    """

    def _run(c: psycopg.Connection) -> int:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute(
            """
            INSERT INTO events (
                event_id, ts, source, severity, category,
                message, entity, fingerprint, raw
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
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
        row = cursor.fetchone()
        return row["id"] if row else 0

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def insert_events_batch(
    events: list[TelemetryEvent],
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> int:
    """Insert multiple events in a batch.

    More efficient than inserting one at a time.

    Args:
        events: List of TelemetryEvents to insert.
        conn: psycopg connection.

    Returns:
        Number of events inserted.
    """
    if not events:
        return 0

    def _run(c: psycopg.Connection) -> int:  # type: ignore[type-arg]
        cursor = c.cursor()
        inserted = 0
        for event in events:
            cursor.execute(
                """
                INSERT INTO events (
                    event_id, ts, source, severity, category,
                    message, entity, fingerprint, raw
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
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
            inserted += cursor.rowcount
        return inserted

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def get_event(
    event_id: str,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> TelemetryEvent | None:
    """Get an event by its event_id.

    Args:
        event_id: The event UUID.
        conn: psycopg connection.

    Returns:
        TelemetryEvent if found, None otherwise.
    """

    def _run(c: psycopg.Connection) -> TelemetryEvent | None:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute("SELECT * FROM events WHERE event_id = %s", (event_id,))
        row = cursor.fetchone()

        if not row:
            return None

        return _row_to_event(row)

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def get_event_by_id(
    row_id: int,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> TelemetryEvent | None:
    """Get an event by its database row ID.

    Args:
        row_id: The database row ID.
        conn: psycopg connection.

    Returns:
        TelemetryEvent if found, None otherwise.
    """

    def _run(c: psycopg.Connection) -> TelemetryEvent | None:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute("SELECT * FROM events WHERE id = %s", (row_id,))
        row = cursor.fetchone()

        if not row:
            return None

        return _row_to_event(row)

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def query_events(
    since: datetime | None = None,
    until: datetime | None = None,
    category: str | None = None,
    severity: str | None = None,
    entity: str | None = None,
    source: str | None = None,
    limit: int = 100,
    offset: int = 0,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
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
        conn: psycopg connection.

    Returns:
        List of matching TelemetryEvents.
    """

    def _run(c: psycopg.Connection) -> list[TelemetryEvent]:  # type: ignore[type-arg]
        query = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []

        if since:
            query += " AND ts >= %s"
            params.append(_serialize_datetime(since))
        if until:
            query += " AND ts <= %s"
            params.append(_serialize_datetime(until))
        if category:
            query += " AND category = %s"
            params.append(category)
        if severity:
            query += " AND severity = %s"
            params.append(severity)
        if entity:
            query += " AND entity = %s"
            params.append(entity)
        if source:
            query += " AND source = %s"
            params.append(source)

        query += " ORDER BY ts DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cursor = c.cursor()
        cursor.execute(query, params)

        return [_row_to_event(row) for row in cursor.fetchall()]

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def search_events(
    query_text: str,
    limit: int = 100,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> list[TelemetryEvent]:
    """Full-text search events by message.

    Uses PostgreSQL full-text search with ts_query.

    Args:
        query_text: Search query.
        limit: Maximum results.
        conn: psycopg connection.

    Returns:
        List of matching TelemetryEvents.
    """

    def _run(c: psycopg.Connection) -> list[TelemetryEvent]:  # type: ignore[type-arg]
        cursor = c.cursor()
        # Use ILIKE for simple substring matching (compatible fallback)
        # For FTS, the schema migration should create a tsvector column + GIN index
        cursor.execute(
            """
            SELECT * FROM events
            WHERE message ILIKE %s
            ORDER BY ts DESC
            LIMIT %s
            """,
            (f"%{query_text}%", limit),
        )

        return [_row_to_event(row) for row in cursor.fetchall()]

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def count_events(
    since: datetime | None = None,
    category: str | None = None,
    severity: str | None = None,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> int:
    """Count events matching filters.

    Args:
        since: Count events after this time.
        category: Filter by category.
        severity: Filter by severity.
        conn: psycopg connection.

    Returns:
        Number of matching events.
    """

    def _run(c: psycopg.Connection) -> int:  # type: ignore[type-arg]
        query = "SELECT COUNT(*) AS cnt FROM events WHERE 1=1"
        params: list[Any] = []

        if since:
            query += " AND ts >= %s"
            params.append(_serialize_datetime(since))
        if category:
            query += " AND category = %s"
            params.append(category)
        if severity:
            query += " AND severity = %s"
            params.append(severity)

        cursor = c.cursor()
        cursor.execute(query, params)
        row = cursor.fetchone()
        return int(row["cnt"]) if row else 0

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def count_events_by_category(
    since: datetime | None = None,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> dict[str, int]:
    """Count events grouped by category.

    Args:
        since: Count events after this time.
        conn: psycopg connection.

    Returns:
        Dict mapping category to count.
    """

    def _run(c: psycopg.Connection) -> dict[str, int]:  # type: ignore[type-arg]
        query = "SELECT category, COUNT(*) AS cnt FROM events"
        params: list[Any] = []

        if since:
            query += " WHERE ts >= %s"
            params.append(_serialize_datetime(since))

        query += " GROUP BY category"

        cursor = c.cursor()
        cursor.execute(query, params)

        return {row["category"]: row["cnt"] for row in cursor.fetchall()}

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def get_recent_fingerprints(
    window_sec: int = 60,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> set[str]:
    """Get fingerprints of recent events for deduplication.

    Args:
        window_sec: Look back window in seconds.
        conn: psycopg connection.

    Returns:
        Set of fingerprints.
    """

    def _run(c: psycopg.Connection) -> set[str]:  # type: ignore[type-arg]
        since = datetime.now(timezone.utc) - timedelta(seconds=window_sec)
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT DISTINCT fingerprint FROM events
            WHERE ts >= %s AND fingerprint IS NOT NULL
            """,
            (_serialize_datetime(since),),
        )

        return {row["fingerprint"] for row in cursor.fetchall()}

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def delete_old_events(
    older_than_days: int = 30,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> int:
    """Delete events older than specified days.

    Args:
        older_than_days: Delete events older than this.
        conn: psycopg connection.

    Returns:
        Number of deleted events.
    """

    def _run(c: psycopg.Connection) -> int:  # type: ignore[type-arg]
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        cursor = c.cursor()
        cursor.execute(
            "DELETE FROM events WHERE ts < %s",
            (_serialize_datetime(cutoff),),
        )
        return cursor.rowcount

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def _row_to_event(row: dict) -> TelemetryEvent:
    """Convert a database row to TelemetryEvent."""
    ts = row["ts"]
    if isinstance(ts, str):
        ts = _parse_datetime(ts)

    return TelemetryEvent(
        event_id=row["event_id"],
        ts=ts,
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
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> int:
    """Insert a probe result.

    Args:
        result: The ProbeResult to insert.
        conn: psycopg connection.

    Returns:
        The inserted row ID.
    """

    def _run(c: psycopg.Connection) -> int:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute(
            """
            INSERT INTO probe_results (
                probe_name, ts, success, data, error
            ) VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                result.probe_name,
                _serialize_datetime(result.ts),
                result.success,
                _json_dumps(result.data),
                result.error,
            ),
        )
        row = cursor.fetchone()
        return row["id"] if row else 0

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def get_latest_probe_result(
    probe_name: str,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> ProbeResult | None:
    """Get the most recent result for a probe.

    Args:
        probe_name: Name of the probe.
        conn: psycopg connection.

    Returns:
        Latest ProbeResult if found, None otherwise.
    """

    def _run(c: psycopg.Connection) -> ProbeResult | None:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT * FROM probe_results
            WHERE probe_name = %s
            ORDER BY ts DESC
            LIMIT 1
            """,
            (probe_name,),
        )
        row = cursor.fetchone()

        if not row:
            return None

        ts = row["ts"]
        if isinstance(ts, str):
            ts = _parse_datetime(ts)

        return ProbeResult(
            probe_name=row["probe_name"],
            ts=ts,
            success=bool(row["success"]),
            data=_json_loads(row["data"]),
            error=row["error"],
        )

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def delete_old_probe_results(
    older_than_days: int = 7,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> int:
    """Delete probe results older than specified days.

    Args:
        older_than_days: Delete results older than this.
        conn: psycopg connection.

    Returns:
        Number of deleted results.
    """

    def _run(c: psycopg.Connection) -> int:  # type: ignore[type-arg]
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        cursor = c.cursor()
        cursor.execute(
            "DELETE FROM probe_results WHERE ts < %s",
            (_serialize_datetime(cutoff),),
        )
        return cursor.rowcount

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)
