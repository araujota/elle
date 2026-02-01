"""Pre-apply validation for generated configurations.

Validates LLM-generated configuration changes before they are
applied to ensure safety and correctness.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from elle.rag.confgen.domains import get_handler
from elle.rag.confgen.models import (
    ConfigGenResult,
    ConfigGenValidation,
    ValidationIssue,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Forbidden Paths
# =============================================================================

# Paths that should never be modified
FORBIDDEN_PATHS = frozenset(
    [
        "/etc/passwd",
        "/etc/shadow",
        "/etc/group",
        "/etc/gshadow",
        "/etc/sudoers",
        "/boot/grub/grub.cfg",
        "/boot/efi",
    ]
)

# Path prefixes that require extra caution
CRITICAL_PREFIXES = (
    "/boot",
    "/dev",
    "/proc",
    "/sys",
)

# Paths that require mandatory rollback hints
ROLLBACK_REQUIRED_PATHS = (
    "/etc/fstab",
    "/etc/netplan",
    "/etc/ssh/sshd_config",
    "/etc/network",
)


# =============================================================================
# Validator
# =============================================================================


class ConfigValidator:
    """Validates generated configuration before application.

    Performs multiple validation checks:
    - Path safety (not modifying critical system files)
    - Syntax validation (YAML, JSON, etc.)
    - Value safety (no shell injection)
    - Domain-specific validation
    - Rollback requirement checks
    """

    def validate(
        self,
        result: ConfigGenResult,
        current_content: str | None = None,
    ) -> ConfigGenValidation:
        """Validate a configuration generation result.

        Args:
            result: The generated configuration result.
            current_content: Current file content (for context).

        Returns:
            ConfigGenValidation with validation results.
        """
        issues: list[ValidationIssue] = []

        # Validate target path
        path_issues = self._validate_path(result.target_path)
        issues.extend(path_issues)

        # Validate operations or content
        if result.operations:
            op_issues = self._validate_operations(result)
            issues.extend(op_issues)

        if result.generated_content:
            content_issues = self._validate_content(result)
            issues.extend(content_issues)

        # Check rollback requirement
        rollback_issues = self._validate_rollback(result)
        issues.extend(rollback_issues)

        # Run domain-specific validation
        domain_issues = self._validate_domain(result, current_content)
        issues.extend(domain_issues)

        # Determine overall validity
        has_errors = any(i.severity == "error" for i in issues)
        syntax_valid = not any(i.severity == "error" and "syntax" in i.message.lower() for i in issues)
        path_valid = not any(i.severity == "error" and "path" in i.message.lower() for i in issues)
        values_valid = not any(
            i.severity == "error" and ("injection" in i.message.lower() or "value" in i.message.lower()) for i in issues
        )

        return ConfigGenValidation(
            valid=not has_errors,
            issues=tuple(issues),
            syntax_valid=syntax_valid,
            path_valid=path_valid,
            values_valid=values_valid,
        )

    def _validate_path(self, target_path: str) -> list[ValidationIssue]:
        """Validate the target path is safe to modify."""
        issues: list[ValidationIssue] = []
        path = Path(target_path)

        # Check for path traversal (before forbidden paths so traversal is always reported)
        if ".." in target_path:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message="Path contains traversal sequence (..)",
                    path=target_path,
                    suggestion="Use absolute paths",
                )
            )

        # Check forbidden paths
        resolved = str(path.resolve()) if path.exists() else target_path
        if resolved in FORBIDDEN_PATHS or target_path in FORBIDDEN_PATHS:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=f"Forbidden path: {target_path}",
                    path=target_path,
                    suggestion="This file cannot be modified directly",
                )
            )
            return issues

        # Check critical prefixes
        for prefix in CRITICAL_PREFIXES:
            if target_path.startswith(prefix):
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        message=f"Critical system path: {target_path}",
                        path=target_path,
                        suggestion="Exercise extreme caution with this path",
                    )
                )
                break

        # Check for absolute path
        if not target_path.startswith("/"):
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message="Path is not absolute",
                    path=target_path,
                    suggestion="Use absolute path for clarity",
                )
            )

        return issues

    def _validate_operations(
        self,
        result: ConfigGenResult,
    ) -> list[ValidationIssue]:
        """Validate configuration operations."""
        issues: list[ValidationIssue] = []

        for op in result.operations:
            # Check for empty path
            if not op.path:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message="Operation has empty path",
                    )
                )
                continue

            # Check for shell injection in values
            if op.value:
                injection_issues = self._check_shell_injection(op.value, op.path)
                issues.extend(injection_issues)

            # Check path syntax based on file type
            if result.file_type == "augeas":
                if not op.path.startswith("/"):
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            message=f"Augeas path should start with /: {op.path}",
                            path=op.path,
                        )
                    )
            elif result.file_type == "yaml":
                # YAML paths should use dot notation
                if op.path.startswith("/"):
                    issues.append(
                        ValidationIssue(
                            severity="info",
                            message="YAML path uses Augeas notation",
                            path=op.path,
                            suggestion="Consider using dot notation for YAML",
                        )
                    )

        return issues

    def _validate_content(
        self,
        result: ConfigGenResult,
    ) -> list[ValidationIssue]:
        """Validate generated content."""
        issues: list[ValidationIssue] = []
        content = result.generated_content or ""

        # Check for shell injection patterns
        injection_issues = self._check_shell_injection(content, "content")
        issues.extend(injection_issues)

        # Validate syntax based on file type
        if result.file_type == "yaml":
            try:
                from io import StringIO

                from ruamel.yaml import YAML
                from ruamel.yaml.error import YAMLError

                yaml_parser = YAML(typ="safe")
                yaml_parser.load(StringIO(content))
            except YAMLError as e:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Invalid YAML syntax: {e}",
                    )
                )
            except ImportError:
                pass

        elif result.file_type == "json":
            try:
                import json

                json.loads(content)
            except json.JSONDecodeError as e:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Invalid JSON syntax: {e}",
                    )
                )

        elif result.file_type == "toml":
            try:
                try:
                    import tomllib

                    tomllib.loads(content)
                except ImportError:
                    import toml

                    toml.loads(content)
            except Exception as e:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Invalid TOML syntax: {e}",
                    )
                )

        return issues

    def _validate_rollback(
        self,
        result: ConfigGenResult,
    ) -> list[ValidationIssue]:
        """Validate rollback hint for critical files."""
        issues: list[ValidationIssue] = []

        # Check if rollback is required but not provided
        for prefix in ROLLBACK_REQUIRED_PATHS:
            if result.target_path.startswith(prefix):
                if not result.rollback_hint:
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            message=f"No rollback hint for critical file: {result.target_path}",
                            suggestion="Add a rollback_hint for safety",
                        )
                    )
                break

        return issues

    def _validate_domain(
        self,
        result: ConfigGenResult,
        current_content: str | None,
    ) -> list[ValidationIssue]:
        """Run domain-specific validation."""
        handler = get_handler(result.domain)

        if result.operations:
            validation = handler.validate_operations(
                result.operations,
                current_content,
            )
            return list(validation.issues)

        if result.generated_content:
            validation = handler.validate_content(result.generated_content)
            return list(validation.issues)

        return []

    def _check_shell_injection(
        self,
        value: str,
        context: str,
    ) -> list[ValidationIssue]:
        """Check for potential shell injection patterns."""
        issues: list[ValidationIssue] = []

        dangerous_patterns = [
            (r"\$\([^)]+\)", "Command substitution $()"),
            (r"`[^`]+`", "Backtick command substitution"),
            (r";\s*[a-zA-Z]", "Command chaining with ;"),
            (r"\|\s*[a-zA-Z]", "Pipe to command"),
            (r"&&\s*[a-zA-Z]", "Command chaining with &&"),
            (r"\|\|\s*[a-zA-Z]", "Command chaining with ||"),
            (r">\s*/dev/", "Redirect to device"),
            (r"<\s*/dev/", "Read from device"),
            (r"eval\s", "eval command"),
            (r"exec\s", "exec command"),
        ]

        for pattern, description in dangerous_patterns:
            if re.search(pattern, value):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Potential shell injection: {description}",
                        path=context,
                        suggestion="Remove or escape shell metacharacters",
                    )
                )

        return issues


# =============================================================================
# Module-level Singleton
# =============================================================================

_validator: ConfigValidator | None = None


def get_validator() -> ConfigValidator:
    """Get the shared validator instance.

    Returns:
        ConfigValidator singleton.
    """
    global _validator
    if _validator is None:
        _validator = ConfigValidator()
    return _validator


def validate(
    result: ConfigGenResult,
    current_content: str | None = None,
) -> ConfigGenValidation:
    """Validate a configuration generation result (convenience function).

    Args:
        result: The generated configuration result.
        current_content: Current file content.

    Returns:
        ConfigGenValidation with results.
    """
    return get_validator().validate(result, current_content)
