"""Named stages for the agentic loop.

Defines the explicit stages that the agent loop transitions through,
enabling introspection and debugging of loop execution.

Stages:
    OBSERVE     - Receive input, gather initial context
    CLASSIFY    - Determine intent and required actions
    RETRIEVE    - Query vaults for relevant context
    HYPOTHESIZE - Generate hypotheses about solution
    SELECT      - Choose capability to execute
    ACT         - Execute capability
    VERIFY      - Check outcome
    RECORD      - Create/update incident
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LoopStage(str, Enum):
    """Named stages of the agent loop.

    Each stage represents a distinct phase in the reasoning cycle.
    The loop may visit stages multiple times as it iterates.
    """

    OBSERVE = "observe"
    """Receive input, gather initial context from session and environment."""

    CLASSIFY = "classify"
    """Determine intent and required actions."""

    RETRIEVE = "retrieve"
    """Query vaults for relevant context (man vault, incident vault, capabilities)."""

    HYPOTHESIZE = "hypothesize"
    """Generate hypotheses about the solution."""

    SELECT = "select"
    """Choose which capability to execute."""

    ACT = "act"
    """Execute the selected capability."""

    VERIFY = "verify"
    """Check the outcome of the action."""

    RECORD = "record"
    """Create or update incident record."""

    COMPLETE = "complete"
    """Loop has completed successfully."""

    ERROR = "error"
    """Loop encountered an error."""

    @property
    def description(self) -> str:
        """Human-readable description of the stage."""
        descriptions = {
            LoopStage.OBSERVE: "Receiving input and gathering context",
            LoopStage.CLASSIFY: "Determining intent and required actions",
            LoopStage.RETRIEVE: "Querying knowledge vaults for context",
            LoopStage.HYPOTHESIZE: "Generating solution hypotheses",
            LoopStage.SELECT: "Choosing capability to execute",
            LoopStage.ACT: "Executing selected capability",
            LoopStage.VERIFY: "Verifying action outcome",
            LoopStage.RECORD: "Recording to incident vault",
            LoopStage.COMPLETE: "Loop completed successfully",
            LoopStage.ERROR: "Loop encountered an error",
        }
        return descriptions.get(self, "Unknown stage")


class StageTransition(BaseModel):
    """Record of a stage transition.

    Captures when the loop moves from one stage to another,
    including the reason for the transition and relevant data.
    """

    model_config = ConfigDict(frozen=True)

    from_stage: LoopStage
    """Stage transitioning from."""

    to_stage: LoopStage
    """Stage transitioning to."""

    timestamp: datetime
    """When the transition occurred."""

    reason: str
    """Why this transition happened."""

    duration_ms: int = Field(default=0, ge=0)
    """Time spent in from_stage before transitioning."""

    data: dict[str, Any] = Field(default_factory=dict)
    """Stage-specific data relevant to this transition."""


class Hypothesis(BaseModel):
    """A hypothesis considered during reasoning.

    During the HYPOTHESIZE stage, the LLM generates multiple hypotheses
    about what might solve the user's problem. These are evaluated and
    one is selected for execution.
    """

    model_config = ConfigDict(frozen=True)

    hypothesis_id: str
    """Unique identifier for this hypothesis."""

    text: str
    """The hypothesis statement."""

    confidence: float = Field(ge=0.0, le=1.0)
    """Confidence score (0-1) in this hypothesis."""

    source: Literal["retrieval", "llm", "prior_incident"]
    """Where this hypothesis originated."""

    supporting_evidence: tuple[str, ...] = Field(default_factory=tuple)
    """Evidence supporting this hypothesis."""

    capability_candidates: tuple[str, ...] = Field(default_factory=tuple)
    """Capabilities that could test/implement this hypothesis."""

    rejected: bool = False
    """Whether this hypothesis was rejected."""

    rejected_reason: str | None = None
    """Reason for rejection if rejected."""

    chosen: bool = False
    """Whether this hypothesis was chosen for execution."""


class CapabilityEvaluation(BaseModel):
    """Record of capability evaluation during SELECT stage.

    When the loop considers which capability to use, each candidate
    is evaluated and the results are recorded here.
    """

    model_config = ConfigDict(frozen=True)

    capability_name: str
    """Name of the capability evaluated."""

    domain: str
    """Capability domain (service, file, network, etc.)."""

    risk_level: str
    """Risk level (none, low, medium, high, critical)."""

    applicable: bool
    """Whether this capability is applicable to the current task."""

    applicability_reason: str = ""
    """Why the capability is or isn't applicable."""

    score: float = Field(default=0.0, ge=0.0, le=1.0)
    """Ranking score for this capability."""

    chosen: bool = False
    """Whether this capability was chosen for execution."""

    rejection_reason: str | None = None
    """Reason for not choosing this capability."""


