"""CRUD operations for the Incident Vault.

Provides database operations for incidents, actions, and snapshots.
All operations are append-only where appropriate for auditability.

Uses PostgreSQL via psycopg with connection pooling from
``elle.storage.engine``.  The ``get_conn`` context manager handles
commit/rollback automatically.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, cast

import psycopg

from elle.common.pydantic_compat import safe_model_dump
from elle.daemon.incidents.anonymize import AnonymizedIncidentReport
from elle.daemon.incidents.models import (
    ConfidenceBreakdown,
    ConfigFileState,
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
from elle.daemon.incidents.schema import PG_SCHEMA
from elle.storage.engine import get_conn


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
    if isinstance(s, (dict, list)):
        return s
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

    Returns:
        The created IncidentReport.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        incident_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        incident = IncidentReport(
            incident_id=incident_id,
            created_at=now,
            updated_at=now,
            domain=domain,  # type: ignore[arg-type]
            severity=severity,  # type: ignore[arg-type]
            status="open",
            title=title,
            trigger_source=trigger_source,  # type: ignore[arg-type]
            trigger_command=trigger_command,
        )

        conn.execute(
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
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s
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
                _json_dumps([safe_model_dump(p) for p in incident.preconditions]),
                incident.outcome,
                _json_dumps(list(incident.verification_steps)),
                incident.time_to_mitigate_sec,
                incident.time_to_resolve_sec,
                _json_dumps(safe_model_dump(incident.fingerprint)),
                _json_dumps(list(incident.tags)),
                incident.confidence,
                incident.trigger_source,
                incident.trigger_command,
            ),
        )
        return incident


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
    conn: psycopg.Connection | None = None,
) -> IncidentReport | None:
    """Update an existing incident.

    Only provided fields are updated. This maintains append-only
    semantics by updating the updated_at timestamp.

    Args:
        incident_id: ID of the incident to update.
        **kwargs: Fields to update.
        conn: psycopg connection (used internally for transactional calls).

    Returns:
        Updated IncidentReport, or None if not found.
    """
    def _do_update(c: psycopg.Connection) -> IncidentReport | None:
        # Build update statement dynamically
        updates = ["updated_at = %s"]
        values: list[Any] = [_serialize_datetime(datetime.now(timezone.utc))]

        if status is not None:
            updates.append("status = %s")
            values.append(status)
        if summary is not None:
            updates.append("summary = %s")
            values.append(summary)
        if symptoms is not None:
            updates.append("symptoms_json = %s")
            values.append(_json_dumps(symptoms))
        if suspected_causes is not None:
            updates.append("suspected_causes_json = %s")
            values.append(_json_dumps(suspected_causes))
        if root_cause is not None:
            updates.append("root_cause = %s")
            values.append(root_cause)
        if event_ids is not None:
            updates.append("event_ids_json = %s")
            values.append(_json_dumps(event_ids))
        if log_snippets is not None:
            updates.append("log_snippets_json = %s")
            values.append(_json_dumps(log_snippets))
        if metrics is not None:
            updates.append("metrics_json = %s")
            values.append(_json_dumps(metrics))
        if decision is not None:
            updates.append("decision_json = %s")
            values.append(_json_dumps(decision))
        if preconditions is not None:
            updates.append("preconditions_json = %s")
            values.append(_json_dumps([safe_model_dump(p) for p in preconditions]))
        if outcome is not None:
            updates.append("outcome = %s")
            values.append(outcome)
        if verification_steps is not None:
            updates.append("verification_steps_json = %s")
            values.append(_json_dumps(verification_steps))
        if time_to_mitigate_sec is not None:
            updates.append("time_to_mitigate_sec = %s")
            values.append(time_to_mitigate_sec)
        if time_to_resolve_sec is not None:
            updates.append("time_to_resolve_sec = %s")
            values.append(time_to_resolve_sec)
        if fingerprint is not None:
            updates.append("fingerprint_json = %s")
            values.append(_json_dumps(safe_model_dump(fingerprint)))
        if tags is not None:
            updates.append("tags_json = %s")
            values.append(_json_dumps(tags))
        if confidence is not None:
            updates.append("confidence = %s")
            values.append(confidence)

        values.append(incident_id)

        result = c.execute(
            f"UPDATE incidents SET {', '.join(updates)} WHERE id = %s",
            values,
        )

        if result.rowcount == 0:
            return None

        return _get_incident_with_conn(incident_id, c)

    if conn is not None:
        return _do_update(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _do_update(c)


def get_incident(
    incident_id: str,
) -> IncidentReport | None:
    """Get an incident by ID.

    Args:
        incident_id: The incident UUID.

    Returns:
        IncidentReport if found, None otherwise.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        return _get_incident_with_conn(incident_id, conn)


