"""CRUD operations for the Incident Vault.

Provides database operations for incidents, actions, and snapshots.
All operations are append-only where appropriate for auditability.
"""

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from elle.daemon.incidents.models import (
    ConfigFileState,
    ConfidenceBreakdown,
    DecisionRecord,
    Fingerprint,
    IncidentAction,
    IncidentCitation,
    IncidentReport,
    IncidentSnapshot,
    ManPageCitation,
    Precondition,
    Provenance,
    SystemSnapshot,
    TelemetryCitation,
)
from elle.daemon.incidents.schema import ensure_schema, get_connection


def _serialize_datetime(dt: datetime) -> str:
    """Serialize datetime to ISO format string."""
    return dt.isoformat()


def _parse_datetime(s: str) -> datetime:
    """Parse ISO format string to datetime."""
    return datetime.fromisoformat(s)


def _json_dumps(obj: Any) -> str:
    """Serialize object to JSON string."""
    return json.dumps(obj, default=str)


def _json_loads(s: str | None) -> Any:
    """Parse JSON string, returning empty dict/list on None."""
    if not s:
        return {}
    return json.loads(s)


# =============================================================================
# Incident CRUD
# =============================================================================


def create_incident_draft(
    title: str,
    domain: str = "other",
    severity: str = "warning",
    trigger_source: str = "manual",
    trigger_command: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> IncidentReport:
    """Create a new incident draft.

    Creates an incident in 'open' status with minimal information.
    Additional details should be added via update_incident().

    Args:
        title: Brief title for the incident.
        domain: Incident domain category.
        severity: Severity level.
        trigger_source: What created this incident.
        trigger_command: Command that triggered (if applicable).
        conn: SQLite connection. Uses default if not provided.

    Returns:
        The created IncidentReport.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        incident_id = str(uuid.uuid4())
        now = datetime.utcnow()

        incident = IncidentReport(
            incident_id=incident_id,
            created_at=now,
            updated_at=now,
            domain=domain,  # type: ignore
            severity=severity,  # type: ignore
            status="open",
            title=title,
            trigger_source=trigger_source,  # type: ignore
            trigger_command=trigger_command,
        )

        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO incidents (
                id, created_at, updated_at, domain, severity, status,
                title, summary, symptoms_json, suspected_causes_json, root_cause,
                event_ids_json, log_snippets_json, metrics_json,
                decision_json, preconditions_json,
                outcome, verification_steps_json,
                time_to_mitigate_sec, time_to_resolve_sec,
                fingerprint_json, tags_json, confidence,
                trigger_source, trigger_command
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?
            )
            """,
            (
                incident.incident_id,
                _serialize_datetime(incident.created_at),
                _serialize_datetime(incident.updated_at),
                incident.domain,
                incident.severity,
                incident.status,
                incident.title,
                incident.summary,
                _json_dumps(list(incident.symptoms)),
                _json_dumps(list(incident.suspected_causes)),
                incident.root_cause,
                _json_dumps(list(incident.event_ids)),
                _json_dumps(list(incident.log_snippets)),
                _json_dumps(incident.metrics),
                _json_dumps(incident.decision),
                _json_dumps([p.model_dump() for p in incident.preconditions]),
                incident.outcome,
                _json_dumps(list(incident.verification_steps)),
                incident.time_to_mitigate_sec,
                incident.time_to_resolve_sec,
                _json_dumps(incident.fingerprint.model_dump()),
                _json_dumps(list(incident.tags)),
                incident.confidence,
                incident.trigger_source,
                incident.trigger_command,
            ),
        )
        conn.commit()
        return incident

    finally:
        if own_conn:
            conn.close()


