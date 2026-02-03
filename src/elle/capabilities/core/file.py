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
import stat as stat_mod
import time
from pathlib import Path
from typing import Any

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
FORBIDDEN_PATHS = frozenset(
    {
        "/etc/passwd",
        "/etc/shadow",
        "/etc/sudoers",
        "/etc/gshadow",
        "/boot",
        "/boot/grub",
        "/boot/efi",
    }
)

# Paths that require extra confirmation
SENSITIVE_PATHS = frozenset(
    {
        "/etc",
        "/usr",
        "/var",
        "/root",
        "/home",
    }
)


def _is_forbidden_path(path: Path) -> bool:
    """Check if a path is forbidden."""
    path_str = str(path.resolve())
    for forbidden in FORBIDDEN_PATHS:
        if path_str == forbidden or path_str.startswith(forbidden + "/"):
            return True
    return False


def _check_symlink_safety(path: Path) -> str | None:
    """Advisory symlink check. The definitive guard is O_NOFOLLOW in I/O."""
    try:
        st = os.lstat(str(path))
    except OSError:
        return None
    if stat_mod.S_ISLNK(st.st_mode):
        target = path.resolve()
        if _is_forbidden_path(target):
            return f"Symlink {path} points to forbidden path {target}"
    return None


def _safe_open_no_follow(path: Path, flags: int, mode: int = 0o644) -> int:
    """Open a file with O_NOFOLLOW, raising OSError(ELOOP) if symlink."""
    return os.open(str(path), flags | os.O_NOFOLLOW, mode)


def _any_parent_forbidden(path: Path) -> str | None:
    """Check if any parent directory resolves to a forbidden path."""
    try:
        resolved = str(path.resolve())
    except (OSError, ValueError):
        return None
    # Walk up the path string to check each ancestor
    parts = resolved.split("/")
    for i in range(2, len(parts)):
        ancestor = "/".join(parts[:i])
        if not ancestor:
            continue
        if ancestor in FORBIDDEN_PATHS:
            return f"Cannot create under forbidden path: {ancestor}"
    return None


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


class FileReadCapability(BaseCapability[FileReadInput, FileReadOutput]):
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
                preview_text="Cannot read: file does not exist",
            )

        if not path.is_file():
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"Path is not a file: {input.path}",),
                preview_text="Cannot read: path is not a file",
            )

        size = path.stat().st_size
        if size > input.max_size_bytes:
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"File too large: {size} bytes (max: {input.max_size_bytes})",),
                preview_text="Cannot read: file too large",
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


