"""Pydantic models for the Reactive Functions system.

Defines data structures for reactive function definitions, triggers,
conditions, actions, policies, and execution records.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Type Aliases
# =============================================================================

# Trigger types for reactive functions
TriggerType = Literal["event", "schedule", "manual", "forecast"]

# Failure handling modes for actions
OnFailureMode = Literal["continue", "stop", "rollback"]

# Sources for telemetry events (matches daemon/telemetry/models.py)
TelemetrySourceType = Literal["journal", "kernel", "probe", "ebpf"]

# Severity levels (matches daemon/telemetry/models.py)
TelemetrySeverityType = Literal["debug", "info", "notice", "warning", "error", "critical"]


# =============================================================================
# Trigger Models
# =============================================================================


class EventTrigger(BaseModel):
    """Trigger on matching telemetry events.

    Filters events by source, category, severity, and custom field matches.
    All fields are optional; omitted fields match any value.
    """

    model_config = ConfigDict(frozen=True)

    source: TelemetrySourceType | None = Field(
        default=None,
        description="Event source type to match (journal, kernel, probe, ebpf)",
    )
    category: str | None = Field(
        default=None,
        description="Event category to match (e.g., 'disk', 'oom', 'docker')",
    )
    severity: TelemetrySeverityType | None = Field(
        default=None,
        description="Minimum severity to trigger (e.g., 'warning')",
    )
    match: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional field patterns to match in event.raw",
    )


class ScheduleTrigger(BaseModel):
    """Trigger on cron schedule.

    Uses standard cron expression format for scheduling.
    """

    model_config = ConfigDict(frozen=True)

    cron: str = Field(
        description="Cron expression (e.g., '0 */4 * * *' for every 4 hours)",
    )
    timezone: str = Field(
        default="UTC",
        description="Timezone for cron schedule",
    )


class ForecastTrigger(BaseModel):
    """Trigger on metric forecast conditions.

    Fires when a metric is predicted to cross warning or critical thresholds.
    This enables proactive incident prevention.

    - ``"prepare"`` urgency -- Warning threshold crossing imminent, prepare remediation plan
    - ``"act_now"`` urgency -- Critical threshold crossing imminent, execute plan immediately

    Example::

        ForecastTrigger(
            metric="disk.*",       # Match any disk metric
            urgency="prepare",     # Trigger on warning level
            min_confidence=0.6,    # Only trigger if forecast confidence >= 60%
        )
    """

    model_config = ConfigDict(frozen=True)

    metric: str | None = Field(
        default=None,
        description=(
            "Metric pattern to match. Can be exact (e.g., 'disk./.used_pct') "
            "or use wildcards (e.g., 'disk.*' for all disk metrics). "
            "If None, matches all metrics."
        ),
    )
    urgency: Literal["prepare", "act_now"] = Field(
        description=(
            "'prepare' triggers when warning threshold crossing is imminent "
            "(prepare remediation plan), 'act_now' triggers when critical "
            "threshold crossing is imminent (execute plan immediately)"
        ),
    )
    min_confidence: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Minimum forecast confidence required to trigger (0.0-1.0)",
    )


class Trigger(BaseModel):
    """When the reactive function should evaluate.

    Supports four trigger types:
    - event: Fire on matching telemetry events
    - schedule: Fire on cron schedule
    - manual: Fire only via explicit invocation
    - forecast: Fire on metric forecast conditions (predictive prevention)
    """

    model_config = ConfigDict(frozen=True)

    type: TriggerType = Field(
        description="Trigger type (event, schedule, manual, or forecast)",
    )
    event: EventTrigger | None = Field(
        default=None,
        description="Event trigger configuration (required if type='event')",
    )
    schedule: ScheduleTrigger | None = Field(
        default=None,
        description="Schedule trigger configuration (required if type='schedule')",
    )
    forecast: ForecastTrigger | None = Field(
        default=None,
        description="Forecast trigger configuration (required if type='forecast')",
    )


# =============================================================================
# Condition Models
# =============================================================================


class Condition(BaseModel):
    """JSONLogic-style condition tree.

    Evaluates predicates over event payload, state probes, and system metrics.

    Supported operators

    - Logical -- all, any, not
    - Comparison -- eq, ne, gt, gte, lt, lte
    - String -- contains, matches (regex)

    Variables use template syntax

    - ``{event.field}`` -- Event payload fields
    - ``{event.raw.field}`` -- Raw event data
    - ``{state.key}`` -- Probed state values
    - ``{system.metric}`` -- System snapshot metrics

    Example::

        {
            "all": [
                {"gte": ["{event.raw.used_pct}", 90]},
                {"eq": ["{state.docker_running}", true]}
            ]
        }
    """

    model_config = ConfigDict(frozen=True)

    expression: dict[str, Any] = Field(
        description="JSONLogic-style condition expression",
    )


# =============================================================================
# Action Models
# =============================================================================


class ActionSpec(BaseModel):
    """Action to execute when condition is true.

    Actions invoke ELLE capabilities (not raw shell commands) for safety
    and auditability. Input fields may contain {event.X} templates that
    are resolved at execution time.
    """

    model_config = ConfigDict(frozen=True)

    capability: str = Field(
        description="Capability to invoke (e.g., 'service.restart', 'notify.send')",
    )
    input: dict[str, Any] = Field(
        default_factory=dict,
        description="Input parameters for the capability (may contain {event.X} templates)",
    )
    on_failure: OnFailureMode = Field(
        default="stop",
        description="What to do if this action fails",
    )


# =============================================================================
# Policy Models
# =============================================================================


class PolicySpec(BaseModel):
    """Execution constraints and safety limits.

    Prevents runaway loops and enforces safety invariants.
    """

    model_config = ConfigDict(frozen=True)

    max_frequency: str = Field(
        default="5m",
        description="Minimum interval between executions (e.g., '5m', '1h', '1d')",
    )
    max_daily_executions: int = Field(
        default=100,
        ge=1,
        description="Maximum executions per day",
    )
    require_confirmation: bool = Field(
        default=False,
        description="Whether to require user confirmation before execution",
    )
    allowed_hours: tuple[int, int] | None = Field(
        default=None,
        description="Allowed hours for execution as (start_hour, end_hour) in 24h format",
    )
    escalate_on_failure: bool = Field(
        default=True,
        description="Whether to send notification on action failure",
    )
    notification_channel: str | None = Field(
        default=None,
        description="Notification channel for alerts (e.g., ntfy topic)",
    )


# =============================================================================
# State Probe Models
# =============================================================================


class StateProbe(BaseModel):
    """Dynamic state to evaluate in conditions.

    Probes are executed at evaluation time to gather current system state.
    Results are cached briefly to avoid redundant queries.
    """

    model_config = ConfigDict(frozen=True)

    probe: str = Field(
        description="Probe name to execute (e.g., 'disk.usage', 'docker.running')",
    )
    cache_ttl: int = Field(
        default=60,
        ge=0,
        description="Cache TTL in seconds for probe results",
    )


# =============================================================================
# Reactive Function
# =============================================================================


class ReactiveFunction(BaseModel):
    """Complete reactive function definition.

    A reactive function is an "if-this-then-that" automation.

    1. Triggers on events or schedules
    2. Evaluates conditions over system state
    3. Executes ELLE capabilities as actions
    4. Records everything to the incident vault

    Example::

        ReactiveFunction(
            name="docker-disk-cleanup",
            description="Clean Docker when disk pressure detected",
            trigger=Trigger(
                type="event",
                event=EventTrigger(source="probe", category="disk"),
            ),
            condition=Condition(
                expression={"gte": ["{event.raw.used_pct}", 85]},
            ),
            actions=(
                ActionSpec(capability="docker.prune", input={"all": True}),
                ActionSpec(capability="notify.send", input={"title": "Disk cleaned"}),
            ),
            policy=PolicySpec(max_frequency="1h"),
        )
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    id: str = Field(
        default_factory=lambda: uuid4().hex,
        description="Unique identifier for the function",
    )
    name: str = Field(
        description="Human-readable name (unique within store)",
    )
    description: str = Field(
        default="",
        description="Description of what this function does",
    )

    # State
    enabled: bool = Field(
        default=True,
        description="Whether the function is active",
    )

    # Timestamps
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the function was created",
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the function was last modified",
    )
    created_by: str = Field(
        default="user",
        description="Who created this function",
    )

    # Core components
    trigger: Trigger = Field(
        description="When to evaluate this function",
    )
    condition: Condition | None = Field(
        default=None,
        description="Condition that must be true to execute actions",
    )
    actions: tuple[ActionSpec, ...] = Field(
        description="Actions to execute when triggered and condition is true",
    )

    # Configuration
    policy: PolicySpec = Field(
        default_factory=PolicySpec,
        description="Execution policy and constraints",
    )
    state: dict[str, StateProbe] = Field(
        default_factory=dict,
        description="State probes to evaluate in conditions",
    )

    # Metadata
    tags: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Tags for organization and filtering",
    )
    source_prompt: str | None = Field(
        default=None,
        description="Original natural language prompt if created via NL compiler",
    )


