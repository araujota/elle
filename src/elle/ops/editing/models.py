"""Pydantic models for the 6-Tier Deterministic Editing Stack.

Defines data structures for tier selection, edit operations,
and results that flow through the editing stack.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Tier Definitions
# =============================================================================

TierNumber = Literal[0, 1, 2, 3, 4, 5]

TIER_NAMES: dict[int, str] = {
    0: "Native Override",
    1: "Augeas",
    2: "Structured Data",
    3: "Format Editors",
    4: "Document-aware",
    5: "Text Patching",
}


class TierInfo(BaseModel):
    """Information about an editing tier."""

    model_config = ConfigDict(frozen=True)

    number: TierNumber = Field(description="Tier number (0-5, lower is safer)")
    name: str = Field(description="Human-readable tier name")
    tools: tuple[str, ...] = Field(description="Tools used by this tier")
    use_case: str = Field(description="When to use this tier")
    available: bool = Field(
        default=True,
        description="Whether this tier is currently available",
    )
    missing_dependencies: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Dependencies needed to enable this tier",
    )


# =============================================================================
# Edit Operation Types
# =============================================================================


class EditOperation(BaseModel):
    """Specification of an edit operation.

    This is the universal edit operation model that all tiers consume.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["set", "rm", "add", "replace", "append", "insert"] = Field(description="Type of edit operation")
    path: str | None = Field(
        default=None,
        description="Path expression (JSONPath, XPath, Augeas path, INI section.key, etc.)",
    )
    value: str | None = Field(
        default=None,
        description="Value to set/add/replace",
    )
    anchor_pattern: str | None = Field(
        default=None,
        description="For Tier 5: regex pattern to anchor the edit",
    )
    anchor_context_lines: int = Field(
        default=3,
        description="For Tier 5: lines of context for anchor verification",
    )
    section: str | None = Field(
        default=None,
        description="For INI: section name",
    )
    key: str | None = Field(
        default=None,
        description="For INI: parameter key",
    )
    position: Literal["before", "after", "into"] | None = Field(
        default=None,
        description="For insert operations: where to insert relative to path",
    )


# =============================================================================
# Tier Selection
# =============================================================================


class TierApplicability(BaseModel):
    """Result of checking if a tier can handle an edit."""

    model_config = ConfigDict(frozen=True)

    tier: TierNumber = Field(description="Tier number")
    applicable: bool = Field(description="Whether this tier can handle the edit")
    reason: str = Field(description="Why this tier is or isn't applicable")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence that this tier will succeed (0-1)",
    )
    tool_available: bool = Field(
        default=True,
        description="Whether the required tool is installed",
    )
    missing_tool: str | None = Field(
        default=None,
        description="Name of missing tool if not available",
    )


class TierEscalation(BaseModel):
    """Record of why a tier was skipped (escalated to next tier)."""

    model_config = ConfigDict(frozen=True)

    tier: TierNumber = Field(description="Tier that was skipped")
    tier_name: str = Field(description="Name of the skipped tier")
    reason: str = Field(description="Why this tier was skipped")
    tool_unavailable: bool = Field(
        default=False,
        description="Whether escalation was due to missing tool",
    )


class TierSelection(BaseModel):
    """Result of deterministic tier selection.

    Contains the selected tier and full audit trail of why
    higher tiers were not selected.
    """

    model_config = ConfigDict(frozen=True)

    tier: TierNumber = Field(description="Selected tier number")
    tier_name: str = Field(description="Selected tier name")
    reason: str = Field(description="Why this tier was selected")
    escalation_trail: tuple[TierEscalation, ...] = Field(
        default_factory=tuple,
        description="Trail of skipped tiers with reasons",
    )
    tool_used: str | None = Field(
        default=None,
        description="Primary tool that will be used",
    )


# =============================================================================
# Edit Results
# =============================================================================


class ValidationResult(BaseModel):
    """Result of validating content after edit."""

    model_config = ConfigDict(frozen=True)

    passed: bool = Field(description="Whether validation passed")
    method: str = Field(description="Validation method used")
    output: str = Field(
        default="",
        description="Validation command output",
    )
    errors: tuple[str, ...] = Field(
        default_factory=tuple,
        description="List of validation errors",
    )