class FileWriteCapability(BaseCapability[FileWriteInput, FileWriteOutput]):
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
        _requires_priv = not os.access(
            path if path.exists() else path.parent, os.W_OK
        )  # TODO: Use _requires_priv in DryRunResult when field is added

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

        # Advisory symlink check
        if err := _check_symlink_safety(path):
            return CapabilityResult(
                success=False,
                error=err,
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        # O_NOFOLLOW probe: if path exists and is a symlink, reject atomically
        try:
            if path.exists():
                try:
                    probe_fd = _safe_open_no_follow(path, os.O_RDONLY)
                    os.close(probe_fd)
                except OSError as e:
                    import errno

                    if e.errno == errno.ELOOP:
                        return CapabilityResult(
                            success=False,
                            error=f"Refusing to follow symlink: {path}",
                            execution_time_ms=int((time.time() - start_time) * 1000),
                        )
                    # Other OS errors (permission, etc.) -- let the write attempt handle them
        except Exception:
            pass  # Don't let probe failures block the write

        # Check resolved path against forbidden list
        try:
            resolved = path.resolve()
            if _is_forbidden_path(resolved):
                return CapabilityResult(
                    success=False,
                    error=f"Resolved path is forbidden: {resolved}",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )
        except (OSError, ValueError):
            pass

        # Parent directory validation (M7)
        if err := _any_parent_forbidden(path):
            return CapabilityResult(
                success=False,
                error=err,
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        backup_path = None
        created = not path.exists()
        tmp_path: str | None = None

        try:
            # Create backup if requested and file exists
            if input.create_backup and path.exists():
                backup_path = f"{input.path}.bak.{int(time.time())}"
                shutil.copy2(path, backup_path)
                logger.info(f"Created backup: {backup_path}")

            # Write content atomically via tempfile + rename
            import tempfile

            # Symlink check on parent directory
            if path.parent.exists() and path.parent.is_symlink():
                raise ValueError("Parent directory is a symlink")

            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=str(path.parent))
            try:
                os.write(fd, input.content.encode("utf-8"))
                try:
                    os.fsync(fd)
                except OSError:
                    pass  # Best-effort durability
            finally:
                os.close(fd)

            # Pre-rename race guard: verify target hasn't become a symlink
            try:
                st = os.lstat(str(path))
                if stat_mod.S_ISLNK(st.st_mode):
                    os.unlink(tmp_path)
                    tmp_path = None
                    return CapabilityResult(
                        success=False,
                        error=f"Race detected: {path} became a symlink before rename",
                        execution_time_ms=int((time.time() - start_time) * 1000),
                    )
            except OSError:
                pass  # File doesn't exist yet or not accessible, safe to proceed

            os.rename(tmp_path, str(path))  # type: ignore[arg-type]  # tmp_path is str here (set to None only on early return)
            tmp_path = None  # Rename succeeded, no orphan to clean

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
            # Clean up orphaned temp file
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
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


class FileDeleteCapability(BaseCapability[FileDeleteInput, FileDeleteOutput]):
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

        # Advisory symlink check
        if err := _check_symlink_safety(path):
            return CapabilityResult(
                success=False,
                error=err,
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        # O_NOFOLLOW probe: reject symlinks to forbidden paths atomically
        if path.exists():
            try:
                probe_fd = _safe_open_no_follow(path, os.O_RDONLY)
                os.close(probe_fd)
            except OSError as e:
                import errno

                if e.errno == errno.ELOOP:
                    # It's a symlink -- check if target is forbidden
                    target = path.resolve()
                    if _is_forbidden_path(target):
                        return CapabilityResult(
                            success=False,
                            error=f"Refusing to follow symlink to forbidden path: {target}",
                            execution_time_ms=int((time.time() - start_time) * 1000),
                        )

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
                commands_executed=(f"cp {result.output.backup_path} {input.path}",),
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


class FileCopyCapability(BaseCapability[FileCopyInput, FileCopyOutput]):
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
                validation_errors=(f"Destination exists and overwrite=False: {input.destination}",),
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

        for p in (src, dst):
            if err := _check_symlink_safety(p):
                return CapabilityResult(
                    success=False,
                    error=err,
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

        # Parent directory validation (M7)
        if err := _any_parent_forbidden(dst):
            return CapabilityResult(
                success=False,
                error=err,
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        try:
            # Symlink check on parent directory
            if dst.parent.exists() and dst.parent.is_symlink():
                raise ValueError("Parent directory is a symlink")

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
# File Diff Capability
# =============================================================================


class FileDiffInput(BaseModel):
    """Input for file diff operation."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="Path to the file to diff")
    previous_content: str | None = Field(
        default=None,
        description="Previous content to compare against (uses snapshot if not provided)",
    )
    snapshot_id: str | None = Field(
        default=None,
        description="Snapshot ID to compare against",
    )
    context_lines: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Number of context lines in diff",
    )


class FileDiffOutput(BaseModel):
    """Output from file diff operation."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="File path")
    has_changes: bool = Field(description="Whether file has changed")
    diff: str = Field(default="", description="Unified diff output")
    lines_added: int = Field(default=0, description="Lines added")
    lines_removed: int = Field(default=0, description="Lines removed")
    current_sha256: str = Field(default="", description="Current file hash")
    previous_sha256: str = Field(default="", description="Previous file hash")


class FileDiffCapability(BaseCapability[FileDiffInput, FileDiffOutput]):
    """Generate unified diff for a file."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="file.diff",
            summary="Generate unified diff between file states",
            domain="file",
            risk="none",
            side_effects=(),
            requires_privilege=False,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="FileDiffInput",
            output_schema_name="FileDiffOutput",
        )

    def dry_run(self, input: FileDiffInput) -> DryRunResult:
        """Preview diff generation."""
        path = Path(input.path)

        if not path.exists():
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"File does not exist: {input.path}",),
                preview_text="Cannot diff: file does not exist",
            )

        return DryRunResult(
            would_execute=(),
            would_modify=(),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text=f"Would generate diff for {input.path}",
            is_valid=True,
        )

    def run(self, input: FileDiffInput) -> CapabilityResult:
        """Generate the diff."""
        import difflib

        start_time = time.time()
        path = Path(input.path)

        if not path.exists():
            return CapabilityResult(
                success=False,
                error=f"File does not exist: {input.path}",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        try:
            current_content = path.read_text()
            current_sha = hashlib.sha256(current_content.encode()).hexdigest()

            # Get previous content
            previous_content: str
            if input.previous_content is not None:
                previous_content = input.previous_content
            elif input.snapshot_id:
                # Try to load from snapshot store
                snapshot_content = self._get_snapshot_content(input.snapshot_id, input.path)
                if snapshot_content is None:
                    return CapabilityResult(
                        success=False,
                        error=f"Snapshot not found: {input.snapshot_id}",
                        execution_time_ms=int((time.time() - start_time) * 1000),
                    )
                previous_content = snapshot_content
            else:
                # No previous content - show as all new
                previous_content = ""

            previous_sha = hashlib.sha256(previous_content.encode()).hexdigest()

            # Check if changed
            if current_sha == previous_sha:
                return CapabilityResult(
                    success=True,
                    output=FileDiffOutput(
                        path=input.path,
                        has_changes=False,
                        diff="",
                        lines_added=0,
                        lines_removed=0,
                        current_sha256=current_sha,
                        previous_sha256=previous_sha,
                    ),
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    evidence=CapabilityEvidence(
                        rationale=f"No changes to {input.path}",
                    ),
                )

            # Generate diff
            previous_lines = previous_content.splitlines(keepends=True)
            current_lines = current_content.splitlines(keepends=True)

            diff = difflib.unified_diff(
                previous_lines,
                current_lines,
                fromfile=f"a/{path.name}",
                tofile=f"b/{path.name}",
                n=input.context_lines,
            )
            diff_text = "".join(diff)

            # Count changes
            lines_added = sum(
                1 for line in diff_text.split("\n") if line.startswith("+") and not line.startswith("+++")
            )
            lines_removed = sum(
                1 for line in diff_text.split("\n") if line.startswith("-") and not line.startswith("---")
            )

            return CapabilityResult(
                success=True,
                output=FileDiffOutput(
                    path=input.path,
                    has_changes=True,
                    diff=diff_text,
                    lines_added=lines_added,
                    lines_removed=lines_removed,
                    current_sha256=current_sha,
                    previous_sha256=previous_sha,
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Generated diff for {input.path}: +{lines_added}/-{lines_removed}",
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

    def _get_snapshot_content(self, snapshot_id: str, path: str) -> str | None:
        """Load content from a snapshot.

        Args:
            snapshot_id: Snapshot identifier.
            path: File path within snapshot.

        Returns:
            File content or None if not found.
        """
        # Try to load from file watch snapshot store
        try:
            from elle.capabilities.core.file import _SNAPSHOT_TTL_SECONDS, _snapshot_store

            entry = _snapshot_store.get(snapshot_id)
            if entry:
                stored_time, snapshot = entry
                # Check TTL - discard if older than 1 hour
                if time.time() - stored_time > _SNAPSHOT_TTL_SECONDS:
                    del _snapshot_store[snapshot_id]
                    return None
                return snapshot.get("content")
        except Exception:
            pass
        return None


# =============================================================================
# File Watch Snapshot Capability
# =============================================================================

# Simple in-memory snapshot store (could be backed by SQLite in production)
MAX_SNAPSHOTS = 100
_SNAPSHOT_TTL_SECONDS = 3600  # 1 hour
_snapshot_store: dict[str, tuple[float, dict[str, Any]]] = {}


class FileWatchSnapshotInput(BaseModel):
    """Input for file watch snapshot operation."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="Path to the file to snapshot")
    snapshot_id: str | None = Field(
        default=None,
        description="Custom snapshot ID (auto-generated if not provided)",
    )


class FileWatchSnapshotOutput(BaseModel):
    """Output from file watch snapshot operation."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="File path")
    snapshot_id: str = Field(description="Snapshot identifier")
    sha256: str = Field(description="File hash at snapshot time")
    size_bytes: int = Field(description="File size at snapshot time")
    timestamp: str = Field(description="Snapshot timestamp")


class FileWatchSnapshotCapability(BaseCapability[FileWatchSnapshotInput, FileWatchSnapshotOutput]):
    """Take a snapshot of a file for later comparison."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="file.watch_snapshot",
            summary="Take a snapshot of a file for later diff comparison",
            domain="file",
            risk="none",
            side_effects=(),
            requires_privilege=False,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="FileWatchSnapshotInput",
            output_schema_name="FileWatchSnapshotOutput",
        )

    def dry_run(self, input: FileWatchSnapshotInput) -> DryRunResult:
        """Preview snapshot."""
        path = Path(input.path)

        if not path.exists():
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"File does not exist: {input.path}",),
                preview_text="Cannot snapshot: file does not exist",
            )

        return DryRunResult(
            would_execute=(),
            would_modify=(),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text=f"Would snapshot {input.path}",
            is_valid=True,
        )

    def run(self, input: FileWatchSnapshotInput) -> CapabilityResult:
        """Take the snapshot."""
        import uuid
        from datetime import datetime, timezone

        start_time = time.time()
        path = Path(input.path)

        if not path.exists():
            return CapabilityResult(
                success=False,
                error=f"File does not exist: {input.path}",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        try:
            content = path.read_text()
            sha = hashlib.sha256(content.encode()).hexdigest()
            size = len(content.encode())
            timestamp = datetime.now(timezone.utc).isoformat()

            # Generate or use provided snapshot ID
            snapshot_id = input.snapshot_id or f"{path.name}_{uuid.uuid4().hex[:8]}"

            # Evict oldest entry if at capacity
            if len(_snapshot_store) >= MAX_SNAPSHOTS:
                oldest_key = next(iter(_snapshot_store))
                del _snapshot_store[oldest_key]

            # Store snapshot with timestamp for TTL enforcement
            _snapshot_store[snapshot_id] = (
                time.time(),
                {
                    "path": input.path,
                    "content": content,
                    "sha256": sha,
                    "size_bytes": size,
                    "timestamp": timestamp,
                },
            )

            return CapabilityResult(
                success=True,
                output=FileWatchSnapshotOutput(
                    path=input.path,
                    snapshot_id=snapshot_id,
                    sha256=sha,
                    size_bytes=size,
                    timestamp=timestamp,
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Created snapshot '{snapshot_id}' for {input.path}",
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )


# =============================================================================
# Exports
# =============================================================================

FILE_CAPABILITIES = [
    FileReadCapability,
    FileWriteCapability,
    FileDeleteCapability,
    FileCopyCapability,
    FileDiffCapability,
    FileWatchSnapshotCapability,
]