def update_incident(
    incident_id: str,
    *,
    status: str | None = None,
    summary: str | None = None,
    symptoms: list[str] | None = None,
    suspected_causes: list[str] | None = None,
    root_cause: str | None = None,
    event_ids: list[str] | None = None,
    log_snippets: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
    preconditions: list[Precondition] | None = None,
    outcome: str | None = None,
    verification_steps: list[str] | None = None,
    time_to_mitigate_sec: int | None = None,
    time_to_resolve_sec: int | None = None,
    fingerprint: Fingerprint | None = None,
    tags: list[str] | None = None,
    confidence: float | None = None,
    conn: sqlite3.Connection | None = None,
) -> IncidentReport | None:
    """Update an existing incident.

    Only provided fields are updated. This maintains append-only
    semantics by updating the updated_at timestamp.

    Args:
        incident_id: ID of the incident to update.
        **kwargs: Fields to update.
        conn: SQLite connection.

    Returns:
        Updated IncidentReport, or None if not found.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        # Build update statement dynamically
        updates = ["updated_at = ?"]
        values: list[Any] = [_serialize_datetime(datetime.utcnow())]

        if status is not None:
            updates.append("status = ?")
            values.append(status)
        if summary is not None:
            updates.append("summary = ?")
            values.append(summary)
        if symptoms is not None:
            updates.append("symptoms_json = ?")
            values.append(_json_dumps(symptoms))
        if suspected_causes is not None:
            updates.append("suspected_causes_json = ?")
            values.append(_json_dumps(suspected_causes))
        if root_cause is not None:
            updates.append("root_cause = ?")
            values.append(root_cause)
        if event_ids is not None:
            updates.append("event_ids_json = ?")
            values.append(_json_dumps(event_ids))
        if log_snippets is not None:
            updates.append("log_snippets_json = ?")
            values.append(_json_dumps(log_snippets))
        if metrics is not None:
            updates.append("metrics_json = ?")
            values.append(_json_dumps(metrics))
        if decision is not None:
            updates.append("decision_json = ?")
            values.append(_json_dumps(decision))
        if preconditions is not None:
            updates.append("preconditions_json = ?")
            values.append(_json_dumps([p.model_dump() for p in preconditions]))
        if outcome is not None:
            updates.append("outcome = ?")
            values.append(outcome)
        if verification_steps is not None:
            updates.append("verification_steps_json = ?")
            values.append(_json_dumps(verification_steps))
        if time_to_mitigate_sec is not None:
            updates.append("time_to_mitigate_sec = ?")
            values.append(time_to_mitigate_sec)
        if time_to_resolve_sec is not None:
            updates.append("time_to_resolve_sec = ?")
            values.append(time_to_resolve_sec)
        if fingerprint is not None:
            updates.append("fingerprint_json = ?")
            values.append(_json_dumps(fingerprint.model_dump()))
        if tags is not None:
            updates.append("tags_json = ?")
            values.append(_json_dumps(tags))
        if confidence is not None:
            updates.append("confidence = ?")
            values.append(confidence)

        values.append(incident_id)

        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE incidents SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        conn.commit()

        if cursor.rowcount == 0:
            return None

        return get_incident(incident_id, conn=conn)

    finally:
        if own_conn:
            conn.close()


def get_incident(
    incident_id: str,
    conn: sqlite3.Connection | None = None,
) -> IncidentReport | None:
    """Get an incident by ID.

    Args:
        incident_id: The incident UUID.
        conn: SQLite connection.

    Returns:
        IncidentReport if found, None otherwise.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        row = cursor.fetchone()

        if not row:
            return None

        return _row_to_incident(row)

    finally:
        if own_conn:
            conn.close()


