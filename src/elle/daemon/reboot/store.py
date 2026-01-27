"""CRUD operations for the Reboot Persistence module.

Provides database operations for reboot intents and verification checks.
All operations are designed for safe state machine transitions.
"""

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from elle.common.pydantic_compat import safe_model_dump
from elle.daemon.reboot.models import (
    GRUBState,
    PendingVerification,
    RebootIntent,
    RebootIntentStatus,
    RebootIntentSummary,
    RebootOutcome,
    RebootReason,
    RebootStatus,
)
from elle.daemon.reboot.schema import ensure_schema, get_connection


@contextmanager
def _ensure_connection(
    conn: sqlite3.Connection | None = None,
) -> Iterator[sqlite3.Connection]:
    """Context manager that ensures a valid connection with schema."""
    own_conn = conn is None
    actual_conn: sqlite3.Connection
    if own_conn:
        actual_conn = get_connection()
        ensure_schema(actual_conn)
    else:
        actual_conn = conn  # type: ignore[assignment]
    try:
        yield actual_conn
    finally:
        if own_conn:
            actual_conn.close()


def _serialize_datetime(dt: datetime) -> str:
    """Serialize datetime to ISO format string."""
    return dt.isoformat()


def _parse_datetime(s: str | None) -> datetime | None:
    """Parse ISO format string to datetime."""
    if not s:
        return None
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
# Reboot Intent CRUD
# =============================================================================


