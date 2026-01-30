"""Agent loop execution tracking.

Provides complete records of agent loop executions for introspection
and debugging. Each execution captures the full reasoning trace.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from elle.cli.agentic.stages import (
    CapabilityEvaluation,
    Hypothesis,
    LoopStage,
    StageData,
    StageTransition,
)


class ToolCallSummary(BaseModel):
    """Summary of a tool call for display."""

    model_config = ConfigDict(frozen=True)

    tool: str
    """Tool name."""

    reason: str
    """Why this tool was called."""

    result_summary: str
    """Brief result description."""

    success: bool = True
    """Whether the call succeeded."""

    duration_ms: int = 0
    """Call duration in milliseconds."""


class LoopExecution(BaseModel):
    """Complete record of an agent loop execution.

    Captures every aspect of a loop run for introspection:
    - Input and classification
    - Stage transitions
    - Hypotheses considered
    - Capabilities evaluated
    - Tool calls made
    - Final outcome
    """

    model_config = ConfigDict(frozen=True)

    execution_id: str = Field(default_factory=lambda: str(uuid4()))
    """Unique identifier for this execution."""

    started_at: datetime
    """When execution started."""

    completed_at: datetime | None = None
    """When execution completed (None if still running)."""

    # Input
    user_input: str
    """The original user input."""

    classified_intent: str
    """How the input was classified."""

    classification_confidence: float = 0.0
    """Classification confidence score."""

    # Stage trace
    current_stage: LoopStage = LoopStage.OBSERVE
    """Current stage (if still running)."""

    stage_transitions: tuple[StageTransition, ...] = Field(default_factory=tuple)
    """Complete trace of stage transitions."""

    stage_data: StageData = Field(default_factory=StageData)
    """Data collected during stage execution."""

    # Reasoning
    hypotheses: tuple[Hypothesis, ...] = Field(default_factory=tuple)
    """All hypotheses generated during execution."""

    chosen_hypothesis_id: str | None = None
    """ID of the hypothesis that was chosen."""

    # Capability selection
    capabilities_evaluated: tuple[CapabilityEvaluation, ...] = Field(default_factory=tuple)
    """All capabilities that were evaluated."""

    capabilities_executed: tuple[str, ...] = Field(default_factory=tuple)
    """Names of capabilities that were executed."""

    # Tool usage
    tool_calls: tuple[ToolCallSummary, ...] = Field(default_factory=tuple)
    """Summary of all tool calls made."""

    total_tool_calls: int = 0
    """Total number of tool calls."""

    # Outcome
    success: bool = True
    """Whether the execution succeeded."""

    response: str = ""
    """Final response text."""

    error: str | None = None
    """Error message if failed."""

    # Incident linkage
    incident_id: str | None = None
    """Created or linked incident ID."""

    # Metrics
    iterations: int = 1
    """Number of loop iterations."""

    total_duration_ms: int = 0
    """Total execution time."""

    prompt_tokens: int | None = None
    """Tokens used in prompts."""

    completion_tokens: int | None = None
    """Tokens used in completions."""

    @property
    def total_tokens(self) -> int | None:
        """Total tokens used."""
        if self.prompt_tokens is not None and self.completion_tokens is not None:
            return self.prompt_tokens + self.completion_tokens
        return None

    @property
    def chosen_hypothesis(self) -> Hypothesis | None:
        """Get the chosen hypothesis if any."""
        if not self.chosen_hypothesis_id:
            return None
        for h in self.hypotheses:
            if h.hypothesis_id == self.chosen_hypothesis_id:
                return h
        return None

    @property
    def rejected_hypotheses(self) -> tuple[Hypothesis, ...]:
        """Get all rejected hypotheses."""
        return tuple(h for h in self.hypotheses if h.rejected)


# =============================================================================
# Execution Storage
# =============================================================================


class ExecutionStore:
    """PostgreSQL storage for loop executions."""

    def __init__(self, schema: str = "executions") -> None:
        """Initialize the store.

        Args:
            schema: PostgreSQL schema name for execution tables.
        """
        self._schema = schema
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize database schema."""
        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    user_input TEXT NOT NULL,
                    classified_intent TEXT NOT NULL,
                    success INTEGER NOT NULL DEFAULT 1,
                    response TEXT,
                    error TEXT,
                    incident_id TEXT,
                    iterations INTEGER DEFAULT 1,
                    total_duration_ms INTEGER DEFAULT 0,
                    total_tool_calls INTEGER DEFAULT 0,
                    capabilities_executed TEXT,
                    data_json TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_executions_started
                ON executions(started_at DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_executions_incident
                ON executions(incident_id)
            """)

    def store(self, execution: LoopExecution) -> None:
        """Store an execution record.

        Args:
            execution: The execution to store.
        """
        import json

        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            conn.execute(
                """
                INSERT INTO executions (
                    execution_id, started_at, completed_at, user_input,
                    classified_intent, success, response, error, incident_id,
                    iterations, total_duration_ms, total_tool_calls,
                    capabilities_executed, data_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (execution_id) DO UPDATE SET
                    started_at = EXCLUDED.started_at,
                    completed_at = EXCLUDED.completed_at,
                    user_input = EXCLUDED.user_input,
                    classified_intent = EXCLUDED.classified_intent,
                    success = EXCLUDED.success,
                    response = EXCLUDED.response,
                    error = EXCLUDED.error,
                    incident_id = EXCLUDED.incident_id,
                    iterations = EXCLUDED.iterations,
                    total_duration_ms = EXCLUDED.total_duration_ms,
                    total_tool_calls = EXCLUDED.total_tool_calls,
                    capabilities_executed = EXCLUDED.capabilities_executed,
                    data_json = EXCLUDED.data_json
                """,
                (
                    execution.execution_id,
                    execution.started_at.isoformat(),
                    execution.completed_at.isoformat() if execution.completed_at else None,
                    execution.user_input,
                    execution.classified_intent,
                    1 if execution.success else 0,
                    execution.response,
                    execution.error,
                    execution.incident_id,
                    execution.iterations,
                    execution.total_duration_ms,
                    execution.total_tool_calls,
                    ",".join(execution.capabilities_executed),
                    json.dumps(execution.model_dump(mode="json")),
                ),
            )

    def get(self, execution_id: str) -> LoopExecution | None:
        """Get an execution by ID.

        Args:
            execution_id: The execution ID.

        Returns:
            The execution or None if not found.
        """
        import json

        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            cursor = conn.execute(
                "SELECT data_json FROM executions WHERE execution_id = %s",
                (execution_id,),
            )
            row = cursor.fetchone()
            if row and row["data_json"]:
                data = json.loads(row["data_json"])
                return LoopExecution.model_validate(data)
        return None

    def get_recent(self, limit: int = 10) -> list[LoopExecution]:
        """Get recent executions.

        Args:
            limit: Maximum number to return.

        Returns:
            List of executions, most recent first.
        """
        import json

        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            cursor = conn.execute(
                "SELECT data_json FROM executions ORDER BY started_at DESC LIMIT %s",
                (limit,),
            )
            results = []
            for row in cursor.fetchall():
                if row["data_json"]:
                    data = json.loads(row["data_json"])
                    results.append(LoopExecution.model_validate(data))
            return results

    def get_by_incident(self, incident_id: str) -> list[LoopExecution]:
        """Get executions linked to an incident.

        Args:
            incident_id: The incident ID.

        Returns:
            List of linked executions.
        """
        import json

        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            cursor = conn.execute(
                "SELECT data_json FROM executions WHERE incident_id = %s ORDER BY started_at DESC",
                (incident_id,),
            )
            results = []
            for row in cursor.fetchall():
                if row["data_json"]:
                    data = json.loads(row["data_json"])
                    results.append(LoopExecution.model_validate(data))
            return results

    def get_summary(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get execution summaries (lightweight).

        Args:
            limit: Maximum number to return.

        Returns:
            List of summary dicts.
        """
        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            cursor = conn.execute(
                """
                SELECT execution_id, started_at, user_input, classified_intent,
                       success, incident_id, iterations, total_tool_calls,
                       capabilities_executed, total_duration_ms
                FROM executions
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            results = []
            for row in cursor.fetchall():
                results.append({
                    "execution_id": row["execution_id"],
                    "started_at": row["started_at"],
                    "user_input": row["user_input"],
                    "classified_intent": row["classified_intent"],
                    "success": bool(row["success"]),
                    "incident_id": row["incident_id"],
                    "iterations": row["iterations"],
                    "total_tool_calls": row["total_tool_calls"],
                    "capabilities_executed": row["capabilities_executed"].split(",") if row["capabilities_executed"] else [],
                    "total_duration_ms": row["total_duration_ms"],
                })
            return results


# Module-level store instance (lazy initialization)
_store: ExecutionStore | None = None


def get_execution_store() -> ExecutionStore:
    """Get the global execution store instance."""
    global _store
    if _store is None:
        _store = ExecutionStore()
    return _store


def store_execution(execution: LoopExecution) -> None:
    """Store an execution record."""
    get_execution_store().store(execution)


def get_execution(execution_id: str) -> LoopExecution | None:
    """Get an execution by ID."""
    return get_execution_store().get(execution_id)


def get_recent_executions(limit: int = 10) -> list[LoopExecution]:
    """Get recent executions."""
    return get_execution_store().get_recent(limit)


def get_execution_summaries(limit: int = 10) -> list[dict[str, Any]]:
    """Get execution summaries."""
    return get_execution_store().get_summary(limit)
