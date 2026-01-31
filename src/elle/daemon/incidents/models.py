"""Pydantic models for the Incident Report vault.

Defines data structures for incident reports, system snapshots,
actions, similarity features, provenance tracking, and semantic diffs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from elle.daemon.telemetry.ebpf.syscall_models import SyscallSummary

# =============================================================================
# Type Aliases
# =============================================================================

# Domain categories for incident classification
IncidentDomain = Literal[
    "net",  # Network issues
    "disk",  # Disk/storage issues
    "oom",  # Out-of-memory events
    "docker",  # Container issues
    "auth",  # Authentication/permission issues
    "pkg",  # Package management issues
    "fs",  # Filesystem issues
    "service",  # Systemd service issues
    "gui",  # GUI automation
    "other",  # Uncategorized
]

# Severity levels matching TelemetryEvent
IncidentSeverity = Literal["info", "warning", "error", "critical"]

# Incident lifecycle states
IncidentStatus = Literal[
    "open",  # Issue ongoing or not yet mitigated
    "mitigated",  # Immediate symptoms improved, root cause may be unknown
    "resolved",  # Checks passed + stability window elapsed
    "false_positive",  # Correlated noise, not a real issue
]

# Outcome of actions taken
IncidentOutcome = Literal[
    "unknown",  # Not yet determined
    "improved",  # Problem resolved or significantly better
    "partial",  # Some improvement but not fully resolved
    "no_change",  # Actions had no effect
    "worse",  # Actions made the problem worse
]

# Action types
ActionKind = Literal[
    "shell",  # Shell command executed
    "edit",  # File edit via helper
    "privileged",  # Privileged operation via Polkit
    "verify",  # Verification check
    "rollback",  # Rollback action
    "capability",  # Capability execution
    "gui",  # GUI automation action
]


class PackageState(BaseModel):
    """Installed package state at incident time.

    Captures version information for bedrock and relevant packages
    to support incident debugging and version tracking.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Package name")
    version: str = Field(description="Installed version string")
    source: Literal["apt", "dpkg", "snap", "pip"] = Field(
        default="apt",
        description="Package source/manager",
    )
    is_bedrock: bool = Field(
        default=False,
        description="Whether this is a bedrock system package",
    )


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
    cpu_load: tuple[float, float, float] = Field(description="Load averages: 1min, 5min, 15min")

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

    # Package versions (bedrock + incident-relevant + domain-specific)
    packages: tuple[PackageState, ...] = Field(
        default_factory=tuple,
        description="Bedrock, domain-specific, and relevant package versions at snapshot time",
    )

    # Kernel modules (loaded at snapshot time)
    kernel_modules: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Loaded kernel modules: name, size",
    )

    # Docker image versions (for running containers)
    docker_images: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Docker images: image, tag, digest",
    )

    # Recent apt operations (last 24h)
    recent_apt_history: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Recent apt operations: action, package, version, timestamp",
    )

    # GPU metrics (optional - only present if NVIDIA GPU available)
    gpu_available: bool = Field(default=False, description="Whether GPU metrics are available")
    gpu_count: int = Field(ge=0, default=0, description="Number of GPUs detected")
    gpu_max_memory_pct: float = Field(ge=0.0, le=100.0, default=0.0, description="Max VRAM usage across GPUs")
    gpu_max_util_pct: float = Field(ge=0.0, le=100.0, default=0.0, description="Max utilization across GPUs")
    gpu_max_temp_c: int = Field(ge=0, default=0, description="Max temperature across GPUs")
    gpu_ecc_errors: int = Field(ge=0, default=0, description="Total ECC errors across GPUs")

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
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Max disk usage ratio across mounts",
    )
    mem_pressure: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Memory pressure: 1 - (available / total)",
    )
    swap_pressure: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Swap usage ratio",
    )
    cpu_pressure: float = Field(
        ge=0.0,
        default=0.0,
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

    # GPU metrics (0.0 if no GPU available)
    gpu_mem_pressure: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Max GPU memory usage ratio across GPUs",
    )
    gpu_util_pressure: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Max GPU utilization ratio across GPUs",
    )
    gpu_thermal_pressure: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Max GPU temperature normalized (0=0C, 1=100C)",
    )
    gpu_ecc_errors_1h: int = Field(
        ge=0,
        default=0,
        description="GPU ECC errors in last hour",
    )

    # I/O and storage
    inode_pressure: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Max inode usage ratio across mounts",
    )
    io_latency_pressure: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Normalized I/O latency pressure (0=OK, 1=severe)",
    )

    # Network
    conntrack_pressure: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Conntrack table utilization",
    )
    tcp_retransmit_rate: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="TCP retransmit ratio",
    )

    # Process
    zombie_count: int = Field(
        ge=0,
        default=0,
        description="Number of zombie processes",
    )

    # System state
    pending_reboot: int = Field(
        ge=0,
        le=1,
        default=0,
        description="1 if reboot is required, 0 otherwise",
    )
    cert_expiry_days_min: int = Field(
        ge=0,
        default=365,
        description="Minimum days until any tracked cert expires",
    )

    # DNS
    dns_p95_ms: float = Field(
        ge=0.0,
        default=0.0,
        description="DNS query p95 latency in ms",
    )

    # Cgroup
    cgroup_mem_pressure: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Max cgroup memory utilization",
    )

    # PSI
    psi_cpu_avg10: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="CPU PSI some avg10 (normalized to 0-1)",
    )
    psi_memory_avg10: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Memory PSI some avg10 (normalized to 0-1)",
    )

    # Security
    security_events_1h: int = Field(
        ge=0,
        default=0,
        description="Security-relevant events in last hour",
    )

    # Custom features (extensible)
    custom: dict[str, Any] = Field(default_factory=dict)


