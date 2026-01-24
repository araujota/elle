"""File operation capabilities.

Provides typed, policy-governed operations for file management:
- file.read - Read file contents
- file.write - Write content to a file
- file.delete - Delete a file
- file.copy - Copy a file
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from elle.capabilities.models import (
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    RiskLevel,
    RollbackResult,
    SideEffect,
    VerificationResult,
)
from elle.capabilities.protocol import BaseCapability

logger = logging.getLogger(__name__)


# =============================================================================
# Forbidden paths
# =============================================================================

# Paths that should never be modified
FORBIDDEN_PATHS = frozenset({
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
    "/etc/gshadow",
    "/boot",
    "/boot/grub",
    "/boot/efi",
})

# Paths that require extra confirmation
SENSITIVE_PATHS = frozenset({
    "/etc",
    "/usr",
    "/var",
    "/root",
    "/home",
})


def _is_forbidden_path(path: Path) -> bool:
    """Check if a path is forbidden."""
    path_str = str(path.resolve())
    for forbidden in FORBIDDEN_PATHS:
        if path_str == forbidden or path_str.startswith(forbidden + "/"):
            return True
    return False


def _is_sensitive_path(path: Path) -> bool:
    """Check if a path is sensitive."""
    path_str = str(path.resolve())
    for sensitive in SENSITIVE_PATHS:
        if path_str == sensitive or path_str.startswith(sensitive + "/"):
            return True
    return False


def _assess_path_risk(path: Path) -> RiskLevel:
    """Assess risk level based on path."""
    if _is_forbidden_path(path):
        return "critical"
    if _is_sensitive_path(path):
        return "high"
    if str(path).startswith("/tmp") or str(path).startswith("/var/tmp"):
        return "low"
    return "medium"


def _file_hash(path: Path) -> str | None:
    """Compute SHA256 hash of a file."""
    if not path.exists():
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None


# =============================================================================
# Input/Output Models
# =============================================================================


class FileReadInput(BaseModel):
    """Input for file read operation."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="Path to the file to read")
    max_size_bytes: int = Field(
        default=10 * 1024 * 1024,  # 10MB
        ge=1,
        description="Maximum file size to read",
    )


class FileReadOutput(BaseModel):
    """Output from file read operation."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="Path that was read")
    content: str = Field(description="File content")
    size_bytes: int = Field(description="File size in bytes")
    sha256: str = Field(description="SHA256 hash of content")


class FileWriteInput(BaseModel):
    """Input for file write operation."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="Path to write to")
    content: str = Field(description="Content to write")
    create_backup: bool = Field(
        default=True,
        description="Whether to create a backup of existing file",
    )
    mode: int | None = Field(
        default=None,
        description="File mode (permissions) as octal, e.g., 0o644",
    )


class FileWriteOutput(BaseModel):
    """Output from file write operation."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="Path that was written")
    size_bytes: int = Field(description="Bytes written")
    sha256: str = Field(description="SHA256 hash of new content")
    backup_path: str | None = Field(
        default=None,
        description="Path to backup if created",
    )
    created: bool = Field(
        default=False,
        description="Whether file was newly created",
    )


class FileDeleteInput(BaseModel):
    """Input for file delete operation."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="Path to delete")
    create_backup: bool = Field(
        default=True,
        description="Whether to create a backup before deleting",
    )


class FileDeleteOutput(BaseModel):
    """Output from file delete operation."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="Path that was deleted")
    size_bytes: int = Field(description="Size of deleted file")
    backup_path: str | None = Field(
        default=None,
        description="Path to backup if created",
    )


class FileCopyInput(BaseModel):
    """Input for file copy operation."""

    model_config = ConfigDict(frozen=True)

    source: str = Field(description="Source file path")
    destination: str = Field(description="Destination file path")
    overwrite: bool = Field(
        default=False,
        description="Whether to overwrite existing destination",
    )


class FileCopyOutput(BaseModel):
    """Output from file copy operation."""

    model_config = ConfigDict(frozen=True)

    source: str = Field(description="Source path")
    destination: str = Field(description="Destination path")
    size_bytes: int = Field(description="Bytes copied")
    sha256: str = Field(description="SHA256 hash of copied file")


# =============================================================================
# File Read Capability
# =============================================================================


class FileReadCapability(BaseCapability):
    """Read contents of a file."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="file.read",
            summary="Read contents of a file",
            domain="file",
            risk="none",
            side_effects=(),  # Read-only
            requires_privilege=False,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="FileReadInput",
            output_schema_name="FileReadOutput",
        )

    def dry_run(self, input: FileReadInput) -> DryRunResult:
        """Preview file read."""
        path = Path(input.path)

        if not path.exists():
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"File does not exist: {input.path}",),
                preview_text=f"Cannot read: file does not exist",
            )

        if not path.is_file():
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"Path is not a file: {input.path}",),
                preview_text=f"Cannot read: path is not a file",
            )

        size = path.stat().st_size
        if size > input.max_size_bytes:
            return DryRunResult(
                is_valid=False,
                validation_errors=(
                    f"File too large: {size} bytes (max: {input.max_size_bytes})",
                ),
                preview_text=f"Cannot read: file too large",
            )

        return DryRunResult(
            would_execute=(),
            would_modify=(),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text=f"Would read {size} bytes from {input.path}",
            is_valid=True,
        )

    def run(self, input: FileReadInput) -> CapabilityResult:
        """Read the file."""
        start_time = time.time()
        path = Path(input.path)

        try:
            content = path.read_text()
            size = len(content.encode("utf-8"))
            sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

            return CapabilityResult(
                success=True,
                output=FileReadOutput(
                    path=input.path,
                    content=content,
                    size_bytes=size,
                    sha256=sha,
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Read {size} bytes from {input.path}",
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Failed to read {input.path}",
                ),
            )


