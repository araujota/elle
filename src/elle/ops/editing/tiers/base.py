"""Abstract base class for editing tiers.

All tier implementations must inherit from BaseTier and implement
the required methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from elle.ops.editing.models import (
    EditOperation,
    FileEditPreview,
    TierApplicability,
    TierEditResult,
    TierNumber,
    ValidationResult,
)


class BaseTier(ABC):
    """Abstract base class for editing tiers.

    Each tier must implement:
        - can_handle(): Check if this tier can handle the edit
        - validate_syntax(): Validate content syntax
        - preview(): Preview proposed changes
        - apply(): Apply the edit

    Subclasses should set:
        - tier_number: The tier number (0-5)
        - tier_name: Human-readable name
    """

    tier_number: TierNumber
    tier_name: str

    @abstractmethod
    def can_handle(
        self,
        file_path: Path,
        operation: EditOperation,
    ) -> TierApplicability:
        """Check if this tier can handle the given edit.

        Args:
            file_path: Path to the file to edit.
            operation: The edit operation to perform.

        Returns:
            TierApplicability with result and reason.
        """
        ...

    @abstractmethod
    def validate_syntax(
        self,
        content: str,
        file_path: Path,
    ) -> ValidationResult:
        """Validate the syntax of content for this tier's format.

        Args:
            content: Content to validate.
            file_path: Path context (for format detection).

        Returns:
            ValidationResult with outcome and any errors.
        """
        ...

    @abstractmethod
    def preview(
        self,
        file_path: Path,
        operation: EditOperation,
    ) -> FileEditPreview:
        """Preview the changes that would be made.

        Args:
            file_path: Path to the file to edit.
            operation: The edit operation to perform.

        Returns:
            FileEditPreview showing proposed changes.
        """
        ...

    @abstractmethod
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
        """Apply the edit operation.

        Args:
            file_path: Path to the file to edit.
            operation: The edit operation to perform.
            skip_backup: If True, don't create a backup.
            skip_validation: If True, don't validate after apply.
            dropin_name: For Tier 0, name for the drop-in file.
            dropin_priority: For Tier 0, priority number.

        Returns:
            TierEditResult with outcome.
        """
        ...

    def _create_success_result(
        self,
        file_path: str,
        diff: str,
        diff_colored: str,
        backup_path: str | None = None,
        validation: ValidationResult | None = None,
        tool_used: str | None = None,
    ) -> TierEditResult:
        """Create a successful edit result.

        Helper method for subclasses to create consistent results.
        """
        from elle.ops.editing.models import TIER_NAMES

        return TierEditResult(
            success=True,
            file_path=file_path,
            tier_used=self.tier_number,
            tier_name=TIER_NAMES.get(self.tier_number, self.tier_name),
            tier_selection_reason=f"Tier {self.tier_number} applied successfully",
            diff=diff,
            diff_colored=diff_colored,
            backup_path=backup_path,
            validation=validation,
            tool_used=tool_used,
        )

    def _create_failure_result(
        self,
        file_path: str,
        error: str,
        backup_path: str | None = None,
        rollback_applied: bool = False,
        requires_privilege: bool = False,
    ) -> TierEditResult:
        """Create a failed edit result.

        Helper method for subclasses to create consistent results.
        """
        from elle.ops.editing.models import TIER_NAMES

        return TierEditResult(
            success=False,
            file_path=file_path,
            tier_used=self.tier_number,
            tier_name=TIER_NAMES.get(self.tier_number, self.tier_name),
            tier_selection_reason=f"Tier {self.tier_number} failed",
            error=error,
            backup_path=backup_path,
            rollback_applied=rollback_applied,
            requires_privilege=requires_privilege,
        )
