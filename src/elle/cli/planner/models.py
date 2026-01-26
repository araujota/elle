"""Pydantic models for the System Task Planner.

Defines data structures for command plans, verification,
execution tracking, and outcomes.
"""

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Risk Level
# =============================================================================


RiskLevel = Literal["low", "medium", "high"]


# Commands that require rollback plans
REQUIRES_ROLLBACK = frozenset(
    {
        "netplan",
        "ufw",
        "iptables",
        "fstab",
        "crontab",
        "cron",
        "systemctl enable",
        "systemctl disable",
        "systemctl mask",
        "update-grub",
        "grub",
    }
)

# Commands that are inherently high-risk
HIGH_RISK_PATTERNS = frozenset(
    {
        "dd",
        "mkfs",
        "fdisk",
        "parted",
        "wipefs",
        "shred",
        "rm -rf",
        "chmod -R",
        "chown -R",
    }
)

# Commands that are medium-risk
MEDIUM_RISK_PATTERNS = frozenset(
    {
        "apt",
        "dpkg",
        "snap",
        "pip install",
        "systemctl",
        "service",
        "mount",
        "umount",
        "ufw",
        "iptables",
        "netplan",
    }
)


# =============================================================================
# Input Models
# =============================================================================


class TaskRequest(BaseModel):
    """A system task request from the user.

    Captures the natural language request and context.
    """

    model_config = ConfigDict(frozen=True)

    request: str = Field(description="The user's natural language task request")
    cwd: Path = Field(default_factory=Path.cwd, description="Working directory")
    entities: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Detected entities from classification",
    )


