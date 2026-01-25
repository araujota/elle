"""Pydantic models for the Capabilities system.

Defines data structures for capability specifications, results,
evidence, and lifecycle states.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Type Aliases
# =============================================================================

# Risk levels for capability operations
RiskLevel = Literal["none", "low", "medium", "high", "critical"]

# Domain categories for capabilities
CapabilityDomain = Literal[
    "service",       # Systemd service management
    "file",          # File operations
    "config",        # Configuration editing
    "network",       # Network configuration
    "package",       # Package management
    "docker",        # Container operations
    "auth",          # Authentication/authorization
    "notification",  # Desktop/push notifications
    "incident",      # Incident management
    "gui",           # GUI automation via AT-SPI
]

# Types of side effects capabilities can have
SideEffectKind = Literal[
    "file_write",           # Writes to a file
    "file_delete",          # Deletes a file
    "service_change",       # Changes service state
    "network_change",       # Modifies network config
    "privilege_escalation", # Requires elevated privileges
    "package_change",       # Installs/removes packages
    "container_change",     # Container state change
    "notification_sent",    # Sends a notification
    "incident_created",     # Creates/updates an incident
    "ui_interaction",       # Interacts with UI via AT-SPI
    "window_focus",         # Changes window focus
]

# Trust level for capabilities
TrustLevel = Literal[
    "core",         # Built-in, fully trusted
    "official",     # Official extensions
    "third_party",  # Third-party extensions
]


# =============================================================================
# Side Effect
# =============================================================================


class SideEffect(BaseModel):
    """Declared side effect of a capability.

    Side effects describe what changes a capability will make to the system.
    They are declared upfront for policy evaluation and audit purposes.
    """

    model_config = ConfigDict(frozen=True)

    kind: SideEffectKind = Field(description="Type of side effect")
    target: str = Field(
        description="Target of the effect (e.g., '/etc/ssh/sshd_config', 'nginx.service')"
    )
    reversible: bool = Field(
        default=True,
        description="Whether this effect can be undone via rollback",
    )
    description: str = Field(
        default="",
        description="Human-readable description of the effect",
    )


# =============================================================================
# Capability Specification
# =============================================================================


class CapabilitySpec(BaseModel):
    """Metadata describing a capability's contract.

    The spec defines everything needed to understand what a capability
    does before executing it: name, domain, risk level, side effects,
    and schema information.
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    name: str = Field(
        description="Unique capability name (e.g., 'service.restart')"
    )
    summary: str = Field(
        description="One-line description of what this capability does"
    )

    # Classification
    domain: CapabilityDomain = Field(
        description="Domain this capability belongs to"
    )
    risk: RiskLevel = Field(
        description="Risk level of this capability"
    )

    # Effects
    side_effects: tuple[SideEffect, ...] = Field(
        default_factory=tuple,
        description="Declared side effects of this capability",
    )

    # Requirements
    requires_privilege: bool = Field(
        default=False,
        description="Whether elevated privileges are required",
    )
    idempotent: bool = Field(
        default=False,
        description="Whether repeated execution produces same result",
    )

    # Trust
    trust_level: TrustLevel = Field(
        default="core",
        description="Trust level of this capability",
    )
    version: str = Field(
        default="1.0.0",
        description="Capability version",
    )

    # Schema references (stored as strings for serialization)
    input_schema_name: str | None = Field(
        default=None,
        description="Name of the Pydantic model for inputs",
    )
    output_schema_name: str | None = Field(
        default=None,
        description="Name of the Pydantic model for outputs",
    )


# =============================================================================
# Capability Evidence
# =============================================================================


class CapabilityEvidence(BaseModel):
    """Evidence/citations for audit trail.

    Captures exactly what happened during capability execution
    for incident recording and accountability.
    """

    model_config = ConfigDict(frozen=True)

    # What was executed
    commands_executed: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Shell commands that were run",
    )
    files_modified: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Files that were modified",
    )

    # Verification
    verification_passed: bool = Field(
        default=False,
        description="Whether post-execution verification passed",
    )
    verification_details: str = Field(
        default="",
        description="Details from verification check",
    )

    # Provenance
    man_vault_citations: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Man page references (e.g., 'systemctl(1)')",
    )
    rationale: str = Field(
        default="",
        description="Explanation of why this action was taken",
    )


# =============================================================================
# Capability Result
# =============================================================================


class CapabilityResult(BaseModel):
    """Result of executing a capability.

    Contains everything about what happened during execution:
    success/failure, output, errors, applied side effects, and evidence.
    """

    model_config = ConfigDict(frozen=True)

    # Outcome
    success: bool = Field(description="Whether the capability succeeded")
    output: Any = Field(
        default=None,
        description="Schema-validated output (type depends on capability)",
    )
    error: str | None = Field(
        default=None,
        description="Error message if failed",
    )
    warnings: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Warning messages",
    )

    # Side effects
    side_effects_applied: tuple[SideEffect, ...] = Field(
        default_factory=tuple,
        description="Side effects that were actually applied",
    )

    # Timing
    execution_time_ms: int = Field(
        default=0,
        description="Time taken to execute in milliseconds",
    )
    executed_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the capability was executed",
    )

    # Evidence
    evidence: CapabilityEvidence = Field(
        default_factory=CapabilityEvidence,
        description="Evidence for incident recording",
    )


# =============================================================================
# Dry Run Result
# =============================================================================


class DryRunResult(BaseModel):
    """Result of dry_run() - preview without execution.

    Shows what would happen if the capability were executed,
    without actually making any changes.
    """

    model_config = ConfigDict(frozen=True)

    # What would happen
    would_execute: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Commands that would run",
    )
    would_modify: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Files/services that would change",
    )

    # Risk assessment
    estimated_risk: RiskLevel = Field(
        default="low",
        description="Estimated risk level",
    )
    requires_confirmation: bool = Field(
        default=False,
        description="Whether user confirmation is needed",
    )

    # Preview
    preview_text: str = Field(
        default="",
        description="Human-readable summary of what would happen",
    )
    diff: str | None = Field(
        default=None,
        description="Unified diff if applicable",
    )

    # Validation
    is_valid: bool = Field(
        default=True,
        description="Whether the operation can proceed",
    )
    validation_errors: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Errors preventing execution",
    )


# =============================================================================
# Verification Result
# =============================================================================


class VerificationResult(BaseModel):
    """Result of verify() - post-execution check.

    Confirms whether the operation achieved its intended goal
    by checking actual system state against expected state.
    """

    model_config = ConfigDict(frozen=True)

    # Outcome
    passed: bool = Field(description="Whether verification passed")

    # Checks
    checks_performed: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Commands/checks that were run",
    )

    # State comparison
    actual_state: dict[str, Any] = Field(
        default_factory=dict,
        description="Actual system state after execution",
    )
    expected_state: dict[str, Any] = Field(
        default_factory=dict,
        description="Expected system state",
    )
    discrepancies: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Differences between actual and expected",
    )


# =============================================================================
# Rollback Result
# =============================================================================


class RollbackResult(BaseModel):
    """Result of rollback() - undo operation.

    Describes what happened when attempting to reverse
    a capability's effects.
    """

    model_config = ConfigDict(frozen=True)

    # Outcome
    success: bool = Field(description="Whether rollback succeeded")
    error: str | None = Field(
        default=None,
        description="Error message if failed",
    )

    # What was rolled back
    rolled_back: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Items that were successfully rolled back",
    )
    failed_to_rollback: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Items that failed to roll back",
    )

    # Evidence
    commands_executed: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Commands run during rollback",
    )