def list_incidents(
    status: str | None = None,
    domain: str | None = None,
    limit: int = 100,
    offset: int = 0,
    conn: sqlite3.Connection | None = None,
) -> list[IncidentReport]:
    """List incidents with optional filtering.

    Args:
        status: Filter by status.
        domain: Filter by domain.
        limit: Maximum number of results.
        offset: Offset for pagination.
        conn: SQLite connection.

    Returns:
        List of IncidentReports.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        query = "SELECT * FROM incidents WHERE 1=1"
        params: list[Any] = []

        if status:
            query += " AND status = ?"
            params.append(status)
        if domain:
            query += " AND domain = ?"
            params.append(domain)

        query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = conn.cursor()
        cursor.execute(query, params)

        return [_row_to_incident(row) for row in cursor.fetchall()]

    finally:
        if own_conn:
            conn.close()


def delete_incident(
    incident_id: str,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Delete an incident and all related data.

    Note: Prefer marking as false_positive rather than deleting
    for audit trail purposes.

    Args:
        incident_id: The incident UUID.
        conn: SQLite connection.

    Returns:
        True if deleted, False if not found.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
        conn.commit()
        return cursor.rowcount > 0

    finally:
        if own_conn:
            conn.close()


def _row_to_incident(row: sqlite3.Row) -> IncidentReport:
    """Convert a database row to an IncidentReport."""
    preconditions_data = _json_loads(row["preconditions_json"])
    preconditions = tuple(
        Precondition(**p) if isinstance(p, dict) else p
        for p in (preconditions_data if isinstance(preconditions_data, list) else [])
    )

    fingerprint_data = _json_loads(row["fingerprint_json"])
    fingerprint = Fingerprint(**fingerprint_data) if fingerprint_data else Fingerprint()

    return IncidentReport(
        incident_id=row["id"],
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
        domain=row["domain"],
        severity=row["severity"],
        status=row["status"],
        title=row["title"],
        summary=row["summary"] or "",
        symptoms=tuple(_json_loads(row["symptoms_json"]) or []),
        suspected_causes=tuple(_json_loads(row["suspected_causes_json"]) or []),
        root_cause=row["root_cause"],
        event_ids=tuple(_json_loads(row["event_ids_json"]) or []),
        log_snippets=tuple(_json_loads(row["log_snippets_json"]) or []),
        metrics=_json_loads(row["metrics_json"]) or {},
        decision=_json_loads(row["decision_json"]) or {},
        preconditions=preconditions,
        outcome=row["outcome"],
        verification_steps=tuple(_json_loads(row["verification_steps_json"]) or []),
        time_to_mitigate_sec=row["time_to_mitigate_sec"],
        time_to_resolve_sec=row["time_to_resolve_sec"],
        fingerprint=fingerprint,
        tags=tuple(_json_loads(row["tags_json"]) or []),
        confidence=row["confidence"],
        trigger_source=row["trigger_source"],
        trigger_command=row["trigger_command"],
    )


# =============================================================================
# Action CRUD
# =============================================================================


def append_action(
    incident_id: str,
    kind: str,
    command: str | None = None,
    payload: dict[str, Any] | None = None,
    exit_code: int | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
    success: bool = False,
    privileged: bool = False,
    duration_ms: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> IncidentAction:
    """Append an action to an incident.

    Actions are append-only and automatically indexed.

    Args:
        incident_id: Parent incident ID.
        kind: Type of action (shell, edit, privileged, verify, rollback).
        command: Shell command if applicable.
        payload: Action-specific data.
        exit_code: Command exit code.
        stdout: Stdout output (truncated).
        stderr: Stderr output (truncated).
        success: Whether the action succeeded.
        privileged: Whether elevated privileges were used.
        duration_ms: Execution duration.
        conn: SQLite connection.

    Returns:
        The created IncidentAction.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()

        # Get next step index
        cursor.execute(
            "SELECT COALESCE(MAX(step_index), -1) + 1 FROM incident_actions WHERE incident_id = ?",
            (incident_id,),
        )
        step_index = cursor.fetchone()[0]

        now = datetime.utcnow()
        action = IncidentAction(
            incident_id=incident_id,
            step_index=step_index,
            kind=kind,  # type: ignore
            command=command,
            payload=payload or {},
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            success=success,
            privileged=privileged,
            created_at=now,
            duration_ms=duration_ms,
        )

        cursor.execute(
            """
            INSERT INTO incident_actions (
                incident_id, step_index, kind, command, payload_json,
                exit_code, stdout, stderr, success, privileged,
                created_at, duration_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action.incident_id,
                action.step_index,
                action.kind,
                action.command,
                _json_dumps(action.payload),
                action.exit_code,
                action.stdout,
                action.stderr,
                1 if action.success else 0,
                1 if action.privileged else 0,
                _serialize_datetime(action.created_at),
                action.duration_ms,
            ),
        )

        action = IncidentAction(
            id=cursor.lastrowid,
            incident_id=action.incident_id,
            step_index=action.step_index,
            kind=action.kind,
            command=action.command,
            payload=action.payload,
            exit_code=action.exit_code,
            stdout=action.stdout,
            stderr=action.stderr,
            success=action.success,
            privileged=action.privileged,
            created_at=action.created_at,
            duration_ms=action.duration_ms,
        )

        conn.commit()
        return action

    finally:
        if own_conn:
            conn.close()


def get_actions(
    incident_id: str,
    conn: sqlite3.Connection | None = None,
) -> list[IncidentAction]:
    """Get all actions for an incident, ordered by step_index.

    Args:
        incident_id: The incident UUID.
        conn: SQLite connection.

    Returns:
        List of IncidentActions in order.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM incident_actions
            WHERE incident_id = ?
            ORDER BY step_index
            """,
            (incident_id,),
        )

        return [_row_to_action(row) for row in cursor.fetchall()]

    finally:
        if own_conn:
            conn.close()


def _row_to_action(row: sqlite3.Row) -> IncidentAction:
    """Convert a database row to an IncidentAction."""
    return IncidentAction(
        id=row["id"],
        incident_id=row["incident_id"],
        step_index=row["step_index"],
        kind=row["kind"],
        command=row["command"],
        payload=_json_loads(row["payload_json"]) or {},
        exit_code=row["exit_code"],
        stdout=row["stdout"],
        stderr=row["stderr"],
        success=bool(row["success"]),
        privileged=bool(row["privileged"]),
        created_at=_parse_datetime(row["created_at"]),
        duration_ms=row["duration_ms"],
    )


# =============================================================================
# Snapshot CRUD
# =============================================================================


def attach_snapshot(
    incident_id: str,
    which: str,
    snapshot: SystemSnapshot,
    conn: sqlite3.Connection | None = None,
) -> IncidentSnapshot:
    """Attach a system snapshot to an incident.

    Each incident can have one 'pre' and one 'post' snapshot.
    Calling again for the same (incident_id, which) replaces
    the existing snapshot.

    Args:
        incident_id: Parent incident ID.
        which: 'pre' or 'post'.
        snapshot: The SystemSnapshot to attach.
        conn: SQLite connection.

    Returns:
        The created/updated IncidentSnapshot.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        now = datetime.utcnow()

        cursor.execute(
            """
            INSERT OR REPLACE INTO incident_snapshots (
                incident_id, which, snapshot_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                incident_id,
                which,
                _json_dumps(snapshot.model_dump()),
                _serialize_datetime(now),
            ),
        )

        snapshot_record = IncidentSnapshot(
            id=cursor.lastrowid,
            incident_id=incident_id,
            which=which,  # type: ignore
            snapshot=snapshot,
            created_at=now,
        )

        conn.commit()
        return snapshot_record

    finally:
        if own_conn:
            conn.close()


def get_snapshot(
    incident_id: str,
    which: str,
    conn: sqlite3.Connection | None = None,
) -> IncidentSnapshot | None:
    """Get a specific snapshot for an incident.

    Args:
        incident_id: The incident UUID.
        which: 'pre' or 'post'.
        conn: SQLite connection.

    Returns:
        IncidentSnapshot if found, None otherwise.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM incident_snapshots
            WHERE incident_id = ? AND which = ?
            """,
            (incident_id, which),
        )
        row = cursor.fetchone()

        if not row:
            return None

        return _row_to_snapshot(row)

    finally:
        if own_conn:
            conn.close()


def get_snapshots(
    incident_id: str,
    conn: sqlite3.Connection | None = None,
) -> dict[str, IncidentSnapshot]:
    """Get all snapshots for an incident.

    Args:
        incident_id: The incident UUID.
        conn: SQLite connection.

    Returns:
        Dict mapping 'pre'/'post' to IncidentSnapshot.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM incident_snapshots WHERE incident_id = ?",
            (incident_id,),
        )

        result = {}
        for row in cursor.fetchall():
            snapshot = _row_to_snapshot(row)
            result[snapshot.which] = snapshot

        return result

    finally:
        if own_conn:
            conn.close()


def _row_to_snapshot(row: sqlite3.Row) -> IncidentSnapshot:
    """Convert a database row to an IncidentSnapshot."""
    snapshot_data = _json_loads(row["snapshot_json"])

    # Handle tuple fields that may have been serialized as lists
    if "cpu_load" in snapshot_data and isinstance(snapshot_data["cpu_load"], list):
        snapshot_data["cpu_load"] = tuple(snapshot_data["cpu_load"])
    if "disks" in snapshot_data and isinstance(snapshot_data["disks"], list):
        snapshot_data["disks"] = tuple(snapshot_data["disks"])
    if "interfaces" in snapshot_data and isinstance(snapshot_data["interfaces"], list):
        snapshot_data["interfaces"] = tuple(snapshot_data["interfaces"])
    if "services" in snapshot_data and isinstance(snapshot_data["services"], list):
        snapshot_data["services"] = tuple(snapshot_data["services"])
    if "docker_containers" in snapshot_data and isinstance(snapshot_data["docker_containers"], list):
        snapshot_data["docker_containers"] = tuple(snapshot_data["docker_containers"])
    if "temps" in snapshot_data and isinstance(snapshot_data["temps"], list):
        snapshot_data["temps"] = tuple(snapshot_data["temps"])
    if "smart" in snapshot_data and isinstance(snapshot_data["smart"], list):
        snapshot_data["smart"] = tuple(snapshot_data["smart"])

    # Parse collected_at if present
    if "collected_at" in snapshot_data and isinstance(snapshot_data["collected_at"], str):
        snapshot_data["collected_at"] = _parse_datetime(snapshot_data["collected_at"])

    return IncidentSnapshot(
        id=row["id"],
        incident_id=row["incident_id"],
        which=row["which"],
        snapshot=SystemSnapshot(**snapshot_data),
        created_at=_parse_datetime(row["created_at"]),
    )


# =============================================================================
# Event Links
# =============================================================================


def link_events(
    incident_id: str,
    event_ids: list[str],
    conn: sqlite3.Connection | None = None,
) -> int:
    """Link telemetry events to an incident.

    Args:
        incident_id: The incident UUID.
        event_ids: List of event IDs to link.
        conn: SQLite connection.

    Returns:
        Number of new links created.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        created = 0

        for event_id in event_ids:
            try:
                cursor.execute(
                    "INSERT INTO incident_events (incident_id, event_id) VALUES (?, ?)",
                    (incident_id, event_id),
                )
                created += 1
            except sqlite3.IntegrityError:
                # Already linked
                pass

        conn.commit()
        return created

    finally:
        if own_conn:
            conn.close()


def get_linked_events(
    incident_id: str,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    """Get event IDs linked to an incident.

    Args:
        incident_id: The incident UUID.
        conn: SQLite connection.

    Returns:
        List of event IDs.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT event_id FROM incident_events WHERE incident_id = ?",
            (incident_id,),
        )
        return [row["event_id"] for row in cursor.fetchall()]

    finally:
        if own_conn:
            conn.close()