class ManDocContext(BaseModel):
    """Summarized Man Vault documentation for context."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Command name")
    section: str = Field(description="Man section")
    snippet: str = Field(description="Relevant excerpt")
    flags_used: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Flags referenced in snippet",
    )
    relevance_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Relevance score from search",
    )
    match_section: str | None = Field(
        default=None,
        description="Section heading within man page",
    )


class PriorPlanContext(BaseModel):
    """Summarized prior plan for context.

    Includes successful execution history for learning.
    """

    model_config = ConfigDict(frozen=True)

    incident_id: str
    title: str
    outcome: str
    plan_summary: str = ""
    commands_executed: tuple[str, ...] = Field(default_factory=tuple)
    rollback_used: bool = False
    score: float = 0.0
    days_ago: int = 0
    match_type: Literal["fingerprint", "lexical", "semantic", "hybrid"] = Field(
        default="hybrid",
        description="How this prior plan was matched",
    )


class DockerState(BaseModel):
    """Current Docker environment state for planning context."""

    model_config = ConfigDict(frozen=True)

    running_containers: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Currently running containers",
    )
    images: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Available Docker images",
    )
    networks: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Docker networks",
    )
    volumes: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Docker volumes",
    )
    docker_available: bool = Field(
        default=False,
        description="Whether Docker is available",
    )


class NetworkState(BaseModel):
    """Current network state for planning context."""

    model_config = ConfigDict(frozen=True)

    interfaces: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Network interfaces and their status",
    )
    firewall_active: bool = Field(
        default=False,
        description="Whether firewall is active",
    )
    firewall_type: str = Field(
        default="unknown",
        description="Firewall type (ufw, iptables, none)",
    )
    default_gateway: str | None = Field(
        default=None,
        description="Default gateway IP",
    )
    dns_servers: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Configured DNS servers",
    )
    wireguard_interfaces: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Active WireGuard interfaces",
    )


class TrendContextRef(BaseModel):
    """Reference to trend context for planning.

    Lightweight model that references trend data without duplicating it.
    Actual TrendContext is in elle.daemon.telemetry.trends.
    """

    model_config = ConfigDict(frozen=True)

    has_warnings: bool = Field(
        default=False,
        description="Whether any metrics have warnings",
    )
    warning_messages: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Human-readable warning messages",
    )
    disk_usage_pct: dict[str, float] = Field(
        default_factory=dict,
        description="Disk usage by mount point",
    )
    memory_usage_pct: float | None = Field(
        default=None,
        description="Memory usage percentage",
    )
    active_anomalies: int = Field(
        default=0,
        description="Number of active anomalies",
    )


class PlanContext(BaseModel):
    """Complete context for task planning.

    Combines task request with documentation and prior art.
    """

    model_config = ConfigDict(frozen=True)

    request: TaskRequest
    man_docs: tuple[ManDocContext, ...] = Field(
        default_factory=tuple,
        description="Relevant Man Vault documentation",
    )
    prior_plans: tuple[PriorPlanContext, ...] = Field(
        default_factory=tuple,
        description="Similar past task executions",
    )
    docker_state: DockerState | None = Field(
        default=None,
        description="Docker environment state (for container tasks)",
    )
    network_state: NetworkState | None = Field(
        default=None,
        description="Network state (for network tasks)",
    )
    trend_context: TrendContextRef | None = Field(
        default=None,
        description="System trend context for pre-execution validation",
    )

    def has_vault_context(self) -> bool:
        """Check if we have context from vaults."""
        return bool(self.man_docs) or bool(self.prior_plans)

    @property
    def primary_source(self) -> Literal["man_vault", "incident_vault", "llm_only"]:
        """Determine the primary source of context."""
        # Prior plans with good outcomes are most valuable
        good_prior = [p for p in self.prior_plans if p.outcome in ("improved", "partial", "success") and p.score > 0.5]
        if good_prior:
            return "incident_vault"

        # Man pages provide documentation grounding
        if self.man_docs:
            return "man_vault"

        return "llm_only"

    def build_rationale_summary(self) -> str:
        """Build a summary string for rationale display."""
        parts = []

        if self.man_docs:
            man_refs = [f"{m.name}({m.section})" for m in self.man_docs[:2]]
            parts.append(f"man {', '.join(man_refs)}")

        if self.prior_plans:
            successful = sum(1 for p in self.prior_plans if p.outcome in ("improved", "partial", "success"))
            if successful:
                parts.append(f"{successful} similar task(s) succeeded")

        if not parts:
            return "Suggested by LLM analysis"

        return "Suggested because " + " + ".join(parts)

    def to_rationale_kwargs(self) -> dict[str, Any]:
        """Convert context to kwargs for rationale panel display.

        Returns:
            Dict of kwargs for plan_panel's rationale display.
        """
        man_citations = [
            {
                "name": m.name,
                "section": m.section,
                "snippet": m.snippet,
                "relevance_score": m.relevance_score,
                "match_section": m.match_section or "",
            }
            for m in self.man_docs
        ]

        incident_citations = [
            {
                "incident_id": p.incident_id,
                "title": p.title,
                "outcome": p.outcome,
                "similarity_score": p.score,
                "match_type": p.match_type,
                "successful_actions": list(p.commands_executed),
            }
            for p in self.prior_plans
        ]

        # Build confidence breakdown based on sources
        overall = 0.5  # Default LLM confidence
        from_man = 0.0
        from_inc = 0.0

        if man_citations:
            from_man = max(c.get("relevance_score", 0) for c in man_citations) * 0.3
            overall += from_man

        if incident_citations:
            good_outcomes = [c for c in incident_citations if c.get("outcome") in ("improved", "partial", "success")]
            if good_outcomes:
                from_inc = max(c.get("similarity_score", 0) for c in good_outcomes) * 0.4
                overall += from_inc

        overall = min(1.0, overall)

        confidence = {
            "overall": overall,
            "from_man_vault": from_man,
            "from_incident_vault": from_inc,
            "from_llm": 0.5 - from_man - from_inc if from_man + from_inc < 0.5 else 0.0,
        }

        return {
            "man_citations": man_citations if man_citations else None,
            "incident_citations": incident_citations if incident_citations else None,
            "confidence": confidence,
            "rationale_summary": self.build_rationale_summary(),
            "show_rationale": self.has_vault_context(),
        }


# =============================================================================
# Plan Models
# =============================================================================


class PlanStep(BaseModel):
    """A single step in a command plan.

    Each step represents one command to execute, or a capability to invoke.
    """

    model_config = ConfigDict(frozen=True)

    # Command-based execution (legacy)
    command: str | None = Field(
        default=None,
        description="The command to execute (optional if capability is set)",
    )
    explanation: str = Field(description="What this step does")
    risk_level: RiskLevel = Field(description="Risk assessment for this step")
    requires_privilege: bool = Field(
        default=False,
        description="Whether elevated privileges are needed",
    )
    can_fail: bool = Field(
        default=False,
        description="Whether failure of this step is acceptable",
    )

    # Capability-based execution (preferred)
    capability: str | None = Field(
        default=None,
        description="Capability name to invoke (e.g., 'service.restart')",
    )
    capability_input: dict[str, Any] | None = Field(
        default=None,
        description="Input parameters for the capability",
    )

    @property
    def uses_capability(self) -> bool:
        """Check if this step uses a capability."""
        return self.capability is not None

    @property
    def executable(self) -> str:
        """Get the executable command or capability name."""
        return self.capability or self.command or ""


class ValidationCheck(BaseModel):
    """A validation check to verify plan success.

    Checks are deterministic commands that verify state.
    """

    model_config = ConfigDict(frozen=True)

    command: str = Field(description="The validation command")
    description: str = Field(description="What this check verifies")
    expected: str | None = Field(
        default=None,
        description="Expected output pattern (regex or substring)",
    )


class RollbackStep(BaseModel):
    """A rollback step to undo changes.

    Used when plan execution fails or user requests rollback.
    """

    model_config = ConfigDict(frozen=True)

    command: str = Field(description="The rollback command")
    explanation: str = Field(description="What this undoes")
    requires_privilege: bool = Field(default=False)


class CommandPlan(BaseModel):
    """A verified plan for system modification.

    Generated by the LLM, verified locally before presentation.
    All plans follow: Explain -> Plan -> Confirm -> Apply -> Check.
    """

    model_config = ConfigDict(frozen=True)

    # Summary
    title: str = Field(description="Short title for the plan")
    explanation: str = Field(description="What this plan accomplishes")

    # Steps
    steps: tuple[PlanStep, ...] = Field(
        description="Ordered list of steps to execute",
    )

    # Validation
    checks: tuple[ValidationCheck, ...] = Field(
        default_factory=tuple,
        description="Commands to verify success",
    )

    # Rollback
    rollback: tuple[RollbackStep, ...] = Field(
        default_factory=tuple,
        description="Commands to undo changes",
    )

    # Metadata
    risks: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Risk warnings for the user",
    )
    requires_privilege: bool = Field(
        default=False,
        description="Whether any step requires elevated privileges",
    )
    grounded_in: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Man pages used to ground this plan",
    )

    @property
    def overall_risk(self) -> RiskLevel:
        """Compute overall risk level from steps."""
        if any(s.risk_level == "high" for s in self.steps):
            return "high"
        if any(s.risk_level == "medium" for s in self.steps):
            return "medium"
        return "low"

    @property
    def step_count(self) -> int:
        """Number of steps in the plan."""
        return len(self.steps)

    @property
    def has_rollback(self) -> bool:
        """Check if rollback is available."""
        return len(self.rollback) > 0

    @property
    def has_checks(self) -> bool:
        """Check if validation checks are available."""
        return len(self.checks) > 0


# =============================================================================
# Verification Models
# =============================================================================


class StepVerification(BaseModel):
    """Verification result for a single step."""

    model_config = ConfigDict(frozen=True)

    step_index: int = Field(description="Index of the step verified")
    command: str = Field(description="The command that was verified")
    is_valid: bool = Field(description="Whether the step passed verification")
    checks_passed: tuple[str, ...] = Field(default_factory=tuple)
    checks_failed: tuple[str, ...] = Field(default_factory=tuple)
    warnings: tuple[str, ...] = Field(default_factory=tuple)


class PlanVerification(BaseModel):
    """Complete verification results for a plan."""

    model_config = ConfigDict(frozen=True)

    is_valid: bool = Field(description="Whether the plan is safe to execute")
    step_results: tuple[StepVerification, ...] = Field(default_factory=tuple)
    errors: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Critical errors that block execution",
    )
    warnings: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Non-critical warnings",
    )
    rollback_required: bool = Field(
        default=False,
        description="Whether rollback is required but missing",
    )
    checks_required: bool = Field(
        default=False,
        description="Whether checks are required but missing",
    )


# =============================================================================
# Execution Models
# =============================================================================


class StepResult(BaseModel):
    """Result of executing a single step."""

    model_config = ConfigDict(frozen=True)

    step_index: int = Field(description="Index of the step executed")
    command: str = Field(description="The command that was executed")
    exit_code: int = Field(description="Exit code from the command")
    stdout: str = Field(default="", description="Standard output")
    stderr: str = Field(default="", description="Error output")
    success: bool = Field(description="Whether the step succeeded")
    privileged: bool = Field(default=False, description="Used elevated privileges")
    duration_ms: int | None = Field(
        default=None,
        description="Execution duration in milliseconds",
    )
    executed_at: datetime = Field(default_factory=datetime.utcnow)


class CheckResult(BaseModel):
    """Result of running a validation check."""

    model_config = ConfigDict(frozen=True)

    check_index: int
    command: str
    passed: bool
    output: str = ""
    expected: str | None = None
    actual: str | None = None


class PlanOutcome(str, Enum):
    """Outcome of plan execution."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    CANCELLED = "cancelled"
    ERROR = "error"


