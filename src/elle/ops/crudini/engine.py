"""Core crudini wrapper for INI file editing.

Provides a high-level interface to the crudini command-line tool for
INI/CFG file querying, editing, and validation.
"""

from __future__ import annotations

import configparser
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from elle.ops.crudini.models import (
    CrudiniChange,
    CrudiniEditPreview,
    CrudiniEditRequest,
    CrudiniEditResult,
    CrudiniRequest,
    CrudiniResult,
    CrudiniStatus,
    CrudiniUnavailableError,
    INIFileContent,
    INISection,
)

logger = logging.getLogger(__name__)

# Cache for availability check
_crudini_path: str | None = None
_crudini_version: str | None = None
_crudini_checked: bool = False


def _find_crudini() -> str | None:
    """Find the crudini executable."""
    global _crudini_path, _crudini_checked
    if not _crudini_checked:
        _crudini_path = shutil.which("crudini")
        _crudini_checked = True
    return _crudini_path


def is_available() -> bool:
    """Check if crudini is available on the system.

    Returns:
        True if crudini can be executed.
    """
    return _find_crudini() is not None


def get_version() -> str | None:
    """Get the crudini version string.

    Returns:
        Version string or None if unavailable.
    """
    global _crudini_version
    if _crudini_version is not None:
        return _crudini_version

    crudini_path = _find_crudini()
    if crudini_path is None:
        return None

    try:
        result = subprocess.run(
            [crudini_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            _crudini_version = result.stdout.strip()
            return _crudini_version
    except Exception:
        pass
    return None


def get_status() -> CrudiniStatus:
    """Get the current status of crudini availability.

    Returns:
        CrudiniStatus with availability information.
    """
    crudini_path = _find_crudini()
    return CrudiniStatus(
        available=crudini_path is not None,
        version=get_version(),
        path=crudini_path,
    )


class CrudiniEngine:
    """Wrapper around the crudini command-line tool for INI file editing.

    Provides methods to get, set, delete, and merge INI file sections
    and parameters.

    Usage:
        engine = CrudiniEngine()

        # Get a value
        value = engine.get("/etc/myapp.conf", "database", "host")

        # Set a value
        engine.set("/etc/myapp.conf", "database", "port", "5432")

        # Delete a parameter
        engine.delete("/etc/myapp.conf", "deprecated", "old_setting")

        # List sections
        sections = engine.list_sections("/etc/myapp.conf")

    Context manager usage:
        with CrudiniEngine() as engine:
            engine.set("/etc/myapp.conf", "server", "port", "8080")
    """

    def __init__(self) -> None:
        """Initialize the crudini engine.

        Raises:
            CrudiniUnavailableError: If crudini is not installed.
        """
        self._crudini_path = _find_crudini()
        if self._crudini_path is None:
            raise CrudiniUnavailableError("crudini is not installed. Install with: sudo apt install crudini")
        logger.debug(f"Initialized CrudiniEngine with crudini at {self._crudini_path}")

    def __enter__(self) -> CrudiniEngine:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        pass  # No cleanup needed

    # =========================================================================
    # Core Operations
    # =========================================================================

    def _run_crudini(
        self,
        *args: str,
        input_data: str | None = None,
    ) -> tuple[bool, str, str, int]:
        """Run crudini with the given arguments.

        Args:
            *args: Command-line arguments for crudini.
            input_data: Optional input data for stdin.

        Returns:
            Tuple of (success, stdout, stderr, exit_code).
        """
        if self._crudini_path is None:
            raise CrudiniUnavailableError("crudini is not available")

        cmd = [self._crudini_path] + list(args)

        try:
            result = subprocess.run(
                cmd,
                input=input_data,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return (
                result.returncode == 0,
                result.stdout,
                result.stderr,
                result.returncode,
            )
        except subprocess.TimeoutExpired:
            return (False, "", "crudini operation timed out", -1)
        except Exception as e:
            return (False, "", str(e), -1)

    # =========================================================================
    # Get Operations
    # =========================================================================

    def get(
        self,
        file_path: str,
        section: str,
        parameter: str,
    ) -> str | None:
        """Get a parameter value from an INI file.

        Args:
            file_path: Path to the INI file.
            section: Section name.
            parameter: Parameter name.

        Returns:
            The parameter value, or None if not found.
        """
        success, stdout, stderr, _ = self._run_crudini(
            "--get",
            file_path,
            section,
            parameter,
        )

        if success:
            return stdout.strip()
        return None

    def get_section(
        self,
        file_path: str,
        section: str,
    ) -> dict[str, str]:
        """Get all parameters in a section.

        Args:
            file_path: Path to the INI file.
            section: Section name.

        Returns:
            Dictionary of parameter names to values.
        """
        success, stdout, stderr, _ = self._run_crudini(
            "--get",
            "--format=sh",
            file_path,
            section,
        )

        if not success:
            return {}

        result: dict[str, str] = {}
        for line in stdout.strip().split("\n"):
            if "=" in line:
                key, value = line.split("=", 1)
                # Remove shell quoting
                value = value.strip("'\"")
                result[key] = value

        return result

    def list_sections(
        self,
        file_path: str,
    ) -> list[str]:
        """List all sections in an INI file.

        Args:
            file_path: Path to the INI file.

        Returns:
            List of section names.
        """
        success, stdout, stderr, _ = self._run_crudini(
            "--get",
            file_path,
        )

        if not success:
            return []

        return [s.strip() for s in stdout.strip().split("\n") if s.strip()]

    def list_parameters(
        self,
        file_path: str,
        section: str,
    ) -> list[str]:
        """List all parameters in a section.

        Args:
            file_path: Path to the INI file.
            section: Section name.

        Returns:
            List of parameter names.
        """
        success, stdout, stderr, _ = self._run_crudini(
            "--get",
            file_path,
            section,
        )

        if not success:
            return []

        return [p.strip() for p in stdout.strip().split("\n") if p.strip()]

    def get_file_content(
        self,
        file_path: str,
    ) -> INIFileContent:
        """Parse and return the complete content of an INI file.

        Args:
            file_path: Path to the INI file.

        Returns:
            INIFileContent with all sections and parameters.
        """
        sections_list: list[INISection] = []

        section_names = self.list_sections(file_path)
        for section_name in section_names:
            params = self.get_section(file_path, section_name)
            sections_list.append(
                INISection(
                    name=section_name,
                    parameters=params,
                )
            )

        return INIFileContent(
            file_path=file_path,
            sections=tuple(sections_list),
            global_parameters={},  # crudini doesn't expose global params directly
        )

    # =========================================================================
    # Set Operations
    # =========================================================================

    def set(
        self,
        file_path: str,
        section: str,
        parameter: str,
        value: str,
        *,
        existing: str = "set",
    ) -> CrudiniResult:
        """Set a parameter value in an INI file.

        Args:
            file_path: Path to the INI file.
            section: Section name (created if doesn't exist).
            parameter: Parameter name.
            value: Value to set.
            existing: How to handle existing: 'set' (overwrite), 'keep', 'error'.

        Returns:
            CrudiniResult with operation outcome.
        """
        args = ["--set"]

        if existing == "keep":
            args.append("--existing=keep")
        elif existing == "error":
            args.append("--existing=error")

        args.extend([file_path, section, parameter, value])

        success, stdout, stderr, exit_code = self._run_crudini(*args)

        return CrudiniResult(
            success=success,
            output=stdout,
            error=stderr if not success else None,
            exit_code=exit_code,
        )

    def set_section(
        self,
        file_path: str,
        section: str,
        parameters: dict[str, str],
    ) -> CrudiniResult:
        """Set multiple parameters in a section.

        Args:
            file_path: Path to the INI file.
            section: Section name.
            parameters: Dictionary of parameter names to values.

        Returns:
            CrudiniResult with operation outcome.
        """
        for param, value in parameters.items():
            result = self.set(file_path, section, param, value)
            if not result.success:
                return result

        return CrudiniResult(
            success=True,
            output=f"Set {len(parameters)} parameters in [{section}]",
        )

    # =========================================================================
    # Delete Operations
    # =========================================================================

    def delete(
        self,
        file_path: str,
        section: str,
        parameter: str | None = None,
    ) -> CrudiniResult:
        """Delete a parameter or section from an INI file.

        Args:
            file_path: Path to the INI file.
            section: Section name.
            parameter: Parameter name (if None, deletes entire section).

        Returns:
            CrudiniResult with operation outcome.
        """
        args = ["--del", file_path, section]
        if parameter:
            args.append(parameter)

        success, stdout, stderr, exit_code = self._run_crudini(*args)

        return CrudiniResult(
            success=success,
            output=stdout,
            error=stderr if not success else None,
            exit_code=exit_code,
        )

    # =========================================================================
    # Merge Operations
    # =========================================================================

    def merge(
        self,
        file_path: str,
        merge_content: str,
    ) -> CrudiniResult:
        """Merge INI content into an existing file.

        Args:
            file_path: Path to the INI file to merge into.
            merge_content: INI-formatted content to merge.

        Returns:
            CrudiniResult with operation outcome.
        """
        success, stdout, stderr, exit_code = self._run_crudini(
            "--merge",
            file_path,
            input_data=merge_content,
        )

        return CrudiniResult(
            success=success,
            output=stdout,
            error=stderr if not success else None,
            exit_code=exit_code,
        )

    # =========================================================================
    # Validation
    # =========================================================================

    def validate(
        self,
        file_path: str,
    ) -> CrudiniResult:
        """Validate that a file is valid INI format.

        Args:
            file_path: Path to the INI file.

        Returns:
            CrudiniResult with success=True if valid.
        """
        # Try to list sections - if this succeeds, the file is valid
        success, stdout, stderr, exit_code = self._run_crudini(
            "--get",
            file_path,
        )

        return CrudiniResult(
            success=success,
            output="Valid INI file" if success else "",
            error=stderr if not success else None,
            exit_code=exit_code,
        )

    def validate_content(
        self,
        content: str,
    ) -> bool:
        """Validate that a string is valid INI format.

        Args:
            content: INI content to validate.

        Returns:
            True if valid INI format.
        """
        try:
            parser = configparser.ConfigParser()
            parser.read_string(content)
            return True
        except configparser.Error:
            return False

    # =========================================================================
    # Operation Processing
    # =========================================================================

    def process(self, request: CrudiniRequest) -> CrudiniResult:
        """Process a CrudiniRequest.

        Args:
            request: The request to process.

        Returns:
            CrudiniResult with the operation outcome.
        """
        op = request.operation

        if op.kind == "get":
            if op.parameter:
                value = self.get(request.file_path, op.section, op.parameter)
                return CrudiniResult(
                    success=value is not None,
                    output=value or "",
                    error="Parameter not found" if value is None else None,
                )
            else:
                params = self.get_section(request.file_path, op.section)
                return CrudiniResult(
                    success=True,
                    output="\n".join(f"{k}={v}" for k, v in params.items()),
                )
        elif op.kind == "set":
            if op.parameter is None or op.value is None:
                return CrudiniResult(
                    success=False,
                    error="set operation requires parameter and value",
                    exit_code=1,
                )
            return self.set(
                request.file_path,
                op.section,
                op.parameter,
                op.value,
                existing=op.existing,
            )
        elif op.kind == "del":
            return self.delete(request.file_path, op.section, op.parameter)
        elif op.kind == "merge":
            if op.value is None:
                return CrudiniResult(
                    success=False,
                    error="merge operation requires content in value",
                    exit_code=1,
                )
            return self.merge(request.file_path, op.value)
        else:
            return CrudiniResult(
                success=False,
                error=f"Unknown operation kind: {op.kind}",
                exit_code=1,
            )

    # =========================================================================
    # File Editing
    # =========================================================================

    def preview_edit(self, request: CrudiniEditRequest) -> CrudiniEditPreview:
        """Preview changes that would be made to an INI file.

        Args:
            request: The edit request to preview.

        Returns:
            CrudiniEditPreview showing the proposed changes.
        """
        from elle.ops.augeas.diff import colored_diff, unified_diff

        file_path = Path(request.file_path)

        # Check file exists
        if not file_path.exists():
            return CrudiniEditPreview(
                file_path=request.file_path,
                original_content="",
                proposed_content="",
                diff="",
                diff_colored="",
                is_valid_ini=False,
            )

        # Read original content
        original_content = file_path.read_text()

        # Apply operations using Python configparser for preview
        # (we don't want to actually modify the file)
        try:
            parser = configparser.ConfigParser()
            parser.read_string(original_content)

            for op in request.operations:
                if op.kind == "set" and op.parameter and op.value:
                    if not parser.has_section(op.section):
                        parser.add_section(op.section)
                    parser.set(op.section, op.parameter, op.value)
                elif op.kind == "del":
                    if op.parameter:
                        parser.remove_option(op.section, op.parameter)
                    else:
                        parser.remove_section(op.section)

            # Write to string
            import io

            output = io.StringIO()
            parser.write(output)
            proposed_content = output.getvalue()

        except Exception:
            return CrudiniEditPreview(
                file_path=request.file_path,
                original_content=original_content,
                proposed_content="",
                diff="",
                diff_colored="",
                is_valid_ini=False,
            )

        # Generate diffs
        diff = unified_diff(original_content, proposed_content, request.file_path)
        diff_col = colored_diff(original_content, proposed_content, request.file_path)

        # Check privileges
        requires_privilege = False
        try:
            import os

            if file_path.exists():
                requires_privilege = not os.access(file_path, os.W_OK)
        except Exception:
            pass

        return CrudiniEditPreview(
            file_path=request.file_path,
            original_content=original_content,
            proposed_content=proposed_content,
            diff=diff,
            diff_colored=diff_col,
            requires_privilege=requires_privilege,
            is_valid_ini=True,
        )

    def execute_edit(self, request: CrudiniEditRequest) -> CrudiniEditResult:
        """Execute INI file edits.

        Args:
            request: The edit request to execute.

        Returns:
            CrudiniEditResult with the operation outcome.
        """
        from elle.ops.augeas.backup import backup_file, restore_file
        from elle.ops.augeas.diff import colored_diff, unified_diff

        file_path = Path(request.file_path)
        backup_path: str | None = None

        # Check file exists
        if not file_path.exists():
            return CrudiniEditResult(
                success=False,
                file_path=request.file_path,
                error=f"File not found: {request.file_path}",
            )

        # Read original content
        original_content = file_path.read_text()

        # Create backup unless skipped
        if not request.skip_backup:
            try:
                backup_record = backup_file(request.file_path, domain="ini")
                backup_path = backup_record.backup_path
            except Exception as e:
                return CrudiniEditResult(
                    success=False,
                    file_path=request.file_path,
                    error=f"Failed to create backup: {e}",
                )

        # Dry run - just return preview
        if request.dry_run:
            preview = self.preview_edit(request)
            return CrudiniEditResult(
                success=True,
                file_path=request.file_path,
                diff=preview.diff,
                diff_colored=preview.diff_colored,
                backup_path=backup_path,
                validation_passed=preview.is_valid_ini,
            )

        # Execute operations
        changes: list[CrudiniChange] = []
        for op in request.operations:
            # Record old value for change tracking
            old_value = None
            if op.kind in ("set", "del") and op.parameter:
                old_value = self.get(request.file_path, op.section, op.parameter)

            # Execute the operation
            result = self.process(
                CrudiniRequest(
                    file_path=request.file_path,
                    operation=op,
                    description=request.description,
                )
            )

            if not result.success:
                # Rollback on failure
                if backup_path:
                    try:
                        restore_file(backup_path)
                        return CrudiniEditResult(
                            success=False,
                            file_path=request.file_path,
                            error=f"Operation failed, changes rolled back: {result.error}",
                            backup_path=backup_path,
                            rollback_applied=True,
                        )
                    except Exception as e:
                        return CrudiniEditResult(
                            success=False,
                            file_path=request.file_path,
                            error=f"Operation failed and rollback failed: {result.error} / {e}",
                            backup_path=backup_path,
                        )
                return CrudiniEditResult(
                    success=False,
                    file_path=request.file_path,
                    error=result.error,
                    backup_path=backup_path,
                )

            # Record the change
            changes.append(
                CrudiniChange(
                    section=op.section,
                    parameter=op.parameter,
                    old_value=old_value,
                    new_value=op.value if op.kind == "set" else None,
                    operation=op.kind,
                )
            )

        # Read modified content
        modified_content = file_path.read_text()

        # Validate unless skipped
        validation_passed = True
        if not request.skip_validation:
            validation_result = self.validate(request.file_path)
            validation_passed = validation_result.success

            if not validation_passed and backup_path:
                try:
                    restore_file(backup_path)
                    return CrudiniEditResult(
                        success=False,
                        file_path=request.file_path,
                        error="Validation failed, changes rolled back",
                        backup_path=backup_path,
                        validation_passed=False,
                        rollback_applied=True,
                    )
                except Exception as e:
                    return CrudiniEditResult(
                        success=False,
                        file_path=request.file_path,
                        error=f"Validation failed and rollback failed: {e}",
                        backup_path=backup_path,
                        validation_passed=False,
                    )

        # Generate diffs
        diff = unified_diff(original_content, modified_content, request.file_path)
        diff_col = colored_diff(original_content, modified_content, request.file_path)

        return CrudiniEditResult(
            success=True,
            file_path=request.file_path,
            diff=diff,
            diff_colored=diff_col,
            backup_path=backup_path,
            validation_passed=validation_passed,
            changes=tuple(changes),
            completed_at=datetime.utcnow(),
        )