# =============================================================================
# File Write Capability
# =============================================================================


class FileWriteCapability(BaseCapability):
    """Write content to a file."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="file.write",
            summary="Write content to a file",
            domain="file",
            risk="medium",
            side_effects=(
                SideEffect(
                    kind="file_write",
                    target="{path}",
                    reversible=True,
                    description="File content will be replaced",
                ),
            ),
            requires_privilege=False,  # Depends on path
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="FileWriteInput",
            output_schema_name="FileWriteOutput",
        )

    def dry_run(self, input: FileWriteInput) -> DryRunResult:
        """Preview file write."""
        path = Path(input.path)

        # Check forbidden paths
        if _is_forbidden_path(path):
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"Cannot write to forbidden path: {input.path}",),
                preview_text="Cannot write: path is forbidden",
            )

        # Generate diff if file exists
        diff = None
        if path.exists():
            try:
                current = path.read_text()
                import difflib

                diff_lines = difflib.unified_diff(
                    current.splitlines(keepends=True),
                    input.content.splitlines(keepends=True),
                    fromfile=f"a/{path.name}",
                    tofile=f"b/{path.name}",
                )
                diff = "".join(diff_lines)
            except Exception:
                pass
        else:
            diff = f"+++ {input.path} (new file)\n{input.content[:500]}..."

        risk = _assess_path_risk(path)
        requires_priv = not os.access(
            path if path.exists() else path.parent, os.W_OK
        )

        return DryRunResult(
            would_execute=(),
            would_modify=(input.path,),
            estimated_risk=risk,
            requires_confirmation=risk in ("high", "critical"),
            preview_text=f"Would write {len(input.content)} bytes to {input.path}",
            diff=diff,
            is_valid=True,
        )

    def run(self, input: FileWriteInput) -> CapabilityResult:
        """Write content to the file."""
        start_time = time.time()
        path = Path(input.path)
        backup_path = None
        created = not path.exists()

        try:
            # Create backup if requested and file exists
            if input.create_backup and path.exists():
                backup_path = f"{input.path}.bak.{int(time.time())}"
                shutil.copy2(path, backup_path)
                logger.info(f"Created backup: {backup_path}")

            # Write content
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(input.content)

            # Set mode if specified
            if input.mode is not None:
                os.chmod(path, input.mode)

            size = len(input.content.encode("utf-8"))
            sha = hashlib.sha256(input.content.encode("utf-8")).hexdigest()

            return CapabilityResult(
                success=True,
                output=FileWriteOutput(
                    path=input.path,
                    size_bytes=size,
                    sha256=sha,
                    backup_path=backup_path,
                    created=created,
                ),
                side_effects_applied=(
                    SideEffect(
                        kind="file_write",
                        target=input.path,
                        reversible=True,
                        description=f"Wrote {size} bytes",
                    ),
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    files_modified=(input.path,),
                    rationale=f"Wrote {size} bytes to {input.path}",
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Failed to write to {input.path}",
                ),
            )

    def verify(self, input: FileWriteInput) -> VerificationResult:
        """Verify file was written correctly."""
        path = Path(input.path)

        if not path.exists():
            return VerificationResult(
                passed=False,
                checks_performed=("file exists",),
                actual_state={"exists": False},
                expected_state={"exists": True},
                discrepancies=("File does not exist after write",),
            )

        try:
            content = path.read_text()
            actual_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            expected_sha = hashlib.sha256(input.content.encode("utf-8")).hexdigest()

            if actual_sha != expected_sha:
                return VerificationResult(
                    passed=False,
                    checks_performed=("content hash",),
                    actual_state={"sha256": actual_sha},
                    expected_state={"sha256": expected_sha},
                    discrepancies=("Content hash mismatch",),
                )

            return VerificationResult(
                passed=True,
                checks_performed=("file exists", "content hash"),
                actual_state={"exists": True, "sha256": actual_sha},
                expected_state={"exists": True, "sha256": expected_sha},
                discrepancies=(),
            )

        except Exception as e:
            return VerificationResult(
                passed=False,
                discrepancies=(str(e),),
            )

    def rollback(
        self,
        input: FileWriteInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback by restoring from backup."""
        if not result.output:
            return RollbackResult(
                success=False,
                error="No output to determine backup path",
            )

        backup_path = result.output.backup_path
        if not backup_path:
            # File was newly created, delete it
            if result.output.created:
                try:
                    Path(input.path).unlink()
                    return RollbackResult(
                        success=True,
                        rolled_back=(input.path,),
                    )
                except Exception as e:
                    return RollbackResult(
                        success=False,
                        error=str(e),
                        failed_to_rollback=(input.path,),
                    )
            return RollbackResult(
                success=False,
                error="No backup available for rollback",
            )

        try:
            shutil.copy2(backup_path, input.path)
            return RollbackResult(
                success=True,
                rolled_back=(input.path,),
                commands_executed=(f"cp {backup_path} {input.path}",),
            )
        except Exception as e:
            return RollbackResult(
                success=False,
                error=str(e),
                failed_to_rollback=(input.path,),
            )


