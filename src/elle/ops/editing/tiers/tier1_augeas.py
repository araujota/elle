"""Tier 1: Augeas grammar-based editing.

Wraps the existing elle.ops.augeas module for structured
configuration file editing using Augeas lenses.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from elle.ops.editing.models import (
    EditOperation,
    FileEditPreview,
    TierApplicability,
    TierEditResult,
    ValidationResult,
)
from elle.ops.editing.tier_registry import TIER1_AUGEAS_PATTERNS, matches_pattern
from elle.ops.editing.tiers.base import BaseTier

if TYPE_CHECKING:
    from elle.ops.augeas import AugeasOp

logger = logging.getLogger(__name__)


class Tier1Augeas(BaseTier):
    """Tier 1: Augeas grammar-based editing.

    Uses Augeas lenses to parse, modify, and write configuration
    files while preserving structure and comments.

    Advantages:
        - Grammar-aware parsing
        - Structure preservation
        - Validation via lens
        - Wide format support
    """

    tier_number = 1
    tier_name = "Augeas"

    def _is_available(self) -> bool:
        """Check if Augeas is available."""
        try:
            from elle.ops.augeas import is_available

            return is_available()
        except ImportError:
            return False

    def can_handle(
        self,
        file_path: Path,
        operation: EditOperation,
    ) -> TierApplicability:
        """Check if Augeas can handle this file."""
        if not self._is_available():
            return TierApplicability(
                tier=1,
                applicable=True,
                reason="Augeas not installed",
                tool_available=False,
                missing_tool="python-augeas",
            )

        if not matches_pattern(str(file_path), TIER1_AUGEAS_PATTERNS):
            return TierApplicability(
                tier=1,
                applicable=False,
                reason="File does not match Augeas-supported patterns",
            )

        # Check if Augeas has a lens
        try:
            from elle.ops.augeas import detect_lens

            lens = detect_lens(str(file_path))
            if lens:
                return TierApplicability(
                    tier=1,
                    applicable=True,
                    reason=f"Augeas lens '{lens}' available",
                    confidence=0.9,
                )
            else:
                return TierApplicability(
                    tier=1,
                    applicable=False,
                    reason="No Augeas lens found for this file type",
                )
        except Exception as e:
            return TierApplicability(
                tier=1,
                applicable=False,
                reason=f"Augeas lens detection failed: {e}",
            )

    def validate_syntax(
        self,
        content: str,
        file_path: Path,
    ) -> ValidationResult:
        """Validate using Augeas lens parsing."""
        if not self._is_available():
            return ValidationResult(
                passed=True,
                method="skipped",
                output="Augeas not available",
            )

        try:
            from elle.ops.augeas import validate_config

            result = validate_config(str(file_path))
            return ValidationResult(
                passed=result.valid if hasattr(result, "valid") else True,
                method="augeas",
                output=str(result),
            )
        except Exception as e:
            return ValidationResult(
                passed=False,
                method="augeas",
                output=str(e),
                errors=(str(e),),
            )

    def preview(
        self,
        file_path: Path,
        operation: EditOperation,
    ) -> FileEditPreview:
        """Preview Augeas edit."""

        if not self._is_available():
            return FileEditPreview(
                file_path=str(file_path),
                tier_selected=1,
                tier_name="Augeas",
                original_content="",
                proposed_content="",
                diff="",
                diff_colored="",
                is_valid=False,
                tool_to_use="augeas",
            )

        try:
            from elle.ops.augeas import AugeasEditRequest, preview_edit

            # Convert our operation to Augeas operation
            aug_ops = self._convert_to_augeas_ops(operation)

            request = AugeasEditRequest(
                file_path=str(file_path),
                operations=tuple(aug_ops),
                description=f"Tier 1 edit: {operation.kind}",
                dry_run=True,
            )

            result = preview_edit(request)

            return FileEditPreview(
                file_path=str(file_path),
                tier_selected=1,
                tier_name="Augeas",
                original_content=result.original_content,
                proposed_content=result.proposed_content,
                diff=result.diff,
                diff_colored=result.diff_colored,
                is_valid=True,
                requires_privilege=result.requires_privilege,
                tool_to_use="augeas",
            )
        except Exception:
            # If preview fails, return empty preview
            original = file_path.read_text() if file_path.exists() else ""
            return FileEditPreview(
                file_path=str(file_path),
                tier_selected=1,
                tier_name="Augeas",
                original_content=original,
                proposed_content="",
                diff="",
                diff_colored="",
                is_valid=False,
                tool_to_use="augeas",
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
        """Apply edit using Augeas."""
        if not self._is_available():
            return self._create_failure_result(
                str(file_path),
                "Augeas not available",
            )

        try:
            from elle.ops.augeas import AugeasEditRequest, execute_edit

            # Convert operation to Augeas ops
            aug_ops = self._convert_to_augeas_ops(operation)

            request = AugeasEditRequest(
                file_path=str(file_path),
                operations=tuple(aug_ops),
                description=f"Tier 1 edit: {operation.kind}",
                skip_backup=skip_backup,
                skip_validation=skip_validation,
            )

            result = await execute_edit(request)

            if result.success:
                validation = None
                if not skip_validation:
                    validation = ValidationResult(
                        passed=result.validation_passed,
                        method="augeas",
                        output=result.validation_output,
                    )

                return self._create_success_result(
                    str(file_path),
                    result.diff,
                    result.diff_colored,
                    backup_path=result.backup_path,
                    validation=validation,
                    tool_used="augeas",
                )
            else:
                return self._create_failure_result(
                    str(file_path),
                    result.error or "Augeas edit failed",
                    backup_path=result.backup_path,
                    rollback_applied=result.rollback_applied,
                    requires_privilege=result.requires_privilege,
                )

        except Exception as e:
            return self._create_failure_result(
                str(file_path),
                f"Augeas error: {e}",
            )

    def _convert_to_augeas_ops(self, operation: EditOperation) -> list[AugeasOp]:
        """Convert EditOperation to Augeas operations."""
        from elle.ops.augeas import AugeasOp

        ops = []

        if operation.kind == "set":
            if operation.path and operation.value:
                ops.append(
                    AugeasOp(
                        kind="set",
                        path=operation.path,
                        value=operation.value,
                    )
                )
        elif operation.kind == "rm":
            if operation.path:
                ops.append(
                    AugeasOp(
                        kind="rm",
                        path=operation.path,
                    )
                )
        elif operation.kind in ("add", "insert"):
            if operation.path and operation.value:
                ops.append(
                    AugeasOp(
                        kind="ins",
                        path=operation.path,
                        label=operation.key or "new",
                        before=(operation.position == "before"),
                    )
                )
                if operation.value:
                    ops.append(
                        AugeasOp(
                            kind="set",
                            path=f"{operation.path}/../{operation.key or 'new'}",
                            value=operation.value,
                        )
                    )

        return ops
