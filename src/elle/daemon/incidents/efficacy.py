"""Efficacy tracking models for machine-specific success learning.

Tracks success rates by domain, entity, and solution approach,
enabling dynamic outcome weighting based on what actually works
on THIS machine rather than static weights.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DomainEfficacy(BaseModel):
    """Success rate statistics per domain (net, disk, oom, etc.).

    Tracks how well ELLE's interventions work for each incident
    domain on this specific machine.
    """

    model_config = ConfigDict(frozen=True)

    domain: str = Field(description="Incident domain (net, disk, oom, etc.)")

    # Outcome counts
    total_incidents: int = Field(ge=0, default=0, description="Total finalized incidents")
    improved_count: int = Field(ge=0, default=0, description="Fully resolved incidents")
    partial_count: int = Field(ge=0, default=0, description="Partially resolved incidents")
    no_change_count: int = Field(ge=0, default=0, description="No improvement incidents")
    worse_count: int = Field(ge=0, default=0, description="Made things worse incidents")

    # Computed success rate (time-decayed)
    success_rate: float = Field(
        ge=0.0,
        le=1.0,
        default=0.5,
        description="Time-decayed success rate (0.0-1.0)",
    )

    # Metadata
    last_updated: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this record was last updated",
    )


class EntityEfficacy(BaseModel):
    """Success rate for a specific entity (service:nginx, disk:/, etc.).

    Tracks how well interventions work for specific system entities,
    enabling entity-specific learning.
    """

    model_config = ConfigDict(frozen=True)

    entity: str = Field(description="Entity identifier (e.g., service:nginx)")

    # Outcome counts
    total_incidents: int = Field(ge=0, default=0)
    improved_count: int = Field(ge=0, default=0)
    partial_count: int = Field(ge=0, default=0)
    no_change_count: int = Field(ge=0, default=0)
    worse_count: int = Field(ge=0, default=0)

    # Computed success rate
    success_rate: float = Field(ge=0.0, le=1.0, default=0.5)

    # Metadata
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class SolutionApproachEfficacy(BaseModel):
    """Success rate for a specific solution approach under specific preconditions.

    Groups incidents by domain + precondition pattern + key commands
    to learn which approaches work best for similar situations.
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    approach_signature: str = Field(
        description="Hash of domain + preconditions + commands for deduplication",
    )

    # Context
    domain: str = Field(description="Incident domain")
    precondition_summary: str = Field(
        default="",
        description="Summary of preconditions (e.g., 'disk_pressure > 0.9')",
    )
    key_commands: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Key commands used in this approach",
    )

    # Statistics
    total_uses: int = Field(ge=0, default=0)
    improved_count: int = Field(ge=0, default=0)
    partial_count: int = Field(ge=0, default=0)
    no_change_count: int = Field(ge=0, default=0)
    worse_count: int = Field(ge=0, default=0)

    # Computed metrics
    success_rate: float = Field(ge=0.0, le=1.0, default=0.5)
    avg_time_to_resolve_sec: int | None = Field(
        default=None,
        description="Average time to resolve using this approach",
    )

    # Metadata
    last_used: datetime = Field(default_factory=datetime.utcnow)


class EfficacyStats(BaseModel):
    """Aggregated efficacy statistics for reporting.

    Provides a summary view of how well ELLE is performing
    across different dimensions.
    """

    model_config = ConfigDict(frozen=True)

    # Overall
    total_finalized_incidents: int = Field(ge=0, default=0)
    overall_success_rate: float = Field(ge=0.0, le=1.0, default=0.5)

    # By domain
    domain_stats: tuple[DomainEfficacy, ...] = Field(default_factory=tuple)

    # Top entities (by incident count)
    top_entities: tuple[EntityEfficacy, ...] = Field(default_factory=tuple)

    # Top approaches (by success rate with min uses)
    top_approaches: tuple[SolutionApproachEfficacy, ...] = Field(default_factory=tuple)

    # Computed at
    computed_at: datetime = Field(default_factory=datetime.utcnow)


class EfficacyContext(BaseModel):
    """Efficacy context for dynamic weight computation.

    Passed to the retriever to compute dynamic outcome weights
    based on learned efficacy data.
    """

    model_config = ConfigDict(frozen=True)

    # Domain efficacy (if available)
    domain_success_rate: float | None = Field(
        default=None,
        description="Success rate for this domain",
    )
    domain_sample_size: int = Field(
        ge=0,
        default=0,
        description="Number of incidents in domain sample",
    )

    # Entity efficacy (if available)
    entity_success_rates: dict[str, float] = Field(
        default_factory=dict,
        description="Success rates for involved entities",
    )

    # Approach efficacy (if available)
    approach_success_rate: float | None = Field(
        default=None,
        description="Success rate for matched approach",
    )
    approach_sample_size: int = Field(
        ge=0,
        default=0,
        description="Number of uses of matched approach",
    )


# =============================================================================
# Constants
# =============================================================================

# Time decay for success rate computation
DECAY_HALF_LIFE_DAYS = 60

# Minimum samples before trusting learned rates
MIN_SAMPLES_FOR_CONFIDENCE = 5

# Prior success rate (Bayesian prior, regress toward this)
PRIOR_SUCCESS_RATE = 0.5

# Weights for combining efficacy sources in dynamic weight computation
EFFICACY_WEIGHT_BASE_OUTCOME = 0.4  # Static outcome quality
EFFICACY_WEIGHT_DOMAIN = 0.2  # Domain success rate
EFFICACY_WEIGHT_ENTITY = 0.2  # Entity success rate
EFFICACY_WEIGHT_APPROACH = 0.2  # Approach success rate