# =============================================================================
# File Delete Capability
# =============================================================================


class FileDeleteCapability(BaseCapability):
    """Delete a file."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="file.delete",
            summary="Delete a file",
            domain="file",
            risk="high",
            side_effects=(
                SideEffect(
                    kind="file_delete",
                    target="{path}",
                    reversible=True,  # If backup is created
                    description="File will be deleted",
                ),
            ),
            requires_privilege=False,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="FileDeleteInput",
            output_schema_name="FileDeleteOutput",
        )

    def dry_run(self, input: FileDeleteInput) -> DryRunResult:
        """Preview file deletion."""
        path = Path(input.path)

        if _is_forbidden_path(path):
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"Cannot delete forbidden path: {input.path}",),
                preview_text="Cannot delete: path is forbidden",
            )

        if not path.exists():
            return DryRunResult(
                is_valid=True,
                would_execute=(),
                would_modify=(),
                estimated_risk="none",
                requires_confirmation=False,
                preview_text=f"File does not exist: {input.path}",
            )

        size = path.stat().st_size

        return DryRunResult(
            would_execute=(),
            would_modify=(input.path,),
            estimated_risk=_assess_path_risk(path),
            requires_confirmation=True,
            preview_text=f"Would delete {input.path} ({size} bytes)",
            is_valid=True,
        )

    def run(self, input: FileDeleteInput) -> CapabilityResult:
        """Delete the file."""
        start_time = time.time()
        path = Path(input.path)
        backup_path = None

        if not path.exists():
            return CapabilityResult(
                success=True,
                output=FileDeleteOutput(
                    path=input.path,
                    size_bytes=0,
                    backup_path=None,
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"File already deleted: {input.path}",
                ),
            )

        try:
            size = path.stat().st_size

            # Create backup if requested
            if input.create_backup:
                backup_path = f"{input.path}.deleted.{int(time.time())}"
                shutil.copy2(path, backup_path)
                logger.info(f"Created backup before delete: {backup_path}")

            # Delete
            path.unlink()

            return CapabilityResult(
                success=True,
                output=FileDeleteOutput(
                    path=input.path,
                    size_bytes=size,
                    backup_path=backup_path,
                ),
                side_effects_applied=(
                    SideEffect(
                        kind="file_delete",
                        target=input.path,
                        reversible=backup_path is not None,
                        description=f"Deleted {size} bytes",
                    ),
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    files_modified=(input.path,),
                    rationale=f"Deleted {input.path}",
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Failed to delete {input.path}",
                ),
            )

    def verify(self, input: FileDeleteInput) -> VerificationResult:
        """Verify file was deleted."""
        path = Path(input.path)

        if path.exists():
            return VerificationResult(
                passed=False,
                checks_performed=("file deleted",),
                actual_state={"exists": True},
                expected_state={"exists": False},
                discrepancies=("File still exists after delete",),
            )

        return VerificationResult(
            passed=True,
            checks_performed=("file deleted",),
            actual_state={"exists": False},
            expected_state={"exists": False},
            discrepancies=(),
        )

    def rollback(
        self,
        input: FileDeleteInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback by restoring from backup."""
        if not result.output or not result.output.backup_path:
            return RollbackResult(
                success=False,
                error="No backup available for rollback",
            )

        try:
            shutil.copy2(result.output.backup_path, input.path)
            return RollbackResult(
                success=True,
                rolled_back=(input.path,),
                commands_executed=(
                    f"cp {result.output.backup_path} {input.path}",
                ),
            )
        except Exception as e:
            return RollbackResult(
                success=False,
                error=str(e),
                failed_to_rollback=(input.path,),
            )