def create_intent(
    goal: str,
    task_description: str,
    reason: RebootReason,
    reason_detail: str | None = None,
    incident_id: str | None = None,
    session_history: list[str] | None = None,
    plan_json: dict[str, Any] | None = None,
    pre_snapshot_json: dict[str, Any] | None = None,
    boot_id: str | None = None,
    grub_entry: str | None = None,
    grub_default_saved: str | None = None,
    grub_state: GRUBState | None = None,
    verifications: list[PendingVerification] | None = None,
    conn: sqlite3.Connection | None = None,
) -> RebootIntent:
    """Create a new reboot intent.

    Creates an intent in 'pending' status with the provided details.
    Automatically adds verification checks if provided.

    Args:
        goal: High-level goal of this reboot.
        task_description: Detailed task description.
        reason: Why reboot is needed.
        reason_detail: Additional detail about the reason.
        incident_id: Linked incident ID (optional).
        session_history: Commands executed before reboot.
        plan_json: The plan being executed.
        pre_snapshot_json: System snapshot before reboot.
        boot_id: Current systemd boot ID.
        grub_entry: GRUB entry to boot into.
        grub_default_saved: Original GRUB default.
        grub_state: Complete GRUB state.
        verifications: Verification checks to add.
        conn: SQLite connection.

    Returns:
        The created RebootIntent.
    """
    with _ensure_connection(conn) as c:
        intent_id = str(uuid.uuid4())
        now = datetime.utcnow()

        cursor = c.cursor()
        cursor.execute(
            """
            INSERT INTO reboot_intents (
                id, incident_id, created_at, updated_at,
                goal, task_description, reason, reason_detail,
                session_history_json, plan_json,
                status,
                pre_snapshot_json, boot_id,
                grub_entry, grub_default_saved, grub_state_json,
                post_snapshot_json, verification_attempts,
                outcome, outcome_detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent_id,
                incident_id,
                _serialize_datetime(now),
                _serialize_datetime(now),
                goal,
                task_description,
                reason,
                reason_detail,
                _json_dumps(session_history or []),
                _json_dumps(plan_json or {}),
                "pending",
                _json_dumps(pre_snapshot_json or {}),
                boot_id,
                grub_entry,
                grub_default_saved,
                _json_dumps(safe_model_dump(grub_state)) if grub_state else None,
                "{}",
                0,
                "unknown",
                None,
            ),
        )
        c.commit()

        # Add verifications if provided
        if verifications:
            for i, v in enumerate(verifications):
                add_verification(
                    intent_id,
                    step_index=i,
                    check_type=v.check_type,
                    check_command=v.check_command,
                    expected_exit_code=v.expected_exit_code,
                    expected_output_contains=v.expected_output_contains,
                    required=v.required,
                    conn=c,
                )

        return get_intent(intent_id, conn=c)  # type: ignore


def get_intent(
    intent_id: str,
    conn: sqlite3.Connection | None = None,
) -> RebootIntent | None:
    """Get a reboot intent by ID.

    Args:
        intent_id: The intent UUID.
        conn: SQLite connection.

    Returns:
        RebootIntent if found, None otherwise.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute("SELECT * FROM reboot_intents WHERE id = ?", (intent_id,))
        row = cursor.fetchone()

        if not row:
            return None

        # Get verifications
        verifications = get_verifications(intent_id, conn=c)

        return _row_to_intent(row, verifications)


def get_intents_by_status(
    status: RebootIntentStatus,
    limit: int = 100,
    conn: sqlite3.Connection | None = None,
) -> list[RebootIntent]:
    """Get reboot intents by status.

    Args:
        status: Status to filter by.
        limit: Maximum number of results.
        conn: SQLite connection.

    Returns:
        List of RebootIntents with the specified status.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT * FROM reboot_intents
            WHERE status = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (status, limit),
        )

        result = []
        for row in cursor.fetchall():
            verifications = get_verifications(row["id"], conn=c)
            result.append(_row_to_intent(row, verifications))

        return result


def get_active_intent(
    conn: sqlite3.Connection | None = None,
) -> RebootIntent | None:
    """Get the currently active reboot intent.

    Active means status is 'rebooting' or 'verifying'.

    Args:
        conn: SQLite connection.

    Returns:
        Active RebootIntent if exists, None otherwise.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT * FROM reboot_intents
            WHERE status IN ('rebooting', 'verifying')
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()

        if not row:
            return None

        verifications = get_verifications(row["id"], conn=c)
        return _row_to_intent(row, verifications)


def list_intents(
    status: RebootIntentStatus | None = None,
    outcome: RebootOutcome | None = None,
    limit: int = 100,
    offset: int = 0,
    conn: sqlite3.Connection | None = None,
) -> list[RebootIntent]:
    """List reboot intents with optional filtering.

    Args:
        status: Filter by status.
        outcome: Filter by outcome.
        limit: Maximum number of results.
        offset: Offset for pagination.
        conn: SQLite connection.

    Returns:
        List of RebootIntents.
    """
    with _ensure_connection(conn) as c:
        query = "SELECT * FROM reboot_intents WHERE 1=1"
        params: list[Any] = []

        if status:
            query += " AND status = ?"
            params.append(status)
        if outcome:
            query += " AND outcome = ?"
            params.append(outcome)

        query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = c.cursor()
        cursor.execute(query, params)

        result = []
        for row in cursor.fetchall():
            verifications = get_verifications(row["id"], conn=c)
            result.append(_row_to_intent(row, verifications))

        return result


def list_recent_intents(
    days: int = 7,
    limit: int = 10,
    conn: sqlite3.Connection | None = None,
) -> list[RebootIntentSummary]:
    """List recent reboot intent summaries.

    Args:
        days: Number of days to look back.
        limit: Maximum number of results.
        conn: SQLite connection.

    Returns:
        List of RebootIntentSummary objects.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT id, created_at, goal, reason, status, outcome, verification_attempts
            FROM reboot_intents
            WHERE created_at >= datetime('now', ? || ' days')
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (f"-{days}", limit),
        )

        result = []
        for row in cursor.fetchall():
            result.append(
                RebootIntentSummary(
                    id=row["id"],
                    created_at=_parse_datetime(row["created_at"]),  # type: ignore
                    goal=row["goal"],
                    reason=row["reason"],
                    status=row["status"],
                    outcome=row["outcome"],
                    verification_attempts=row["verification_attempts"],
                )
            )

        return result


def update_intent_status(
    intent_id: str,
    status: RebootIntentStatus,
    outcome: RebootOutcome | None = None,
    outcome_detail: str | None = None,
    new_boot_id: str | None = None,
    post_snapshot_json: dict[str, Any] | None = None,
    verification_attempts: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> RebootIntent | None:
    """Update a reboot intent's status.

    Updates the status and optionally other fields.
    Automatically updates updated_at and last_verification_at.

    Args:
        intent_id: The intent UUID.
        status: New status.
        outcome: Final outcome (optional).
        outcome_detail: Outcome explanation (optional).
        new_boot_id: Boot ID after reboot (optional).
        post_snapshot_json: System snapshot after reboot (optional).
        verification_attempts: Update attempt count (optional).
        conn: SQLite connection.

    Returns:
        Updated RebootIntent, or None if not found.
    """
    with _ensure_connection(conn) as c:
        now = datetime.utcnow()

        updates = ["updated_at = ?", "status = ?"]
        values: list[Any] = [_serialize_datetime(now), status]

        if outcome is not None:
            updates.append("outcome = ?")
            values.append(outcome)
        if outcome_detail is not None:
            updates.append("outcome_detail = ?")
            values.append(outcome_detail)
        if new_boot_id is not None:
            updates.append("new_boot_id = ?")
            values.append(new_boot_id)
        if post_snapshot_json is not None:
            updates.append("post_snapshot_json = ?")
            values.append(_json_dumps(post_snapshot_json))
        if verification_attempts is not None:
            updates.append("verification_attempts = ?")
            values.append(verification_attempts)

        # Update last_verification_at for verifying status
        if status == "verifying":
            updates.append("last_verification_at = ?")
            values.append(_serialize_datetime(now))

        values.append(intent_id)

        cursor = c.cursor()
        cursor.execute(
            f"UPDATE reboot_intents SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        c.commit()

        if cursor.rowcount == 0:
            return None

        return get_intent(intent_id, conn=c)


def mark_rebooting(
    intent_id: str,
    boot_id: str,
    conn: sqlite3.Connection | None = None,
) -> RebootIntent | None:
    """Mark an intent as rebooting.

    This is the last write before reboot - sets boot_id and status.

    Args:
        intent_id: The intent UUID.
        boot_id: Current systemd boot ID.
        conn: SQLite connection.

    Returns:
        Updated RebootIntent.
    """
    with _ensure_connection(conn) as c:
        now = datetime.utcnow()

        cursor = c.cursor()
        cursor.execute(
            """
            UPDATE reboot_intents
            SET updated_at = ?, status = ?, boot_id = ?
            WHERE id = ?
            """,
            (_serialize_datetime(now), "rebooting", boot_id, intent_id),
        )
        c.commit()

        if cursor.rowcount == 0:
            return None

        return get_intent(intent_id, conn=c)


def cancel_intent(
    intent_id: str,
    conn: sqlite3.Connection | None = None,
) -> RebootIntent | None:
    """Cancel a pending reboot intent.

    Only intents in 'pending' status can be cancelled.

    Args:
        intent_id: The intent UUID.
        conn: SQLite connection.

    Returns:
        Updated RebootIntent, or None if not found/not cancellable.
    """
    with _ensure_connection(conn) as c:
        now = datetime.utcnow()

        cursor = c.cursor()
        cursor.execute(
            """
            UPDATE reboot_intents
            SET updated_at = ?, status = 'cancelled'
            WHERE id = ? AND status = 'pending'
            """,
            (_serialize_datetime(now), intent_id),
        )
        c.commit()

        if cursor.rowcount == 0:
            return None

        return get_intent(intent_id, conn=c)


def delete_intent(
    intent_id: str,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Delete a reboot intent.

    Note: Prefer marking as cancelled/failed rather than deleting
    for audit trail purposes.

    Args:
        intent_id: The intent UUID.
        conn: SQLite connection.

    Returns:
        True if deleted, False if not found.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute("DELETE FROM reboot_intents WHERE id = ?", (intent_id,))
        c.commit()
        return cursor.rowcount > 0


def _row_to_intent(
    row: sqlite3.Row,
    verifications: list[PendingVerification],
) -> RebootIntent:
    """Convert a database row to a RebootIntent."""
    grub_state_data = _json_loads(row["grub_state_json"])
    grub_state = None
    if grub_state_data:
        # Handle tuple fields
        if "entries" in grub_state_data and isinstance(grub_state_data["entries"], list):
            grub_state_data["entries"] = tuple(grub_state_data["entries"])
        grub_state = GRUBState(**grub_state_data)

    session_history = _json_loads(row["session_history_json"]) or []
    if isinstance(session_history, list):
        session_history = tuple(session_history)

    return RebootIntent(
        id=row["id"],
        incident_id=row["incident_id"],
        created_at=_parse_datetime(row["created_at"]),  # type: ignore
        updated_at=_parse_datetime(row["updated_at"]),  # type: ignore
        goal=row["goal"],
        task_description=row["task_description"],
        reason=row["reason"],
        reason_detail=row["reason_detail"],
        session_history=session_history,
        plan_json=_json_loads(row["plan_json"]) or {},
        status=row["status"],
        pre_snapshot_json=_json_loads(row["pre_snapshot_json"]) or {},
        boot_id=row["boot_id"],
        grub_entry=row["grub_entry"],
        grub_default_saved=row["grub_default_saved"],
        grub_state=grub_state,
        post_snapshot_json=_json_loads(row["post_snapshot_json"]) or {},
        new_boot_id=row["new_boot_id"],
        verification_attempts=row["verification_attempts"],
        last_verification_at=_parse_datetime(row["last_verification_at"]),
        outcome=row["outcome"],
        outcome_detail=row["outcome_detail"],
        verifications=tuple(verifications),
    )


# =============================================================================
# Verification CRUD
# =============================================================================


def add_verification(
    intent_id: str,
    step_index: int,
    check_type: str,
    check_command: str,
    expected_exit_code: int = 0,
    expected_output_contains: str | None = None,
    required: bool = True,
    conn: sqlite3.Connection | None = None,
) -> PendingVerification:
    """Add a verification check to a reboot intent.

    Args:
        intent_id: Parent intent ID.
        step_index: Order in verification sequence.
        check_type: Type of check (command, service_active, etc.).
        check_command: Command or service name.
        expected_exit_code: Expected exit code for command checks.
        expected_output_contains: Expected output substring.
        required: Whether failure triggers rollback.
        conn: SQLite connection.

    Returns:
        The created PendingVerification.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            """
            INSERT INTO pending_verifications (
                reboot_intent_id, step_index,
                check_type, check_command,
                expected_exit_code, expected_output_contains,
                required
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent_id,
                step_index,
                check_type,
                check_command,
                expected_exit_code,
                expected_output_contains,
                1 if required else 0,
            ),
        )
        c.commit()

        return PendingVerification(
            id=cursor.lastrowid,
            reboot_intent_id=intent_id,
            step_index=step_index,
            check_type=check_type,  # type: ignore
            check_command=check_command,
            expected_exit_code=expected_exit_code,
            expected_output_contains=expected_output_contains,
            required=required,
        )


def get_verifications(
    intent_id: str,
    conn: sqlite3.Connection | None = None,
) -> list[PendingVerification]:
    """Get all verification checks for an intent.

    Args:
        intent_id: The intent UUID.
        conn: SQLite connection.

    Returns:
        List of PendingVerifications in step order.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT * FROM pending_verifications
            WHERE reboot_intent_id = ?
            ORDER BY step_index
            """,
            (intent_id,),
        )

        return [_row_to_verification(row) for row in cursor.fetchall()]


def update_verification_result(
    verification_id: int,
    exit_code: int,
    stdout: str | None = None,
    stderr: str | None = None,
    passed: bool = False,
    conn: sqlite3.Connection | None = None,
) -> PendingVerification | None:
    """Update a verification check with results.

    Args:
        verification_id: The verification ID.
        exit_code: Command exit code.
        stdout: Stdout from check.
        stderr: Stderr from check.
        passed: Whether the check passed.
        conn: SQLite connection.

    Returns:
        Updated PendingVerification, or None if not found.
    """
    with _ensure_connection(conn) as c:
        now = datetime.utcnow()

        cursor = c.cursor()
        cursor.execute(
            """
            UPDATE pending_verifications
            SET executed_at = ?, exit_code = ?, stdout = ?, stderr = ?, passed = ?
            WHERE id = ?
            """,
            (
                _serialize_datetime(now),
                exit_code,
                stdout[:4096] if stdout else None,  # Truncate
                stderr[:4096] if stderr else None,  # Truncate
                1 if passed else 0,
                verification_id,
            ),
        )
        c.commit()

        if cursor.rowcount == 0:
            return None

        cursor.execute(
            "SELECT * FROM pending_verifications WHERE id = ?",
            (verification_id,),
        )
        row = cursor.fetchone()
        if row:
            return _row_to_verification(row)
        return None


def reset_verifications(
    intent_id: str,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Reset all verifications for an intent (clear results).

    Used when retrying verification.

    Args:
        intent_id: The intent UUID.
        conn: SQLite connection.

    Returns:
        Number of verifications reset.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            """
            UPDATE pending_verifications
            SET executed_at = NULL, exit_code = NULL, stdout = NULL,
                stderr = NULL, passed = NULL
            WHERE reboot_intent_id = ?
            """,
            (intent_id,),
        )
        c.commit()
        return cursor.rowcount


def _row_to_verification(row: sqlite3.Row) -> PendingVerification:
    """Convert a database row to a PendingVerification."""
    return PendingVerification(
        id=row["id"],
        reboot_intent_id=row["reboot_intent_id"],
        step_index=row["step_index"],
        check_type=row["check_type"],
        check_command=row["check_command"],
        expected_exit_code=row["expected_exit_code"],
        expected_output_contains=row["expected_output_contains"],
        required=bool(row["required"]),
        executed_at=_parse_datetime(row["executed_at"]),
        exit_code=row["exit_code"],
        stdout=row["stdout"],
        stderr=row["stderr"],
        passed=bool(row["passed"]) if row["passed"] is not None else None,
    )


# =============================================================================
# Statistics
# =============================================================================


def get_reboot_status(
    conn: sqlite3.Connection | None = None,
) -> RebootStatus:
    """Get status report for the Reboot module.

    Args:
        conn: SQLite connection.

    Returns:
        RebootStatus with counts and active operations.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()

        # Get counts by status
        cursor.execute("SELECT COUNT(*) FROM reboot_intents")
        row = cursor.fetchone()
        total = int(row[0]) if row else 0

        cursor.execute("SELECT COUNT(*) FROM reboot_intents WHERE status = 'pending'")
        row = cursor.fetchone()
        pending = int(row[0]) if row else 0

        cursor.execute("SELECT COUNT(*) FROM reboot_intents WHERE status = 'rebooting'")
        row = cursor.fetchone()
        rebooting = int(row[0]) if row else 0

        cursor.execute("SELECT COUNT(*) FROM reboot_intents WHERE status = 'verifying'")
        row = cursor.fetchone()
        verifying = int(row[0]) if row else 0

        cursor.execute("SELECT COUNT(*) FROM reboot_intents WHERE status = 'completed'")
        row = cursor.fetchone()
        completed = int(row[0]) if row else 0

        cursor.execute("SELECT COUNT(*) FROM reboot_intents WHERE status = 'failed'")
        row = cursor.fetchone()
        failed = int(row[0]) if row else 0

        cursor.execute("SELECT COUNT(*) FROM reboot_intents WHERE status = 'rolled_back'")
        row = cursor.fetchone()
        rolled_back = int(row[0]) if row else 0

        # Get counts by outcome
        cursor.execute("SELECT outcome, COUNT(*) FROM reboot_intents GROUP BY outcome")
        by_outcome = dict(cursor.fetchall())

        # Get active intent
        active_intent = get_active_intent(conn=c)
        active_summary = None
        if active_intent:
            active_summary = RebootIntentSummary(
                id=active_intent.id,
                created_at=active_intent.created_at,
                goal=active_intent.goal,
                reason=active_intent.reason,
                status=active_intent.status,
                outcome=active_intent.outcome,
                verification_attempts=active_intent.verification_attempts,
            )

        # Get recent intents
        recent = list_recent_intents(days=7, limit=5, conn=c)

        # Get database size - handle cases where db path may not be accessible
        from elle.daemon.reboot.schema import get_db_path

        db_size = 0
        try:
            db_path = get_db_path()
            if db_path.exists():
                db_size = db_path.stat().st_size
        except (PermissionError, OSError):
            pass

        # Get oldest/newest
        cursor.execute("SELECT MIN(created_at), MAX(created_at) FROM reboot_intents")
        row = cursor.fetchone()
        oldest = _parse_datetime(row[0]) if row and row[0] else None
        newest = _parse_datetime(row[1]) if row and row[1] else None

        return RebootStatus(
            total_intents=total,
            pending_count=pending,
            rebooting_count=rebooting,
            verifying_count=verifying,
            completed_count=completed,
            failed_count=failed,
            rolled_back_count=rolled_back,
            by_outcome=by_outcome,
            active_intent=active_summary,
            recent_intents=tuple(recent),
            db_size_bytes=db_size,
            oldest_intent=oldest,
            newest_intent=newest,
        )


def get_intent_count(
    conn: sqlite3.Connection | None = None,
) -> int:
    """Get total number of reboot intents.

    Args:
        conn: SQLite connection.

    Returns:
        Total intent count.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute("SELECT COUNT(*) FROM reboot_intents")
        row = cursor.fetchone()
        return int(row[0]) if row else 0
