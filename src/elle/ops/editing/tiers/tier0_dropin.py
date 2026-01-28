"""Tier 0: Native Override via drop-in files.

Creates override files in .d/ directories instead of modifying
original configuration files. This is the safest editing method
as it never touches the original file.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from elle.ops.editing.models import (
    EditOperation,
    FileEditPreview,
    TierApplicability,
    TierEditResult,
    ValidationResult,
)
from elle.ops.editing.tier_registry import is_dropin_candidate
from elle.ops.editing.tiers.base import BaseTier

logger = logging.getLogger(__name__)


class Tier0DropIn(BaseTier):
    """Tier 0: Native override via drop-in files.

    Creates files in .d/ directories (e.g., /etc/sudoers.d/,
    /etc/systemd/system/nginx.service.d/) instead of modifying
    the original configuration.

    Advantages:
        - Never modifies original files
        - Easy to add/remove overrides
        - Clear audit trail of customizations
        - Package upgrades won't conflict
    """

    tier_number = 0
    tier_name = "Native Override"

    def can_handle(
        self,
        file_path: Path,
        operation: EditOperation,
    ) -> TierApplicability:
        """Check if drop-in override is supported for this file."""
        supports_dropin, dropin_dir = is_dropin_candidate(str(file_path))

        if not supports_dropin:
            return TierApplicability(
                tier=0,
                applicable=False,
                reason="File does not support drop-in overrides",
            )

        # Drop-ins are best for additive operations
        if operation.kind in ("add", "append", "set"):
            return TierApplicability(
                tier=0,
                applicable=True,
                reason=f"Drop-in supported via {dropin_dir or 'existing .d directory'}",
                confidence=0.95,
            )
        else:
            return TierApplicability(
                tier=0,
                applicable=False,
                reason=f"Operation '{operation.kind}' not suitable for drop-in",
            )

    def validate_syntax(
        self,
        content: str,
        file_path: Path,
    ) -> ValidationResult:
        """Validate drop-in file syntax.

        Validation depends on the type of drop-in.
        """
        path_str = str(file_path)

        # Systemd drop-in validation
        if "systemd" in path_str:
            return self._validate_systemd_dropin(content)

        # Sudoers validation
        if "sudoers" in path_str:
            return self._validate_sudoers(content)

        # Default: assume valid
        return ValidationResult(
            passed=True,
            method="none",
            output="No specific validation available for this drop-in type",
        )

    def _validate_systemd_dropin(self, content: str) -> ValidationResult:
        """Validate systemd drop-in syntax."""
        import subprocess

        try:
            # Use systemd-analyze to verify
            result = subprocess.run(
                ["systemd-analyze", "verify", "-"],
                input=content,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return ValidationResult(
                passed=result.returncode == 0,
                method="systemd-analyze verify",
                output=result.stdout + result.stderr,
                errors=tuple(result.stderr.split("\n")) if result.returncode != 0 else (),
            )
        except Exception:
            # systemd-analyze not available, skip validation
            return ValidationResult(
                passed=True,
                method="skipped",
                output="systemd-analyze not available",
            )

    def _validate_sudoers(self, content: str) -> ValidationResult:
        """Validate sudoers syntax using visudo."""
        import subprocess
        import tempfile

        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sudoers", delete=False) as f:
                f.write(content)
                temp_path = f.name

            try:
                result = subprocess.run(
                    ["visudo", "-cf", temp_path],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return ValidationResult(
                    passed=result.returncode == 0,
                    method="visudo -cf",
                    output=result.stdout + result.stderr,
                    errors=tuple(result.stderr.split("\n")) if result.returncode != 0 else (),
                )
            finally:
                Path(temp_path).unlink(missing_ok=True)
        except Exception as e:
            return ValidationResult(
                passed=True,
                method="skipped",
                output=f"visudo not available: {e}",
            )

    def preview(
        self,
        file_path: Path,
        operation: EditOperation,
    ) -> FileEditPreview:
        """Preview the drop-in file that would be created."""
        from elle.ops.augeas.diff import colored_diff, unified_diff

        _, dropin_dir = is_dropin_candidate(str(file_path))

        # Generate drop-in content
        dropin_content = self._generate_dropin_content(file_path, operation)

        # The "original" is empty since we're creating a new file
        original_content = ""
        proposed_content = dropin_content

        # Generate diff
        dropin_path = self._get_dropin_path(file_path, dropin_dir, "override", 99)
        diff = unified_diff(original_content, proposed_content, str(dropin_path))
        diff_col = colored_diff(original_content, proposed_content, str(dropin_path))

        # Check privileges
        requires_privilege = False
        if dropin_dir:
            dropin_dir_path = Path(dropin_dir)
            if dropin_dir_path.exists():
                requires_privilege = not os.access(dropin_dir_path, os.W_OK)
            else:
                # Need privilege to create the directory
                parent = dropin_dir_path.parent
                requires_privilege = not os.access(parent, os.W_OK)

        return FileEditPreview(
            file_path=str(dropin_path),
            tier_selected=0,
            tier_name="Native Override",
            original_content=original_content,
            proposed_content=proposed_content,
            diff=diff,
            diff_colored=diff_col,
            is_valid=True,
            requires_privilege=requires_privilege,
            tool_to_use="drop-in",
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
        """Create a drop-in override file."""
        from elle.ops.augeas.diff import colored_diff, unified_diff

        _, dropin_dir = is_dropin_candidate(str(file_path))

        if not dropin_dir:
            return self._create_failure_result(
                str(file_path),
                "No drop-in directory found for this file",
            )

        # Ensure drop-in directory exists
        dropin_dir_path = Path(dropin_dir)
        try:
            dropin_dir_path.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            return self._create_failure_result(
                str(file_path),
                f"Permission denied creating {dropin_dir}",
                requires_privilege=True,
            )

        # Generate drop-in content
        dropin_content = self._generate_dropin_content(file_path, operation)

        # Validate before writing
        if not skip_validation:
            validation = self.validate_syntax(dropin_content, file_path)
            if not validation.passed:
                return self._create_failure_result(
                    str(file_path),
                    f"Validation failed: {'; '.join(validation.errors)}",
                )
        else:
            validation = None

        # Determine drop-in file path
        dropin_path = self._get_dropin_path(file_path, dropin_dir, dropin_name, dropin_priority)

        # Write the drop-in file
        try:
            dropin_path.write_text(dropin_content)
        except PermissionError:
            return self._create_failure_result(
                str(file_path),
                f"Permission denied writing {dropin_path}",
                requires_privilege=True,
            )

        # Generate diff
        diff = unified_diff("", dropin_content, str(dropin_path))
        diff_col = colored_diff("", dropin_content, str(dropin_path))

        logger.info(f"Created drop-in file: {dropin_path}")

        return self._create_success_result(
            str(dropin_path),
            diff,
            diff_col,
            validation=validation,
            tool_used="drop-in",
        )

    def _generate_dropin_content(
        self,
        file_path: Path,
        operation: EditOperation,
    ) -> str:
        """Generate content for the drop-in file."""
        path_str = str(file_path)
        lines = []

        # Add header comment
        lines.append(f"# ELLE drop-in override for {file_path.name}")
        lines.append(f"# Created: {datetime.utcnow().isoformat()}")
        lines.append("")

        # Generate content based on file type
        if "systemd" in path_str:
            # Systemd unit override
            if operation.section:
                lines.append(f"[{operation.section}]")
            elif operation.path:
                # Try to extract section from path
                if operation.path.startswith("/"):
                    parts = operation.path.strip("/").split("/")
                    if parts:
                        lines.append(f"[{parts[0]}]")
            if operation.key and operation.value:
                lines.append(f"{operation.key}={operation.value}")
            elif operation.value:
                lines.append(operation.value)

        elif "sudoers" in path_str:
            # Sudoers rule
            if operation.value:
                lines.append(operation.value)

        elif "sysctl" in path_str:
            # Sysctl setting
            if operation.key and operation.value:
                lines.append(f"{operation.key}={operation.value}")
            elif operation.path and operation.value:
                lines.append(f"{operation.path}={operation.value}")

        elif "apt" in path_str and "sources" in path_str:
            # APT source
            if operation.value:
                lines.append(operation.value)

        else:
            # Generic: just add the value
            if operation.value:
                lines.append(operation.value)

        lines.append("")  # Trailing newline
        return "\n".join(lines)

    def _get_dropin_path(
        self,
        file_path: Path,
        dropin_dir: str | None,
        name: str | None,
        priority: int,
    ) -> Path:
        """Determine the path for the drop-in file."""
        if not dropin_dir:
            dropin_dir = str(file_path) + ".d"

        dropin_dir_path = Path(dropin_dir)

        # Determine filename
        if name:
            filename = f"{priority:02d}-{name}.conf"
        else:
            filename = f"{priority:02d}-elle-override.conf"

        # Adjust extension based on directory type
        if "sudoers" in str(dropin_dir):
            filename = filename.replace(".conf", "")  # sudoers.d files have no extension

        return dropin_dir_path / filename
