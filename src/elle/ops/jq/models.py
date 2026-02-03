"""Pydantic models for jq JSON processing operations.

Defines data structures for JSON querying and transformation
using the jq command-line tool.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Operation Types
# =============================================================================


class JQOperation(BaseModel):
    """A single jq operation to perform on JSON data.

    Supports query (read-only), transform (modify), and validate operations.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["query", "transform", "validate"] = Field(
        description="Type of operation: query (read), transform (modify), validate (check)"
    )
    filter: str = Field(description="jq filter expression, e.g., '.key', '.[] | select(.active)', '.key = \"value\"'")
    raw_output: bool = Field(
        default=False,
        description="If True, output raw strings without JSON encoding (-r flag)",
    )
    slurp: bool = Field(
        default=False,
        description="If True, read entire input as array (-s flag)",
    )
    compact: bool = Field(
        default=False,
        description="If True, output compact JSON (-c flag)",
    )
    sort_keys: bool = Field(
        default=False,
        description="If True, sort object keys (-S flag)",
    )


class JQChange(BaseModel):
    """Record of a JSON change made by a transformation.

    Captures what was changed for diff generation and audit trail.
    """

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="JSON path that was modified (e.g., '.config.timeout')")
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


class JQRequest(BaseModel):
    """Request to process JSON data via jq.

    Encapsulates all information needed to perform a jq operation
    on JSON content or a JSON file.
    """

    model_config = ConfigDict(frozen=True)

    # Input source (one of these must be provided)
    file_path: str | None = Field(
        default=None,
        description="Path to JSON file to process",
    )
    json_input: str | None = Field(
        default=None,
        description="JSON string to process (alternative to file_path)",
    )

    # Operation
    filter: str = Field(
        description="jq filter expression to apply",
    )

    # Options
    raw_output: bool = Field(
        default=False,
        description="Output raw strings without JSON encoding",
    )
    slurp: bool = Field(
        default=False,
        description="Read entire input as array",
    )
    compact: bool = Field(
        default=False,
        description="Output compact JSON",
    )
    sort_keys: bool = Field(
        default=False,
        description="Sort object keys in output",
    )
    null_input: bool = Field(
        default=False,
        description="Use null as input (for generating JSON)",
    )

    # Metadata
    description: str = Field(
        default="",
        description="Human-readable summary of the operation",
    )


class JQResult(BaseModel):
    """Result of a jq operation.

    Contains the output, any errors, and metadata about the operation.
    """

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether the operation completed successfully")
    output: str = Field(
        default="",
        description="jq output (JSON or raw string depending on options)",
    )
    parsed_output: Any = Field(
        default=None,
        description="Parsed JSON output (None if raw_output was used or parse failed)",
    )
    error: str | None = Field(
        default=None,
        description="Error message if operation failed",
    )
    exit_code: int = Field(
        default=0,
        description="jq process exit code",
    )
    filter_used: str = Field(
        default="",
        description="The jq filter that was applied",
    )
    completed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the operation completed",
    )


# =============================================================================
# File Edit Models
# =============================================================================


class JQEditRequest(BaseModel):
    """Request to edit a JSON file using jq.

    Similar to AugeasEditRequest but for JSON files using jq transformations.
    """

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(
        description="Absolute path to the JSON file to edit",
    )
    filter: str = Field(
        description="jq filter to apply (must produce valid JSON output)",
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
    sort_keys: bool = Field(
        default=False,
        description="Sort object keys in output",
    )
    indent: int = Field(
        default=2,
        ge=0,
        le=8,
        description="Indentation level for output (0 for compact)",
    )


class JQEditResult(BaseModel):
    """Result of a jq file edit operation.

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
        description="Whether the output is valid JSON",
    )
    error: str | None = Field(
        default=None,
        description="Error message if operation failed",
    )
    rollback_applied: bool = Field(
        default=False,
        description="Whether rollback was needed and applied",
    )
    changes: tuple[JQChange, ...] = Field(
        default_factory=tuple,
        description="List of changes detected",
    )
    requires_privilege: bool = Field(
        default=False,
        description="Whether privileged access was required",
    )
    completed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the operation completed",
    )


# =============================================================================
# Preview Models
# =============================================================================


class JQEditPreview(BaseModel):
    """Preview of proposed JSON changes before applying.

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
    is_valid_json: bool = Field(
        default=True,
        description="Whether the proposed content is valid JSON",
    )


# =============================================================================
# Status Models
# =============================================================================


class JQStatus(BaseModel):
    """Status of the jq tool availability."""

    model_config = ConfigDict(frozen=True)

    available: bool = Field(
        description="Whether jq is installed and working",
    )
    version: str | None = Field(
        default=None,
        description="jq version string",
    )
    path: str | None = Field(
        default=None,
        description="Path to jq executable",
    )


# =============================================================================
# Error Types
# =============================================================================


class JQError(Exception):
    """Base exception for jq operations."""

    pass


class JQUnavailableError(JQError):
    """Raised when jq is not installed."""

    pass


class JQFilterError(JQError):
    """Raised when a jq filter is invalid."""

    def __init__(self, filter_expr: str, details: str | None = None) -> None:
        self.filter_expr = filter_expr
        self.details = details
        msg = f"Invalid jq filter: {filter_expr}"
        if details:
            msg += f" - {details}"
        super().__init__(msg)


class JQParseError(JQError):
    """Raised when input JSON is invalid."""

    def __init__(self, source: str, details: str | None = None) -> None:
        self.source = source
        self.details = details
        msg = f"Failed to parse JSON from: {source}"
        if details:
            msg += f" - {details}"
        super().__init__(msg)
