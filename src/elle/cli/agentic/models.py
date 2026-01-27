"""Pydantic data models for the unified agentic execution system.

Defines frozen, immutable data structures for:
- Unified intent analysis (information needs + action requests)
- Capability call planning with dependencies
- Parallel execution groups
- Evidence gathering and evaluation
- Response synthesis
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Enums
# =============================================================================


class EvaluationStatus(str, Enum):
    """Result of goal evaluation."""

    SATISFIED = "satisfied"  # Goal achieved
    NEEDS_MORE = "needs_more"  # Need additional info/actions
    FAILED = "failed"  # Cannot achieve goal


class ActionType(str, Enum):
    """Type of action requested."""

    RESTART = "restart"
    START = "start"
    STOP = "stop"
    INSTALL = "install"
    REMOVE = "remove"
    UPDATE = "update"
    CONFIGURE = "configure"
    CREATE = "create"
    DELETE = "delete"
    FIX = "fix"
    ENABLE = "enable"
    DISABLE = "disable"
    CUSTOM = "custom"


# =============================================================================
# Information Need Analysis
# =============================================================================


InformationCategory = Literal[
    "service",  # Systemd service status/logs
    "file",  # File content/metadata
    "package",  # Package information
    "docker",  # Container status/logs
    "network",  # Network listeners/connections
    "config",  # Configuration files
    "system",  # System resources/info
]


class InformationNeed(BaseModel):
    """What information is needed to answer the question.

    Represents a single atomic information requirement extracted from
    the user's question. Multiple needs may be identified for complex questions.
    """

    model_config = ConfigDict(frozen=True)

    category: InformationCategory = Field(
        description="Category of information needed",
    )
    target: str = Field(
        description="Target of the query (e.g., 'nginx', '/etc/hosts', 'postgresql')",
    )
    aspects: tuple[str, ...] = Field(
        description="Specific aspects needed (e.g., ('status', 'logs', 'config'))",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in this need being correct",
    )


# =============================================================================
# Action Requests (NEW)
# =============================================================================


class ActionRequest(BaseModel):
    """What action the user wants performed.

    Represents a single atomic action request extracted from the user's
    input. Actions are system mutations that require capabilities to execute.
    """

    model_config = ConfigDict(frozen=True)

    action_type: ActionType = Field(
        description="Type of action requested",
    )
    target: str = Field(
        description="Target of the action (e.g., 'nginx', '/etc/hosts')",
    )
    domain: str = Field(
        description="Domain category (service, file, package, docker, etc.)",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional parameters for the action",
    )
    condition: str | None = Field(
        default=None,
        description="Conditional execution (e.g., 'if not running')",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in this action being correct",
    )


# =============================================================================
# Unified Intent (NEW)
# =============================================================================


class AgenticIntent(BaseModel):
    """Unified intent from user input - both info needs and actions.

    This is the output of the IntentAnalyzer. It captures BOTH what
    information the user needs AND what actions they want performed.
    This enables handling mixed requests like "Why is nginx failing and
    can you restart it?"
    """

    model_config = ConfigDict(frozen=True)

    # What information is needed (read operations)
    information_needs: tuple[InformationNeed, ...] = Field(
        default_factory=tuple,
        description="Information gathering requirements",
    )

    # What actions are requested (write operations)
    action_requests: tuple[ActionRequest, ...] = Field(
        default_factory=tuple,
        description="Actions to be performed",
    )

    # User's overall goal
    goal_summary: str = Field(
        description="Natural language summary of what user wants",
    )

    # Confidence in interpretation
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in this interpretation",
    )

    # Whether this is read-only (no actions)
    @property
    def is_read_only(self) -> bool:
        """Check if this intent only requires information gathering."""
        return len(self.action_requests) == 0

    # Whether this has both info needs and actions
    @property
    def is_mixed(self) -> bool:
        """Check if this intent has both information needs and actions."""
        return len(self.information_needs) > 0 and len(self.action_requests) > 0


# =============================================================================
# Capability Planning
# =============================================================================


class CapabilityCall(BaseModel):
    """A planned capability invocation.

    Represents a single capability call that will be executed to gather
    information or perform an action. Contains all arguments needed for execution.
    """

    model_config = ConfigDict(frozen=True)

    capability: str = Field(
        description="Capability name (e.g., 'service.status', 'service.restart')",
    )
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments for the capability",
    )
    purpose: str = Field(
        description="Human-readable explanation of why this capability is called",
    )
    depends_on: tuple[int, ...] = Field(
        default_factory=tuple,
        description="Indices of prior calls this depends on (for dependency resolution)",
    )
    requires_confirmation: bool = Field(
        default=False,
        description="Whether this call requires user confirmation",
    )
    is_read_only: bool = Field(
        default=True,
        description="Whether this is a read-only operation",
    )


# =============================================================================
# Parallel Execution Groups (NEW)
# =============================================================================


class ParallelGroup(BaseModel):
    """A group of capabilities that can execute in parallel.

    Capabilities in the same group have no dependencies on each other
    and can be executed concurrently.
    """

    model_config = ConfigDict(frozen=True)

    calls: tuple[CapabilityCall, ...] = Field(
        description="Capabilities to execute in parallel",
    )


class ExecutionPlan(BaseModel):
    """Execution plan with dependency-aware parallel groups.

    Groups are executed sequentially, but calls within each group
    run in parallel. This enables maximum concurrency while respecting
    dependencies.
    """

    model_config = ConfigDict(frozen=True)

    parallel_groups: tuple[ParallelGroup, ...] = Field(
        description="Groups of parallel capability calls (executed sequentially)",
    )
    intent: AgenticIntent = Field(
        description="The intent this plan implements",
    )
    estimated_duration_ms: int = Field(
        default=0,
        ge=0,
        description="Estimated total execution time in milliseconds",
    )
    requires_confirmation: bool = Field(
        default=False,
        description="Whether any capability in the plan requires confirmation",
    )

    @property
    def total_calls(self) -> int:
        """Total number of capability calls in the plan."""
        return sum(len(g.calls) for g in self.parallel_groups)

    @property
    def all_calls(self) -> tuple[CapabilityCall, ...]:
        """Flat list of all capability calls."""
        calls: list[CapabilityCall] = []
        for group in self.parallel_groups:
            calls.extend(group.calls)
        return tuple(calls)


# =============================================================================
# Legacy GatherPlan (kept for backwards compatibility)
# =============================================================================


class GatherPlan(BaseModel):
    """Plan for gathering information to answer a question.

    Contains the analyzed needs and the capability calls selected
    to satisfy those needs.

    DEPRECATED: Use ExecutionPlan for new code. This is kept for
    backwards compatibility with the legacy analyzer/selector.
    """

    model_config = ConfigDict(frozen=True)

    needs: tuple[InformationNeed, ...] = Field(
        description="Information needs identified from the question",
    )
    calls: tuple[CapabilityCall, ...] = Field(
        description="Capability calls to execute",
    )
    estimated_duration_ms: int = Field(
        default=0,
        ge=0,
        description="Estimated total execution time in milliseconds",
    )


# =============================================================================
# Evidence Gathering
# =============================================================================


class GatheredEvidence(BaseModel):
    """Evidence collected from a capability execution.

    Captures the complete result of executing a single capability,
    including timing information for performance tracking.
    """

    model_config = ConfigDict(frozen=True)

    capability: str = Field(
        description="Capability that was executed",
    )
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments used for execution",
    )
    success: bool = Field(
        description="Whether the capability executed successfully",
    )
    output: str | None = Field(
        default=None,
        description="String representation of the output",
    )
    error: str | None = Field(
        default=None,
        description="Error message if failed",
    )
    duration_ms: int = Field(
        ge=0,
        description="Execution time in milliseconds",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the capability was executed",
    )


class ExecutionEvidence(BaseModel):
    """Evidence from capability execution via the real CapabilityExecutor.

    This is the new evidence model that captures the full lifecycle
    of capability execution including policy checks, verification, and
    incident recording.
    """

    model_config = ConfigDict(frozen=True)

    capability: str = Field(
        description="Capability that was executed",
    )
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments used for execution",
    )
    success: bool = Field(
        description="Whether the capability executed successfully",
    )
    output: Any = Field(
        default=None,
        description="Output from the capability (can be Pydantic model)",
    )
    output_text: str | None = Field(
        default=None,
        description="String representation of output for display",
    )
    error: str | None = Field(
        default=None,
        description="Error message if failed",
    )
    duration_ms: int = Field(
        default=0,
        ge=0,
        description="Execution time in milliseconds",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the capability was executed",
    )

    # Additional fields from CapabilityResult
    warnings: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Non-fatal warnings from execution",
    )
    verification_passed: bool | None = Field(
        default=None,
        description="Result of post-execution verification",
    )
    verification_details: str | None = Field(
        default=None,
        description="Details from verification",
    )
    incident_id: str | None = Field(
        default=None,
        description="Incident ID if recorded",
    )
    was_confirmed: bool = Field(
        default=False,
        description="Whether user confirmation was obtained",
    )
    policy_effect: str | None = Field(
        default=None,
        description="Policy effect that was applied",
    )


class GatherResult(BaseModel):
    """Result of executing a gather plan.

    Contains all evidence collected and an assessment of whether
    it's sufficient to answer the original question.

    DEPRECATED: Use ExecutionResult for new code.
    """

    model_config = ConfigDict(frozen=True)

    plan: GatherPlan = Field(
        description="The plan that was executed",
    )
    evidence: tuple[GatheredEvidence, ...] = Field(
        description="All evidence collected",
    )
    sufficient: bool = Field(
        description="Whether gathered evidence is sufficient to answer",
    )
    missing: tuple[str, ...] = Field(
        default_factory=tuple,
        description="What information we couldn't determine",
    )


class ExecutionResult(BaseModel):
    """Result of executing an ExecutionPlan.

    Contains all evidence from capability executions and metadata
    about the execution process.
    """

    model_config = ConfigDict(frozen=True)

    plan: ExecutionPlan = Field(
        description="The plan that was executed",
    )
    evidence: tuple[ExecutionEvidence, ...] = Field(
        description="Evidence from all capability executions",
    )
    total_duration_ms: int = Field(
        default=0,
        ge=0,
        description="Total execution time in milliseconds",
    )

    @property
    def success_count(self) -> int:
        """Number of successful capability executions."""
        return sum(1 for e in self.evidence if e.success)

    @property
    def failure_count(self) -> int:
        """Number of failed capability executions."""
        return sum(1 for e in self.evidence if not e.success)

    @property
    def all_succeeded(self) -> bool:
        """Whether all capabilities executed successfully."""
        return all(e.success for e in self.evidence)

    @property
    def any_succeeded(self) -> bool:
        """Whether any capability executed successfully."""
        return any(e.success for e in self.evidence)


# =============================================================================
# Goal Evaluation (NEW)
# =============================================================================


class EvaluationResult(BaseModel):
    """Result of evaluating whether execution achieved the user's intent.

    Used by the GoalEvaluator to determine if we should continue
    the agentic loop or return a response.
    """

    model_config = ConfigDict(frozen=True)

    status: EvaluationStatus = Field(
        description="Overall evaluation status",
    )
    info_satisfied: bool = Field(
        default=True,
        description="Whether all information needs were satisfied",
    )
    actions_completed: bool = Field(
        default=True,
        description="Whether all requested actions were completed",
    )
    gaps: tuple[str, ...] = Field(
        default_factory=tuple,
        description="What's still missing or incomplete",
    )
    suggested_capabilities: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Capabilities that might fill the gaps",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in this evaluation",
    )
    rationale: str = Field(
        default="",
        description="Explanation of the evaluation",
    )


# =============================================================================
# Response Synthesis
# =============================================================================


class AgenticResponse(BaseModel):
    """Final response with provenance.

    The complete result of agentic execution, including
    the synthesized answer, supporting evidence, and suggestions
    for follow-up actions.

    Works with both the legacy GatheredEvidence and new ExecutionEvidence.
    """

    model_config = ConfigDict(frozen=True)

    answer: str = Field(
        description="Natural language answer/summary of what was done",
    )
    evidence: tuple[GatheredEvidence | ExecutionEvidence, ...] = Field(
        description="Evidence from capability executions",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in the answer's accuracy",
    )
    follow_up_suggestions: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Suggested follow-up questions or actions",
    )

    # New fields for unified handler
    intent: AgenticIntent | None = Field(
        default=None,
        description="The interpreted intent (if using unified handler)",
    )
    actions_taken: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Summary of actions that were performed",
    )
    success: bool = Field(
        default=True,
        description="Whether the overall request succeeded",
    )
    iterations: int = Field(
        default=1,
        ge=1,
        description="Number of agentic loop iterations",
    )
