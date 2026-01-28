"""Tier 2: Structured Data editing (JSON/YAML/TOML).

Uses jq, yq, or Python libraries for editing structured data files.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from elle.ops.editing.models import (
    EditOperation,
    FileEditPreview,
    TierApplicability,
    TierEditResult,
    ValidationResult,
)
from elle.ops.editing.tier_registry import (
    TIER2_JSON_PATTERNS,
    TIER2_TOML_PATTERNS,
    TIER2_YAML_PATTERNS,
    matches_pattern,
)
from elle.ops.editing.tiers.base import BaseTier

logger = logging.getLogger(__name__)


class Tier2Structured(BaseTier):
    """Tier 2: Structured Data editing.

    Handles JSON, YAML, and TOML files using:
        - jq for JSON (falls back to Python json)
        - yq for YAML (falls back to ruamel.yaml or PyYAML)
        - Python toml/tomllib for TOML
    """

    tier_number = 2
    tier_name = "Structured Data"

    def _detect_format(self, file_path: str) -> str | None:
        """Detect the structured data format."""
        if matches_pattern(file_path, TIER2_JSON_PATTERNS):
            return "json"
        if matches_pattern(file_path, TIER2_YAML_PATTERNS):
            return "yaml"
        if matches_pattern(file_path, TIER2_TOML_PATTERNS):
            return "toml"
        return None

    def _is_jq_available(self) -> bool:
        """Check if jq is available."""
        try:
            from elle.ops.jq import is_available

            return is_available()
        except ImportError:
            return False

    def _is_yq_available(self) -> bool:
        """Check if yq is available."""
        try:
            from elle.ops.yq import is_available

            return is_available()
        except ImportError:
            return False

    def can_handle(
        self,
        file_path: Path,
        operation: EditOperation,
    ) -> TierApplicability:
        """Check if this tier can handle the file."""
        fmt = self._detect_format(str(file_path))

        if not fmt:
            return TierApplicability(
                tier=2,
                applicable=False,
                reason="File is not JSON, YAML, or TOML",
            )

        if fmt == "json":
            if self._is_jq_available():
                return TierApplicability(
                    tier=2,
                    applicable=True,
                    reason="JSON file - using jq",
                    confidence=0.95,
                )
            else:
                return TierApplicability(
                    tier=2,
                    applicable=True,
                    reason="JSON file - using Python json (jq not available)",
                    confidence=0.85,
                )

        if fmt == "yaml":
            if self._is_yq_available():
                return TierApplicability(
                    tier=2,
                    applicable=True,
                    reason="YAML file - using yq",
                    confidence=0.95,
                )
            else:
                return TierApplicability(
                    tier=2,
                    applicable=True,
                    reason="YAML file - using Python yaml library",
                    confidence=0.8,
                )

        if fmt == "toml":
            return TierApplicability(
                tier=2,
                applicable=True,
                reason="TOML file - using Python toml library",
                confidence=0.9,
            )

        return TierApplicability(
            tier=2,
            applicable=False,
            reason="Unknown structured data format",
        )

    def validate_syntax(
        self,
        content: str,
        file_path: Path,
    ) -> ValidationResult:
        """Validate structured data syntax."""
        fmt = self._detect_format(str(file_path))

        if fmt == "json":
            try:
                json.loads(content)
                return ValidationResult(passed=True, method="json.loads")
            except json.JSONDecodeError as e:
                return ValidationResult(
                    passed=False,
                    method="json.loads",
                    errors=(str(e),),
                )

        if fmt == "yaml":
            try:
                import yaml

                yaml.safe_load(content)
                return ValidationResult(passed=True, method="yaml.safe_load")
            except yaml.YAMLError as e:
                return ValidationResult(
                    passed=False,
                    method="yaml.safe_load",
                    errors=(str(e),),
                )

        if fmt == "toml":
            try:
                try:
                    import tomllib

                    tomllib.loads(content)
                except ImportError:
                    import toml

                    toml.loads(content)
                return ValidationResult(passed=True, method="toml.loads")
            except Exception as e:
                return ValidationResult(
                    passed=False,
                    method="toml.loads",
                    errors=(str(e),),
                )

        return ValidationResult(
            passed=True,
            method="none",
            output="Unknown format",
        )

    def preview(
        self,
        file_path: Path,
        operation: EditOperation,
    ) -> FileEditPreview:
        """Preview structured data edit."""
        from elle.ops.augeas.diff import colored_diff, unified_diff

        fmt = self._detect_format(str(file_path))
        original = file_path.read_text() if file_path.exists() else ""

        try:
            if fmt == "json":
                proposed = self._apply_json_edit(original, operation)
            elif fmt == "yaml":
                proposed = self._apply_yaml_edit(original, operation)
            elif fmt == "toml":
                proposed = self._apply_toml_edit(original, operation)
            else:
                proposed = original

            diff = unified_diff(original, proposed, str(file_path))
            diff_col = colored_diff(original, proposed, str(file_path))

            return FileEditPreview(
                file_path=str(file_path),
                tier_selected=2,
                tier_name="Structured Data",
                original_content=original,
                proposed_content=proposed,
                diff=diff,
                diff_colored=diff_col,
                is_valid=True,
                tool_to_use=fmt,
            )
        except Exception as e:
            return FileEditPreview(
                file_path=str(file_path),
                tier_selected=2,
                tier_name="Structured Data",
                original_content=original,
                proposed_content="",
                diff="",
                diff_colored="",
                is_valid=False,
                tool_to_use=fmt,
            )

    async def apply(
        self,
        file_path: Path,
        operation: EditOperation,
        *,
        skip_backup: bool = False,
        skip_validation: bool = False,
        dropin_name: str | None = None,
        dropin_priority: int = 99,
    ) -> TierEditResult:
        """Apply structured data edit."""
        from elle.ops.augeas.backup import backup_file, restore_file
        from elle.ops.augeas.diff import colored_diff, unified_diff

        fmt = self._detect_format(str(file_path))

        if not fmt:
            return self._create_failure_result(
                str(file_path),
                "Unknown structured data format",
            )

        # Read original
        if not file_path.exists():
            return self._create_failure_result(
                str(file_path),
                "File not found",
            )

        original = file_path.read_text()

        # Create backup
        backup_path = None
        if not skip_backup:
            try:
                record = backup_file(str(file_path), domain=fmt)
                backup_path = record.backup_path
            except Exception as e:
                return self._create_failure_result(
                    str(file_path),
                    f"Backup failed: {e}",
                )

        # Apply edit
        try:
            if fmt == "json":
                proposed = self._apply_json_edit(original, operation)
                tool = "jq" if self._is_jq_available() else "python-json"
            elif fmt == "yaml":
                proposed = self._apply_yaml_edit(original, operation)
                tool = "yq" if self._is_yq_available() else "python-yaml"
            elif fmt == "toml":
                proposed = self._apply_toml_edit(original, operation)
                tool = "python-toml"
            else:
                return self._create_failure_result(
                    str(file_path),
                    "Unknown format",
                    backup_path=backup_path,
                )
        except Exception as e:
            return self._create_failure_result(
                str(file_path),
                f"Edit failed: {e}",
                backup_path=backup_path,
            )

        # Validate
        validation = None
        if not skip_validation:
            validation = self.validate_syntax(proposed, file_path)
            if not validation.passed:
                return self._create_failure_result(
                    str(file_path),
                    f"Validation failed: {'; '.join(validation.errors)}",
                    backup_path=backup_path,
                )

        # Write
        try:
            file_path.write_text(proposed)
        except PermissionError:
            return self._create_failure_result(
                str(file_path),
                "Permission denied",
                backup_path=backup_path,
                requires_privilege=True,
            )

        # Generate diff
        diff = unified_diff(original, proposed, str(file_path))
        diff_col = colored_diff(original, proposed, str(file_path))

        return self._create_success_result(
            str(file_path),
            diff,
            diff_col,
            backup_path=backup_path,
            validation=validation,
            tool_used=tool,
        )

    def _apply_json_edit(self, content: str, operation: EditOperation) -> str:
        """Apply edit to JSON content."""
        if self._is_jq_available():
            from elle.ops.jq import JQEngine

            engine = JQEngine()

            if operation.kind == "set" and operation.path and operation.value is not None:
                return engine.set_value(content, operation.path, operation.value)
            elif operation.kind == "rm" and operation.path:
                return engine.delete_value(content, operation.path)
            else:
                # Use transform for complex operations
                filter_expr = self._build_jq_filter(operation)
                result = engine.transform(content, filter_expr)
                if result.success:
                    return result.output
                raise ValueError(result.error)
        else:
            # Python fallback
            data = json.loads(content)
            data = self._apply_dict_edit(data, operation)
            return json.dumps(data, indent=2) + "\n"

    def _apply_yaml_edit(self, content: str, operation: EditOperation) -> str:
        """Apply edit to YAML content."""
        if self._is_yq_available():
            from elle.ops.yq import YQEngine

            engine = YQEngine()

            if operation.kind == "set" and operation.path and operation.value is not None:
                return engine.set_value(content, operation.path, operation.value)
            elif operation.kind == "rm" and operation.path:
                return engine.delete_value(content, operation.path)
            else:
                filter_expr = self._build_jq_filter(operation)
                result = engine.transform(content, filter_expr)
                if result.success:
                    return result.output
                raise ValueError(result.error)
        else:
            # Python fallback
            try:
                from ruamel.yaml import YAML

                yaml = YAML()
                yaml.preserve_quotes = True
                from io import StringIO

                data = yaml.load(StringIO(content))
                data = self._apply_dict_edit(data, operation)
                output = StringIO()
                yaml.dump(data, output)
                return output.getvalue()
            except ImportError:
                import yaml

                data = yaml.safe_load(content)
                data = self._apply_dict_edit(data, operation)
                return yaml.dump(data, default_flow_style=False)

    def _apply_toml_edit(self, content: str, operation: EditOperation) -> str:
        """Apply edit to TOML content."""
        try:
            import tomllib

            data = tomllib.loads(content)
        except ImportError:
            import toml

            data = toml.loads(content)

        data = self._apply_dict_edit(data, operation)

        try:
            import toml

            return toml.dumps(data)
        except ImportError:
            # tomllib is read-only, need toml for writing
            raise ImportError("toml library needed for writing TOML files")

    def _apply_dict_edit(self, data: dict, operation: EditOperation) -> dict:
        """Apply edit operation to a dictionary."""
        if not operation.path:
            return data

        # Parse path (e.g., ".config.timeout" or "config.timeout")
        path = operation.path.lstrip(".")
        keys = path.split(".")

        if operation.kind == "set" and operation.value is not None:
            # Navigate and set
            current = data
            for key in keys[:-1]:
                if key not in current:
                    current[key] = {}
                current = current[key]
            # Try to parse value as JSON for complex types
            try:
                current[keys[-1]] = json.loads(operation.value)
            except (json.JSONDecodeError, TypeError):
                current[keys[-1]] = operation.value

        elif operation.kind == "rm":
            # Navigate and delete
            current = data
            for key in keys[:-1]:
                if key not in current:
                    return data  # Path doesn't exist
                current = current[key]
            if keys[-1] in current:
                del current[keys[-1]]

        return data

    def _build_jq_filter(self, operation: EditOperation) -> str:
        """Build a jq filter expression from an operation."""
        if operation.kind == "set" and operation.path and operation.value is not None:
            # Try to determine if value should be a string or parsed
            try:
                json.loads(operation.value)
                return f"{operation.path} = {operation.value}"
            except (json.JSONDecodeError, TypeError):
                return f'{operation.path} = "{operation.value}"'
        elif operation.kind == "rm" and operation.path:
            return f"del({operation.path})"
        else:
            return "."  # Identity
