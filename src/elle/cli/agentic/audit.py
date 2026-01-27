"""Audit trail recording for the agentic system.

Records complete execution traces including:
- Trigger information (user message, incident, command failure)
- All retrieval contexts consulted
- Tool calls and their results
- Verification outcomes
- Final result and any errors

This enables:
- Complete provenance for decisions
- Post-hoc analysis of agent behavior
- Debugging and improvement
- Compliance and audit requirements
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from elle.cli.agentic.retrieval_pipeline import RetrievalContext
from elle.cli.agentic.unified_input import AgenticInput

logger = logging.getLogger(__name__)

# Default database path
DEFAULT_AUDIT_DB = Path("/var/lib/elle/agentic_audit.db")


# =============================================================================
# Models
# =============================================================================


class ToolCallRecord(BaseModel):
    """Record of a tool call during execution."""

    model_config = ConfigDict(frozen=True)

    call_id: str = Field(description="Unique ID for this call")
    tool_name: str = Field(description="Name of the tool called")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Arguments passed")
    result_success: bool = Field(description="Whether the call succeeded")
    result_output: str = Field(default="", description="Output from the tool")
    result_error: str | None = Field(default=None, description="Error message if failed")
    duration_ms: int = Field(default=0, description="Execution time")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the call was made",
    )
    iteration: int = Field(default=1, description="Which loop iteration this was in")

    @classmethod
    def from_tool_result(
        cls,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        iteration: int = 1,
    ) -> ToolCallRecord:
        """Create from a ToolResult.

        Args:
            tool_name: Name of the tool.
            arguments: Arguments passed to the tool.
            result: ToolResult from execution.
            iteration: Loop iteration number.

        Returns:
            ToolCallRecord instance.
        """
        return cls(
            call_id=str(uuid.uuid4()),
            tool_name=tool_name,
            arguments=arguments,
            result_success=getattr(result, "success", False),
            result_output=getattr(result, "output", ""),
            result_error=getattr(result, "error", None),
            duration_ms=getattr(result, "duration_ms", 0),
            iteration=iteration,
        )


class VerificationResult(BaseModel):
    """Result of verifying an operation outcome."""

    model_config = ConfigDict(frozen=True)

    verification_id: str = Field(description="Unique ID")
    tool_call_id: str = Field(description="ID of the tool call being verified")
    passed: bool = Field(description="Whether verification passed")
    method: str = Field(description="Verification method used")
    evidence: str = Field(default="", description="Evidence supporting the result")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Provenance(BaseModel):
    """Provenance information for a decision or action."""

    model_config = ConfigDict(frozen=True)

    # Sources consulted
    incident_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Incident IDs that informed the decision",
    )
    man_pages: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Man pages consulted",
    )
    capabilities_considered: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Capabilities that were considered",
    )

    # Decision trail
    reasoning_steps: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Steps in the reasoning process",
    )

    @classmethod
    def from_context(cls, context: RetrievalContext) -> Provenance:
        """Build provenance from retrieval context.

        Args:
            context: RetrievalContext to extract provenance from.

        Returns:
            Provenance instance.
        """
        return cls(
            incident_ids=tuple(i.incident_id for i in context.prior_incidents),
            man_pages=tuple(f"{s.name}({s.section})" for s in context.man_vault_snippets),
            capabilities_considered=tuple(c.capability_name for c in context.capability_matches),
        )


class AuditRecord(BaseModel):
    """Complete audit record for an agentic execution."""

    model_config = ConfigDict(frozen=True)

    # Identity
    execution_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique execution ID",
    )
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When execution started",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="When execution completed",
    )

    # Trigger
    trigger: AgenticInput = Field(description="What triggered this execution")
    linked_incident_id: str | None = Field(
        default=None,
        description="Incident ID if this was incident-triggered",
    )

    # Decision trail
    retrieval_contexts: tuple[RetrievalContext, ...] = Field(
        default_factory=tuple,
        description="All retrieval contexts consulted",
    )
    tool_calls: tuple[ToolCallRecord, ...] = Field(
        default_factory=tuple,
        description="All tool calls made",
    )
    verifications: tuple[VerificationResult, ...] = Field(
        default_factory=tuple,
        description="All verification results",
    )

    # Outcome
    final_response: str | None = Field(
        default=None,
        description="Final response to user",
    )
    success: bool = Field(default=False, description="Whether execution succeeded")
    error: str | None = Field(default=None, description="Error message if failed")
    iterations: int = Field(default=0, description="Number of loop iterations")

    @property
    def provenance(self) -> Provenance:
        """Build provenance from audit data."""
        all_incidents: list[str] = []
        all_man_pages: list[str] = []
        all_capabilities: list[str] = []

        for ctx in self.retrieval_contexts:
            all_incidents.extend(i.incident_id for i in ctx.prior_incidents)
            all_man_pages.extend(f"{s.name}({s.section})" for s in ctx.man_vault_snippets)
            all_capabilities.extend(c.capability_name for c in ctx.capability_matches)

        return Provenance(
            incident_ids=tuple(set(all_incidents)),
            man_pages=tuple(set(all_man_pages)),
            capabilities_considered=tuple(set(all_capabilities)),
        )

    @property
    def duration_ms(self) -> int:
        """Get total execution duration in milliseconds."""
        if self.completed_at is None:
            return 0
        delta = self.completed_at - self.started_at
        return int(delta.total_seconds() * 1000)

    @property
    def tool_call_count(self) -> int:
        """Get total number of tool calls."""
        return len(self.tool_calls)

    @property
    def successful_tool_calls(self) -> tuple[ToolCallRecord, ...]:
        """Get all successful tool calls."""
        return tuple(tc for tc in self.tool_calls if tc.result_success)


# =============================================================================
# Audit Recorder
# =============================================================================


class AuditRecorder:
    """Records audit trails for agentic executions.

    Provides a simple API for building audit records incrementally
    and persisting them to SQLite.

    Usage:
        recorder = AuditRecorder()
        record = recorder.start(input)
        recorder.add_retrieval(record, context)
        recorder.add_tool_call(record, call_record)
        recorder.complete(record, response)
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS agentic_audit (
        execution_id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        trigger_json TEXT NOT NULL,
        linked_incident_id TEXT,
        retrieval_contexts_json TEXT,
        tool_calls_json TEXT,
        verifications_json TEXT,
        final_response TEXT,
        success INTEGER DEFAULT 0,
        error TEXT,
        iterations INTEGER DEFAULT 0,
        provenance_json TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_audit_started ON agentic_audit(started_at);
    CREATE INDEX IF NOT EXISTS idx_audit_incident ON agentic_audit(linked_incident_id);
    CREATE INDEX IF NOT EXISTS idx_audit_success ON agentic_audit(success);
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        """Initialize the recorder.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = Path(db_path) if db_path else DEFAULT_AUDIT_DB
        self._ensure_schema()

        # In-memory tracking for incomplete records
        self._active_records: dict[str, dict[str, Any]] = {}

    def _ensure_schema(self) -> None:
        """Ensure database schema exists."""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.db_path) as conn:
                conn.executescript(self.SCHEMA)
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize audit database: {e}")

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def start(self, input: AgenticInput) -> AuditRecord:
        """Start a new audit record.

        Args:
            input: The triggering input.

        Returns:
            New AuditRecord.
        """
        record = AuditRecord(
            trigger=input,
            linked_incident_id=input.incident_id,
        )

        # Track in memory for incremental updates
        self._active_records[record.execution_id] = {
            "record": record,
            "retrieval_contexts": [],
            "tool_calls": [],
            "verifications": [],
        }

        return record

    def add_retrieval(self, execution_id: str, context: RetrievalContext) -> None:
        """Add a retrieval context to the audit trail.

        Args:
            execution_id: Execution ID from start().
            context: Retrieved context to add.
        """
        if execution_id in self._active_records:
            self._active_records[execution_id]["retrieval_contexts"].append(context)

    def add_tool_call(self, execution_id: str, call_record: ToolCallRecord) -> None:
        """Add a tool call record to the audit trail.

        Args:
            execution_id: Execution ID from start().
            call_record: Tool call to add.
        """
        if execution_id in self._active_records:
            self._active_records[execution_id]["tool_calls"].append(call_record)

    def add_verification(self, execution_id: str, verification: VerificationResult) -> None:
        """Add a verification result to the audit trail.

        Args:
            execution_id: Execution ID from start().
            verification: Verification result to add.
        """
        if execution_id in self._active_records:
            self._active_records[execution_id]["verifications"].append(verification)

    def complete(
        self,
        execution_id: str,
        final_response: str,
        success: bool = True,
        iterations: int = 1,
    ) -> AuditRecord | None:
        """Complete an audit record successfully.

        Args:
            execution_id: Execution ID from start().
            final_response: Final response text.
            success: Whether execution was successful.
            iterations: Number of loop iterations.

        Returns:
            Completed AuditRecord, or None if not found.
        """
        if execution_id not in self._active_records:
            return None

        data = self._active_records.pop(execution_id)
        base_record = data["record"]

        record = AuditRecord(
            execution_id=base_record.execution_id,
            started_at=base_record.started_at,
            completed_at=datetime.now(UTC),
            trigger=base_record.trigger,
            linked_incident_id=base_record.linked_incident_id,
            retrieval_contexts=tuple(data["retrieval_contexts"]),
            tool_calls=tuple(data["tool_calls"]),
            verifications=tuple(data["verifications"]),
            final_response=final_response,
            success=success,
            iterations=iterations,
        )

        self._persist(record)
        return record

    def fail(
        self,
        execution_id: str,
        error: str,
        iterations: int = 0,
    ) -> AuditRecord | None:
        """Mark an audit record as failed.

        Args:
            execution_id: Execution ID from start().
            error: Error message.
            iterations: Number of iterations before failure.

        Returns:
            Failed AuditRecord, or None if not found.
        """
        if execution_id not in self._active_records:
            return None

        data = self._active_records.pop(execution_id)
        base_record = data["record"]

        record = AuditRecord(
            execution_id=base_record.execution_id,
            started_at=base_record.started_at,
            completed_at=datetime.now(UTC),
            trigger=base_record.trigger,
            linked_incident_id=base_record.linked_incident_id,
            retrieval_contexts=tuple(data["retrieval_contexts"]),
            tool_calls=tuple(data["tool_calls"]),
            verifications=tuple(data["verifications"]),
            success=False,
            error=error,
            iterations=iterations,
        )

        self._persist(record)
        return record

    def _persist(self, record: AuditRecord) -> None:
        """Persist an audit record to the database."""
        try:
            with self._get_connection() as conn:
                # Serialize complex objects
                trigger_json = record.trigger.model_dump_json()
                retrieval_json = json.dumps([
                    ctx.model_dump() for ctx in record.retrieval_contexts
                ], default=str)
                tool_calls_json = json.dumps([
                    tc.model_dump() for tc in record.tool_calls
                ], default=str)
                verifications_json = json.dumps([
                    v.model_dump() for v in record.verifications
                ], default=str)
                provenance_json = record.provenance.model_dump_json()

                conn.execute(
                    """
                    INSERT OR REPLACE INTO agentic_audit (
                        execution_id, started_at, completed_at, trigger_json,
                        linked_incident_id, retrieval_contexts_json, tool_calls_json,
                        verifications_json, final_response, success, error,
                        iterations, provenance_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.execution_id,
                        record.started_at.isoformat(),
                        record.completed_at.isoformat() if record.completed_at else None,
                        trigger_json,
                        record.linked_incident_id,
                        retrieval_json,
                        tool_calls_json,
                        verifications_json,
                        record.final_response,
                        1 if record.success else 0,
                        record.error,
                        record.iterations,
                        provenance_json,
                    ),
                )
                conn.commit()

        except Exception as e:
            logger.error(f"Failed to persist audit record: {e}")

    def get(self, execution_id: str) -> AuditRecord | None:
        """Get an audit record by ID.

        Args:
            execution_id: Execution ID to look up.

        Returns:
            AuditRecord or None.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT * FROM agentic_audit WHERE execution_id = ?",
                    (execution_id,),
                )
                row = cursor.fetchone()
                if row:
                    return self._row_to_record(row)
        except Exception as e:
            logger.error(f"Failed to get audit record: {e}")
        return None

    def list_recent(self, limit: int = 50) -> list[AuditRecord]:
        """List recent audit records.

        Args:
            limit: Maximum records to return.

        Returns:
            List of AuditRecord.
        """
        records: list[AuditRecord] = []
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT * FROM agentic_audit
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                for row in cursor.fetchall():
                    record = self._row_to_record(row)
                    if record:
                        records.append(record)
        except Exception as e:
            logger.error(f"Failed to list audit records: {e}")
        return records

    def list_by_incident(self, incident_id: str) -> list[AuditRecord]:
        """List audit records for an incident.

        Args:
            incident_id: Incident ID to filter by.

        Returns:
            List of AuditRecord.
        """
        records: list[AuditRecord] = []
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT * FROM agentic_audit
                    WHERE linked_incident_id = ?
                    ORDER BY started_at DESC
                    """,
                    (incident_id,),
                )
                for row in cursor.fetchall():
                    record = self._row_to_record(row)
                    if record:
                        records.append(record)
        except Exception as e:
            logger.error(f"Failed to list audit records by incident: {e}")
        return records

    def _row_to_record(self, row: sqlite3.Row) -> AuditRecord | None:
        """Convert a database row to an AuditRecord."""
        try:
            trigger = AgenticInput.model_validate_json(row["trigger_json"])

            # Parse JSON arrays
            retrieval_data = json.loads(row["retrieval_contexts_json"] or "[]")
            tool_calls_data = json.loads(row["tool_calls_json"] or "[]")
            verifications_data = json.loads(row["verifications_json"] or "[]")

            return AuditRecord(
                execution_id=row["execution_id"],
                started_at=datetime.fromisoformat(row["started_at"]),
                completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
                trigger=trigger,
                linked_incident_id=row["linked_incident_id"],
                retrieval_contexts=tuple(),  # Skip deserializing complex objects for listing
                tool_calls=tuple(
                    ToolCallRecord.model_validate(tc) for tc in tool_calls_data
                ),
                verifications=tuple(
                    VerificationResult.model_validate(v) for v in verifications_data
                ),
                final_response=row["final_response"],
                success=bool(row["success"]),
                error=row["error"],
                iterations=row["iterations"],
            )

        except Exception as e:
            logger.warning(f"Failed to deserialize audit record: {e}")
            return None


# =============================================================================
# Module-level recorder instance
# =============================================================================

_recorder: AuditRecorder | None = None


def get_audit_recorder(db_path: Path | str | None = None) -> AuditRecorder:
    """Get the shared audit recorder instance.

    Args:
        db_path: Optional database path override.

    Returns:
        AuditRecorder instance.
    """
    global _recorder
    if _recorder is None or (db_path and Path(db_path) != _recorder.db_path):
        _recorder = AuditRecorder(db_path)
    return _recorder


def reset_audit_recorder() -> None:
    """Reset the shared recorder (for testing)."""
    global _recorder
    _recorder = None