# =============================================================================
# File Copy Capability
# =============================================================================


class FileCopyCapability(BaseCapability):
    """Copy a file."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="file.copy",
            summary="Copy a file to a new location",
            domain="file",
            risk="low",
            side_effects=(
                SideEffect(
                    kind="file_write",
                    target="{destination}",
                    reversible=True,
                    description="File will be copied to destination",
                ),
            ),
            requires_privilege=False,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="FileCopyInput",
            output_schema_name="FileCopyOutput",
        )

    def dry_run(self, input: FileCopyInput) -> DryRunResult:
        """Preview file copy."""
        src = Path(input.source)
        dst = Path(input.destination)

        if not src.exists():
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"Source does not exist: {input.source}",),
                preview_text="Cannot copy: source does not exist",
            )

        if dst.exists() and not input.overwrite:
            return DryRunResult(
                is_valid=False,
                validation_errors=(
                    f"Destination exists and overwrite=False: {input.destination}",
                ),
                preview_text="Cannot copy: destination exists",
            )

        if _is_forbidden_path(dst):
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"Cannot copy to forbidden path: {input.destination}",),
                preview_text="Cannot copy: destination is forbidden",
            )

        size = src.stat().st_size

        return DryRunResult(
            would_execute=(),
            would_modify=(input.destination,),
            estimated_risk=_assess_path_risk(dst),
            requires_confirmation=_is_sensitive_path(dst),
            preview_text=f"Would copy {size} bytes from {input.source} to {input.destination}",
            is_valid=True,
        )

    def run(self, input: FileCopyInput) -> CapabilityResult:
        """Copy the file."""
        start_time = time.time()
        src = Path(input.source)
        dst = Path(input.destination)

        try:
            # Ensure destination directory exists
            dst.parent.mkdir(parents=True, exist_ok=True)

            # Copy with metadata
            shutil.copy2(src, dst)

            size = dst.stat().st_size
            sha = _file_hash(dst) or ""

            return CapabilityResult(
                success=True,
                output=FileCopyOutput(
                    source=input.source,
                    destination=input.destination,
                    size_bytes=size,
                    sha256=sha,
                ),
                side_effects_applied=(
                    SideEffect(
                        kind="file_write",
                        target=input.destination,
                        reversible=True,
                        description=f"Copied {size} bytes",
                    ),
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    files_modified=(input.destination,),
                    rationale=f"Copied {input.source} to {input.destination}",
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Failed to copy {input.source}",
                ),
            )

    def verify(self, input: FileCopyInput) -> VerificationResult:
        """Verify file was copied correctly."""
        src = Path(input.source)
        dst = Path(input.destination)

        if not dst.exists():
            return VerificationResult(
                passed=False,
                checks_performed=("destination exists",),
                actual_state={"exists": False},
                expected_state={"exists": True},
                discrepancies=("Destination does not exist after copy",),
            )

        src_hash = _file_hash(src)
        dst_hash = _file_hash(dst)

        if src_hash and dst_hash and src_hash != dst_hash:
            return VerificationResult(
                passed=False,
                checks_performed=("content hash",),
                actual_state={"sha256": dst_hash},
                expected_state={"sha256": src_hash},
                discrepancies=("Content hash mismatch after copy",),
            )

        return VerificationResult(
            passed=True,
            checks_performed=("destination exists", "content hash"),
            actual_state={"exists": True, "sha256": dst_hash},
            expected_state={"exists": True, "sha256": src_hash},
            discrepancies=(),
        )

    def rollback(
        self,
        input: FileCopyInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback by deleting the copied file."""
        try:
            dst = Path(input.destination)
            if dst.exists():
                dst.unlink()
            return RollbackResult(
                success=True,
                rolled_back=(input.destination,),
            )
        except Exception as e:
            return RollbackResult(
                success=False,
                error=str(e),
                failed_to_rollback=(input.destination,),
            )


# =============================================================================
# Exports
# =============================================================================

FILE_CAPABILITIES = [
    FileReadCapability,
    FileWriteCapability,
    FileDeleteCapability,
    FileCopyCapability,
]