class IncidentAction(BaseModel):
    """A single action taken during incident handling.

    Actions are append-only and form an ordered sequence
    of what ELLE actually did.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

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

    # Syscall trace (if tracing was enabled)
    syscall_summary: SyscallSummary | None = Field(
        default=None,
        description="Syscall-level summary of what the action actually did",
    )

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

    expression: str = Field(description="Condition expression, e.g., 'disk./.used_pct > 95'")
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

    # Decision (legacy unstructured field kept for backwards compatibility)
    decision: dict[str, Any] = Field(
        default_factory=dict,
        description="Chosen plan and rationale (legacy, prefer decision_record)",
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
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Confidence in root cause diagnosis",
    )

    # Trigger source
    trigger_source: Literal["telemetry", "command_failure", "user_task", "manual", "gui_automation"] = Field(
        default="manual",
        description="What created this incident",
    )
    trigger_command: str | None = Field(
        default=None,
        description="Command that triggered (if command_failure)",
    )

    # NEW: Structured decision record (replaces unstructured decision dict)
    # This is stored separately in the database but loaded into the model
    # Use decision_record when available, fall back to decision dict otherwise
    decision_record: DecisionRecord | None = Field(
        default=None,
        description="Structured decision record with provenance",
    )

    # NEW: Config file states tracked during this incident
    config_states: tuple[ConfigFileState, ...] = Field(
        default_factory=tuple,
        description="Config files modified during incident resolution",
    )

    # V4: Separated telemetry and control surface snapshots
    # These are stored separately in the database but can be loaded into the model.
    # Use telemetry snapshots for continuous evidence (what's happening).
    # Use control surface snapshots for configuration state (what's configured).
    # Note: These use Any type to avoid circular imports. The actual types are:
    #   - TelemetrySnapshot from elle.daemon.incidents.telemetry_snapshot
    #   - ControlSurfaceSnapshot from elle.daemon.incidents.control_surface
    telemetry_pre: Any | None = Field(
        default=None,
        description="Telemetry snapshot before actions",
    )
    telemetry_post: Any | None = Field(
        default=None,
        description="Telemetry snapshot after actions",
    )
    control_surface_pre: Any | None = Field(
        default=None,
        description="Control surface snapshot before actions",
    )
    control_surface_post: Any | None = Field(
        default=None,
        description="Control surface snapshot after actions",
    )
    surface_drift: dict[str, bool] = Field(
        default_factory=dict,
        description="Surface drift detection: surface name -> drifted (True = changed)",
    )


class IncidentSearchResult(BaseModel):
    """Search result for incident retrieval."""

    model_config = ConfigDict(frozen=True)

    incident: IncidentReport
    score: float = Field(ge=0.0, description="Relevance score")
    match_type: Literal["fingerprint", "lexical", "semantic", "hybrid", "cloud"] = Field(default="hybrid")
    precondition_match_ratio: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="How many preconditions match current state",
    )
    outcome_weight: float = Field(
        ge=0.0,
        le=1.0,
        default=0.5,
        description="Weight based on past outcome quality",
    )
    source: Literal["local", "cloud"] = Field(
        default="local",
        description="Whether result came from local vault or cloud",
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


# =============================================================================
# Provenance Models (Trust Surfaces)
# =============================================================================


class ManPageCitation(BaseModel):
    """Citation of a man page that informed a decision.

    Records which documentation snippets were consulted during
    incident resolution, enabling attribution and trust assessment.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Command name, e.g., 'systemctl'")
    section: str = Field(description="Man section, e.g., '1', '5', '8'")
    snippet: str = Field(description="Relevant excerpt that informed decision")
    match_section: str | None = Field(
        default=None,
        description="Section heading within man page, e.g., 'OPTIONS'",
    )
    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="How relevant this citation was to the decision",
    )