class RetrievalResult(BaseModel):
    """Results from vault retrieval during RETRIEVE stage."""

    model_config = ConfigDict(frozen=True)

    man_vault_hits: int = 0
    """Number of man vault results retrieved."""

    man_vault_sources: tuple[str, ...] = Field(default_factory=tuple)
    """Man pages consulted."""

    incident_vault_hits: int = 0
    """Number of incident vault results retrieved."""

    incident_ids: tuple[str, ...] = Field(default_factory=tuple)
    """Incident IDs retrieved for context."""

    capability_matches: int = 0
    """Number of matching capabilities found."""

    capability_names: tuple[str, ...] = Field(default_factory=tuple)
    """Names of matching capabilities."""

    retrieval_duration_ms: int = 0
    """Time spent on retrieval."""


class StageData(BaseModel):
    """Container for stage-specific data collected during loop execution."""

    model_config = ConfigDict(frozen=True)

    # OBSERVE stage
    user_input: str = ""
    session_context: dict[str, Any] = Field(default_factory=dict)

    # CLASSIFY stage
    classified_intent: str = ""
    classification_confidence: float = 0.0

    # RETRIEVE stage
    retrieval: RetrievalResult | None = None

    # HYPOTHESIZE stage
    hypotheses: tuple[Hypothesis, ...] = Field(default_factory=tuple)
    chosen_hypothesis_id: str | None = None

    # SELECT stage
    evaluations: tuple[CapabilityEvaluation, ...] = Field(default_factory=tuple)
    selected_capability: str | None = None

    # ACT stage
    action_input: dict[str, Any] = Field(default_factory=dict)
    action_output: str = ""
    action_success: bool = False

    # VERIFY stage
    verification_passed: bool = False
    verification_notes: str = ""

    # RECORD stage
    incident_id: str | None = None
    incident_action_id: str | None = None


def explain_stages() -> str:
    """Generate explanation of all loop stages.

    Returns:
        Formatted explanation text.
    """
    lines = [
        "AGENTIC LOOP STAGES",
        "",
        "The agent loop moves through these stages as it processes a request:",
        "",
    ]

    for stage in LoopStage:
        if stage not in (LoopStage.COMPLETE, LoopStage.ERROR):
            lines.append(f"  {stage.value.upper()}")
            lines.append(f"    {stage.description}")
            lines.append("")

    lines.extend(
        [
            "STAGE FLOW:",
            "",
            "  Simple query (no action needed):",
            "    OBSERVE -> CLASSIFY -> RETRIEVE -> HYPOTHESIZE -> COMPLETE",
            "",
            "  Action required:",
            "    OBSERVE -> CLASSIFY -> RETRIEVE -> HYPOTHESIZE -> SELECT -> ACT -> VERIFY -> RECORD -> COMPLETE",
            "",
            "  Multi-step action:",
            "    ... -> ACT -> VERIFY -> SELECT -> ACT -> VERIFY -> RECORD -> COMPLETE",
            "",
            "The loop may iterate through SELECT/ACT/VERIFY multiple times",
            "until the task is complete or an error occurs.",
        ]
    )

    return "\n".join(lines)