# =============================================================================
# Embeddings
# =============================================================================


def upsert_embedding(
    incident_id: str,
    embedding: list[float],
    model: str,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Store or update an embedding for an incident.

    Args:
        incident_id: The incident UUID.
        embedding: The embedding vector.
        model: Model used to generate the embedding.
        conn: SQLite connection.
    """
    import struct

    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()

        # Convert to bytes
        embedding_bytes = struct.pack(f"{len(embedding)}f", *embedding)

        cursor.execute(
            """
            INSERT OR REPLACE INTO incident_embeddings (
                incident_id, embedding, model, dim, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                embedding_bytes,
                model,
                len(embedding),
                _serialize_datetime(datetime.utcnow()),
            ),
        )
        conn.commit()

    finally:
        if own_conn:
            conn.close()


def get_embedding(
    incident_id: str,
    conn: sqlite3.Connection | None = None,
) -> list[float] | None:
    """Get the embedding for an incident.

    Args:
        incident_id: The incident UUID.
        conn: SQLite connection.

    Returns:
        Embedding vector, or None if not found.
    """
    import struct

    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT embedding, dim FROM incident_embeddings WHERE incident_id = ?",
            (incident_id,),
        )
        row = cursor.fetchone()

        if not row:
            return None

        dim = row["dim"]
        embedding = struct.unpack(f"{dim}f", row["embedding"])
        return list(embedding)

    finally:
        if own_conn:
            conn.close()


