"""CRUD operations for Reactive Functions.

Provides database operations for reactive functions, execution history,
and function state. All operations follow ELLE's store patterns.

Storage backend: PostgreSQL via psycopg (schema ``reactive``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg

from elle.common.pydantic_compat import safe_model_dump
from elle.reactive.models import (
    ActionResult,
    ActionSpec,
    Condition,
    EventTrigger,
    ExecutionRecord,
    ForecastTrigger,
    PolicySpec,
    RateLimitState,
    ReactiveFunction,
    ScheduleTrigger,
    StateProbe,
    Trigger,
)
from elle.storage.engine import get_conn
from elle.storage.helpers import json_dumps, json_loads, parse_datetime, serialize_datetime

PG_SCHEMA = "reactive"



# =============================================================================
# Reactive Function CRUD
# =============================================================================


def create_function(
    func: ReactiveFunction,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> ReactiveFunction:
    """Create a new reactive function.

    Args:
        func: The ReactiveFunction to create.
        conn: psycopg connection. Uses pool if not provided.

    Returns:
        The created ReactiveFunction.

    Raises:
        psycopg.errors.UniqueViolation: If name already exists.
    """

    def _run(c: psycopg.Connection) -> ReactiveFunction:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute(
            """
            INSERT INTO reactive_functions (
                id, name, description, enabled,
                created_at, updated_at, created_by,
                trigger_json, condition_json, actions_json,
                policy_json, state_json, tags_json, source_prompt
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                func.id,
                func.name,
                func.description,
                func.enabled,
                serialize_datetime(func.created_at),
                serialize_datetime(func.updated_at),
                func.created_by,
                json_dumps(safe_model_dump(func.trigger)),
                json_dumps(safe_model_dump(func.condition)) if func.condition else None,
                json_dumps([safe_model_dump(a) for a in func.actions]),
                json_dumps(safe_model_dump(func.policy)),
                json_dumps({k: safe_model_dump(v) for k, v in func.state.items()}) if func.state else None,
                json_dumps(list(func.tags)) if func.tags else None,
                func.source_prompt,
            ),
        )
        return func

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def update_function(
    function_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    enabled: bool | None = None,
    trigger: Trigger | None = None,
    condition: Condition | None = None,
    actions: tuple[ActionSpec, ...] | None = None,
    policy: PolicySpec | None = None,
    state: dict[str, StateProbe] | None = None,
    tags: tuple[str, ...] | None = None,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> ReactiveFunction | None:
    """Update an existing reactive function.

    Only provided fields are updated.

    Args:
        function_id: ID of the function to update.
        **kwargs: Fields to update.
        conn: psycopg connection.

    Returns:
        Updated ReactiveFunction, or None if not found.
    """

    def _run(c: psycopg.Connection) -> ReactiveFunction | None:  # type: ignore[type-arg]
        # Build update statement dynamically
        updates = ["updated_at = %s"]
        values: list[Any] = [serialize_datetime(datetime.utcnow())]

        if name is not None:
            updates.append("name = %s")
            values.append(name)
        if description is not None:
            updates.append("description = %s")
            values.append(description)
        if enabled is not None:
            updates.append("enabled = %s")
            values.append(enabled)
        if trigger is not None:
            updates.append("trigger_json = %s")
            values.append(json_dumps(safe_model_dump(trigger)))
        if condition is not None:
            updates.append("condition_json = %s")
            values.append(json_dumps(safe_model_dump(condition)))
        if actions is not None:
            updates.append("actions_json = %s")
            values.append(json_dumps([safe_model_dump(a) for a in actions]))
        if policy is not None:
            updates.append("policy_json = %s")
            values.append(json_dumps(safe_model_dump(policy)))
        if state is not None:
            updates.append("state_json = %s")
            values.append(json_dumps({k: safe_model_dump(v) for k, v in state.items()}))
        if tags is not None:
            updates.append("tags_json = %s")
            values.append(json_dumps(list(tags)))

        values.append(function_id)

        cursor = c.cursor()
        cursor.execute(
            f"UPDATE reactive_functions SET {', '.join(updates)} WHERE id = %s",
            values,
        )

        if cursor.rowcount == 0:
            return None

        return _get_function_inner(c, function_id)

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def get_function(
    function_id: str,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> ReactiveFunction | None:
    """Get a reactive function by ID.

    Args:
        function_id: The function UUID.
        conn: psycopg connection.

    Returns:
        ReactiveFunction if found, None otherwise.
    """
    if conn is not None:
        return _get_function_inner(conn, function_id)

    with get_conn(schema=PG_SCHEMA) as c:
        return _get_function_inner(c, function_id)


def _get_function_inner(
    c: psycopg.Connection,  # type: ignore[type-arg]
    function_id: str,
) -> ReactiveFunction | None:
    """Internal helper to get a function within an existing connection."""
    cursor = c.cursor()
    cursor.execute("SELECT * FROM reactive_functions WHERE id = %s", (function_id,))
    row = cursor.fetchone()

    if not row:
        return None

    return _row_to_function(row)


def get_function_by_name(
    name: str,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> ReactiveFunction | None:
    """Get a reactive function by name.

    Args:
        name: The function name.
        conn: psycopg connection.

    Returns:
        ReactiveFunction if found, None otherwise.
    """

    def _run(c: psycopg.Connection) -> ReactiveFunction | None:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute("SELECT * FROM reactive_functions WHERE name = %s", (name,))
        row = cursor.fetchone()

        if not row:
            return None

        return _row_to_function(row)

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def list_functions(
    enabled_only: bool = False,
    tags: list[str] | None = None,
    limit: int = 100,
    offset: int = 0,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> list[ReactiveFunction]:
    """List reactive functions with optional filtering.

    Args:
        enabled_only: Only return enabled functions.
        tags: Filter by tags (any match).
        limit: Maximum number of results.
        offset: Offset for pagination.
        conn: psycopg connection.

    Returns:
        List of ReactiveFunctions.
    """

    def _run(c: psycopg.Connection) -> list[ReactiveFunction]:  # type: ignore[type-arg]
        query = "SELECT * FROM reactive_functions WHERE 1=1"
        params: list[Any] = []

        if enabled_only:
            query += " AND enabled = TRUE"

        query += " ORDER BY updated_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cursor = c.cursor()
        cursor.execute(query, params)

        functions = [_row_to_function(row) for row in cursor.fetchall()]

        # Filter by tags in Python (JSON field filtering)
        if tags:
            functions = [f for f in functions if any(t in f.tags for t in tags)]

        return functions

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def list_enabled_with_event_trigger(
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> list[ReactiveFunction]:
    """List enabled functions that have event triggers.

    Used by the event router to find functions to evaluate.

    Args:
        conn: psycopg connection.

    Returns:
        List of enabled ReactiveFunctions with event triggers.
    """

    def _run(c: psycopg.Connection) -> list[ReactiveFunction]:  # type: ignore[type-arg]
        cursor = c.cursor()
        # Use PostgreSQL JSONB extraction for trigger type
        cursor.execute(
            """
            SELECT * FROM reactive_functions
            WHERE enabled = TRUE
            AND trigger_json::jsonb->>'type' = 'event'
            ORDER BY name
            """
        )

        return [_row_to_function(row) for row in cursor.fetchall()]

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def list_enabled_with_schedule_trigger(
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> list[ReactiveFunction]:
    """List enabled functions that have schedule triggers.

    Used by the scheduler to find functions to schedule.

    Args:
        conn: psycopg connection.

    Returns:
        List of enabled ReactiveFunctions with schedule triggers.
    """

    def _run(c: psycopg.Connection) -> list[ReactiveFunction]:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT * FROM reactive_functions
            WHERE enabled = TRUE
            AND trigger_json::jsonb->>'type' = 'schedule'
            ORDER BY name
            """
        )

        return [_row_to_function(row) for row in cursor.fetchall()]

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def list_enabled_with_forecast_trigger(
    urgency: str | None = None,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> list[ReactiveFunction]:
    """List enabled functions that have forecast triggers.

    Used by the forecast handler to find functions to evaluate
    when a metric's urgency changes.

    Args:
        urgency: Optional urgency filter ('prepare' or 'act_now').
        conn: psycopg connection.

    Returns:
        List of enabled ReactiveFunctions with forecast triggers.
    """

    def _run(c: psycopg.Connection) -> list[ReactiveFunction]:  # type: ignore[type-arg]
        cursor = c.cursor()

        if urgency:
            cursor.execute(
                """
                SELECT * FROM reactive_functions
                WHERE enabled = TRUE
                AND trigger_json::jsonb->>'type' = 'forecast'
                AND trigger_json::jsonb->'forecast'->>'urgency' = %s
                ORDER BY name
                """,
                (urgency,),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM reactive_functions
                WHERE enabled = TRUE
                AND trigger_json::jsonb->>'type' = 'forecast'
                ORDER BY name
                """
            )

        return [_row_to_function(row) for row in cursor.fetchall()]

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def delete_function(
    function_id: str,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> bool:
    """Delete a reactive function and all related data.

    Args:
        function_id: The function UUID.
        conn: psycopg connection.

    Returns:
        True if deleted, False if not found.
    """

    def _run(c: psycopg.Connection) -> bool:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute("DELETE FROM reactive_functions WHERE id = %s", (function_id,))
        return cursor.rowcount > 0

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def delete_function_by_name(
    name: str,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> bool:
    """Delete a reactive function by name.

    Args:
        name: The function name.
        conn: psycopg connection.

    Returns:
        True if deleted, False if not found.
    """

    def _run(c: psycopg.Connection) -> bool:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute("DELETE FROM reactive_functions WHERE name = %s", (name,))
        return cursor.rowcount > 0

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def _row_to_function(row: dict) -> ReactiveFunction:
    """Convert a database row to a ReactiveFunction."""
    # Parse trigger
    trigger_data = json_loads(row["trigger_json"])
    trigger = _parse_trigger(trigger_data)

    # Parse condition
    condition = None
    if row["condition_json"]:
        condition_data = json_loads(row["condition_json"])
        if condition_data:
            condition = Condition(**condition_data)

    # Parse actions
    actions_data = json_loads(row["actions_json"]) or []
    actions = tuple(ActionSpec(**a) for a in actions_data)

    # Parse policy
    policy_data = json_loads(row["policy_json"]) or {}
    # Handle allowed_hours tuple
    if "allowed_hours" in policy_data and isinstance(policy_data["allowed_hours"], list):
        policy_data["allowed_hours"] = tuple(policy_data["allowed_hours"])
    policy = PolicySpec(**policy_data) if policy_data else PolicySpec()

    # Parse state probes
    state_data = json_loads(row["state_json"]) or {}
    state_map = {k: StateProbe(**v) for k, v in state_data.items()}

    # Parse tags
    tags = tuple(json_loads(row["tags_json"]) or [])

    created_at = row["created_at"]
    if isinstance(created_at, str):
        created_at = parse_datetime(created_at)

    updated_at = row["updated_at"]
    if isinstance(updated_at, str):
        updated_at = parse_datetime(updated_at)

    return ReactiveFunction(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        enabled=bool(row["enabled"]),
        created_at=created_at,
        updated_at=updated_at,
        created_by=row["created_by"] or "user",
        trigger=trigger,
        condition=condition,
        actions=actions,
        policy=policy,
        state=state_map,
        tags=tags,
        source_prompt=row["source_prompt"],
    )


def _parse_trigger(data: dict[str, Any]) -> Trigger:
    """Parse a trigger dict into a Trigger model."""
    event_trigger = None
    schedule_trigger = None
    forecast_trigger = None

    if data.get("event"):
        event_trigger = EventTrigger(**data["event"])

    if data.get("schedule"):
        schedule_trigger = ScheduleTrigger(**data["schedule"])

    if data.get("forecast"):
        forecast_trigger = ForecastTrigger(**data["forecast"])

    return Trigger(
        type=data["type"],
        event=event_trigger,
        schedule=schedule_trigger,
        forecast=forecast_trigger,
    )


# =============================================================================
# Execution History CRUD
# =============================================================================


def record_execution(
    record: ExecutionRecord,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> ExecutionRecord:
    """Record a reactive function execution.

    Args:
        record: The ExecutionRecord to store.
        conn: psycopg connection.

    Returns:
        The stored ExecutionRecord.
    """

    def _run(c: psycopg.Connection) -> ExecutionRecord:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute(
            """
            INSERT INTO execution_history (
                id, function_id, function_name, triggered_at,
                trigger_event_json, condition_result, condition_explanation,
                actions_executed_json, actions_results_json,
                success, error, execution_time_ms, incident_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.id,
                record.function_id,
                record.function_name,
                serialize_datetime(record.triggered_at),
                json_dumps(record.trigger_event) if record.trigger_event else None,
                record.condition_result,
                record.condition_explanation,
                json_dumps(list(record.actions_executed)),
                json_dumps([safe_model_dump(r) for r in record.actions_results]),
                record.success,
                record.error,
                record.execution_time_ms,
                record.incident_id,
            ),
        )
        return record

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def get_execution_history(
    function_id: str,
    limit: int = 50,
    offset: int = 0,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> list[ExecutionRecord]:
    """Get execution history for a function.

    Args:
        function_id: The function UUID.
        limit: Maximum number of results.
        offset: Offset for pagination.
        conn: psycopg connection.

    Returns:
        List of ExecutionRecords, most recent first.
    """

    def _run(c: psycopg.Connection) -> list[ExecutionRecord]:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT * FROM execution_history
            WHERE function_id = %s
            ORDER BY triggered_at DESC
            LIMIT %s OFFSET %s
            """,
            (function_id, limit, offset),
        )

        return [_row_to_execution(row) for row in cursor.fetchall()]

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def get_recent_executions(
    limit: int = 50,
    success_only: bool = False,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> list[ExecutionRecord]:
    """Get recent executions across all functions.

    Args:
        limit: Maximum number of results.
        success_only: Only return successful executions.
        conn: psycopg connection.

    Returns:
        List of ExecutionRecords, most recent first.
    """

    def _run(c: psycopg.Connection) -> list[ExecutionRecord]:  # type: ignore[type-arg]
        query = "SELECT * FROM execution_history"
        params: list[Any] = []

        if success_only:
            query += " WHERE success = TRUE"

        query += " ORDER BY triggered_at DESC LIMIT %s"
        params.append(limit)

        cursor = c.cursor()
        cursor.execute(query, params)

        return [_row_to_execution(row) for row in cursor.fetchall()]

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def _row_to_execution(row: dict) -> ExecutionRecord:
    """Convert a database row to an ExecutionRecord."""
    # Parse actions_results
    actions_results_data = json_loads(row["actions_results_json"]) or []
    actions_results = tuple(ActionResult(**r) for r in actions_results_data)

    triggered_at = row["triggered_at"]
    if isinstance(triggered_at, str):
        triggered_at = parse_datetime(triggered_at)

    return ExecutionRecord(
        id=row["id"],
        function_id=row["function_id"],
        function_name=row["function_name"],
        triggered_at=triggered_at,
        trigger_event=json_loads(row["trigger_event_json"]) if row["trigger_event_json"] else None,
        condition_result=bool(row["condition_result"]),
        condition_explanation=row["condition_explanation"] or "",
        actions_executed=tuple(json_loads(row["actions_executed_json"]) or []),
        actions_results=actions_results,
        success=bool(row["success"]),
        error=row["error"],
        execution_time_ms=row["execution_time_ms"] or 0,
        incident_id=row["incident_id"],
    )


# =============================================================================
# Function State (Rate Limiting)
# =============================================================================


def get_rate_limit_state(
    function_id: str,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> RateLimitState:
    """Get rate limiting state for a function.

    Args:
        function_id: The function UUID.
        conn: psycopg connection.

    Returns:
        RateLimitState (with defaults if no state exists).
    """

    def _run(c: psycopg.Connection) -> RateLimitState:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT key, value_json FROM function_state
            WHERE function_id = %s
            """,
            (function_id,),
        )

        state_dict: dict[str, Any] = {}
        for row in cursor.fetchall():
            state_dict[row["key"]] = json_loads(row["value_json"])

        last_execution = None
        if state_dict.get("last_execution"):
            last_execution = parse_datetime(state_dict["last_execution"])

        return RateLimitState(
            function_id=function_id,
            last_execution=last_execution,
            daily_executions=state_dict.get("daily_executions", 0),
            daily_reset_date=state_dict.get("daily_reset_date", ""),
        )

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def update_rate_limit_state(
    function_id: str,
    last_execution: datetime,
    daily_executions: int,
    daily_reset_date: str,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> None:
    """Update rate limiting state for a function.

    Args:
        function_id: The function UUID.
        last_execution: Last execution timestamp.
        daily_executions: Daily execution count.
        daily_reset_date: Date string for daily counter.
        conn: psycopg connection.
    """

    def _run(c: psycopg.Connection) -> None:  # type: ignore[type-arg]
        cursor = c.cursor()
        now = serialize_datetime(datetime.utcnow())

        # Upsert each state key
        state_items = [
            ("last_execution", serialize_datetime(last_execution)),
            ("daily_executions", daily_executions),
            ("daily_reset_date", daily_reset_date),
        ]

        for key, value in state_items:
            cursor.execute(
                """
                INSERT INTO function_state (function_id, key, value_json, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (function_id, key) DO UPDATE
                SET value_json = EXCLUDED.value_json, updated_at = EXCLUDED.updated_at
                """,
                (function_id, key, json_dumps(value), now),
            )

    if conn is not None:
        _run(conn)
        return

    with get_conn(schema=PG_SCHEMA) as c:
        _run(c)


def set_function_state(
    function_id: str,
    key: str,
    value: Any,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> None:
    """Set a custom state value for a function.

    Args:
        function_id: The function UUID.
        key: State key.
        value: State value (will be JSON serialized).
        conn: psycopg connection.
    """

    def _run(c: psycopg.Connection) -> None:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute(
            """
            INSERT INTO function_state (function_id, key, value_json, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (function_id, key) DO UPDATE
            SET value_json = EXCLUDED.value_json, updated_at = EXCLUDED.updated_at
            """,
            (function_id, key, json_dumps(value), serialize_datetime(datetime.utcnow())),
        )

    if conn is not None:
        _run(conn)
        return

    with get_conn(schema=PG_SCHEMA) as c:
        _run(c)


def get_function_state(
    function_id: str,
    key: str,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> Any:
    """Get a custom state value for a function.

    Args:
        function_id: The function UUID.
        key: State key.
        conn: psycopg connection.

    Returns:
        State value, or None if not found.
    """

    def _run(c: psycopg.Connection) -> Any:  # type: ignore[type-arg]
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT value_json FROM function_state
            WHERE function_id = %s AND key = %s
            """,
            (function_id, key),
        )
        row = cursor.fetchone()

        if not row:
            return None

        return json_loads(row["value_json"])

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


# =============================================================================
# Statistics
# =============================================================================


def get_function_count(
    enabled_only: bool = False,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> int:
    """Get total number of reactive functions.

    Args:
        enabled_only: Only count enabled functions.
        conn: psycopg connection.

    Returns:
        Total function count.
    """

    def _run(c: psycopg.Connection) -> int:  # type: ignore[type-arg]
        cursor = c.cursor()
        if enabled_only:
            cursor.execute("SELECT COUNT(*) AS cnt FROM reactive_functions WHERE enabled = TRUE")
        else:
            cursor.execute("SELECT COUNT(*) AS cnt FROM reactive_functions")
        row = cursor.fetchone()
        return int(row["cnt"]) if row else 0

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)


def get_execution_count(
    function_id: str | None = None,
    since: datetime | None = None,
    conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
) -> int:
    """Get total number of executions.

    Args:
        function_id: Filter by function (optional).
        since: Only count executions after this time.
        conn: psycopg connection.

    Returns:
        Total execution count.
    """

    def _run(c: psycopg.Connection) -> int:  # type: ignore[type-arg]
        query = "SELECT COUNT(*) AS cnt FROM execution_history WHERE 1=1"
        params: list[Any] = []

        if function_id:
            query += " AND function_id = %s"
            params.append(function_id)

        if since:
            query += " AND triggered_at >= %s"
            params.append(serialize_datetime(since))

        cursor = c.cursor()
        cursor.execute(query, params)
        row = cursor.fetchone()
        return int(row["cnt"]) if row else 0

    if conn is not None:
        return _run(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _run(c)
