"""Narrative generation models for causal chain building.

Defines data structures for representing causal relationships
between events and incidents, and for synthesizing human-readable
narratives from those chains.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Causal Link Models
# =============================================================================


CauseType = Literal["event", "incident", "condition"]
EffectType = Literal["event", "incident", "symptom"]
Relationship = Literal["triggered", "contributed_to", "preceded", "correlated"]


class CausalLink(BaseModel):
    """Single cause-effect relationship between two items.

    Represents a directed edge in the causal graph connecting
    a cause (event, incident, or condition) to an effect.
    """

    model_config = ConfigDict(frozen=True)

    # Cause side
    cause_type: CauseType = Field(description="Type of cause")
    cause_id: str = Field(description="ID of the cause (event_id, incident_id, or condition key)")
    cause_summary: str = Field(description="Brief summary of the cause")
    cause_timestamp: datetime = Field(description="When the cause occurred")

    # Effect side
    effect_type: EffectType = Field(description="Type of effect")
    effect_id: str = Field(description="ID of the effect")
    effect_summary: str = Field(description="Brief summary of the effect")
    effect_timestamp: datetime = Field(description="When the effect occurred")

    # Relationship
    relationship: Relationship = Field(
        default="correlated",
        description="Type of causal relationship",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        default=0.5,
        description="Confidence in this causal link (0.0-1.0)",
    )

    # Evidence
    evidence: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Evidence supporting this link",
    )
    temporal_delta_sec: int = Field(
        ge=0,
        default=0,
        description="Time between cause and effect in seconds",
    )


class CausalChain(BaseModel):
    """Chain of causal links forming a narrative path.

    Represents a connected sequence of cause-effect relationships
    from a root cause to a final symptom.
    """

    model_config = ConfigDict(frozen=True)

    chain_id: str = Field(description="Unique ID for this chain")

    # Chain structure
    root_cause: CausalLink | None = Field(
        default=None,
        description="The originating cause (if identified)",
    )
    links: tuple[CausalLink, ...] = Field(
        default_factory=tuple,
        description="Ordered sequence of causal links",
    )
    final_symptom: str = Field(
        default="",
        description="The observable symptom at the end of the chain",
    )

    # Metadata
    overall_confidence: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Overall confidence in the chain",
    )
    search_iterations: int = Field(
        ge=0,
        default=0,
        description="Number of search iterations to build this chain",
    )

    # Time bounds
    earliest_timestamp: datetime | None = Field(
        default=None,
        description="Earliest timestamp in the chain",
    )
    latest_timestamp: datetime | None = Field(
        default=None,
        description="Latest timestamp in the chain",
    )

    def compute_overall_confidence(self) -> float:
        """Compute overall confidence as product of link confidences."""
        if not self.links:
            return 0.0
        confidence = 1.0
        for link in self.links:
            confidence *= link.confidence
        return confidence


class Narrative(BaseModel):
    """Human-readable narrative synthesized from a causal chain.

    Provides multiple views of the chain: summary, timeline,
    detailed explanation, and keywords for indexing.
    """

    model_config = ConfigDict(frozen=True)

    # Source chain
    chain: CausalChain = Field(description="The underlying causal chain")

    # Narrative content
    summary: str = Field(
        default="",
        description="2-3 sentence summary of what happened",
    )
    timeline: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Chronological bullet points",
    )
    explanation: str = Field(
        default="",
        description="Detailed explanation with evidence",
    )
    keywords: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Keywords for indexing and search",
    )

    # Metadata
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this narrative was generated",
    )
    model_used: str = Field(
        default="",
        description="LLM model used for generation",
    )


# =============================================================================
# Search State Models
# =============================================================================


class SearchHop(BaseModel):
    """Single hop in multi-hop search.

    Records what was searched for and what was found at each
    iteration of the search.
    """

    model_config = ConfigDict(frozen=True)

    iteration: int = Field(ge=0, description="Iteration number (0-indexed)")

    # Search parameters
    query: str = Field(description="Query used for this hop")
    search_type: Literal["incident", "event", "entity"] = Field(description="Type of search performed")
    time_window_start: datetime | None = Field(default=None)
    time_window_end: datetime | None = Field(default=None)

    # Results
    results_count: int = Field(ge=0, default=0)
    high_relevance_count: int = Field(ge=0, default=0)
    entities_discovered: tuple[str, ...] = Field(default_factory=tuple)
    keywords_extracted: tuple[str, ...] = Field(default_factory=tuple)


class MultiHopResult(BaseModel):
    """Complete result of a multi-hop search.

    Contains all discovered chains, the search path taken,
    and summary statistics.
    """

    model_config = ConfigDict(frozen=True)

    # Query info
    initial_query: str = Field(description="Original search query")
    initial_event_id: str | None = Field(
        default=None,
        description="Starting event ID if provided",
    )

    # Results
    chains: tuple[CausalChain, ...] = Field(
        default_factory=tuple,
        description="Discovered causal chains",
    )
    best_chain: CausalChain | None = Field(
        default=None,
        description="Highest confidence chain",
    )

    # Search path
    hops: tuple[SearchHop, ...] = Field(
        default_factory=tuple,
        description="Record of search iterations",
    )

    # Statistics
    total_iterations: int = Field(ge=0, default=0)
    total_events_examined: int = Field(ge=0, default=0)
    total_incidents_examined: int = Field(ge=0, default=0)

    # Timing
    search_started: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    search_completed: datetime | None = Field(default=None)
    duration_ms: int | None = Field(default=None)


# =============================================================================
# Configuration
# =============================================================================


class MultiHopConfig(BaseModel):
    """Configuration for multi-hop search."""

    model_config = ConfigDict(frozen=True)

    max_iterations: int = Field(
        ge=1,
        le=10,
        default=5,
        description="Maximum search iterations",
    )
    time_window_hours: int = Field(
        ge=1,
        le=168,
        default=24,
        description="Time window for event search (hours)",
    )
    min_confidence: float = Field(
        ge=0.0,
        le=1.0,
        default=0.5,
        description="Minimum confidence to include a link",
    )
    max_results_per_hop: int = Field(
        ge=1,
        le=100,
        default=20,
        description="Maximum results to consider per hop",
    )
    high_relevance_threshold: float = Field(
        ge=0.0,
        le=1.0,
        default=0.7,
        description="Threshold for high-relevance results",
    )
    expand_entities: bool = Field(
        default=True,
        description="Expand search to related entities",
    )
    extract_keywords: bool = Field(
        default=True,
        description="Extract keywords for query expansion",
    )