# =============================================================================
# Execution Record
# =============================================================================


class ActionResult(BaseModel):
    """Result of executing a single action."""

    model_config = ConfigDict(frozen=True)

    capability: str = Field(description="Capability that was executed")
    success: bool = Field(description="Whether the action succeeded")
    output: Any = Field(default=None, description="Action output")
    error: str | None = Field(default=None, description="Error message if failed")
    execution_time_ms: int = Field(default=0, description="Execution time in milliseconds")


class ExecutionRecord(BaseModel):
    """Record of a reactive function execution.

    Captures everything about what happened when a reactive function
    was evaluated and executed, for debugging and audit purposes.
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    id: str = Field(
        default_factory=lambda: uuid4().hex,
        description="Unique execution identifier",
    )
    function_id: str = Field(
        description="ID of the reactive function that executed",
    )
    function_name: str = Field(
        description="Name of the reactive function",
    )

    # Timing
    triggered_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the function was triggered",
    )

    # Trigger context
    trigger_event: dict[str, Any] | None = Field(
        default=None,
        description="Event that triggered execution (if event-triggered)",
    )

    # Condition evaluation
    condition_result: bool = Field(
        description="Whether the condition evaluated to true",
    )
    condition_explanation: str = Field(
        default="",
        description="Human-readable explanation of condition evaluation",
    )

    # Action execution
    actions_executed: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Capability names that were executed",
    )
    actions_results: tuple[ActionResult, ...] = Field(
        default_factory=tuple,
        description="Results of each action execution",
    )

    # Outcome
    success: bool = Field(
        description="Whether overall execution succeeded",
    )
    error: str | None = Field(
        default=None,
        description="Error message if execution failed",
    )
    execution_time_ms: int = Field(
        default=0,
        description="Total execution time in milliseconds",
    )

    # Incident integration
    incident_id: str | None = Field(
        default=None,
        description="Incident ID if an incident was created/updated",
    )


# =============================================================================
# Rate Limit State
# =============================================================================


class RateLimitState(BaseModel):
    """Rate limiting state for a reactive function.

    Tracks execution history to enforce rate limits.
    """

    model_config = ConfigDict(frozen=True)

    function_id: str = Field(description="Function ID")
    last_execution: datetime | None = Field(
        default=None,
        description="Timestamp of last execution",
    )
    daily_executions: int = Field(
        default=0,
        description="Executions today",
    )
    daily_reset_date: str = Field(
        default="",
        description="Date string for daily counter reset (YYYY-MM-DD)",
    )


# =============================================================================
# Condition Context
# =============================================================================


class ConditionContext(BaseModel):
    """Context for condition evaluation.

    Provides access to event data, probed state, and system metrics
    for use in condition expressions.
    """

    model_config = ConfigDict(frozen=True)

    event: dict[str, Any] | None = Field(
        default=None,
        description="Trigger event data (if event-triggered)",
    )
    state: dict[str, Any] = Field(
        default_factory=dict,
        description="Probed state values",
    )
    system: dict[str, Any] = Field(
        default_factory=dict,
        description="System snapshot metrics",
    )