class TierEditResult(BaseModel):
    """Result of an edit operation from any tier.

    This is the unified result model returned by all tier implementations.
    """

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether the edit completed successfully")
    file_path: str = Field(description="Path to the edited file")

    # Tier info
    tier_used: TierNumber = Field(description="Tier that performed the edit")
    tier_name: str = Field(description="Name of the tier used")
    tier_selection_reason: str = Field(description="Why this tier was selected")
    escalation_trail: tuple[TierEscalation, ...] = Field(
        default_factory=tuple,
        description="Trail of skipped tiers with reasons",
    )
    tool_used: str | None = Field(
        default=None,
        description="Tool that performed the edit",
    )

    # Diff and changes
    diff: str = Field(
        default="",
        description="Unified diff showing changes",
    )
    diff_colored: str = Field(
        default="",
        description="ANSI-colored diff for terminal display",
    )

    # Backup and recovery
    backup_path: str | None = Field(
        default=None,
        description="Path to backup file (if created)",
    )
    rollback_applied: bool = Field(
        default=False,
        description="Whether rollback was needed and applied",
    )

    # Validation
    validation: ValidationResult | None = Field(
        default=None,
        description="Validation result (if performed)",
    )

    # Error handling
    error: str | None = Field(
        default=None,
        description="Error message if operation failed",
    )

    # Privilege
    requires_privilege: bool = Field(
        default=False,
        description="Whether privileged access was required",
    )

    # Timing
    completed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the operation completed",
    )


# =============================================================================
# Edit Request
# =============================================================================


class FileEditRequest(BaseModel):
    """Request to edit a file using the tier stack.

    This is the primary input to the editing stack.
    """

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(
        description="Absolute path to the file to edit",
    )
    operation: EditOperation = Field(
        description="The edit operation to perform",
    )
    description: str = Field(
        description="Human-readable summary of the changes",
    )

    # Tier preferences
    preferred_tier: TierNumber | None = Field(
        default=None,
        description="Preferred tier (will still validate applicability)",
    )
    max_tier: TierNumber = Field(
        default=5,
        description="Maximum tier to use (won't go beyond this)",
    )
    tier5_justification: str | None = Field(
        default=None,
        description="Required justification if Tier 5 is used",
    )

    # Drop-in options (Tier 0)
    dropin_name: str | None = Field(
        default=None,
        description="For Tier 0: name for the drop-in file",
    )
    dropin_priority: int = Field(
        default=99,
        ge=0,
        le=99,
        description="For Tier 0: priority number for drop-in ordering",
    )

    # Options
    dry_run: bool = Field(
        default=False,
        description="If True, preview changes without applying",
    )
    skip_backup: bool = Field(
        default=False,
        description="If True, skip backup creation",
    )
    skip_validation: bool = Field(
        default=False,
        description="If True, skip validation after apply",
    )

    # Incident integration
    incident_id: str | None = Field(
        default=None,
        description="Optional incident ID to link this edit to",
    )


# =============================================================================
# Preview Model
# =============================================================================


class FileEditPreview(BaseModel):
    """Preview of proposed file changes."""

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(description="Path to the file that would be edited")
    tier_selected: TierNumber = Field(description="Tier that would be used")
    tier_name: str = Field(description="Name of the selected tier")
    escalation_trail: tuple[TierEscalation, ...] = Field(
        default_factory=tuple,
        description="Trail of skipped tiers",
    )
    original_content: str = Field(description="Current file content")
    proposed_content: str = Field(description="Content after proposed changes")
    diff: str = Field(description="Unified diff of changes")
    diff_colored: str = Field(description="ANSI-colored diff")
    is_valid: bool = Field(
        default=True,
        description="Whether the proposed content is valid",
    )
    requires_privilege: bool = Field(
        default=False,
        description="Whether privileged access is required",
    )
    tool_to_use: str | None = Field(
        default=None,
        description="Tool that will be used",
    )


# =============================================================================
# Stack Status
# =============================================================================


class EditingStackStatus(BaseModel):
    """Status of the entire editing stack."""

    model_config = ConfigDict(frozen=True)

    tiers: tuple[TierInfo, ...] = Field(
        description="Information about each tier",
    )
    available_tiers: tuple[TierNumber, ...] = Field(
        description="Tiers that are currently available",
    )
    unavailable_tiers: tuple[TierNumber, ...] = Field(
        description="Tiers that are missing dependencies",
    )
    missing_dependencies: dict[int, tuple[str, ...]] = Field(
        default_factory=dict,
        description="Missing dependencies by tier number",
    )


# =============================================================================
# Error Types
# =============================================================================


class EditingStackError(Exception):
    """Base exception for editing stack operations."""

    pass


class TierUnavailableError(EditingStackError):
    """Raised when a required tier is not available."""

    def __init__(self, tier: int, missing_tool: str) -> None:
        self.tier = tier
        self.missing_tool = missing_tool
        super().__init__(f"Tier {tier} unavailable: {missing_tool} not installed")


class NoApplicableTierError(EditingStackError):
    """Raised when no tier can handle the edit."""

    def __init__(self, file_path: str, escalation_trail: list[TierEscalation]) -> None:
        self.file_path = file_path
        self.escalation_trail = escalation_trail
        reasons = [f"Tier {e.tier}: {e.reason}" for e in escalation_trail]
        super().__init__(f"No tier can handle edit for {file_path}: {'; '.join(reasons)}")


class Tier5RequiresJustificationError(EditingStackError):
    """Raised when Tier 5 is selected but no justification was provided."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        super().__init__(
            f"Tier 5 (Text Patching) requires justification for {file_path}. Set tier5_justification in the request."
        )
