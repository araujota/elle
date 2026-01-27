"""Pydantic data models for the agentic question answering system.

Defines frozen, immutable data structures for:
- Information needs analysis
- Capability call planning
- Evidence gathering
- Response synthesis
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
# Capability Planning
# =============================================================================


class CapabilityCall(BaseModel):
    """A planned capability invocation.

    Represents a single capability call that will be executed to gather
    information. Contains all arguments needed for execution.
    """

    model_config = ConfigDict(frozen=True)

    capability: str = Field(
        description="Capability name (e.g., 'service.status')",
    )
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments for the capability",
    )
    purpose: str = Field(
        description="Human-readable explanation of why this capability is called",
    )


class GatherPlan(BaseModel):
    """Plan for gathering information to answer a question.

    Contains the analyzed needs and the capability calls selected
    to satisfy those needs.
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
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the capability was executed",
    )


class GatherResult(BaseModel):
    """Result of executing a gather plan.

    Contains all evidence collected and an assessment of whether
    it's sufficient to answer the original question.
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


# =============================================================================
# Response Synthesis
# =============================================================================


class AgenticResponse(BaseModel):
    """Final response with provenance.

    The complete result of agentic question answering, including
    the synthesized answer, supporting evidence, and suggestions
    for follow-up actions.
    """

    model_config = ConfigDict(frozen=True)

    answer: str = Field(
        description="Natural language answer to the question",
    )
    evidence: tuple[GatheredEvidence, ...] = Field(
        description="Evidence used to generate the answer",
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
