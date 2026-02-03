"""Core yq wrapper for YAML processing.

Provides a high-level interface to the yq command-line tool for
YAML querying, transformation, and validation.

Supports both yq variants:
- kislyuk/yq (Python, jq wrapper): Uses jq syntax, requires jq installed
- mikefarah/yq (Go): Native Go implementation with extended syntax

This module auto-detects and adapts to whichever variant is installed.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from elle.ops.yq.models import (
    YQChange,
    YQEditPreview,
    YQEditRequest,
    YQEditResult,
    YQFilterError,
    YQRequest,
    YQResult,
    YQStatus,
    YQUnavailableError,
)

logger = logging.getLogger(__name__)

# Cache for availability check
_yq_path: str | None = None
_yq_version: str | None = None
_yq_variant: str | None = None
_yq_checked: bool = False


def _find_yq() -> str | None:
    """Find the yq executable."""
    global _yq_path, _yq_checked
    if not _yq_checked:
        _yq_path = shutil.which("yq")
        _yq_checked = True
    return _yq_path


def _detect_variant() -> str | None:
    """Detect which yq variant is installed.

    Returns:
        'kislyuk' for Python jq-wrapper, 'mikefarah' for Go version, or None.
    """
    global _yq_variant
    if _yq_variant is not None:
        return _yq_variant

    yq_path = _find_yq()
    if yq_path is None:
        return None

    try:
        # mikefarah/yq has --version flag
        result = subprocess.run(
            [yq_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout.lower() + result.stderr.lower()

        if "mikefarah" in output or "yq (https://github.com/mikefarah/yq/)" in output:
            _yq_variant = "mikefarah"
        elif "version" in output and "kislyuk" in output:
            _yq_variant = "kislyuk"
        elif result.returncode == 0 and "yq" in output:
            # Try to detect by behavior - kislyuk uses jq syntax
            test_result = subprocess.run(
                [yq_path, "-r", ".", "-"],
                input="key: value",
                capture_output=True,
                text=True,
                timeout=5,
            )
            if test_result.returncode == 0:
                _yq_variant = "kislyuk"
            else:
                # Try mikefarah syntax
                test_result = subprocess.run(
                    [yq_path, ".", "-"],
                    input="key: value",
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if test_result.returncode == 0:
                    _yq_variant = "mikefarah"

    except Exception:
        pass

    return _yq_variant


def is_available() -> bool:
    """Check if yq is available on the system.

    Returns:
        True if yq can be executed.
    """
    return _find_yq() is not None


def get_version() -> str | None:
    """Get the yq version string.

    Returns:
        Version string or None if unavailable.
    """
    global _yq_version
    if _yq_version is not None:
        return _yq_version

    yq_path = _find_yq()
    if yq_path is None:
        return None

    try:
        result = subprocess.run(
            [yq_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            _yq_version = result.stdout.strip()
            return _yq_version
    except Exception:
        pass
    return None


def get_status() -> YQStatus:
    """Get the current status of yq availability.

    Returns:
        YQStatus with availability information.
    """
    yq_path = _find_yq()
    return YQStatus(
        available=yq_path is not None,
        version=get_version(),
        path=yq_path,
        variant=_detect_variant(),
    )


class YQEngine:
    """Wrapper around the yq command-line tool for YAML processing.

    Provides methods to query, transform, and validate YAML data
    using jq-style filter expressions.

    Automatically adapts to the installed yq variant (kislyuk or mikefarah).

    Usage:
        engine = YQEngine()
        result = engine.query('key: value', '.key')
        # result.output == 'value'

        # Transform YAML
        result = engine.transform('a: 1', '.b = 2')
        # result.output contains 'a: 1\\nb: 2'

        # Query a file
        result = engine.query_file("/etc/netplan/01-netcfg.yaml", '.network.ethernets')

    Context manager usage:
        with YQEngine() as engine:
            result = engine.query_file("/path/to/config.yaml", '.settings')
    """

    def __init__(self) -> None:
        """Initialize the yq engine.

        Raises:
            YQUnavailableError: If yq is not installed.
        """
        self._yq_path = _find_yq()
        if self._yq_path is None:
            raise YQUnavailableError(
                "yq is not installed. Install with: pip install yq (requires jq) or snap install yq"
            )
        self._variant = _detect_variant()
        logger.debug(f"Initialized YQEngine with yq at {self._yq_path} (variant: {self._variant})")

    def __enter__(self) -> YQEngine:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        pass  # No cleanup needed

    # =========================================================================
    # Core Operations
    # =========================================================================

    def _run_yq(
        self,
        filter_expr: str,
        input_data: str | None = None,
        *,
        yaml_output: bool = True,
        raw_output: bool = False,
        from_file: str | None = None,
    ) -> tuple[bool, str, str, int]:
        """Run yq with the given filter and options.

        Args:
            filter_expr: The yq/jq filter expression.
            input_data: YAML string input (if not using from_file).
            yaml_output: Output YAML (True) or JSON (False).
            raw_output: Output raw strings.
            from_file: Read input from file instead of input_data.

        Returns:
            Tuple of (success, stdout, stderr, exit_code).
        """
        if self._yq_path is None:
            raise YQUnavailableError("yq is not available")

        cmd = [self._yq_path]

        if self._variant == "mikefarah":
            # mikefarah/yq syntax
            if raw_output:
                cmd.append("-r")
            if not yaml_output:
                cmd.extend(["-o", "json"])
            cmd.append(filter_expr)
            if from_file:
                cmd.append(from_file)
        else:
            # kislyuk/yq syntax (jq-based)
            if raw_output:
                cmd.append("-r")
            if yaml_output:
                cmd.append("-y")  # YAML output
            cmd.append(filter_expr)
            if from_file:
                cmd.append(from_file)

        try:
            result = subprocess.run(
                cmd,
                input=input_data if not from_file else None,
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
            return (False, "", "yq operation timed out", -1)
        except Exception as e:
            return (False, "", str(e), -1)

    def query(
        self,
        yaml_input: str,
        filter_expr: str,
        *,
        yaml_output: bool = True,
        raw_output: bool = False,
    ) -> YQResult:
        """Query YAML data using a yq filter.

        Args:
            yaml_input: YAML string to query.
            filter_expr: yq filter expression (e.g., '.key', '.items[].name').
            yaml_output: If True, output YAML. If False, output JSON.
            raw_output: If True, output raw strings without YAML/JSON encoding.

        Returns:
            YQResult with query output.
        """
        success, stdout, stderr, exit_code = self._run_yq(
            filter_expr,
            yaml_input,
            yaml_output=yaml_output,
            raw_output=raw_output,
        )

        parsed = None
        if success and not raw_output:
            try:
                import yaml

                parsed = yaml.safe_load(stdout)
            except Exception:
                pass

        return YQResult(
            success=success,
            output=stdout.rstrip("\n"),
            parsed_output=parsed,
            error=stderr if not success else None,
            exit_code=exit_code,
            filter_used=filter_expr,
        )

    def query_file(
        self,
        file_path: str | Path,
        filter_expr: str,
        *,
        yaml_output: bool = True,
        raw_output: bool = False,
    ) -> YQResult:
        """Query a YAML file using a yq filter.

        Args:
            file_path: Path to the YAML file.
            filter_expr: yq filter expression.
            yaml_output: If True, output YAML. If False, output JSON.
            raw_output: If True, output raw strings.

        Returns:
            YQResult with query output.
        """
        path_str = str(file_path)

        if not Path(path_str).exists():
            return YQResult(
                success=False,
                error=f"File not found: {path_str}",
                exit_code=1,
                filter_used=filter_expr,
            )

        success, stdout, stderr, exit_code = self._run_yq(
            filter_expr,
            from_file=path_str,
            yaml_output=yaml_output,
            raw_output=raw_output,
        )

        parsed = None
        if success and not raw_output:
            try:
                import yaml

                parsed = yaml.safe_load(stdout)
            except Exception:
                pass

        return YQResult(
            success=success,
            output=stdout.rstrip("\n"),
            parsed_output=parsed,
            error=stderr if not success else None,
            exit_code=exit_code,
            filter_used=filter_expr,
        )

    def transform(
        self,
        yaml_input: str,
        filter_expr: str,
        *,
        yaml_output: bool = True,
    ) -> YQResult:
        """Transform YAML data using a yq filter.

        Args:
            yaml_input: YAML string to transform.
            filter_expr: yq filter expression that modifies the input
                        (e.g., '.key = "new value"', 'del(.unwanted)').
            yaml_output: If True, output YAML. If False, output JSON.

        Returns:
            YQResult with transformed output.
        """
        success, stdout, stderr, exit_code = self._run_yq(
            filter_expr,
            yaml_input,
            yaml_output=yaml_output,
        )

        parsed = None
        if success:
            try:
                import yaml

                parsed = yaml.safe_load(stdout)
            except Exception:
                pass

        return YQResult(
            success=success,
            output=stdout.rstrip("\n"),
            parsed_output=parsed,
            error=stderr if not success else None,
            exit_code=exit_code,
            filter_used=filter_expr,
        )

    def validate(self, yaml_input: str) -> YQResult:
        """Validate that input is valid YAML.

        Args:
            yaml_input: String to validate as YAML.

        Returns:
            YQResult with success=True if valid YAML.
        """
        # Use identity filter - if it succeeds, YAML is valid
        return self.query(yaml_input, ".")

    def validate_file(self, file_path: str | Path) -> YQResult:
        """Validate that a file contains valid YAML.

        Args:
            file_path: Path to the file to validate.

        Returns:
            YQResult with success=True if valid YAML.
        """
        return self.query_file(file_path, ".")

    # =========================================================================
    # High-Level Operations
    # =========================================================================

    def get_value(
        self,
        yaml_input: str,
        path: str,
    ) -> Any:
        """Get a value at a specific path.

        Args:
            yaml_input: YAML string to query.
            path: YAML/jq path (e.g., '.config.timeout', '.items[0].name').

        Returns:
            The value at the path, or None if not found.

        Raises:
            YQFilterError: If the path expression is invalid.
        """
        result = self.query(yaml_input, path, raw_output=False)
        if not result.success:
            raise YQFilterError(path, result.error)
        return result.parsed_output

    def set_value(
        self,
        yaml_input: str,
        path: str,
        value: Any,
    ) -> str:
        """Set a value at a specific path.

        Args:
            yaml_input: YAML string to modify.
            path: YAML/jq path (e.g., '.config.timeout').
            value: Value to set.

        Returns:
            Modified YAML string.

        Raises:
            YQFilterError: If the operation fails.
        """
        import json

        # Convert Python value to jq-compatible string
        if isinstance(value, str):
            value_expr = f'"{value}"'
        elif isinstance(value, bool):
            value_expr = "true" if value else "false"
        elif value is None:
            value_expr = "null"
        elif isinstance(value, (int, float)):
            value_expr = str(value)
        else:
            # For complex types, JSON encode
            value_expr = json.dumps(value)

        filter_expr = f"{path} = {value_expr}"
        result = self.transform(yaml_input, filter_expr)

        if not result.success:
            raise YQFilterError(filter_expr, result.error)

        return result.output

    def delete_value(
        self,
        yaml_input: str,
        path: str,
    ) -> str:
        """Delete a value at a specific path.

        Args:
            yaml_input: YAML string to modify.
            path: YAML/jq path to delete (e.g., '.config.deprecated').

        Returns:
            Modified YAML string.

        Raises:
            YQFilterError: If the operation fails.
        """
        filter_expr = f"del({path})"
        result = self.transform(yaml_input, filter_expr)

        if not result.success:
            raise YQFilterError(filter_expr, result.error)

        return result.output

    def process(self, request: YQRequest) -> YQResult:
        """Process a YQRequest.

        Args:
            request: The request to process.

        Returns:
            YQResult with the operation outcome.
        """
        # Determine input source
        if request.file_path:
            return self.query_file(
                request.file_path,
                request.filter,
                yaml_output=request.yaml_output,
                raw_output=request.raw_output,
            )
        elif request.yaml_input:
            return self.query(
                request.yaml_input,
                request.filter,
                yaml_output=request.yaml_output,
                raw_output=request.raw_output,
            )
        else:
            return YQResult(
                success=False,
                error="No input source specified (file_path or yaml_input required)",
                exit_code=1,
                filter_used=request.filter,
            )

    # =========================================================================
    # File Editing
    # =========================================================================

    def preview_edit(self, request: YQEditRequest) -> YQEditPreview:
        """Preview changes that would be made to a YAML file.

        Args:
            request: The edit request to preview.

        Returns:
            YQEditPreview showing the proposed changes.
        """
        from elle.ops.augeas.diff import colored_diff, unified_diff

        file_path = Path(request.file_path)

        # Check file exists
        if not file_path.exists():
            return YQEditPreview(
                file_path=request.file_path,
                original_content="",
                proposed_content="",
                diff="",
                diff_colored="",
                is_valid_yaml=False,
            )

        # Read original content
        original_content = file_path.read_text()

        # Apply transformation
        result = self.transform(original_content, request.filter)

        if not result.success:
            return YQEditPreview(
                file_path=request.file_path,
                original_content=original_content,
                proposed_content="",
                diff="",
                diff_colored="",
                is_valid_yaml=False,
            )

        proposed_content = result.output
        if not proposed_content.endswith("\n"):
            proposed_content += "\n"

        # Generate diffs
        diff = unified_diff(original_content, proposed_content, request.file_path)
        diff_col = colored_diff(original_content, proposed_content, request.file_path)

        # Check privileges
        requires_privilege = False
        try:
            if file_path.exists():
                import os

                requires_privilege = not os.access(file_path, os.W_OK)
        except Exception:
            pass

        return YQEditPreview(
            file_path=request.file_path,
            original_content=original_content,
            proposed_content=proposed_content,
            diff=diff,
            diff_colored=diff_col,
            requires_privilege=requires_privilege,
            is_valid_yaml=True,
        )

    def execute_edit(self, request: YQEditRequest) -> YQEditResult:
        """Execute a YAML file edit.

        Args:
            request: The edit request to execute.

        Returns:
            YQEditResult with the operation outcome.
        """
        from elle.ops.augeas.backup import backup_file, restore_file
        from elle.ops.augeas.diff import colored_diff, unified_diff

        file_path = Path(request.file_path)
        backup_path: str | None = None

        # Check file exists
        if not file_path.exists():
            return YQEditResult(
                success=False,
                file_path=request.file_path,
                error=f"File not found: {request.file_path}",
            )

        # Read original content
        original_content = file_path.read_text()

        # Create backup unless skipped
        if not request.skip_backup:
            try:
                backup_record = backup_file(request.file_path, domain="yaml")
                backup_path = backup_record.backup_path
            except Exception as e:
                return YQEditResult(
                    success=False,
                    file_path=request.file_path,
                    error=f"Failed to create backup: {e}",
                )

        # Apply transformation
        result = self.transform(original_content, request.filter)

        if not result.success:
            return YQEditResult(
                success=False,
                file_path=request.file_path,
                error=result.error,
                backup_path=backup_path,
            )

        proposed_content = result.output
        if not proposed_content.endswith("\n"):
            proposed_content += "\n"

        # Dry run - just return preview info
        if request.dry_run:
            diff = unified_diff(original_content, proposed_content, request.file_path)
            diff_col = colored_diff(original_content, proposed_content, request.file_path)
            return YQEditResult(
                success=True,
                file_path=request.file_path,
                diff=diff,
                diff_colored=diff_col,
                backup_path=backup_path,
                validation_passed=True,
            )

        # Write the new content
        try:
            file_path.write_text(proposed_content)
        except PermissionError:
            return YQEditResult(
                success=False,
                file_path=request.file_path,
                error=f"Permission denied: {request.file_path}",
                backup_path=backup_path,
                requires_privilege=True,
            )
        except Exception as e:
            return YQEditResult(
                success=False,
                file_path=request.file_path,
                error=f"Failed to write file: {e}",
                backup_path=backup_path,
            )

        # Validate the result unless skipped
        validation_passed = True
        if not request.skip_validation:
            validation_result = self.validate_file(request.file_path)
            validation_passed = validation_result.success

            # Rollback on validation failure
            if not validation_passed and backup_path:
                try:
                    restore_file(backup_path)
                    return YQEditResult(
                        success=False,
                        file_path=request.file_path,
                        error="Validation failed, changes rolled back",
                        backup_path=backup_path,
                        validation_passed=False,
                        rollback_applied=True,
                    )
                except Exception as e:
                    return YQEditResult(
                        success=False,
                        file_path=request.file_path,
                        error=f"Validation failed and rollback failed: {e}",
                        backup_path=backup_path,
                        validation_passed=False,
                    )

        # Generate diffs
        diff = unified_diff(original_content, proposed_content, request.file_path)
        diff_col = colored_diff(original_content, proposed_content, request.file_path)

        # Detect changes
        changes = self._detect_changes(original_content, proposed_content)

        return YQEditResult(
            success=True,
            file_path=request.file_path,
            diff=diff,
            diff_colored=diff_col,
            backup_path=backup_path,
            validation_passed=validation_passed,
            changes=tuple(changes),
            completed_at=datetime.now(timezone.utc),
        )

    def _detect_changes(
        self,
        original: str,
        modified: str,
    ) -> list[YQChange]:
        """Detect changes between original and modified YAML.

        Args:
            original: Original YAML string.
            modified: Modified YAML string.

        Returns:
            List of detected changes.
        """
        changes: list[YQChange] = []

        try:
            import yaml

            orig_data = yaml.safe_load(original)
            mod_data = yaml.safe_load(modified)

            def _compare(
                orig: Any,
                mod: Any,
                path: str = "$",
            ) -> None:
                if type(orig) is not type(mod):
                    changes.append(
                        YQChange(
                            path=path,
                            old_value=orig,
                            new_value=mod,
                            operation="type_change",
                        )
                    )
                    return

                if isinstance(orig, dict):
                    all_keys = set(orig.keys()) | set(mod.keys())
                    for key in all_keys:
                        child_path = f"{path}.{key}"
                        if key not in orig:
                            changes.append(
                                YQChange(
                                    path=child_path,
                                    old_value=None,
                                    new_value=mod[key],
                                    operation="add",
                                )
                            )
                        elif key not in mod:
                            changes.append(
                                YQChange(
                                    path=child_path,
                                    old_value=orig[key],
                                    new_value=None,
                                    operation="delete",
                                )
                            )
                        else:
                            _compare(orig[key], mod[key], child_path)
                elif isinstance(orig, list):
                    if len(orig) != len(mod):
                        changes.append(
                            YQChange(
                                path=path,
                                old_value=orig,
                                new_value=mod,
                                operation="array_resize",
                            )
                        )
                    else:
                        for i, (o, m) in enumerate(zip(orig, mod, strict=False)):
                            _compare(o, m, f"{path}[{i}]")
                else:
                    if orig != mod:
                        changes.append(
                            YQChange(
                                path=path,
                                old_value=orig,
                                new_value=mod,
                                operation="modify",
                            )
                        )

            _compare(orig_data, mod_data)
        except Exception:
            pass

        return changes
