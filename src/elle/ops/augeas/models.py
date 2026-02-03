"""Pydantic models for Augeas file editing operations.

Defines data structures for config file manipulation, edit requests,
results, and related types for the Augeas-based editing system.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Operation Types
# =============================================================================


class AugeasOp(BaseModel):
    """A single Augeas operation to perform on a config file.

    Supports set (create/modify), rm (delete), ins (insert), and mv (move)
    operations using Augeas path expressions.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["set", "rm", "ins", "mv"] = Field(
        description="Type of operation: set (create/modify), rm (delete), ins (insert), mv (move)"
    )
    path: str = Field(description="Augeas path expression, e.g., /files/etc/fstab/1/spec")
    value: str | None = Field(
        default=None,
        description="Value for set operations, destination for mv operations",
    )
    label: str | None = Field(
        default=None,
        description="Label name for ins (insert) operations",
    )
    before: bool = Field(
        default=False,
        description="For ins operations: insert before (True) or after (False) the path",
    )


class AugeasChange(BaseModel):
    """Record of a single change made to a config file.

    Captures what was changed for diff generation and rollback.
    """

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="Augeas path that was modified")
    old_value: str | None = Field(
        default=None,
        description="Previous value (None if newly created)",
    )
    new_value: str | None = Field(
        default=None,
        description="New value (None if deleted)",
    )
    operation: Literal["set", "rm", "ins", "mv"] = Field(
        default="set",
        description="Type of operation performed",
    )


# =============================================================================
# Request/Result Models
# =============================================================================


class AugeasEditRequest(BaseModel):
    """Request to edit a config file via Augeas.

    Encapsulates all information needed to perform a structured edit
    with validation, backup, and rollback support.
    """

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(description="Absolute path to the config file, e.g., /etc/fstab")
    lens: str | None = Field(
        default=None,
        description="Augeas lens to use. Auto-detected if None.",
    )
    operations: tuple[AugeasOp, ...] = Field(
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
        description="If True, skip validation after apply (use with caution)",
    )