def get_all_embeddings(
    conn: sqlite3.Connection | None = None,
) -> dict[str, list[float]]:
    """Get all incident embeddings.

    Args:
        conn: SQLite connection.

    Returns:
        Dict mapping incident_id to embedding vector.
    """
    import struct

    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT incident_id, embedding, dim FROM incident_embeddings")

        result = {}
        for row in cursor.fetchall():
            dim = row["dim"]
            embedding = struct.unpack(f"{dim}f", row["embedding"])
            result[row["incident_id"]] = list(embedding)

        return result

    finally:
        if own_conn:
            conn.close()


def get_incidents_without_embeddings(
    limit: int = 100,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    """Get incident IDs that don't have embeddings.

    Args:
        limit: Maximum number to return.
        conn: SQLite connection.

    Returns:
        List of incident IDs.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT i.id FROM incidents i
            LEFT JOIN incident_embeddings e ON i.id = e.incident_id
            WHERE e.incident_id IS NULL
            LIMIT ?
            """,
            (limit,),
        )
        return [row["id"] for row in cursor.fetchall()]

    finally:
        if own_conn:
            conn.close()


# =============================================================================
# Outcome helpers
# =============================================================================


def finalize_outcome(
    incident_id: str,
    outcome: str,
    verification_steps: list[str] | None = None,
    root_cause: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> IncidentReport | None:
    """Finalize an incident with outcome and verification results.

    Calculates time_to_mitigate_sec and time_to_resolve_sec based
    on status transitions. Also records the outcome for efficacy tracking.

    Args:
        incident_id: The incident UUID.
        outcome: Final outcome.
        verification_steps: Verification checks that were run.
        root_cause: Confirmed root cause (if known).
        conn: SQLite connection.

    Returns:
        Updated IncidentReport.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        # Get current incident
        incident = get_incident(incident_id, conn=conn)
        if not incident:
            return None

        now = datetime.utcnow()
        delta_sec = int((now - incident.created_at).total_seconds())

        # Determine status based on outcome
        if outcome == "improved":
            new_status = "resolved"
            time_to_resolve = delta_sec
            time_to_mitigate = incident.time_to_mitigate_sec or delta_sec
        elif outcome == "partial":
            new_status = "mitigated"
            time_to_resolve = None
            time_to_mitigate = delta_sec
        elif outcome == "worse" or outcome == "no_change":
            new_status = "open"  # Keep open for further work
            time_to_resolve = None
            time_to_mitigate = None
        else:
            new_status = incident.status
            time_to_resolve = incident.time_to_resolve_sec
            time_to_mitigate = incident.time_to_mitigate_sec

        updated_incident = update_incident(
            incident_id,
            status=new_status,
            outcome=outcome,
            verification_steps=verification_steps,
            root_cause=root_cause,
            time_to_mitigate_sec=time_to_mitigate,
            time_to_resolve_sec=time_to_resolve,
            conn=conn,
        )

        # Record outcome for efficacy tracking
        if updated_incident and outcome in ("improved", "partial", "no_change", "worse"):
            try:
                from elle.daemon.incidents.efficacy_tracker import record_outcome
                record_outcome(updated_incident, outcome, conn=conn)
            except Exception:
                # Don't fail finalization if efficacy tracking fails
                pass

        return updated_incident

    finally:
        if own_conn:
            conn.close()


# =============================================================================
# Statistics
# =============================================================================


def get_incident_count(
    conn: sqlite3.Connection | None = None,
) -> int:
    """Get total number of incidents.

    Args:
        conn: SQLite connection.

    Returns:
        Total incident count.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM incidents")
        return cursor.fetchone()[0]

    finally:
        if own_conn:
            conn.close()


def get_action_count(
    conn: sqlite3.Connection | None = None,
) -> int:
    """Get total number of actions.

    Args:
        conn: SQLite connection.

    Returns:
        Total action count.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM incident_actions")
        return cursor.fetchone()[0]

    finally:
        if own_conn:
            conn.close()


def get_snapshot_count(
    conn: sqlite3.Connection | None = None,
) -> int:
    """Get total number of snapshots.

    Args:
        conn: SQLite connection.

    Returns:
        Total snapshot count.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM incident_snapshots")
        return cursor.fetchone()[0]

    finally:
        if own_conn:
            conn.close()


# =============================================================================
# Decision Record CRUD (V2)
# =============================================================================


def store_decision_record(
    incident_id: str,
    decision_record: DecisionRecord,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Store a structured decision record for an incident.

    Replaces any existing decision record for this incident.

    Args:
        incident_id: The incident UUID.
        decision_record: The structured decision record.
        conn: SQLite connection.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()

        # Serialize nested models
        confidence_json = _json_dumps(decision_record.confidence.model_dump())
        provenance_json = _json_dumps(decision_record.provenance.model_dump())
        planned_commands_json = _json_dumps(list(decision_record.planned_commands))

        cursor.execute(
            """
            INSERT OR REPLACE INTO decision_records (
                incident_id, chosen_approach, rationale,
                confidence_json, provenance_json, planned_commands_json,
                decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                decision_record.chosen_approach,
                decision_record.rationale,
                confidence_json,
                provenance_json,
                planned_commands_json,
                _serialize_datetime(decision_record.decided_at),
            ),
        )
        conn.commit()

    finally:
        if own_conn:
            conn.close()


def get_decision_record(
    incident_id: str,
    conn: sqlite3.Connection | None = None,
) -> DecisionRecord | None:
    """Get the decision record for an incident.

    Args:
        incident_id: The incident UUID.
        conn: SQLite connection.

    Returns:
        DecisionRecord if found, None otherwise.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM decision_records WHERE incident_id = ?",
            (incident_id,),
        )
        row = cursor.fetchone()

        if not row:
            return None

        return _row_to_decision_record(row)

    finally:
        if own_conn:
            conn.close()


def _row_to_decision_record(row: sqlite3.Row) -> DecisionRecord:
    """Convert a database row to a DecisionRecord."""
    # Parse confidence
    confidence_data = _json_loads(row["confidence_json"])
    confidence = ConfidenceBreakdown(**confidence_data) if confidence_data else ConfidenceBreakdown(overall=0.0)

    # Parse provenance
    provenance_data = _json_loads(row["provenance_json"])
    provenance = _parse_provenance(provenance_data)

    # Parse planned commands
    planned_commands = tuple(_json_loads(row["planned_commands_json"]) or [])

    return DecisionRecord(
        chosen_approach=row["chosen_approach"],
        rationale=row["rationale"] or "",
        confidence=confidence,
        provenance=provenance,
        planned_commands=planned_commands,
        decided_at=_parse_datetime(row["decided_at"]),
    )


def _parse_provenance(data: dict[str, Any]) -> Provenance:
    """Parse a provenance dict into a Provenance model."""
    if not data:
        return Provenance()

    # Parse man page citations
    man_pages = []
    for mp in data.get("man_pages", []):
        man_pages.append(ManPageCitation(**mp))

    # Parse incident citations
    prior_incidents = []
    for pi in data.get("prior_incidents", []):
        # Handle successful_actions as tuple
        if "successful_actions" in pi and isinstance(pi["successful_actions"], list):
            pi["successful_actions"] = tuple(pi["successful_actions"])
        prior_incidents.append(IncidentCitation(**pi))

    # Parse triggering events
    triggering_events = None
    if data.get("triggering_events"):
        te_data = data["triggering_events"]
        # Handle tuples
        if "event_ids" in te_data and isinstance(te_data["event_ids"], list):
            te_data["event_ids"] = tuple(te_data["event_ids"])
        if "event_summaries" in te_data and isinstance(te_data["event_summaries"], list):
            te_data["event_summaries"] = tuple(te_data["event_summaries"])
        triggering_events = TelemetryCitation(**te_data)

    return Provenance(
        man_pages=tuple(man_pages),
        prior_incidents=tuple(prior_incidents),
        triggering_events=triggering_events,
        primary_source=data.get("primary_source", "llm_only"),
    )


# =============================================================================
# Config State CRUD (V2)
# =============================================================================


def store_config_state(
    incident_id: str,
    which: str,
    config_state: ConfigFileState,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Store a config file state for an incident.

    Replaces any existing state for this (incident_id, which, path).

    Args:
        incident_id: The incident UUID.
        which: 'pre' or 'post' snapshot.
        config_state: The config file state.
        conn: SQLite connection.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()

        mtime_str = (
            _serialize_datetime(config_state.mtime)
            if config_state.mtime
            else None
        )

        cursor.execute(
            """
            INSERT OR REPLACE INTO config_states (
                incident_id, snapshot_which, path,
                sha256_before, sha256_after, size_bytes,
                mtime, backup_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                which,
                config_state.path,
                config_state.sha256_before,
                config_state.sha256_after,
                config_state.size_bytes,
                mtime_str,
                config_state.backup_path,
            ),
        )
        conn.commit()

    finally:
        if own_conn:
            conn.close()


def get_config_states(
    incident_id: str,
    which: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[ConfigFileState]:
    """Get config file states for an incident.

    Args:
        incident_id: The incident UUID.
        which: Filter by 'pre' or 'post' (None for all).
        conn: SQLite connection.

    Returns:
        List of ConfigFileState.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()

        if which:
            cursor.execute(
                """
                SELECT * FROM config_states
                WHERE incident_id = ? AND snapshot_which = ?
                ORDER BY path
                """,
                (incident_id, which),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM config_states
                WHERE incident_id = ?
                ORDER BY snapshot_which, path
                """,
                (incident_id,),
            )

        return [_row_to_config_state(row) for row in cursor.fetchall()]

    finally:
        if own_conn:
            conn.close()


def _row_to_config_state(row: sqlite3.Row) -> ConfigFileState:
    """Convert a database row to a ConfigFileState."""
    mtime = None
    if row["mtime"]:
        mtime = _parse_datetime(row["mtime"])

    return ConfigFileState(
        path=row["path"],
        sha256_before=row["sha256_before"],
        sha256_after=row["sha256_after"],
        size_bytes=row["size_bytes"],
        mtime=mtime,
        backup_path=row["backup_path"],
    )


def get_config_state_by_path(
    path: str,
    limit: int = 10,
    conn: sqlite3.Connection | None = None,
) -> list[tuple[str, str, ConfigFileState]]:
    """Get all config states for a path across incidents.

    Useful for tracking history of changes to a specific file.

    Args:
        path: The config file path.
        limit: Maximum number of results.
        conn: SQLite connection.

    Returns:
        List of (incident_id, which, ConfigFileState) tuples.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT incident_id, snapshot_which, *
            FROM config_states
            WHERE path = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (path, limit),
        )

        results = []
        for row in cursor.fetchall():
            state = _row_to_config_state(row)
            results.append((row["incident_id"], row["snapshot_which"], state))

        return results

    finally:
        if own_conn:
            conn.close()
