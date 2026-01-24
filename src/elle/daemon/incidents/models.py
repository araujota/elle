"""Pydantic models for the Incident Report vault.

Defines data structures for incident reports, system snapshots,
actions, and similarity features.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# Domain categories for incident classification
IncidentDomain = Literal[
    "net",     # Network issues
    "disk",    # Disk/storage issues
    "oom",     # Out-of-memory events
    "docker",  # Container issues
    "auth",    # Authentication/permission issues
    "pkg",     # Package management issues
    "fs",      # Filesystem issues
    "service", # Systemd service issues
    "other",   # Uncategorized
]

# Severity levels matching TelemetryEvent
IncidentSeverity = Literal["info", "warning", "error", "critical"]

# Incident lifecycle states
IncidentStatus = Literal[
    "open",           # Issue ongoing or not yet mitigated
    "mitigated",      # Immediate symptoms improved, root cause may be unknown
    "resolved",       # Checks passed + stability window elapsed
    "false_positive", # Correlated noise, not a real issue
]

# Outcome of actions taken
IncidentOutcome = Literal[
    "unknown",   # Not yet determined
    "improved",  # Problem resolved or significantly better
    "partial",   # Some improvement but not fully resolved
    "no_change", # Actions had no effect
    "worse",     # Actions made the problem worse
]

# Action types
ActionKind = Literal[
    "shell",      # Shell command executed
    "edit",       # File edit via helper
    "privileged", # Privileged operation via Polkit
    "verify",     # Verification check
    "rollback",   # Rollback action
]


class SystemSnapshot(BaseModel):
    """Point-in-time system state for comparison and replay.

    Captures essential system metrics for incident analysis,
    precondition matching, and diff computation.
    """

    model_config = ConfigDict(frozen=True)

    # System info
    os: str = Field(description="OS name and version, e.g., 'Ubuntu 24.04'")
    kernel: str = Field(description="Kernel version")
    uptime_sec: int = Field(ge=0, description="System uptime in seconds")
    hostname: str = Field(default="", description="System hostname")

    # CPU
    cpu_load: tuple[float, float, float] = Field(
        description="Load averages: 1min, 5min, 15min"
    )

    # Memory
    mem_total_mb: int = Field(ge=0, description="Total memory in MB")
    mem_free_mb: int = Field(ge=0, description="Free memory in MB")
    mem_available_mb: int = Field(ge=0, description="Available memory in MB")
    swap_total_mb: int = Field(ge=0, default=0, description="Total swap in MB")
    swap_used_mb: int = Field(ge=0, default=0, description="Used swap in MB")

    # Disk
    disks: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Disk info: mount, used_pct, avail_gb, device",
    )

    # Network
    interfaces: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Network interfaces: name, state, rx_err, tx_err, ip",
    )

    # Services
    services: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Systemd services: name, active, failed",
    )

    # Docker (if available)
    docker_running: int = Field(default=0, description="Running containers")
    docker_exited: int = Field(default=0, description="Exited containers")
    docker_containers: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Container details: name, state, image",
    )

    # Temperatures (if available)
    temps: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Temperature sensors: sensor, celsius",
    )

    # SMART data (if available)
    smart: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="SMART info: dev, health, pct_used, media_errors",
    )

    # Collected at timestamp
    collected_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this snapshot was collected",
    )


class Fingerprint(BaseModel):
    """Derived features for incident similarity matching.

    Computed from snapshots and events for fast filtering
    and precondition evaluation.
    """

    model_config = ConfigDict(frozen=True)

    # Resource pressure (0.0 - 1.0)
    disk_pressure: float = Field(
        ge=0.0, le=1.0, default=0.0,
        description="Max disk usage ratio across mounts",
    )
    mem_pressure: float = Field(
        ge=0.0, le=1.0, default=0.0,
        description="Memory pressure: 1 - (available / total)",
    )
    swap_pressure: float = Field(
        ge=0.0, le=1.0, default=0.0,
        description="Swap usage ratio",
    )
    cpu_pressure: float = Field(
        ge=0.0, default=0.0,
        description="CPU load (1min average)",
    )

    # Event counts (last hour)
    oom_count_1h: int = Field(ge=0, default=0, description="OOM kills in last hour")
    net_flaps_1h: int = Field(ge=0, default=0, description="Network state changes in last hour")
    service_failures_1h: int = Field(ge=0, default=0, description="Service failures in last hour")
    auth_failures_1h: int = Field(ge=0, default=0, description="Auth failures in last hour")

    # Entities involved (for matching)
    entities: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Involved entities: service names, devices, interfaces",
    )

    # SMART warnings
    smart_pct_used_max: int = Field(ge=0, le=100, default=0)
    smart_media_errors: int = Field(ge=0, default=0)

    # Temperatures
    temp_max_c: int = Field(default=0, description="Max temperature in Celsius")

    # Docker
    docker_exited_count: int = Field(ge=0, default=0)

    # Custom features (extensible)
    custom: dict[str, Any] = Field(default_factory=dict)


class IncidentAction(BaseModel):
    """A single action taken during incident handling.

    Actions are append-only and form an ordered sequence
    of what ELLE actually did.
    """

    model_config = ConfigDict(frozen=True)

    id: int | None = Field(default=None, description="Database ID")
    incident_id: str = Field(description="Parent incident UUID")
    step_index: int = Field(ge=0, description="Order in the action sequence")
    kind: ActionKind = Field(description="Type of action")

    # Action details
    command: str | None = Field(default=None, description="Shell command if applicable")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Action-specific data (file path, edit content, etc.)",
    )

    # Results
    exit_code: int | None = Field(default=None, description="Command exit code")
    stdout: str | None = Field(default=None, description="Command stdout (truncated)")
    stderr: str | None = Field(default=None, description="Command stderr (truncated)")
    success: bool = Field(default=False, description="Whether action succeeded")
    privileged: bool = Field(default=False, description="Required elevated privileges")

    # Timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    duration_ms: int | None = Field(default=None, description="Execution duration")


class IncidentSnapshot(BaseModel):
    """Snapshot attached to an incident (pre or post).

    Links a SystemSnapshot to an incident with metadata
    about when and why it was captured.
    """

    model_config = ConfigDict(frozen=True)

    id: int | None = Field(default=None, description="Database ID")
    incident_id: str = Field(description="Parent incident UUID")
    which: Literal["pre", "post"] = Field(description="Before or after actions")
    snapshot: SystemSnapshot = Field(description="The system state")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Precondition(BaseModel):
    """A boolean-ish condition that must be true for incident to apply.

    Used to determine if a past incident's solution is applicable
    to the current system state.
    """

    model_config = ConfigDict(frozen=True)

    expression: str = Field(
        description="Condition expression, e.g., 'disk./.used_pct > 95'"
    )
    description: str = Field(
        default="",
        description="Human-readable explanation",
    )
    required: bool = Field(
        default=True,
        description="If True, must match for incident to be applicable",
    )


class IncidentReport(BaseModel):
    """Complete incident report - the core artifact.

    Captures everything about what happened, what was decided,
    what was done, and the outcome. Used for:
    - Audit trail
    - Learning from past incidents
    - Guiding future decisions
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    incident_id: str = Field(description="UUID for this incident")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Classification
    domain: IncidentDomain = Field(default="other")
    severity: IncidentSeverity = Field(default="warning")
    status: IncidentStatus = Field(default="open")

    # What happened
    title: str = Field(description="Brief title for the incident")
    summary: str = Field(
        default="",
        description="2-5 sentence summary",
    )
    symptoms: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Human-readable symptom bullets",
    )
    suspected_causes: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Hypotheses about the cause",
    )
    root_cause: str | None = Field(
        default=None,
        description="Confirmed root cause (set once known)",
    )

    # Evidence
    event_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Linked telemetry event IDs",
    )
    log_snippets: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Relevant log excerpts",
    )
    metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Key metrics: disk%, temp, SMART, etc.",
    )

    # Decision
    decision: dict[str, Any] = Field(
        default_factory=dict,
        description="Chosen plan and rationale",
    )
    preconditions: tuple[Precondition, ...] = Field(
        default_factory=tuple,
        description="Conditions for this solution to apply",
    )

    # Outcome
    outcome: IncidentOutcome = Field(default="unknown")
    verification_steps: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Verification checks run",
    )
    time_to_mitigate_sec: int | None = Field(
        default=None,
        description="Seconds from created_at to mitigated",
    )
    time_to_resolve_sec: int | None = Field(
        default=None,
        description="Seconds from created_at to resolved",
    )

    # Similarity / retrieval
    fingerprint: Fingerprint = Field(
        default_factory=Fingerprint,
        description="Features for matching",
    )
    tags: tuple[str, ...] = Field(
        default_factory=tuple,
        description="User-defined tags",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, default=0.0,
        description="Confidence in root cause diagnosis",
    )

    # Trigger source
    trigger_source: Literal["telemetry", "command_failure", "user_task", "manual"] = Field(
        default="manual",
        description="What created this incident",
    )
    trigger_command: str | None = Field(
        default=None,
        description="Command that triggered (if command_failure)",
    )