class AugeasEditResult(BaseModel):
    """Result of an Augeas edit operation.

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
    validation_output: str = Field(
        default="",
        description="Output from config validation command",
    )
    validation_passed: bool = Field(
        default=True,
        description="Whether validation succeeded",
    )
    error: str | None = Field(
        default=None,
        description="Error message if operation failed",
    )
    rollback_applied: bool = Field(
        default=False,
        description="Whether rollback was needed and applied",
    )
    changes: tuple[AugeasChange, ...] = Field(
        default_factory=tuple,
        description="List of changes made",
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


class EditPreview(BaseModel):
    """Preview of proposed changes before applying.

    Generated during dry_run to show what would change.
    """

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(description="Path to the file that would be edited")
    original_content: str = Field(
        description="Current file content",
    )
    proposed_content: str = Field(
        description="Content after proposed changes",
    )
    diff: str = Field(
        description="Unified diff of changes",
    )
    diff_colored: str = Field(
        description="ANSI-colored diff",
    )
    changes: tuple[AugeasChange, ...] = Field(
        default_factory=tuple,
        description="List of proposed changes",
    )
    requires_privilege: bool = Field(
        default=False,
        description="Whether privileged access is required",
    )
    validation_command: str | None = Field(
        default=None,
        description="Command that will be used to validate",
    )


# =============================================================================
# Batch Operations
# =============================================================================


class BatchEditRequest(BaseModel):
    """Request to edit multiple config files atomically.

    All edits succeed or all are rolled back.
    """

    model_config = ConfigDict(frozen=True)

    edits: tuple[AugeasEditRequest, ...] = Field(
        description="Sequence of edit requests to perform",
    )
    description: str = Field(
        description="Human-readable summary of the batch operation",
    )
    incident_id: str | None = Field(
        default=None,
        description="Optional incident ID to link all edits to",
    )
    dry_run: bool = Field(
        default=False,
        description="If True, preview all changes without applying",
    )


class BatchEditResult(BaseModel):
    """Result of a batch edit operation.

    Contains results for each individual edit and overall status.
    """

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether all edits completed successfully")
    results: tuple[AugeasEditResult, ...] = Field(
        default_factory=tuple,
        description="Results for each individual edit",
    )
    rollback_applied: bool = Field(
        default=False,
        description="Whether batch rollback was needed",
    )
    error: str | None = Field(
        default=None,
        description="Error message if batch failed",
    )
    completed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the batch completed",
    )


# =============================================================================
# Backup Models
# =============================================================================


class BackupRecord(BaseModel):
    """Record of a file backup.

    Tracks backups for restoration and cleanup.
    """

    model_config = ConfigDict(frozen=True)

    backup_path: str = Field(description="Path where backup is stored")
    original_path: str = Field(description="Original file path")
    domain: str = Field(
        default="general",
        description="Domain category (fstab, sshd, netplan, etc.)",
    )
    sha256: str = Field(description="SHA256 hash of original file")
    size_bytes: int = Field(ge=0, description="File size in bytes")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the backup was created",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata (edit description, incident_id, etc.)",
    )


class BackupListResult(BaseModel):
    """Result of listing available backups."""

    model_config = ConfigDict(frozen=True)

    backups: tuple[BackupRecord, ...] = Field(
        default_factory=tuple,
        description="Available backups",
    )
    total_size_bytes: int = Field(
        ge=0,
        default=0,
        description="Total size of all backups",
    )
    by_domain: dict[str, int] = Field(
        default_factory=dict,
        description="Backup count by domain",
    )


# =============================================================================
# Status Models
# =============================================================================


class AugeasStatus(BaseModel):
    """Status of the Augeas editing system."""

    model_config = ConfigDict(frozen=True)

    augeas_available: bool = Field(
        description="Whether python-augeas is installed and working",
    )
    augeas_version: str | None = Field(
        default=None,
        description="Augeas library version",
    )
    lenses_available: tuple[str, ...] = Field(
        default_factory=tuple,
        description="List of available lens names",
    )
    backup_dir: str = Field(
        description="Backup storage directory",
    )
    backup_count: int = Field(
        ge=0,
        default=0,
        description="Number of stored backups",
    )
    backup_size_bytes: int = Field(
        ge=0,
        default=0,
        description="Total backup storage used",
    )
    yaml_handler_available: bool = Field(
        default=False,
        description="Whether ruamel.yaml is available for YAML files",
    )


# =============================================================================
# Error Types
# =============================================================================


class AugeasError(Exception):
    """Base exception for Augeas operations."""

    pass


class AugeasUnavailableError(AugeasError):
    """Raised when python-augeas is not installed."""

    pass


class AugeasLensNotFoundError(AugeasError):
    """Raised when no lens can be found for a file."""

    def __init__(self, file_path: str, message: str | None = None) -> None:
        self.file_path = file_path
        super().__init__(message or f"No lens found for file: {file_path}")


class AugeasParseError(AugeasError):
    """Raised when Augeas fails to parse a config file."""

    def __init__(self, file_path: str, details: str | None = None) -> None:
        self.file_path = file_path
        self.details = details
        msg = f"Failed to parse config file: {file_path}"
        if details:
            msg += f" - {details}"
        super().__init__(msg)


class AugeasValidationError(AugeasError):
    """Raised when config validation fails after edit."""

    def __init__(
        self,
        file_path: str,
        validator: str,
        output: str,
    ) -> None:
        self.file_path = file_path
        self.validator = validator
        self.output = output
        super().__init__(f"Validation failed for {file_path} using {validator}: {output}")


class AugeasBackupError(AugeasError):
    """Raised when backup or restore fails."""

    def __init__(self, operation: str, path: str, details: str | None = None) -> None:
        self.operation = operation
        self.path = path
        self.details = details
        msg = f"Backup {operation} failed for {path}"
        if details:
            msg += f": {details}"
        super().__init__(msg)


class AugeasPermissionError(AugeasError):
    """Raised when privileged access is required but not available."""

    def __init__(self, file_path: str, action: str | None = None) -> None:
        self.file_path = file_path
        self.action = action
        msg = f"Privileged access required for {file_path}"
        if action:
            msg += f" ({action})"
        super().__init__(msg)