class IncidentCitation(BaseModel):
    """Citation of a prior incident that informed a decision.

    Records which past incidents were consulted and how they
    influenced the current resolution approach.
    """

    model_config = ConfigDict(frozen=True)

    incident_id: str = Field(description="UUID of the cited incident")
    title: str = Field(description="Title of the cited incident")
    outcome: IncidentOutcome = Field(description="Outcome of the cited incident")
    similarity_score: float = Field(
        ge=0.0,
        le=1.0,
        description="How similar this incident was to the current one",
    )
    match_type: Literal["fingerprint", "lexical", "semantic", "hybrid", "daemon_api"] = Field(
        description="How the match was found",
    )
    successful_actions: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Commands that worked in the cited incident",
    )


class TelemetryCitation(BaseModel):
    """Citation of telemetry events that triggered the incident.

    Links the incident to the specific telemetry events that
    led to its creation.
    """

    model_config = ConfigDict(frozen=True)

    event_ids: tuple[str, ...] = Field(
        description="IDs of triggering telemetry events",
    )
    event_summaries: tuple[str, ...] = Field(
        description="Brief summaries of each event",
    )
    trigger_time: datetime = Field(
        description="When the triggering events occurred",
    )


# Type alias for primary source identification
PrimarySource = Literal["man_vault", "incident_vault", "telemetry", "llm_only"]


class Provenance(BaseModel):
    """Complete provenance record for a decision.

    Tracks all sources that informed a decision, enabling
    attribution, trust assessment, and learning.
    """

    model_config = ConfigDict(frozen=True)

    man_pages: tuple[ManPageCitation, ...] = Field(
        default_factory=tuple,
        description="Man page citations that informed the decision",
    )
    prior_incidents: tuple[IncidentCitation, ...] = Field(
        default_factory=tuple,
        description="Prior incident citations that informed the decision",
    )
    triggering_events: TelemetryCitation | None = Field(
        default=None,
        description="Telemetry events that triggered this incident",
    )
    primary_source: PrimarySource = Field(
        default="llm_only",
        description="Primary source of the decision",
    )

    def summary(self) -> str:
        """Generate a human-readable summary of provenance.

        Returns:
            String like 'Suggested because man systemctl(1) + 3 similar incidents succeeded.'
        """
        parts = []

        if self.man_pages:
            man_refs = [f"{m.name}({m.section})" for m in self.man_pages[:2]]
            parts.append(f"man {', '.join(man_refs)}")

        if self.prior_incidents:
            successful = sum(1 for i in self.prior_incidents if i.outcome in ("improved", "partial"))
            if successful:
                parts.append(f"{successful} similar incident(s) succeeded")

        if not parts:
            return "Suggested by LLM analysis"

        return "Suggested because " + " + ".join(parts)


# =============================================================================
# Config File Tracking
# =============================================================================


