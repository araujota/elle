"""Pydantic models for yq YAML processing operations.

Defines data structures for YAML querying and transformation
using the yq command-line tool (jq wrapper for YAML).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Operation Types
# =============================================================================


class YQOperation(BaseModel):
    """A single yq operation to perform on YAML data.

    Supports query (read-only), transform (modify), and validate operations.
    yq uses jq filter syntax but operates on YAML files.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["query", "transform", "validate"] = Field(
        description="Type of operation: query (read), transform (modify), validate (check)"
    )
    filter: str = Field(
        description="yq/jq filter expression, e.g., '.key', '.services[].image'"
    )
    in_place: bool = Field(
        default=False,
        description="If True, modify file in place (-i flag)",
    )
    yaml_output: bool = Field(
        default=True,
        description="If True, output YAML (default). If False, output JSON (-o json)",
    )
    preserve_comments: bool = Field(
        default=True,
        description="If True, preserve YAML comments where possible",
    )


class YQChange(BaseModel):
    """Record of a YAML change made by a transformation.

    Captures what was changed for diff generation and audit trail.
    """

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="YAML path that was modified (e.g., '.services.web.image')")
    old_value: Any = Field(
        default=None,
        description="Previous value (None if newly created)",
    )
    new_value: Any = Field(
        default=None,
        description="New value (None if deleted)",
    )
    operation: str = Field(
        default="transform",
        description="Description of the transformation applied",
    )


# =============================================================================
# Request/Result Models
# =============================================================================


class YQRequest(BaseModel):
    """Request to process YAML data via yq.

    Encapsulates all information needed to perform a yq operation
    on YAML content or a YAML file.
    """

    model_config = ConfigDict(frozen=True)

    # Input source (one of these must be provided)
    file_path: str | None = Field(
        default=None,
        description="Path to YAML file to process",
    )
    yaml_input: str | None = Field(
        default=None,
        description="YAML string to process (alternative to file_path)",
    )

    # Operation
    filter: str = Field(
        description="yq/jq filter expression to apply",
    )

    # Options
    yaml_output: bool = Field(
        default=True,
        description="Output YAML (default) or JSON",
    )
    raw_output: bool = Field(
        default=False,
        description="Output raw strings without YAML/JSON encoding",
    )

    # Metadata
    description: str = Field(
        default="",
        description="Human-readable summary of the operation",
    )


class YQResult(BaseModel):
    """Result of a yq operation.

    Contains the output, any errors, and metadata about the operation.
    """

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether the operation completed successfully")
    output: str = Field(
        default="",
        description="yq output (YAML, JSON, or raw string depending on options)",
    )
    parsed_output: Any = Field(
        default=None,
        description="Parsed output (None if parse failed)",
    )
    error: str | None = Field(
        default=None,
        description="Error message if operation failed",
    )
    exit_code: int = Field(
        default=0,
        description="yq process exit code",
    )
    filter_used: str = Field(
        default="",
        description="The yq filter that was applied",
    )
    completed_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the operation completed",
    )


# =============================================================================
# File Edit Models
# =============================================================================


class YQEditRequest(BaseModel):
    """Request to edit a YAML file using yq.

    Similar to AugeasEditRequest but for YAML files using yq transformations.
    """

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(
        description="Absolute path to the YAML file to edit",
    )
    filter: str = Field(
        description="yq filter to apply (must produce valid YAML output)",
    )
    description: str = Field(
        description="Human-readable summary of the changes",
    )
    incident_id: str | None = Field(
        default=None,
        description="Optional incident ID to link this edit to",
    )
    dry_run: bool = Field(
        default=False,
        description="If True, preview changes without applying",
    )
    skip_backup: bool = Field(
        default=False,
        description="If True, skip backup creation (use with caution)",
    )
    skip_validation: bool = Field(
        default=False,
        description="If True, skip validation after apply",
    )
    preserve_comments: bool = Field(
        default=True,
        description="Preserve YAML comments where possible",
    )


class YQEditResult(BaseModel):
    """Result of a yq file edit operation.

    Contains the outcome, diff, backup info, and validation results.
    """

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether the edit completed successfully")
    file_path: str = Field(description="Path to the edited file")
    diff: str = Field(
        default="",
        description="Unified diff showing changes",
    )
    diff_colored: str = Field(
        default="",
        description="ANSI-colored diff for terminal display",
    )
    backup_path: str | None = Field(
        default=None,
        description="Path to backup file (if created)",
    )
    validation_passed: bool = Field(
        default=True,
        description="Whether the output is valid YAML",
    )
    error: str | None = Field(
        default=None,
        description="Error message if operation failed",
    )
    rollback_applied: bool = Field(
        default=False,
        description="Whether rollback was needed and applied",
    )
    changes: tuple[YQChange, ...] = Field(
        default_factory=tuple,
        description="List of changes detected",
    )
    requires_privilege: bool = Field(
        default=False,
        description="Whether privileged access was required",
    )
    completed_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the operation completed",
    )


# =============================================================================
# Preview Models
# =============================================================================


class YQEditPreview(BaseModel):
    """Preview of proposed YAML changes before applying.

    Generated during dry_run to show what would change.
    """

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(description="Path to the file that would be edited")
    original_content: str = Field(description="Current file content")
    proposed_content: str = Field(description="Content after proposed changes")
    diff: str = Field(description="Unified diff of changes")
    diff_colored: str = Field(description="ANSI-colored diff")
    requires_privilege: bool = Field(
        default=False,
        description="Whether privileged access is required",
    )
    is_valid_yaml: bool = Field(
        default=True,
        description="Whether the proposed content is valid YAML",
    )


# =============================================================================
# Status Models
# =============================================================================


class YQStatus(BaseModel):
    """Status of the yq tool availability."""

    model_config = ConfigDict(frozen=True)

    available: bool = Field(
        description="Whether yq is installed and working",
    )
    version: str | None = Field(
        default=None,
        description="yq version string",
    )
    path: str | None = Field(
        default=None,
        description="Path to yq executable",
    )
    variant: str | None = Field(
        default=None,
        description="yq variant: 'kislyuk' (Python, jq-based) or 'mikefarah' (Go)",
    )


# =============================================================================
# Error Types
# =============================================================================


class YQError(Exception):
    """Base exception for yq operations."""

    pass


class YQUnavailableError(YQError):
    """Raised when yq is not installed."""

    pass


class YQFilterError(YQError):
    """Raised when a yq filter is invalid."""

    def __init__(self, filter_expr: str, details: str | None = None) -> None:
        self.filter_expr = filter_expr
        self.details = details
        msg = f"Invalid yq filter: {filter_expr}"
        if details:
            msg += f" - {details}"
        super().__init__(msg)


class YQParseError(YQError):
    """Raised when input YAML is invalid."""

    def __init__(self, source: str, details: str | None = None) -> None:
        self.source = source
        self.details = details
        msg = f"Failed to parse YAML from: {source}"
        if details:
            msg += f" - {details}"
        super().__init__(msg)