class IncidentSearchResult(BaseModel):
    """Search result for incident retrieval."""

    model_config = ConfigDict(frozen=True)

    incident: IncidentReport
    score: float = Field(ge=0.0, description="Relevance score")
    match_type: Literal["fingerprint", "lexical", "semantic", "hybrid"] = Field(
        default="hybrid"
    )
    precondition_match_ratio: float = Field(
        ge=0.0, le=1.0, default=0.0,
        description="How many preconditions match current state",
    )
    outcome_weight: float = Field(
        ge=0.0, le=1.0, default=0.5,
        description="Weight based on past outcome quality",
    )


class IncidentVaultStatus(BaseModel):
    """Status report for the Incident Vault."""

    model_config = ConfigDict(frozen=True)

    total_incidents: int = Field(ge=0)
    total_actions: int = Field(ge=0)
    total_snapshots: int = Field(ge=0)
    embedded_incidents: int = Field(ge=0)

    # By status
    open_count: int = Field(ge=0, default=0)
    mitigated_count: int = Field(ge=0, default=0)
    resolved_count: int = Field(ge=0, default=0)

    # By domain
    by_domain: dict[str, int] = Field(default_factory=dict)

    # By outcome
    by_outcome: dict[str, int] = Field(default_factory=dict)

    # Database info
    db_size_bytes: int = Field(ge=0)
    oldest_incident: datetime | None = Field(default=None)
    newest_incident: datetime | None = Field(default=None)
