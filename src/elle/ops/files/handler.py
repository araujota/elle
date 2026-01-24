"""File operation handler.

Provides safe file operations with preview, atomic execution,
and rollback support.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

from elle.ops.files.models import (
    DirectoryNotEmptyError,
    FileExistsError,
    FileNotFoundError,
    FileOp,
    FileOpPreview,
    FileOpResult,
    FilePermissionError,
    FilePreview,
    FileRequest,
    FileResult,
    ReadResult,
    WriteResult,
)

logger = logging.getLogger(__name__)


class FileHandler:
    """Handles file operations with safety guarantees.

    Provides move, copy, delete, rename, and mkdir operations with:
    - Preview before execution
    - Atomic execution (all or nothing)
    - Rollback support
    - Permission checking

    Usage:
        handler = FileHandler()

        # Preview operations
        preview = handler.preview(request)
        if not preview.has_warnings:
            # Execute
            result = handler.execute(request)
    """

    def __init__(self, temp_dir: Path | None = None) -> None:
        """Initialize the file handler.

        Args:
            temp_dir: Directory for temporary files during operations.
        """
        self._temp_dir = temp_dir or Path("/tmp/elle-file-ops")
        self._rollback_stack: list[tuple[FileOp, str | None]] = []

    # =========================================================================
    # Preview
    # =========================================================================

    def preview(self, request: FileRequest) -> FilePreview:
        """Generate a preview of proposed file operations.

        Args:
            request: File operation request.

        Returns:
            FilePreview with details of each operation.
        """
        previews: list[FileOpPreview] = []
        total_bytes = 0
        total_files = 0
        has_warnings = False
        has_overwrites = False

        for op in request.operations:
            source = Path(op.source)
            dest = Path(op.dest) if op.dest else None

            # Check source
            source_exists = source.exists()
            source_size = 0
            source_type: Literal["file", "directory", "symlink", "unknown"] = "unknown"

            if source_exists:
                if source.is_symlink():
                    source_type = "symlink"
                elif source.is_file():
                    source_type = "file"
                    source_size = source.stat().st_size
                elif source.is_dir():
                    source_type = "directory"
                    # Calculate total size
                    for f in source.rglob("*"):
                        if f.is_file():
                            source_size += f.stat().st_size
                            total_files += 1

            # Check destination
            dest_exists = dest.exists() if dest else False
            would_overwrite = dest_exists and op.kind in ("move", "copy", "rename")

            # Generate warnings
            warning = None
            if not source_exists and op.kind != "mkdir":
                warning = "Source does not exist"
                has_warnings = True
            elif source_size > 100 * 1024 * 1024:  # 100MB
                warning = f"Large file/directory ({source_size / 1024 / 1024:.1f} MB)"
                has_warnings = True
            elif would_overwrite and not op.overwrite:
                warning = "Would overwrite existing file"
                has_warnings = True
                has_overwrites = True

            total_bytes += source_size
            if source_type == "file":
                total_files += 1

            previews.append(
                FileOpPreview(
                    operation=op,
                    source_exists=source_exists,
                    dest_exists=dest_exists,
                    source_size=source_size,
                    source_type=source_type,
                    would_overwrite=would_overwrite,
                    warning=warning,
                )
            )

        return FilePreview(
            previews=tuple(previews),
            total_bytes=total_bytes,
            total_files=total_files,
            has_warnings=has_warnings,
            has_overwrites=has_overwrites,
        )

    # =========================================================================
    # Execute
    # =========================================================================

    def execute(self, request: FileRequest) -> FileResult:
        """Execute file operations.

        Args:
            request: File operation request.

        Returns:
            FileResult with outcome of all operations.
        """
        if request.dry_run:
            preview = self.preview(request)
            return FileResult(
                success=True,
                results=tuple(
                    FileOpResult(
                        success=True,
                        operation=p.operation,
                        source_existed=p.source_exists,
                        dest_existed=p.dest_exists,
                        bytes_affected=p.source_size,
                    )
                    for p in preview.previews
                ),
                total_bytes=preview.total_bytes,
                total_files=preview.total_files,
            )

        results: list[FileOpResult] = []
        self._rollback_stack.clear()
        total_bytes = 0
        total_files = 0

        try:
            for op in request.operations:
                result = self._execute_op(op)
                results.append(result)

                if result.success:
                    total_bytes += result.bytes_affected
                    if Path(op.source).is_file():
                        total_files += 1
                else:
                    # Operation failed
                    if request.atomic:
                        # Rollback previous operations
                        self._rollback()
                        return FileResult(
                            success=False,
                            results=tuple(results),
                            total_bytes=total_bytes,
                            total_files=total_files,
                            error=result.error,
                            rollback_applied=True,
                        )

            return FileResult(
                success=True,
                results=tuple(results),
                total_bytes=total_bytes,
                total_files=total_files,
            )

        except Exception as e:
            logger.exception(f"File operation failed: {e}")
            if request.atomic:
                self._rollback()
            return FileResult(
                success=False,
                results=tuple(results),
                total_bytes=total_bytes,
                total_files=total_files,
                error=str(e),
                rollback_applied=request.atomic,
            )

    def _execute_op(self, op: FileOp) -> FileOpResult:
        """Execute a single file operation.

        Args:
            op: Operation to execute.

        Returns:
            FileOpResult with outcome.
        """
        source = Path(op.source)
        dest = Path(op.dest) if op.dest else None

        source_existed = source.exists()
        dest_existed = dest.exists() if dest else False

        try:
            if op.kind == "move":
                return self._move(op, source, dest)
            elif op.kind == "copy":
                return self._copy(op, source, dest)
            elif op.kind == "delete":
                return self._delete(op, source)
            elif op.kind == "rename":
                return self._rename(op, source, dest)
            elif op.kind == "mkdir":
                return self._mkdir(op, source)
            elif op.kind == "read":
                return self._read(op, source)
            elif op.kind == "write":
                return self._write(op, source)
            else:
                return FileOpResult(
                    success=False,
                    operation=op,
                    source_existed=source_existed,
                    dest_existed=dest_existed,
                    error=f"Unknown operation kind: {op.kind}",
                )
        except FileNotFoundError as e:
            return FileOpResult(
                success=False,
                operation=op,
                source_existed=source_existed,
                dest_existed=dest_existed,
                error=str(e),
            )
        except FileExistsError as e:
            return FileOpResult(
                success=False,
                operation=op,
                source_existed=source_existed,
                dest_existed=dest_existed,
                error=str(e),
            )
        except FilePermissionError as e:
            return FileOpResult(
                success=False,
                operation=op,
                source_existed=source_existed,
                dest_existed=dest_existed,
                error=str(e),
            )
        except Exception as e:
            return FileOpResult(
                success=False,
                operation=op,
                source_existed=source_existed,
                dest_existed=dest_existed,
                error=str(e),
            )

    def _move(
        self,
        op: FileOp,
        source: Path,
        dest: Path | None,
    ) -> FileOpResult:
        """Execute a move operation."""
        if not source.exists():
            raise FileNotFoundError(str(source))
        if dest is None:
            raise ValueError("Move requires destination")
        if dest.exists() and not op.overwrite:
            raise FileExistsError(str(dest))

        # Check permissions
        if not os.access(source.parent, os.W_OK):
            raise FilePermissionError(str(source), "move")
        # For destination, check the first existing ancestor
        dest_check_dir = dest.parent
        while not dest_check_dir.exists():
            dest_check_dir = dest_check_dir.parent
        if not os.access(dest_check_dir, os.W_OK):
            raise FilePermissionError(str(dest), "move")

        # Calculate size before move
        size = self._get_size(source)

        # Perform move
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(dest))

        # Record for rollback
        self._rollback_stack.append((
            FileOp(kind="move", source=str(dest), dest=str(source)),
            None,
        ))

        logger.info(f"Moved {source} -> {dest}")

        return FileOpResult(
            success=True,
            operation=op,
            source_existed=True,
            dest_existed=dest.exists(),
            bytes_affected=size,
        )

    def _copy(
        self,
        op: FileOp,
        source: Path,
        dest: Path | None,
    ) -> FileOpResult:
        """Execute a copy operation."""
        if not source.exists():
            raise FileNotFoundError(str(source))
        if dest is None:
            raise ValueError("Copy requires destination")
        if dest.exists() and not op.overwrite:
            raise FileExistsError(str(dest))

        # Check permissions
        if not os.access(source, os.R_OK):
            raise FilePermissionError(str(source), "copy")
        # For destination, check the first existing ancestor
        dest_check_dir = dest.parent
        while not dest_check_dir.exists():
            dest_check_dir = dest_check_dir.parent
        if not os.access(dest_check_dir, os.W_OK):
            raise FilePermissionError(str(dest.parent), "copy")

        # Calculate size
        size = self._get_size(source)

        # Perform copy
        dest.parent.mkdir(parents=True, exist_ok=True)

        if source.is_dir():
            if op.recursive:
                shutil.copytree(str(source), str(dest), dirs_exist_ok=op.overwrite)
            else:
                raise DirectoryNotEmptyError(str(source))
        else:
            shutil.copy2(str(source), str(dest))

        # Record for rollback (delete the copy)
        self._rollback_stack.append((
            FileOp(kind="delete", source=str(dest), recursive=source.is_dir()),
            None,
        ))

        logger.info(f"Copied {source} -> {dest}")

        return FileOpResult(
            success=True,
            operation=op,
            source_existed=True,
            dest_existed=False,
            bytes_affected=size,
        )

    def _delete(
        self,
        op: FileOp,
        source: Path,
    ) -> FileOpResult:
        """Execute a delete operation."""
        if not source.exists():
            # Already deleted, consider success
            return FileOpResult(
                success=True,
                operation=op,
                source_existed=False,
                bytes_affected=0,
            )

        # Check permissions
        if not os.access(source.parent, os.W_OK):
            raise FilePermissionError(str(source), "delete")

        # Calculate size
        size = self._get_size(source)

        # Create backup for rollback
        backup_path = self._create_backup(source)

        # Perform delete
        if source.is_dir():
            if op.recursive:
                shutil.rmtree(str(source))
            else:
                source.rmdir()  # Only works if empty
        else:
            source.unlink()

        # Record for rollback
        self._rollback_stack.append((
            FileOp(kind="copy", source=backup_path, dest=str(source), recursive=True),
            backup_path,
        ))

        logger.info(f"Deleted {source}")

        return FileOpResult(
            success=True,
            operation=op,
            source_existed=True,
            bytes_affected=size,
        )

    def _rename(
        self,
        op: FileOp,
        source: Path,
        dest: Path | None,
    ) -> FileOpResult:
        """Execute a rename operation."""
        if not source.exists():
            raise FileNotFoundError(str(source))
        if dest is None:
            raise ValueError("Rename requires destination")
        if dest.exists() and not op.overwrite:
            raise FileExistsError(str(dest))

        # Check permissions
        if not os.access(source.parent, os.W_OK):
            raise FilePermissionError(str(source), "rename")

        # Calculate size
        size = self._get_size(source)

        # Perform rename
        source.rename(dest)

        # Record for rollback
        self._rollback_stack.append((
            FileOp(kind="rename", source=str(dest), dest=str(source)),
            None,
        ))

        logger.info(f"Renamed {source} -> {dest}")

        return FileOpResult(
            success=True,
            operation=op,
            source_existed=True,
            dest_existed=False,
            bytes_affected=size,
        )

    def _mkdir(
        self,
        op: FileOp,
        source: Path,
    ) -> FileOpResult:
        """Execute a mkdir operation."""
        if source.exists():
            if source.is_dir():
                return FileOpResult(
                    success=True,
                    operation=op,
                    source_existed=True,
                    bytes_affected=0,
                )
            else:
                raise FileExistsError(str(source))

        # Check permissions - find first existing ancestor
        check_dir = source.parent
        while not check_dir.exists():
            check_dir = check_dir.parent
        if not os.access(check_dir, os.W_OK):
            raise FilePermissionError(str(source.parent), "mkdir")

        # Create directory
        source.mkdir(parents=True, exist_ok=True)

        # Record for rollback
        self._rollback_stack.append((
            FileOp(kind="delete", source=str(source)),
            None,
        ))

        logger.info(f"Created directory {source}")

        return FileOpResult(
            success=True,
            operation=op,
            source_existed=False,
            bytes_affected=0,
        )

    def _read(
        self,
        op: FileOp,
        source: Path,
    ) -> FileOpResult:
        """Execute a read operation."""
        if not source.exists():
            raise FileNotFoundError(str(source))
        if source.is_dir():
            return FileOpResult(
                success=False,
                operation=op,
                source_existed=True,
                error="Cannot read a directory",
            )

        # Check read permission
        if not os.access(source, os.R_OK):
            raise FilePermissionError(str(source), "read")

        # Read file content
        try:
            content = source.read_text(encoding=op.encoding)
        except UnicodeDecodeError as e:
            return FileOpResult(
                success=False,
                operation=op,
                source_existed=True,
                error=f"Encoding error: {e}",
            )

        size = source.stat().st_size
        logger.info(f"Read {source} ({size} bytes)")

        return FileOpResult(
            success=True,
            operation=op,
            source_existed=True,
            bytes_affected=size,
            content=content,
        )

    def _write(
        self,
        op: FileOp,
        source: Path,
    ) -> FileOpResult:
        """Execute a write operation."""
        if op.content is None:
            return FileOpResult(
                success=False,
                operation=op,
                source_existed=source.exists(),
                error="Write operation requires content",
            )

        # Check if file exists
        source_existed = source.exists()

        if source_existed and not op.overwrite:
            raise FileExistsError(str(source))

        # Check write permission on parent directory
        parent = source.parent
        if not parent.exists():
            # Will create parent directories
            check_dir = parent
            while not check_dir.exists():
                check_dir = check_dir.parent
            if not os.access(check_dir, os.W_OK):
                raise FilePermissionError(str(parent), "write")
        elif source_existed and not os.access(source, os.W_OK):
            raise FilePermissionError(str(source), "write")
        elif not source_existed and not os.access(parent, os.W_OK):
            raise FilePermissionError(str(parent), "write")

        # Create backup if overwriting
        backup_path = None
        if source_existed:
            backup_path = self._create_backup(source)

        # Create parent directories if needed
        source.parent.mkdir(parents=True, exist_ok=True)

        # Write content
        try:
            source.write_text(op.content, encoding=op.encoding)
        except Exception as e:
            # Restore backup if write failed
            if backup_path:
                shutil.copy2(backup_path, str(source))
            raise

        bytes_written = len(op.content.encode(op.encoding))

        # Record for rollback
        if source_existed:
            # Rollback: restore from backup
            self._rollback_stack.append((
                FileOp(kind="copy", source=backup_path, dest=str(source), overwrite=True),
                backup_path,
            ))
        else:
            # Rollback: delete the new file
            self._rollback_stack.append((
                FileOp(kind="delete", source=str(source)),
                None,
            ))

        logger.info(f"Wrote {source} ({bytes_written} bytes)")

        return FileOpResult(
            success=True,
            operation=op,
            source_existed=source_existed,
            bytes_affected=bytes_written,
        )

    # =========================================================================
    # Rollback
    # =========================================================================

    def _rollback(self) -> None:
        """Rollback all operations in reverse order."""
        logger.info("Rolling back file operations")

        for rollback_op, backup_path in reversed(self._rollback_stack):
            try:
                source = Path(rollback_op.source)
                dest = Path(rollback_op.dest) if rollback_op.dest else None

                if rollback_op.kind == "move":
                    if source.exists() and dest:
                        shutil.move(str(source), str(dest))
                elif rollback_op.kind == "copy":
                    if source.exists() and dest:
                        if source.is_dir():
                            shutil.copytree(str(source), str(dest), dirs_exist_ok=True)
                        else:
                            shutil.copy2(str(source), str(dest))
                elif rollback_op.kind == "delete":
                    if source.exists():
                        if source.is_dir():
                            shutil.rmtree(str(source))
                        else:
                            source.unlink()
                elif rollback_op.kind == "rename":
                    if source.exists() and dest:
                        source.rename(dest)

                # Clean up backup
                if backup_path:
                    backup = Path(backup_path)
                    if backup.exists():
                        if backup.is_dir():
                            shutil.rmtree(str(backup))
                        else:
                            backup.unlink()

            except Exception as e:
                logger.error(f"Rollback operation failed: {e}")

        self._rollback_stack.clear()

    # =========================================================================
    # Helpers
    # =========================================================================

    def _get_size(self, path: Path) -> int:
        """Get total size of a file or directory."""
        if path.is_file():
            return path.stat().st_size

        total = 0
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
        return total

    def _create_backup(self, path: Path) -> str:
        """Create a backup of a file/directory for rollback."""
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = self._temp_dir / f"{path.name}_{timestamp}"

        if path.is_dir():
            shutil.copytree(str(path), str(backup_path))
        else:
            shutil.copy2(str(path), str(backup_path))

        return str(backup_path)


# =============================================================================
# Module-level convenience functions
# =============================================================================

_handler: FileHandler | None = None


def get_handler() -> FileHandler:
    """Get the global FileHandler instance.

    Returns:
        FileHandler instance.
    """
    global _handler
    if _handler is None:
        _handler = FileHandler()
    return _handler


def preview_operations(request: FileRequest) -> FilePreview:
    """Preview file operations (convenience function).

    Args:
        request: File operation request.

    Returns:
        FilePreview with operation details.
    """
    return get_handler().preview(request)


def execute_operations(request: FileRequest) -> FileResult:
    """Execute file operations (convenience function).

    Args:
        request: File operation request.

    Returns:
        FileResult with outcome.
    """
    return get_handler().execute(request)


def move_file(
    source: str | Path,
    dest: str | Path,
    overwrite: bool = False,
) -> FileResult:
    """Move a file (convenience function).

    Args:
        source: Source path.
        dest: Destination path.
        overwrite: Allow overwriting existing files.

    Returns:
        FileResult with outcome.
    """
    request = FileRequest(
        operations=(
            FileOp(kind="move", source=str(source), dest=str(dest), overwrite=overwrite),
        ),
        description=f"Move {source} to {dest}",
    )
    return get_handler().execute(request)


def copy_file(
    source: str | Path,
    dest: str | Path,
    overwrite: bool = False,
    recursive: bool = False,
) -> FileResult:
    """Copy a file (convenience function).

    Args:
        source: Source path.
        dest: Destination path.
        overwrite: Allow overwriting existing files.
        recursive: Copy directories recursively.

    Returns:
        FileResult with outcome.
    """
    request = FileRequest(
        operations=(
            FileOp(
                kind="copy",
                source=str(source),
                dest=str(dest),
                overwrite=overwrite,
                recursive=recursive,
            ),
        ),
        description=f"Copy {source} to {dest}",
    )
    return get_handler().execute(request)


def delete_file(
    path: str | Path,
    recursive: bool = False,
) -> FileResult:
    """Delete a file (convenience function).

    Args:
        path: Path to delete.
        recursive: Delete directories recursively.

    Returns:
        FileResult with outcome.
    """
    request = FileRequest(
        operations=(
            FileOp(kind="delete", source=str(path), recursive=recursive),
        ),
        description=f"Delete {path}",
    )
    return get_handler().execute(request)


def create_directory(path: str | Path) -> FileResult:
    """Create a directory (convenience function).

    Args:
        path: Directory path to create.

    Returns:
        FileResult with outcome.
    """
    request = FileRequest(
        operations=(FileOp(kind="mkdir", source=str(path)),),
        description=f"Create directory {path}",
    )
    return get_handler().execute(request)


def read_file(
    path: str | Path,
    encoding: str = "utf-8",
) -> ReadResult:
    """Read a file (convenience function).

    Args:
        path: Path to the file to read.
        encoding: File encoding (default: utf-8).

    Returns:
        ReadResult with file content or error.
    """
    path = Path(path)

    try:
        if not path.exists():
            return ReadResult(
                success=False,
                path=str(path),
                encoding=encoding,
                error=f"File not found: {path}",
            )

        if path.is_dir():
            return ReadResult(
                success=False,
                path=str(path),
                encoding=encoding,
                error="Cannot read a directory",
            )

        if not os.access(path, os.R_OK):
            return ReadResult(
                success=False,
                path=str(path),
                encoding=encoding,
                error=f"Permission denied: {path}",
            )

        content = path.read_text(encoding=encoding)
        size = path.stat().st_size

        return ReadResult(
            success=True,
            path=str(path),
            content=content,
            size_bytes=size,
            encoding=encoding,
        )

    except UnicodeDecodeError as e:
        return ReadResult(
            success=False,
            path=str(path),
            encoding=encoding,
            error=f"Encoding error: {e}",
        )
    except Exception as e:
        return ReadResult(
            success=False,
            path=str(path),
            encoding=encoding,
            error=str(e),
        )


def write_file(
    path: str | Path,
    content: str,
    overwrite: bool = False,
    encoding: str = "utf-8",
) -> WriteResult:
    """Write content to a file (convenience function).

    Args:
        path: Path to the file to write.
        content: Content to write.
        overwrite: Allow overwriting existing files (default: False).
        encoding: File encoding (default: utf-8).

    Returns:
        WriteResult with outcome.
    """
    path = Path(path)
    created = not path.exists()

    try:
        if path.exists() and not overwrite:
            return WriteResult(
                success=False,
                path=str(path),
                error=f"File already exists: {path}",
            )

        # Check write permission
        parent = path.parent
        if not parent.exists():
            # Find first existing ancestor
            check_dir = parent
            while not check_dir.exists():
                check_dir = check_dir.parent
            if not os.access(check_dir, os.W_OK):
                return WriteResult(
                    success=False,
                    path=str(path),
                    error=f"Permission denied: {parent}",
                )
        elif path.exists() and not os.access(path, os.W_OK):
            return WriteResult(
                success=False,
                path=str(path),
                error=f"Permission denied: {path}",
            )
        elif not path.exists() and not os.access(parent, os.W_OK):
            return WriteResult(
                success=False,
                path=str(path),
                error=f"Permission denied: {parent}",
            )

        # Create parent directories if needed
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write file
        path.write_text(content, encoding=encoding)
        bytes_written = len(content.encode(encoding))

        return WriteResult(
            success=True,
            path=str(path),
            bytes_written=bytes_written,
            created=created,
        )

    except Exception as e:
        return WriteResult(
            success=False,
            path=str(path),
            error=str(e),
        )