# =============================================================================
# Final Result Model
# =============================================================================


class PlanResult(BaseModel):
    """Complete result of a planning and execution operation.

    Captures the entire flow: context, plan, verification,
    execution, checks, and outcome.
    """

    model_config = ConfigDict(frozen=True)

    # Input
    context: PlanContext

    # Generated plan (may be None if LLM unavailable)
    plan: CommandPlan | None = None

    # Verification
    verification: PlanVerification | None = None

    # Execution state
    approved: bool = Field(default=False, description="User approved the plan")
    step_results: tuple[StepResult, ...] = Field(
        default_factory=tuple,
        description="Results from executing steps",
    )
    check_results: tuple[CheckResult, ...] = Field(
        default_factory=tuple,
        description="Results from validation checks",
    )
    rollback_results: tuple[StepResult, ...] = Field(
        default_factory=tuple,
        description="Results from rollback (if triggered)",
    )

    # Outcome
    outcome: PlanOutcome = Field(default=PlanOutcome.CANCELLED)

    # Incident tracking
    incident_id: str | None = Field(
        default=None,
        description="Linked Incident Vault ID",
    )

    # LLM fallback indicator
    used_fallback: bool = Field(
        default=False,
        description="Whether rule-based fallback was used",
    )

    def with_plan(self, plan: CommandPlan) -> "PlanResult":
        """Return a new result with the given plan."""
        return PlanResult(
            context=self.context,
            plan=plan,
            verification=self.verification,
            approved=self.approved,
            step_results=self.step_results,
            check_results=self.check_results,
            rollback_results=self.rollback_results,
            outcome=self.outcome,
            incident_id=self.incident_id,
            used_fallback=self.used_fallback,
        )

    def with_verification(self, verification: PlanVerification) -> "PlanResult":
        """Return a new result with verification."""
        return PlanResult(
            context=self.context,
            plan=self.plan,
            verification=verification,
            approved=self.approved,
            step_results=self.step_results,
            check_results=self.check_results,
            rollback_results=self.rollback_results,
            outcome=self.outcome,
            incident_id=self.incident_id,
            used_fallback=self.used_fallback,
        )

    def with_approved(self) -> "PlanResult":
        """Return a new result marked as approved."""
        return PlanResult(
            context=self.context,
            plan=self.plan,
            verification=self.verification,
            approved=True,
            step_results=self.step_results,
            check_results=self.check_results,
            rollback_results=self.rollback_results,
            outcome=self.outcome,
            incident_id=self.incident_id,
            used_fallback=self.used_fallback,
        )

    def with_step_result(self, result: StepResult) -> "PlanResult":
        """Return a new result with an additional step result."""
        return PlanResult(
            context=self.context,
            plan=self.plan,
            verification=self.verification,
            approved=self.approved,
            step_results=(*self.step_results, result),
            check_results=self.check_results,
            rollback_results=self.rollback_results,
            outcome=self.outcome,
            incident_id=self.incident_id,
            used_fallback=self.used_fallback,
        )

    def with_check_results(self, results: tuple[CheckResult, ...]) -> "PlanResult":
        """Return a new result with check results."""
        return PlanResult(
            context=self.context,
            plan=self.plan,
            verification=self.verification,
            approved=self.approved,
            step_results=self.step_results,
            check_results=results,
            rollback_results=self.rollback_results,
            outcome=self.outcome,
            incident_id=self.incident_id,
            used_fallback=self.used_fallback,
        )

    def with_rollback_results(self, results: tuple[StepResult, ...]) -> "PlanResult":
        """Return a new result with rollback results."""
        return PlanResult(
            context=self.context,
            plan=self.plan,
            verification=self.verification,
            approved=self.approved,
            step_results=self.step_results,
            check_results=self.check_results,
            rollback_results=results,
            outcome=self.outcome,
            incident_id=self.incident_id,
            used_fallback=self.used_fallback,
        )

    def with_outcome(self, outcome: PlanOutcome) -> "PlanResult":
        """Return a new result with the given outcome."""
        return PlanResult(
            context=self.context,
            plan=self.plan,
            verification=self.verification,
            approved=self.approved,
            step_results=self.step_results,
            check_results=self.check_results,
            rollback_results=self.rollback_results,
            outcome=outcome,
            incident_id=self.incident_id,
            used_fallback=self.used_fallback,
        )

    def with_incident_id(self, incident_id: str) -> "PlanResult":
        """Return a new result with the given incident ID."""
        return PlanResult(
            context=self.context,
            plan=self.plan,
            verification=self.verification,
            approved=self.approved,
            step_results=self.step_results,
            check_results=self.check_results,
            rollback_results=self.rollback_results,
            outcome=self.outcome,
            incident_id=incident_id,
            used_fallback=self.used_fallback,
        )

    @property
    def is_executable(self) -> bool:
        """Check if the plan can be executed."""
        return self.plan is not None and self.verification is not None and self.verification.is_valid

    @property
    def steps_succeeded(self) -> int:
        """Count of successfully executed steps."""
        return sum(1 for r in self.step_results if r.success)

    @property
    def steps_failed(self) -> int:
        """Count of failed steps."""
        return sum(1 for r in self.step_results if not r.success)

    @property
    def checks_passed(self) -> int:
        """Count of passed validation checks."""
        return sum(1 for r in self.check_results if r.passed)

    @property
    def checks_failed(self) -> int:
        """Count of failed validation checks."""
        return sum(1 for r in self.check_results if not r.passed)
