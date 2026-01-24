"""Pydantic models for general file operations.

Defines data structures for file move, copy, delete, rename,
and organization operations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Operation Types
# =============================================================================


class FileOp(BaseModel):
    """A single file operation.

    Supports move, copy, delete, rename, mkdir, read, and write operations.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["move", "copy", "delete", "rename", "mkdir", "read", "write"] = Field(
        description="Type of operation"
    )
    source: str = Field(
        description="Source file/directory path"
    )
    dest: str | None = Field(
        default=None,
        description="Destination path (for move, copy, rename)",
    )
    content: str | None = Field(
        default=None,
        description="Content for write operations",
    )
    encoding: str = Field(
        default="utf-8",
        description="File encoding for read/write operations",
    )
    recursive: bool = Field(
        default=False,
        description="Apply operation recursively to directories",
    )
    overwrite: bool = Field(
        default=False,
        description="Overwrite existing files at destination",
    )


class FileRequest(BaseModel):
    """Request for general file operations.

    Encapsulates a sequence of file operations to perform.
    """

    model_config = ConfigDict(frozen=True)

    operations: tuple[FileOp, ...] = Field(
        description="Sequence of operations to perform"
    )
    description: str = Field(
        description="Human-readable summary of the operations",
    )
    dry_run: bool = Field(
        default=False,
        description="If True, preview changes without applying",
    )
    atomic: bool = Field(
        default=True,
        description="If True, rollback all on any failure",
    )


class FileOpResult(BaseModel):
    """Result of a single file operation."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether the operation succeeded")
    operation: FileOp = Field(description="The operation that was executed")
    source_existed: bool = Field(
        default=True,
        description="Whether source existed before operation",
    )
    dest_existed: bool = Field(
        default=False,
        description="Whether destination existed before operation",
    )
    bytes_affected: int = Field(
        ge=0,
        default=0,
        description="Bytes moved/copied/deleted",
    )
    content: str | None = Field(
        default=None,
        description="File content (for read operations)",
    )
    error: str | None = Field(
        default=None,
        description="Error message if operation failed",
    )


class ReadResult(BaseModel):
    """Result of a file read operation."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether the read succeeded")
    path: str = Field(description="Path to the file that was read")
    content: str | None = Field(
        default=None,
        description="File content if successful",
    )
    size_bytes: int = Field(
        ge=0,
        default=0,
        description="Size of the file in bytes",
    )
    encoding: str = Field(
        default="utf-8",
        description="Encoding used to read the file",
    )
    error: str | None = Field(
        default=None,
        description="Error message if read failed",
    )


class WriteResult(BaseModel):
    """Result of a file write operation."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether the write succeeded")
    path: str = Field(description="Path to the file that was written")
    bytes_written: int = Field(
        ge=0,
        default=0,
        description="Number of bytes written",
    )
    created: bool = Field(
        default=False,
        description="True if file was created (vs overwritten)",
    )
    error: str | None = Field(
        default=None,
        description="Error message if write failed",
    )


class FileResult(BaseModel):
    """Result of a file operation request."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether all operations succeeded")
    results: tuple[FileOpResult, ...] = Field(
        default_factory=tuple,
        description="Results for each operation",
    )
    total_bytes: int = Field(
        ge=0,
        default=0,
        description="Total bytes affected",
    )
    total_files: int = Field(
        ge=0,
        default=0,
        description="Total files affected",
    )
    error: str | None = Field(
        default=None,
        description="Error message if request failed",
    )
    rollback_applied: bool = Field(
        default=False,
        description="Whether rollback was needed and applied",
    )
    completed_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the operation completed",
    )


# =============================================================================
# Preview Models
# =============================================================================


class FileOpPreview(BaseModel):
    """Preview of a single file operation."""

    model_config = ConfigDict(frozen=True)

    operation: FileOp = Field(description="The proposed operation")
    source_exists: bool = Field(description="Whether source currently exists")
    dest_exists: bool = Field(
        default=False,
        description="Whether destination currently exists",
    )
    source_size: int = Field(
        ge=0,
        default=0,
        description="Source file size in bytes",
    )
    source_type: Literal["file", "directory", "symlink", "unknown"] = Field(
        default="unknown",
        description="Type of source",
    )
    would_overwrite: bool = Field(
        default=False,
        description="Whether operation would overwrite existing file",
    )
    warning: str | None = Field(
        default=None,
        description="Warning message (e.g., large file, many files)",
    )


class FilePreview(BaseModel):
    """Preview of all proposed file operations."""

    model_config = ConfigDict(frozen=True)

    previews: tuple[FileOpPreview, ...] = Field(
        default_factory=tuple,
        description="Preview for each operation",
    )
    total_bytes: int = Field(
        ge=0,
        default=0,
        description="Total bytes that would be affected",
    )
    total_files: int = Field(
        ge=0,
        default=0,
        description="Total files that would be affected",
    )
    has_warnings: bool = Field(
        default=False,
        description="Whether any operations have warnings",
    )
    has_overwrites: bool = Field(
        default=False,
        description="Whether any operations would overwrite existing files",
    )


# =============================================================================
# Organization Models
# =============================================================================


class FileCategory(BaseModel):
    """A category for file organization."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Category name (e.g., 'images', 'documents')")
    patterns: tuple[str, ...] = Field(
        description="File patterns that match this category (glob style)"
    )
    destination: str | None = Field(
        default=None,
        description="Destination subdirectory for this category",
    )


class OrganizeRequest(BaseModel):
    """Request to organize files in a directory."""

    model_config = ConfigDict(frozen=True)

    source_dir: str = Field(
        description="Directory to organize"
    )
    categories: tuple[FileCategory, ...] = Field(
        default_factory=tuple,
        description="Categories to use (uses defaults if empty)",
    )
    mode: Literal["by_type", "by_date", "by_size", "custom"] = Field(
        default="by_type",
        description="Organization mode",
    )
    dry_run: bool = Field(
        default=False,
        description="If True, preview without organizing",
    )
    include_hidden: bool = Field(
        default=False,
        description="Include hidden files (starting with .)",
    )
    recursive: bool = Field(
        default=False,
        description="Organize subdirectories recursively",
    )


class OrganizeResult(BaseModel):
    """Result of an organization request."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether organization succeeded")
    files_moved: int = Field(ge=0, default=0, description="Number of files moved")
    files_skipped: int = Field(ge=0, default=0, description="Number of files skipped")
    bytes_organized: int = Field(ge=0, default=0, description="Total bytes organized")
    by_category: dict[str, int] = Field(
        default_factory=dict,
        description="Files moved per category",
    )
    operations: tuple[FileOp, ...] = Field(
        default_factory=tuple,
        description="Operations that were/would be performed",
    )
    error: str | None = Field(default=None, description="Error if failed")


# =============================================================================
# Error Types
# =============================================================================


class FileOperationError(Exception):
    """Base exception for file operations."""

    pass


class FileNotFoundError(FileOperationError):
    """Raised when source file doesn't exist."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"File not found: {path}")


class FileExistsError(FileOperationError):
    """Raised when destination already exists and overwrite is False."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"File already exists: {path}")


class FilePermissionError(FileOperationError):
    """Raised when operation requires elevated permissions."""

    def __init__(self, path: str, operation: str) -> None:
        self.path = path
        self.operation = operation
        super().__init__(f"Permission denied for {operation} on {path}")


class DirectoryNotEmptyError(FileOperationError):
    """Raised when trying to delete non-empty directory without recursive."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Directory not empty: {path}")
