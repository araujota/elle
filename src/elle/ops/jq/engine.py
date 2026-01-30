"""Core jq wrapper for JSON processing.

Provides a high-level interface to the jq command-line tool for
JSON querying, transformation, and validation.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from elle.ops.jq.models import (
    JQChange,
    JQEditPreview,
    JQEditRequest,
    JQEditResult,
    JQFilterError,
    JQRequest,
    JQResult,
    JQStatus,
    JQUnavailableError,
)

logger = logging.getLogger(__name__)

# Cache for availability check
_jq_path: str | None = None
_jq_version: str | None = None
_jq_checked: bool = False


def _find_jq() -> str | None:
    """Find the jq executable."""
    global _jq_path, _jq_checked
    if not _jq_checked:
        _jq_path = shutil.which("jq")
        _jq_checked = True
    return _jq_path


def is_available() -> bool:
    """Check if jq is available on the system.

    Returns:
        True if jq can be executed.
    """
    return _find_jq() is not None


def get_version() -> str | None:
    """Get the jq version string.

    Returns:
        Version string or None if unavailable.
    """
    global _jq_version
    if _jq_version is not None:
        return _jq_version

    jq_path = _find_jq()
    if jq_path is None:
        return None

    try:
        result = subprocess.run(
            [jq_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            _jq_version = result.stdout.strip()
            return _jq_version
    except Exception:
        pass
    return None


def get_status() -> JQStatus:
    """Get the current status of jq availability.

    Returns:
        JQStatus with availability information.
    """
    jq_path = _find_jq()
    return JQStatus(
        available=jq_path is not None,
        version=get_version(),
        path=jq_path,
    )


class JQEngine:
    """Wrapper around the jq command-line tool for JSON processing.

    Provides methods to query, transform, and validate JSON data
    using jq filter expressions.

    Usage:
        engine = JQEngine()
        result = engine.query('{"key": "value"}', '.key')
        # result.output == '"value"'

        # Transform JSON
        result = engine.transform('{"a": 1}', '.b = 2')
        # result.output == '{"a": 1, "b": 2}'

        # Validate JSON
        result = engine.validate('{"key": "value"}')
        # result.success == True

    Context manager usage:
        with JQEngine() as engine:
            result = engine.query_file("/etc/docker/daemon.json", ".storage-driver")
    """

    def __init__(self) -> None:
        """Initialize the jq engine.

        Raises:
            JQUnavailableError: If jq is not installed.
        """
        self._jq_path = _find_jq()
        if self._jq_path is None:
            raise JQUnavailableError(
                "jq is not installed. Install with: sudo apt install jq"
            )
        logger.debug(f"Initialized JQEngine with jq at {self._jq_path}")

    def __enter__(self) -> JQEngine:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        pass  # No cleanup needed

    # =========================================================================
    # Core Operations
    # =========================================================================

    def _run_jq(
        self,
        filter_expr: str,
        input_data: str | None = None,
        *,
        raw_output: bool = False,
        slurp: bool = False,
        compact: bool = False,
        sort_keys: bool = False,
        null_input: bool = False,
        from_file: str | None = None,
    ) -> tuple[bool, str, str, int]:
        """Run jq with the given filter and options.

        Args:
            filter_expr: The jq filter expression.
            input_data: JSON string input (if not using from_file).
            raw_output: Output raw strings (-r flag).
            slurp: Read input as array (-s flag).
            compact: Compact output (-c flag).
            sort_keys: Sort object keys (-S flag).
            null_input: Use null as input (-n flag).
            from_file: Read input from file instead of input_data.

        Returns:
            Tuple of (success, stdout, stderr, exit_code).
        """
        if self._jq_path is None:
            raise JQUnavailableError("jq is not available")

        cmd = [self._jq_path]

        # Add flags
        if raw_output:
            cmd.append("-r")
        if slurp:
            cmd.append("-s")
        if compact:
            cmd.append("-c")
        if sort_keys:
            cmd.append("-S")
        if null_input:
            cmd.append("-n")

        # Add the filter
        cmd.append(filter_expr)

        # Add input file if specified
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
            return (False, "", "jq operation timed out", -1)
        except Exception as e:
            return (False, "", str(e), -1)

    def query(
        self,
        json_input: str,
        filter_expr: str,
        *,
        raw_output: bool = False,
        slurp: bool = False,
    ) -> JQResult:
        """Query JSON data using a jq filter.

        Args:
            json_input: JSON string to query.
            filter_expr: jq filter expression (e.g., '.key', '.[] | .name').
            raw_output: If True, output raw strings without JSON encoding.
            slurp: If True, read entire input as array.

        Returns:
            JQResult with query output.
        """
        success, stdout, stderr, exit_code = self._run_jq(
            filter_expr,
            json_input,
            raw_output=raw_output,
            slurp=slurp,
        )

        parsed = None
        if success and not raw_output:
            try:
                # Try to parse multi-line output as stream
                lines = stdout.strip().split("\n")
                if len(lines) == 1:
                    parsed = json.loads(stdout)
                else:
                    parsed = [json.loads(line) for line in lines if line.strip()]
            except json.JSONDecodeError:
                pass

        return JQResult(
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
        raw_output: bool = False,
        slurp: bool = False,
    ) -> JQResult:
        """Query a JSON file using a jq filter.

        Args:
            file_path: Path to the JSON file.
            filter_expr: jq filter expression.
            raw_output: If True, output raw strings.
            slurp: If True, read entire input as array.

        Returns:
            JQResult with query output.
        """
        path_str = str(file_path)

        if not Path(path_str).exists():
            return JQResult(
                success=False,
                error=f"File not found: {path_str}",
                exit_code=1,
                filter_used=filter_expr,
            )

        success, stdout, stderr, exit_code = self._run_jq(
            filter_expr,
            from_file=path_str,
            raw_output=raw_output,
            slurp=slurp,
        )

        parsed = None
        if success and not raw_output:
            try:
                lines = stdout.strip().split("\n")
                if len(lines) == 1:
                    parsed = json.loads(stdout)
                else:
                    parsed = [json.loads(line) for line in lines if line.strip()]
            except json.JSONDecodeError:
                pass

        return JQResult(
            success=success,
            output=stdout.rstrip("\n"),
            parsed_output=parsed,
            error=stderr if not success else None,
            exit_code=exit_code,
            filter_used=filter_expr,
        )

    def transform(
        self,
        json_input: str,
        filter_expr: str,
        *,
        sort_keys: bool = False,
        compact: bool = False,
    ) -> JQResult:
        """Transform JSON data using a jq filter.

        Args:
            json_input: JSON string to transform.
            filter_expr: jq filter expression that modifies the input
                        (e.g., '.key = "new value"', 'del(.unwanted)').
            sort_keys: If True, sort object keys in output.
            compact: If True, output compact JSON.

        Returns:
            JQResult with transformed JSON.
        """
        success, stdout, stderr, exit_code = self._run_jq(
            filter_expr,
            json_input,
            sort_keys=sort_keys,
            compact=compact,
        )

        parsed = None
        if success:
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                pass

        return JQResult(
            success=success,
            output=stdout.rstrip("\n"),
            parsed_output=parsed,
            error=stderr if not success else None,
            exit_code=exit_code,
            filter_used=filter_expr,
        )

    def validate(self, json_input: str) -> JQResult:
        """Validate that input is valid JSON.

        Args:
            json_input: String to validate as JSON.

        Returns:
            JQResult with success=True if valid JSON.
        """
        # Use identity filter - if it succeeds, JSON is valid
        return self.query(json_input, ".")

    def validate_file(self, file_path: str | Path) -> JQResult:
        """Validate that a file contains valid JSON.

        Args:
            file_path: Path to the file to validate.

        Returns:
            JQResult with success=True if valid JSON.
        """
        return self.query_file(file_path, ".")

    def format(
        self,
        json_input: str,
        *,
        indent: int = 2,
        sort_keys: bool = False,
    ) -> JQResult:
        """Format/pretty-print JSON.

        Args:
            json_input: JSON string to format.
            indent: Indentation level (0 for compact).
            sort_keys: If True, sort object keys.

        Returns:
            JQResult with formatted JSON.
        """
        # jq always pretty-prints by default
        # Use --tab or compact for different formatting
        return self.transform(
            json_input,
            ".",
            sort_keys=sort_keys,
            compact=(indent == 0),
        )

    # =========================================================================
    # High-Level Operations
    # =========================================================================

    def get_value(
        self,
        json_input: str,
        path: str,
    ) -> Any:
        """Get a value at a specific path.

        Args:
            json_input: JSON string to query.
            path: JSON path (e.g., '.config.timeout', '.items[0].name').

        Returns:
            The value at the path, or None if not found.

        Raises:
            JQFilterError: If the path expression is invalid.
        """
        result = self.query(json_input, path)
        if not result.success:
            if "null" in result.output.lower():
                return None
            raise JQFilterError(path, result.error)
        return result.parsed_output

    def set_value(
        self,
        json_input: str,
        path: str,
        value: Any,
    ) -> str:
        """Set a value at a specific path.

        Args:
            json_input: JSON string to modify.
            path: JSON path (e.g., '.config.timeout').
            value: Value to set. Can be a Python value or a JSON literal string.

        Returns:
            Modified JSON string.

        Raises:
            JQFilterError: If the operation fails.
        """
        # Convert Python value to jq-compatible string
        if isinstance(value, str):
            # Check if value is already a valid JSON literal
            try:
                json.loads(value)
                # It's valid JSON - use directly
                value_expr = value
            except json.JSONDecodeError:
                # Not valid JSON - treat as plain string and quote it
                value_expr = json.dumps(value)
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
        result = self.transform(json_input, filter_expr)

        if not result.success:
            raise JQFilterError(filter_expr, result.error)

        return result.output

    def delete_value(
        self,
        json_input: str,
        path: str,
    ) -> str:
        """Delete a value at a specific path.

        Args:
            json_input: JSON string to modify.
            path: JSON path to delete (e.g., '.config.deprecated').

        Returns:
            Modified JSON string.

        Raises:
            JQFilterError: If the operation fails.
        """
        filter_expr = f"del({path})"
        result = self.transform(json_input, filter_expr)

        if not result.success:
            raise JQFilterError(filter_expr, result.error)

        return result.output

    def has_path(
        self,
        json_input: str,
        path: str,
    ) -> bool:
        """Check if a path exists in the JSON.

        Args:
            json_input: JSON string to check.
            path: JSON path to check for.

        Returns:
            True if the path exists.
        """
        # Use has() for object keys or check array bounds
        filter_expr = f"({path} | type) != null"
        result = self.query(json_input, filter_expr, raw_output=True)
        return result.success and result.output.strip() == "true"

    def process(self, request: JQRequest) -> JQResult:
        """Process a JQRequest.

        Args:
            request: The request to process.

        Returns:
            JQResult with the operation outcome.
        """
        # Determine input source
        if request.file_path:
            return self.query_file(
                request.file_path,
                request.filter,
                raw_output=request.raw_output,
                slurp=request.slurp,
            )
        elif request.json_input:
            return self.query(
                request.json_input,
                request.filter,
                raw_output=request.raw_output,
                slurp=request.slurp,
            )
        elif request.null_input:
            # Generate JSON from null input
            success, stdout, stderr, exit_code = self._run_jq(
                request.filter,
                null_input=True,
                compact=request.compact,
                sort_keys=request.sort_keys,
            )
            return JQResult(
                success=success,
                output=stdout.rstrip("\n"),
                error=stderr if not success else None,
                exit_code=exit_code,
                filter_used=request.filter,
            )
        else:
            return JQResult(
                success=False,
                error="No input source specified (file_path, json_input, or null_input required)",
                exit_code=1,
                filter_used=request.filter,
            )

    # =========================================================================
    # File Editing
    # =========================================================================

    def preview_edit(self, request: JQEditRequest) -> JQEditPreview:
        """Preview changes that would be made to a JSON file.

        Args:
            request: The edit request to preview.

        Returns:
            JQEditPreview showing the proposed changes.
        """
        from elle.ops.augeas.diff import colored_diff, unified_diff

        file_path = Path(request.file_path)

        # Check file exists
        if not file_path.exists():
            return JQEditPreview(
                file_path=request.file_path,
                original_content="",
                proposed_content="",
                diff="",
                diff_colored="",
                is_valid_json=False,
            )

        # Read original content
        original_content = file_path.read_text()

        # Apply transformation
        result = self.transform(
            original_content,
            request.filter,
            sort_keys=request.sort_keys,
            compact=(request.indent == 0),
        )

        if not result.success:
            return JQEditPreview(
                file_path=request.file_path,
                original_content=original_content,
                proposed_content="",
                diff="",
                diff_colored="",
                is_valid_json=False,
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
            # Check if we can write
            if file_path.exists():
                requires_privilege = file_path.stat().st_uid != Path.cwd().stat().st_uid
        except Exception:
            pass

        return JQEditPreview(
            file_path=request.file_path,
            original_content=original_content,
            proposed_content=proposed_content,
            diff=diff,
            diff_colored=diff_col,
            requires_privilege=requires_privilege,
            is_valid_json=True,
        )

    def execute_edit(self, request: JQEditRequest) -> JQEditResult:
        """Execute a JSON file edit.

        Args:
            request: The edit request to execute.

        Returns:
            JQEditResult with the operation outcome.
        """
        from elle.ops.augeas.backup import backup_file, restore_file
        from elle.ops.augeas.diff import colored_diff, unified_diff

        file_path = Path(request.file_path)
        backup_path: str | None = None

        # Check file exists
        if not file_path.exists():
            return JQEditResult(
                success=False,
                file_path=request.file_path,
                error=f"File not found: {request.file_path}",
            )

        # Read original content
        original_content = file_path.read_text()

        # Create backup unless skipped
        if not request.skip_backup:
            try:
                backup_record = backup_file(request.file_path, domain="json")
                backup_path = backup_record.backup_path
            except Exception as e:
                return JQEditResult(
                    success=False,
                    file_path=request.file_path,
                    error=f"Failed to create backup: {e}",
                )

        # Apply transformation
        result = self.transform(
            original_content,
            request.filter,
            sort_keys=request.sort_keys,
            compact=(request.indent == 0),
        )

        if not result.success:
            return JQEditResult(
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
            return JQEditResult(
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
            return JQEditResult(
                success=False,
                file_path=request.file_path,
                error=f"Permission denied: {request.file_path}",
                backup_path=backup_path,
                requires_privilege=True,
            )
        except Exception as e:
            return JQEditResult(
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
                    return JQEditResult(
                        success=False,
                        file_path=request.file_path,
                        error="Validation failed, changes rolled back",
                        backup_path=backup_path,
                        validation_passed=False,
                        rollback_applied=True,
                    )
                except Exception as e:
                    return JQEditResult(
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

        return JQEditResult(
            success=True,
            file_path=request.file_path,
            diff=diff,
            diff_colored=diff_col,
            backup_path=backup_path,
            validation_passed=validation_passed,
            changes=tuple(changes),
            completed_at=datetime.utcnow(),
        )

    def _detect_changes(
        self,
        original: str,
        modified: str,
    ) -> list[JQChange]:
        """Detect changes between original and modified JSON.

        Args:
            original: Original JSON string.
            modified: Modified JSON string.

        Returns:
            List of detected changes.
        """
        changes: list[JQChange] = []

        try:
            orig_data = json.loads(original)
            mod_data = json.loads(modified)

            def _compare(
                orig: Any,
                mod: Any,
                path: str = "$",
            ) -> None:
                if type(orig) is not type(mod):
                    changes.append(
                        JQChange(
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
                                JQChange(
                                    path=child_path,
                                    old_value=None,
                                    new_value=mod[key],
                                    operation="add",
                                )
                            )
                        elif key not in mod:
                            changes.append(
                                JQChange(
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
                            JQChange(
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
                            JQChange(
                                path=path,
                                old_value=orig,
                                new_value=mod,
                                operation="modify",
                            )
                        )

            _compare(orig_data, mod_data)
        except json.JSONDecodeError:
            pass

        return changes
