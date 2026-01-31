"""Pydantic models for crudini INI file editing operations.

Defines data structures for INI/CFG file manipulation using
the crudini command-line tool.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Operation Types
# =============================================================================


class CrudiniOperation(BaseModel):
    """A single crudini operation to perform on an INI file.

    Supports get, set, delete, and merge operations on INI sections and keys.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["get", "set", "del", "merge"] = Field(
        description="Type of operation: get (read), set (write), del (delete), merge (combine)"
    )
    section: str = Field(description="INI section name (e.g., 'DEFAULT', 'database', 'server')")
    parameter: str | None = Field(
        default=None,
        description="Parameter/key name within the section (None for section-level ops)",
    )
    value: str | None = Field(
        default=None,
        description="Value for set operations",
    )
    existing: Literal["set", "keep", "error"] = Field(
        default="set",
        description="How to handle existing values: set (overwrite), keep (preserve), error (fail)",
    )


class CrudiniChange(BaseModel):
    """Record of an INI file change made by an operation.

    Captures what was changed for diff generation and audit trail.
    """

    model_config = ConfigDict(frozen=True)

    section: str = Field(description="Section that was modified")
    parameter: str | None = Field(
        default=None,
        description="Parameter that was modified (None for section-level changes)",
    )
    old_value: str | None = Field(
        default=None,
        description="Previous value (None if newly created or section deleted)",
    )
    new_value: str | None = Field(
        default=None,
        description="New value (None if deleted)",
    )
    operation: Literal["get", "set", "del", "add", "merge"] = Field(
        default="set",
        description="Type of operation performed",
    )


# =============================================================================
# Request/Result Models
# =============================================================================


class CrudiniRequest(BaseModel):
    """Request to perform a crudini operation on an INI file.

    Encapsulates all information needed for a crudini operation.
    """

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(
        description="Path to the INI file to operate on",
    )
    operation: CrudiniOperation = Field(
        description="The operation to perform",
    )
    description: str = Field(
        default="",
        description="Human-readable summary of the operation",
    )


class CrudiniResult(BaseModel):
    """Result of a crudini operation.

    Contains the output, any errors, and metadata about the operation.
    """

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether the operation completed successfully")
    output: str = Field(
        default="",
        description="Output from crudini (value for get operations)",
    )
    error: str | None = Field(
        default=None,
        description="Error message if operation failed",
    )
    exit_code: int = Field(
        default=0,
        description="crudini process exit code",
    )
    completed_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the operation completed",
    )


# =============================================================================
# File Edit Models
# =============================================================================


class CrudiniEditRequest(BaseModel):
    """Request to edit an INI file using crudini.

    Supports multiple operations in a single request.
    """

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(
        description="Absolute path to the INI file to edit",
    )
    operations: tuple[CrudiniOperation, ...] = Field(
        default_factory=tuple,
        description="Sequence of operations to perform",
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


class CrudiniEditResult(BaseModel):
    """Result of a crudini file edit operation.

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
        description="Whether the output is valid INI format",
    )
    error: str | None = Field(
        default=None,
        description="Error message if operation failed",
    )
    rollback_applied: bool = Field(
        default=False,
        description="Whether rollback was needed and applied",
    )
    changes: tuple[CrudiniChange, ...] = Field(
        default_factory=tuple,
        description="List of changes made",
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
# Query Result Models
# =============================================================================


class INISection(BaseModel):
    """Represents an INI file section with its parameters."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Section name")
    parameters: dict[str, str] = Field(
        default_factory=dict,
        description="Key-value parameters in this section",
    )


class INIFileContent(BaseModel):
    """Complete parsed content of an INI file."""

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(description="Path to the INI file")
    sections: tuple[INISection, ...] = Field(
        default_factory=tuple,
        description="Sections in the INI file",
    )
    global_parameters: dict[str, str] = Field(
        default_factory=dict,
        description="Parameters not in any section (before first section)",
    )


# =============================================================================
# Preview Models
# =============================================================================


class CrudiniEditPreview(BaseModel):
    """Preview of proposed INI changes before applying.

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
    is_valid_ini: bool = Field(
        default=True,
        description="Whether the file is valid INI format",
    )


# =============================================================================
# Status Models
# =============================================================================


class CrudiniStatus(BaseModel):
    """Status of the crudini tool availability."""

    model_config = ConfigDict(frozen=True)

    available: bool = Field(
        description="Whether crudini is installed and working",
    )
    version: str | None = Field(
        default=None,
        description="crudini version string",
    )
    path: str | None = Field(
        default=None,
        description="Path to crudini executable",
    )


# =============================================================================
# Error Types
# =============================================================================


class CrudiniError(Exception):
    """Base exception for crudini operations."""

    pass


class CrudiniUnavailableError(CrudiniError):
    """Raised when crudini is not installed."""

    pass


class CrudiniSectionError(CrudiniError):
    """Raised when a section operation fails."""

    def __init__(self, section: str, details: str | None = None) -> None:
        self.section = section
        self.details = details
        msg = f"Section error for [{section}]"
        if details:
            msg += f" - {details}"
        super().__init__(msg)


class CrudiniParameterError(CrudiniError):
    """Raised when a parameter operation fails."""

    def __init__(
        self,
        section: str,
        parameter: str,
        details: str | None = None,
    ) -> None:
        self.section = section
        self.parameter = parameter
        self.details = details
        msg = f"Parameter error for [{section}].{parameter}"
        if details:
            msg += f" - {details}"
        super().__init__(msg)


class CrudiniParseError(CrudiniError):
    """Raised when an INI file cannot be parsed."""

    def __init__(self, file_path: str, details: str | None = None) -> None:
        self.file_path = file_path
        self.details = details
        msg = f"Failed to parse INI file: {file_path}"
        if details:
            msg += f" - {details}"
        super().__init__(msg)