def _get_incident_with_conn(
    incident_id: str,
    conn: psycopg.Connection,
) -> IncidentReport | None:
    """Get an incident by ID using an existing connection.

    Args:
        incident_id: The incident UUID.
        conn: Active psycopg connection.

    Returns:
        IncidentReport if found, None otherwise.
    """
    row = conn.execute(
        "SELECT * FROM incidents WHERE id = %s", (incident_id,)
    ).fetchone()

    if not row:
        return None

    return _row_to_incident(row)


def list_incidents(
    status: str | None = None,
    domain: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[IncidentReport]:
    """List incidents with optional filtering.

    Args:
        status: Filter by status.
        domain: Filter by domain.
        limit: Maximum number of results.
        offset: Offset for pagination.

    Returns:
        List of IncidentReports.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        query = "SELECT * FROM incidents WHERE 1=1"
        params: list[Any] = []

        if status:
            query += " AND status = %s"
            params.append(status)
        if domain:
            query += " AND domain = %s"
            params.append(domain)

        query += " ORDER BY updated_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()

        return [_row_to_incident(row) for row in rows]


def delete_incident(
    incident_id: str,
) -> bool:
    """Delete an incident and all related data.

    Note: Prefer marking as false_positive rather than deleting
    for audit trail purposes.

    Args:
        incident_id: The incident UUID.

    Returns:
        True if deleted, False if not found.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        result = conn.execute(
            "DELETE FROM incidents WHERE id = %s", (incident_id,)
        )
        return result.rowcount > 0


def _row_to_incident(row: dict) -> IncidentReport:
    """Convert a database row to an IncidentReport."""
    preconditions_data = _json_loads(row["preconditions_json"])
    preconditions = tuple(
        Precondition(**p) if isinstance(p, dict) else p
        for p in (preconditions_data if isinstance(preconditions_data, list) else [])
    )

    fingerprint_data = _json_loads(row["fingerprint_json"])
    fingerprint = Fingerprint(**fingerprint_data) if fingerprint_data else Fingerprint()

    created_at = row["created_at"]
    if isinstance(created_at, str):
        created_at = _parse_datetime(created_at)

    updated_at = row["updated_at"]
    if isinstance(updated_at, str):
        updated_at = _parse_datetime(updated_at)

    return IncidentReport(
        incident_id=row["id"],
        created_at=created_at,
        updated_at=updated_at,
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
# Prepared Plan Helpers
# =============================================================================


def get_prepared_plan(
    incident_id: str,
) -> dict[str, Any] | None:
    """Get the prepared plan JSON for a forecast incident.

    Reads the prepared_plan_json column (added in V5 schema migration).

    Args:
        incident_id: The incident UUID.

    Returns:
        Parsed plan dict if found, None otherwise.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        row = conn.execute(
            "SELECT prepared_plan_json FROM incidents WHERE id = %s",
            (incident_id,),
        ).fetchone()

        if not row:
            return None

        plan_json = row.get("prepared_plan_json")
        if not plan_json:
            return None

        return cast(dict[str, Any] | None, _json_loads(plan_json))


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

    Returns:
        The created IncidentAction.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        # Get next step index
        row = conn.execute(
            "SELECT COALESCE(MAX(step_index), -1) + 1 FROM incident_actions WHERE incident_id = %s",
            (incident_id,),
        ).fetchone()
        step_index: int = int(row["coalesce"]) if row else 0

        now = datetime.now(timezone.utc)
        action = IncidentAction(
            incident_id=incident_id,
            step_index=step_index,
            kind=kind,  # type: ignore[arg-type]
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

        inserted = conn.execute(
            """
            INSERT INTO incident_actions (
                incident_id, step_index, kind, command, payload_json,
                exit_code, stdout, stderr, success, privileged,
                created_at, duration_ms
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
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
                action.success,
                action.privileged,
                _serialize_datetime(action.created_at),
                action.duration_ms,
            ),
        ).fetchone()

        action = IncidentAction(
            id=inserted["id"] if inserted else None,
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

        return action


def get_actions(
    incident_id: str,
) -> list[IncidentAction]:
    """Get all actions for an incident, ordered by step_index.

    Args:
        incident_id: The incident UUID.

    Returns:
        List of IncidentActions in order.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        rows = conn.execute(
            """
            SELECT * FROM incident_actions
            WHERE incident_id = %s
            ORDER BY step_index
            """,
            (incident_id,),
        ).fetchall()

        return [_row_to_action(row) for row in rows]


def _row_to_action(row: dict) -> IncidentAction:
    """Convert a database row to an IncidentAction."""
    created_at = row["created_at"]
    if isinstance(created_at, str):
        created_at = _parse_datetime(created_at)

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
        created_at=created_at,
        duration_ms=row["duration_ms"],
    )


# =============================================================================
# Snapshot CRUD
# =============================================================================


def attach_snapshot(
    incident_id: str,
    which: str,
    snapshot: SystemSnapshot,
) -> IncidentSnapshot:
    """Attach a system snapshot to an incident.

    Each incident can have one 'pre' and one 'post' snapshot.
    Calling again for the same (incident_id, which) replaces
    the existing snapshot.

    Args:
        incident_id: Parent incident ID.
        which: 'pre' or 'post'.
        snapshot: The SystemSnapshot to attach.

    Returns:
        The created/updated IncidentSnapshot.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        now = datetime.now(timezone.utc)

        inserted = conn.execute(
            """
            INSERT INTO incident_snapshots (
                incident_id, which, snapshot_json, created_at
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT (incident_id, which) DO UPDATE SET
                snapshot_json = EXCLUDED.snapshot_json,
                created_at = EXCLUDED.created_at
            RETURNING id
            """,
            (
                incident_id,
                which,
                _json_dumps(safe_model_dump(snapshot)),
                _serialize_datetime(now),
            ),
        ).fetchone()

        snapshot_record = IncidentSnapshot(
            id=inserted["id"] if inserted else None,
            incident_id=incident_id,
            which=which,  # type: ignore[arg-type]
            snapshot=snapshot,
            created_at=now,
        )

        return snapshot_record


def get_snapshot(
    incident_id: str,
    which: str,
) -> IncidentSnapshot | None:
    """Get a specific snapshot for an incident.

    Args:
        incident_id: The incident UUID.
        which: 'pre' or 'post'.

    Returns:
        IncidentSnapshot if found, None otherwise.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        row = conn.execute(
            """
            SELECT * FROM incident_snapshots
            WHERE incident_id = %s AND which = %s
            """,
            (incident_id, which),
        ).fetchone()

        if not row:
            return None

        return _row_to_snapshot(row)


def get_snapshots(
    incident_id: str,
) -> dict[str, IncidentSnapshot]:
    """Get all snapshots for an incident.

    Args:
        incident_id: The incident UUID.

    Returns:
        Dict mapping 'pre'/'post' to IncidentSnapshot.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        rows = conn.execute(
            "SELECT * FROM incident_snapshots WHERE incident_id = %s",
            (incident_id,),
        ).fetchall()

        result: dict[str, IncidentSnapshot] = {}
        for row in rows:
            snap = _row_to_snapshot(row)
            result[snap.which] = snap

        return result


def _row_to_snapshot(row: dict) -> IncidentSnapshot:
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

    created_at = row["created_at"]
    if isinstance(created_at, str):
        created_at = _parse_datetime(created_at)

    return IncidentSnapshot(
        id=row["id"],
        incident_id=row["incident_id"],
        which=row["which"],
        snapshot=SystemSnapshot(**snapshot_data),
        created_at=created_at,
    )


# =============================================================================
# Event Links
# =============================================================================


def link_events(
    incident_id: str,
    event_ids: list[str],
) -> int:
    """Link telemetry events to an incident.

    Args:
        incident_id: The incident UUID.
        event_ids: List of event IDs to link.

    Returns:
        Number of new links created.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        created = 0

        for event_id in event_ids:
            result = conn.execute(
                """
                INSERT INTO incident_events (incident_id, event_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (incident_id, event_id),
            )
            if result.rowcount > 0:
                created += 1

        return created


def get_linked_events(
    incident_id: str,
) -> list[str]:
    """Get event IDs linked to an incident.

    Args:
        incident_id: The incident UUID.

    Returns:
        List of event IDs.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        rows = conn.execute(
            "SELECT event_id FROM incident_events WHERE incident_id = %s",
            (incident_id,),
        ).fetchall()
        return [str(row["event_id"]) for row in rows]


# =============================================================================
# Embeddings
# =============================================================================


def upsert_embedding(
    incident_id: str,
    embedding: list[float],
    model: str,
) -> None:
    """Store or update an embedding for an incident.

    Args:
        incident_id: The incident UUID.
        embedding: The embedding vector.
        model: Model used to generate the embedding.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        conn.execute(
            """
            INSERT INTO incident_embeddings (
                incident_id, embedding, model, created_at
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT (incident_id) DO UPDATE SET
                embedding = EXCLUDED.embedding,
                model = EXCLUDED.model,
                created_at = EXCLUDED.created_at
            """,
            (
                incident_id,
                embedding,
                model,
                _serialize_datetime(datetime.now(timezone.utc)),
            ),
        )


def get_embedding(
    incident_id: str,
) -> list[float] | None:
    """Get the embedding for an incident.

    Args:
        incident_id: The incident UUID.

    Returns:
        Embedding vector, or None if not found.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        row = conn.execute(
            "SELECT embedding FROM incident_embeddings WHERE incident_id = %s",
            (incident_id,),
        ).fetchone()

        if not row:
            return None

        return list(row["embedding"])


def get_all_embeddings() -> dict[str, list[float]]:
    """Get all incident embeddings.

    Returns:
        Dict mapping incident_id to embedding vector.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        rows = conn.execute(
            "SELECT incident_id, embedding FROM incident_embeddings"
        ).fetchall()

        result: dict[str, list[float]] = {}
        for row in rows:
            result[str(row["incident_id"])] = list(row["embedding"])

        return result


def get_incidents_without_embeddings(
    limit: int = 100,
) -> list[str]:
    """Get incident IDs that don't have embeddings.

    Args:
        limit: Maximum number to return.

    Returns:
        List of incident IDs.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        rows = conn.execute(
            """
            SELECT i.id FROM incidents i
            LEFT JOIN incident_embeddings e ON i.id = e.incident_id
            WHERE e.incident_id IS NULL
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        return [str(row["id"]) for row in rows]


# =============================================================================
# Outcome helpers
# =============================================================================


def finalize_outcome(
    incident_id: str,
    outcome: str,
    verification_steps: list[str] | None = None,
    root_cause: str | None = None,
) -> IncidentReport | None:
    """Finalize an incident with outcome and verification results.

    Calculates time_to_mitigate_sec and time_to_resolve_sec based
    on status transitions. Also records the outcome for efficacy tracking.

    Args:
        incident_id: The incident UUID.
        outcome: Final outcome.
        verification_steps: Verification checks that were run.
        root_cause: Confirmed root cause (if known).

    Returns:
        Updated IncidentReport.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        # Get current incident
        incident = _get_incident_with_conn(incident_id, conn)
        if not incident:
            return None

        now = datetime.now(timezone.utc)
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


# =============================================================================
# Statistics
# =============================================================================


def get_incident_count() -> int:
    """Get total number of incidents.

    Returns:
        Total incident count.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM incidents").fetchone()
        return int(row["cnt"]) if row else 0


def get_action_count() -> int:
    """Get total number of actions.

    Returns:
        Total action count.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM incident_actions").fetchone()
        return int(row["cnt"]) if row else 0


def get_snapshot_count() -> int:
    """Get total number of snapshots.

    Returns:
        Total snapshot count.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM incident_snapshots").fetchone()
        return int(row["cnt"]) if row else 0


# =============================================================================
# Decision Record CRUD (V2)
# =============================================================================


def store_decision_record(
    incident_id: str,
    decision_record: DecisionRecord,
) -> None:
    """Store a structured decision record for an incident.

    Replaces any existing decision record for this incident.

    Args:
        incident_id: The incident UUID.
        decision_record: The structured decision record.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        # Serialize nested models
        confidence_json = _json_dumps(safe_model_dump(decision_record.confidence))
        provenance_json = _json_dumps(safe_model_dump(decision_record.provenance))
        planned_commands_json = _json_dumps(list(decision_record.planned_commands))

        conn.execute(
            """
            INSERT INTO decision_records (
                incident_id, chosen_approach, rationale,
                confidence_json, provenance_json, planned_commands_json,
                decided_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (incident_id) DO UPDATE SET
                chosen_approach = EXCLUDED.chosen_approach,
                rationale = EXCLUDED.rationale,
                confidence_json = EXCLUDED.confidence_json,
                provenance_json = EXCLUDED.provenance_json,
                planned_commands_json = EXCLUDED.planned_commands_json,
                decided_at = EXCLUDED.decided_at
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


def get_decision_record(
    incident_id: str,
) -> DecisionRecord | None:
    """Get the decision record for an incident.

    Args:
        incident_id: The incident UUID.

    Returns:
        DecisionRecord if found, None otherwise.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        row = conn.execute(
            "SELECT * FROM decision_records WHERE incident_id = %s",
            (incident_id,),
        ).fetchone()

        if not row:
            return None

        return _row_to_decision_record(row)


def _row_to_decision_record(row: dict) -> DecisionRecord:
    """Convert a database row to a DecisionRecord."""
    # Parse confidence
    confidence_data = _json_loads(row["confidence_json"])
    confidence = ConfidenceBreakdown(**confidence_data) if confidence_data else ConfidenceBreakdown(overall=0.0)

    # Parse provenance
    provenance_data = _json_loads(row["provenance_json"])
    provenance = _parse_provenance(provenance_data)

    # Parse planned commands
    planned_commands = tuple(_json_loads(row["planned_commands_json"]) or [])

    decided_at = row["decided_at"]
    if isinstance(decided_at, str):
        decided_at = _parse_datetime(decided_at)

    return DecisionRecord(
        chosen_approach=row["chosen_approach"],
        rationale=row["rationale"] or "",
        confidence=confidence,
        provenance=provenance,
        planned_commands=planned_commands,
        decided_at=decided_at,
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
) -> None:
    """Store a config file state for an incident.

    Replaces any existing state for this (incident_id, which, path).

    Args:
        incident_id: The incident UUID.
        which: 'pre' or 'post' snapshot.
        config_state: The config file state.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        mtime_str = _serialize_datetime(config_state.mtime) if config_state.mtime else None

        conn.execute(
            """
            INSERT INTO config_states (
                incident_id, snapshot_which, path,
                sha256_before, sha256_after, size_bytes,
                mtime, backup_path
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (incident_id, snapshot_which, path) DO UPDATE SET
                sha256_before = EXCLUDED.sha256_before,
                sha256_after = EXCLUDED.sha256_after,
                size_bytes = EXCLUDED.size_bytes,
                mtime = EXCLUDED.mtime,
                backup_path = EXCLUDED.backup_path
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


def get_config_states(
    incident_id: str,
    which: str | None = None,
) -> list[ConfigFileState]:
    """Get config file states for an incident.

    Args:
        incident_id: The incident UUID.
        which: Filter by 'pre' or 'post' (None for all).

    Returns:
        List of ConfigFileState.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        if which:
            rows = conn.execute(
                """
                SELECT * FROM config_states
                WHERE incident_id = %s AND snapshot_which = %s
                ORDER BY path
                """,
                (incident_id, which),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM config_states
                WHERE incident_id = %s
                ORDER BY snapshot_which, path
                """,
                (incident_id,),
            ).fetchall()

        return [_row_to_config_state(row) for row in rows]


def _row_to_config_state(row: dict) -> ConfigFileState:
    """Convert a database row to a ConfigFileState."""
    mtime = None
    if row["mtime"]:
        mtime_val = row["mtime"]
        if isinstance(mtime_val, str):
            mtime = _parse_datetime(mtime_val)
        else:
            mtime = mtime_val

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
) -> list[tuple[str, str, ConfigFileState]]:
    """Get all config states for a path across incidents.

    Useful for tracking history of changes to a specific file.

    Args:
        path: The config file path.
        limit: Maximum number of results.

    Returns:
        List of (incident_id, which, ConfigFileState) tuples.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        rows = conn.execute(
            """
            SELECT incident_id, snapshot_which, *
            FROM config_states
            WHERE path = %s
            ORDER BY id DESC
            LIMIT %s
            """,
            (path, limit),
        ).fetchall()

        results: list[tuple[str, str, ConfigFileState]] = []
        for row in rows:
            state = _row_to_config_state(row)
            results.append((str(row["incident_id"]), str(row["snapshot_which"]), state))

        return results


# =============================================================================
# Telemetry Snapshot CRUD (V4)
# =============================================================================


def attach_telemetry_snapshot(
    incident_id: str,
    which: str,
    snapshot: TelemetrySnapshotModel,
) -> None:
    """Attach a telemetry snapshot to an incident.

    Each incident can have one 'pre' and one 'post' telemetry snapshot.
    Calling again for the same (incident_id, which) replaces the existing snapshot.

    Args:
        incident_id: Parent incident ID.
        which: 'pre' or 'post'.
        snapshot: The TelemetrySnapshot to attach.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        conn.execute(
            """
            INSERT INTO telemetry_snapshots (
                incident_id, which, snapshot_json, collected_at
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT (incident_id, which) DO UPDATE SET
                snapshot_json = EXCLUDED.snapshot_json,
                collected_at = EXCLUDED.collected_at
            """,
            (
                incident_id,
                which,
                _json_dumps(safe_model_dump(snapshot)),
                _serialize_datetime(snapshot.collected_at),
            ),
        )


def get_telemetry_snapshot(
    incident_id: str,
    which: str,
    conn: psycopg.Connection | None = None,
) -> TelemetrySnapshotModel | None:
    """Get a telemetry snapshot for an incident.

    Args:
        incident_id: The incident UUID.
        which: 'pre' or 'post'.
        conn: psycopg connection (used internally for transactional calls).

    Returns:
        TelemetrySnapshot if found, None otherwise.
    """
    def _do_get(c: psycopg.Connection) -> TelemetrySnapshotModel | None:
        row = c.execute(
            """
            SELECT snapshot_json FROM telemetry_snapshots
            WHERE incident_id = %s AND which = %s
            """,
            (incident_id, which),
        ).fetchone()

        if not row:
            return None

        data = _json_loads(row["snapshot_json"])
        return _parse_telemetry_snapshot(data)

    if conn is not None:
        return _do_get(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _do_get(c)


def get_telemetry_snapshots(
    incident_id: str,
) -> dict[str, TelemetrySnapshotModel]:
    """Get all telemetry snapshots for an incident.

    Args:
        incident_id: The incident UUID.

    Returns:
        Dict mapping 'pre'/'post' to TelemetrySnapshot.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        rows = conn.execute(
            "SELECT which, snapshot_json FROM telemetry_snapshots WHERE incident_id = %s",
            (incident_id,),
        ).fetchall()

        result: dict[str, Any] = {}
        for row in rows:
            data = _json_loads(row["snapshot_json"])
            snapshot = _parse_telemetry_snapshot(data)
            if snapshot:
                result[row["which"]] = snapshot

        return result


def _parse_telemetry_snapshot(data: dict[str, Any]) -> TelemetrySnapshotModel | None:
    """Parse telemetry snapshot from JSON data."""
    if not data:
        return None

    # Import here to avoid circular imports
    from elle.daemon.incidents.telemetry_snapshot import (
        CPUPressure,
        DiskInfo,
        DiskIOPressure,
        GPUDevice,
        GPUMetrics,
        InterfaceInfo,
        KernelHardwareSignals,
        MemoryPressure,
        NetworkLiveness,
        ServiceRuntimeSnapshot,
    )
    from elle.daemon.incidents.telemetry_snapshot import (
        TelemetrySnapshot as TelemetrySnapshotModel,
    )

    try:
        # Parse nested models
        cpu_data = data.get("cpu", {})
        cpu = CPUPressure(**cpu_data)

        memory_data = data.get("memory", {})
        memory = MemoryPressure(**memory_data)

        disk_data = data.get("disk", {})
        disks = tuple(DiskInfo(**d) for d in disk_data.get("disks", []))
        disk = DiskIOPressure(
            disks=disks,
            io_wait_pct=disk_data.get("io_wait_pct", 0.0),
            io_errors_1h=disk_data.get("io_errors_1h", 0),
            psi_some_pct=disk_data.get("psi_some_pct", 0.0),
            psi_full_pct=disk_data.get("psi_full_pct", 0.0),
        )

        network_data = data.get("network", {})
        interfaces = tuple(InterfaceInfo(**i) for i in network_data.get("interfaces", []))
        network = NetworkLiveness(
            interfaces=interfaces,
            interfaces_up=network_data.get("interfaces_up", 0),
            interfaces_down=network_data.get("interfaces_down", 0),
            default_route_present=network_data.get("default_route_present", False),
            dns_resolution_ok=network_data.get("dns_resolution_ok", False),
        )

        kernel_data = data.get("kernel", {})
        kernel = KernelHardwareSignals(**kernel_data)

        # Parse GPU metrics
        gpu_data = data.get("gpu", {})
        gpu_devices = tuple(GPUDevice(**d) for d in gpu_data.get("devices", []))
        gpu = GPUMetrics(
            available=gpu_data.get("available", False),
            device_count=gpu_data.get("device_count", 0),
            devices=gpu_devices,
            max_memory_pct=gpu_data.get("max_memory_pct", 0.0),
            max_utilization_pct=gpu_data.get("max_utilization_pct", 0.0),
            max_temperature_c=gpu_data.get("max_temperature_c", 0),
            total_ecc_errors=gpu_data.get("total_ecc_errors", 0),
        )

        services = tuple(ServiceRuntimeSnapshot(**s) for s in data.get("services", []))

        # Parse collected_at
        collected_at = datetime.now(timezone.utc)
        if "collected_at" in data:
            if isinstance(data["collected_at"], str):
                collected_at = _parse_datetime(data["collected_at"])
            elif isinstance(data["collected_at"], datetime):
                collected_at = data["collected_at"]

        return TelemetrySnapshotModel(
            cpu=cpu,
            memory=memory,
            disk=disk,
            network=network,
            kernel=kernel,
            gpu=gpu,
            services=services,
            collected_at=collected_at,
            hostname=data.get("hostname", ""),
            uptime_sec=data.get("uptime_sec", 0),
        )
    except Exception:
        return None


# =============================================================================
# Control Surface Snapshot CRUD (V4)
# =============================================================================


def attach_control_surface_snapshot(
    incident_id: str,
    which: str,
    snapshot: ControlSurfaceSnapshotModel,
) -> None:
    """Attach a control surface snapshot to an incident.

    Each incident can have one 'pre' and one 'post' control surface snapshot.
    Calling again for the same (incident_id, which) replaces the existing snapshot.

    Args:
        incident_id: Parent incident ID.
        which: 'pre' or 'post'.
        snapshot: The ControlSurfaceSnapshot to attach.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        # Store surface hashes as JSON for indexing
        surface_hashes = _json_dumps(snapshot.surface_hashes())

        conn.execute(
            """
            INSERT INTO control_surface_snapshots (
                incident_id, which, snapshot_json, surface_hashes, collected_at
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (incident_id, which) DO UPDATE SET
                snapshot_json = EXCLUDED.snapshot_json,
                surface_hashes = EXCLUDED.surface_hashes,
                collected_at = EXCLUDED.collected_at
            """,
            (
                incident_id,
                which,
                _json_dumps(safe_model_dump(snapshot)),
                surface_hashes,
                _serialize_datetime(snapshot.collected_at),
            ),
        )


def get_control_surface_snapshot(
    incident_id: str,
    which: str,
) -> ControlSurfaceSnapshotModel | None:
    """Get a control surface snapshot for an incident.

    Args:
        incident_id: The incident UUID.
        which: 'pre' or 'post'.

    Returns:
        ControlSurfaceSnapshot if found, None otherwise.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        row = conn.execute(
            """
            SELECT snapshot_json FROM control_surface_snapshots
            WHERE incident_id = %s AND which = %s
            """,
            (incident_id, which),
        ).fetchone()

        if not row:
            return None

        data = _json_loads(row["snapshot_json"])
        return _parse_control_surface_snapshot(data)


def get_control_surface_snapshots(
    incident_id: str,
) -> dict[str, ControlSurfaceSnapshotModel]:
    """Get all control surface snapshots for an incident.

    Args:
        incident_id: The incident UUID.

    Returns:
        Dict mapping 'pre'/'post' to ControlSurfaceSnapshot.
    """
    with get_conn(schema=PG_SCHEMA) as conn:
        rows = conn.execute(
            "SELECT which, snapshot_json FROM control_surface_snapshots WHERE incident_id = %s",
            (incident_id,),
        ).fetchall()

        result: dict[str, Any] = {}
        for row in rows:
            data = _json_loads(row["snapshot_json"])
            snapshot = _parse_control_surface_snapshot(data)
            if snapshot:
                result[row["which"]] = snapshot

        return result


def get_surface_hashes(
    incident_id: str,
    which: str,
    conn: psycopg.Connection | None = None,
) -> dict[str, str] | None:
    """Get surface hashes for an incident without loading full snapshot.

    Useful for quick drift detection without deserializing the full snapshot.

    Args:
        incident_id: The incident UUID.
        which: 'pre' or 'post'.
        conn: psycopg connection (used internally for transactional calls).

    Returns:
        Dict of surface hashes if found, None otherwise.
    """
    def _do_get(c: psycopg.Connection) -> dict[str, str] | None:
        row = c.execute(
            """
            SELECT surface_hashes FROM control_surface_snapshots
            WHERE incident_id = %s AND which = %s
            """,
            (incident_id, which),
        ).fetchone()

        if not row:
            return None

        return cast(dict[str, str] | None, _json_loads(row["surface_hashes"]))

    if conn is not None:
        return _do_get(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _do_get(c)


def _parse_control_surface_snapshot(data: dict[str, Any]) -> ControlSurfaceSnapshotModel | None:
    """Parse control surface snapshot from JSON data."""
    if not data:
        return None

    # Import here to avoid circular imports
    from elle.daemon.incidents.control_surface import (
        ConfigFileSurface,
        HardwareIdentitySurface,
        KernelPolicySurface,
        NetworkControlSurface,
        PackageInfo,
        PackageSurface,
        PrivilegeSurface,
        SchedulerSurface,
        SystemdServiceSurface,
    )
    from elle.daemon.incidents.control_surface import (
        ControlSurfaceSnapshot as ControlSurfaceSnapshotModel,
    )

    try:
        # Parse services
        services = tuple(SystemdServiceSurface(**s) for s in data.get("services", []))

        # Parse configs
        configs_data = data.get("configs", [])
        configs_list: list[ConfigFileSurface] = []
        for c in configs_data:
            # Handle mtime
            if "mtime" in c and c["mtime"] and isinstance(c["mtime"], str):
                c["mtime"] = _parse_datetime(c["mtime"])
            configs_list.append(ConfigFileSurface(**c))
        configs = tuple(configs_list)

        # Parse packages
        packages_data = data.get("packages", {})
        pkg_infos = tuple(PackageInfo(**p) for p in packages_data.get("packages", []))
        packages = PackageSurface(
            packages=pkg_infos,
            surface_hash_value=packages_data.get("surface_hash_value", ""),
        )

        # Parse other surfaces
        scheduler = SchedulerSurface(**data.get("scheduler", {}))
        privilege = PrivilegeSurface(**data.get("privilege", {}))
        network_control = NetworkControlSurface(**data.get("network_control", {}))
        kernel_policy_data = data.get("kernel_policy", {})
        kernel_policy = KernelPolicySurface(**kernel_policy_data)
        hardware_data = data.get("hardware", {})
        if "filesystem_types" in hardware_data and isinstance(hardware_data["filesystem_types"], list):
            hardware_data["filesystem_types"] = tuple(hardware_data["filesystem_types"])
        hardware = HardwareIdentitySurface(**hardware_data)

        # Parse collected_at
        collected_at = datetime.now(timezone.utc)
        if "collected_at" in data:
            if isinstance(data["collected_at"], str):
                collected_at = _parse_datetime(data["collected_at"])
            elif isinstance(data["collected_at"], datetime):
                collected_at = data["collected_at"]

        return ControlSurfaceSnapshotModel(
            services=services,
            configs=configs,
            packages=packages,
            scheduler=scheduler,
            privilege=privilege,
            network_control=network_control,
            kernel_policy=kernel_policy,
            hardware=hardware,
            collected_at=collected_at,
        )
    except Exception:
        return None


# Type hints for imports (for documentation purposes)
TelemetrySnapshotModel = Any  # elle.daemon.incidents.telemetry_snapshot.TelemetrySnapshot
ControlSurfaceSnapshotModel = Any  # elle.daemon.incidents.control_surface.ControlSurfaceSnapshot


# =============================================================================
# Anonymization Helpers
# =============================================================================


def get_anonymized_incident(
    incident_id: str,
) -> AnonymizedIncidentReport | None:
    """Get an anonymized version of an incident.

    Loads the incident and all associated data (actions, telemetry, surface hashes),
    then produces an anonymized version suitable for cloud storage.

    Args:
        incident_id: The incident UUID.

    Returns:
        AnonymizedIncidentReport if found, None otherwise.
    """
    # Import here to avoid circular imports
    from elle.daemon.incidents.anonymize import (
        anonymize_incident,
    )

    with get_conn(schema=PG_SCHEMA) as conn:
        # Get the incident
        incident = _get_incident_with_conn(incident_id, conn)
        if incident is None:
            return None

        # Get actions
        # Use conn directly for actions query to stay in same transaction
        action_rows = conn.execute(
            """
            SELECT * FROM incident_actions
            WHERE incident_id = %s
            ORDER BY step_index
            """,
            (incident_id,),
        ).fetchall()
        actions = [_row_to_action(row) for row in action_rows]

        # Get telemetry snapshots
        telemetry_pre = get_telemetry_snapshot(incident_id, "pre", conn=conn)
        telemetry_post = get_telemetry_snapshot(incident_id, "post", conn=conn)

        # Get surface hashes
        surface_hashes_pre = get_surface_hashes(incident_id, "pre", conn=conn)
        surface_hashes_post = get_surface_hashes(incident_id, "post", conn=conn)

        # Anonymize and return
        return anonymize_incident(
            incident=incident,
            actions=actions,
            telemetry_pre=telemetry_pre,
            telemetry_post=telemetry_post,
            surface_hashes_pre=surface_hashes_pre,
            surface_hashes_post=surface_hashes_post,
        )