class ConfigFileState(BaseModel):
    """Hash and metadata for a config file ELLE touched.

    Tracks file state before and after changes for audit trail
    and diff computation.
    """

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="Absolute path to the config file")
    sha256_before: str | None = Field(
        default=None,
        description="SHA256 hash of file before changes",
    )
    sha256_after: str | None = Field(
        default=None,
        description="SHA256 hash of file after changes",
    )
    size_bytes: int = Field(
        ge=0,
        default=0,
        description="File size in bytes (after changes)",
    )
    mtime: datetime | None = Field(
        default=None,
        description="Modification time (after changes)",
    )
    backup_path: str | None = Field(
        default=None,
        description="Path to backup copy if created",
    )


# =============================================================================
# Structured Decision Record
# =============================================================================


# Type alias for confidence tier identification
ConfidenceTier = Literal["fingerprint", "lexical", "semantic", "llm"]


class ConfidenceBreakdown(BaseModel):
    """Breakdown of confidence by evidence type.

    Shows how confidence in a decision was derived from
    different sources.
    """

    model_config = ConfigDict(frozen=True)

    overall: float = Field(
        ge=0.0,
        le=1.0,
        description="Combined overall confidence",
    )
    from_man_vault: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Confidence contribution from man page matches",
    )
    from_incident_vault: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Confidence contribution from prior incident matches",
    )
    from_telemetry: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Confidence contribution from telemetry correlation",
    )
    from_llm: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Confidence contribution from LLM analysis",
    )
    dominant_tier: ConfidenceTier = Field(
        default="llm",
        description="Which tier contributed most to confidence",
    )


class DecisionRecord(BaseModel):
    """Structured record of what was decided and why.

    Replaces unstructured decision dict with strongly-typed
    fields for better querying and analysis.
    """

    model_config = ConfigDict(frozen=True)

    chosen_approach: str = Field(
        description="Description of the approach taken",
    )
    rationale: str = Field(
        default="",
        description="Explanation of why this approach was selected",
    )
    confidence: ConfidenceBreakdown = Field(
        description="Breakdown of confidence by source",
    )
    provenance: Provenance = Field(
        description="Complete provenance record",
    )
    planned_commands: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Commands planned to execute",
    )
    decided_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the decision was made",
    )


# =============================================================================
# Semantic Diff Models
# =============================================================================


# Category of system change
ChangeCategory = Literal[
    "config",  # Configuration file change
    "service",  # Service state change
    "kernel",  # Kernel parameter or version change
    "network",  # Network configuration change
    "security",  # Security-related change
    "package",  # Package installation/removal
]

# Significance level of a change
ChangeSignificance = Literal["low", "medium", "high"]


class SemanticChange(BaseModel):
    """A human-readable description of a system change.

    Provides a semantic interpretation of what changed,
    not just a raw diff.
    """

    model_config = ConfigDict(frozen=True)

    category: ChangeCategory = Field(
        description="Category of the change",
    )
    description: str = Field(
        description="Human-readable description, e.g., 'SSH root login disabled'",
    )
    technical_detail: str = Field(
        default="",
        description="Technical details, e.g., '/etc/ssh/sshd_config: PermitRootLogin no'",
    )
    significance: ChangeSignificance = Field(
        default="medium",
        description="How significant this change is",
    )


class SemanticDiff(BaseModel):
    """Semantic diff between two points in time.

    Provides human-readable summaries of what changed
    between snapshots.
    """

    model_config = ConfigDict(frozen=True)

    from_time: datetime = Field(
        description="Start time of the diff window",
    )
    to_time: datetime = Field(
        description="End time of the diff window",
    )
    changes: tuple[SemanticChange, ...] = Field(
        default_factory=tuple,
        description="List of semantic changes",
    )

    def summary(self) -> str:
        """Generate a summary of all changes.

        Returns:
            String like 'Between now and last week: SSH root login disabled; kernel updated.'
        """
        if not self.changes:
            return "No significant changes detected"

        # Group by significance
        high = [c for c in self.changes if c.significance == "high"]
        medium = [c for c in self.changes if c.significance == "medium"]

        # Prioritize high significance changes
        to_include = high[:3] if high else medium[:3]

        descriptions = [c.description for c in to_include]
        suffix = f"; +{len(self.changes) - len(to_include)} more" if len(self.changes) > len(to_include) else ""

        return "; ".join(descriptions) + suffix
