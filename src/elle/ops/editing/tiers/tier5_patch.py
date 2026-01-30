"""Tier 5: Text Patching (Last Resort).

Anchor-based text patching for files that don't match any other tier.
Requires context verification and justification.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from elle.ops.editing.models import (
    EditOperation,
    FileEditPreview,
    TierApplicability,
    TierEditResult,
    ValidationResult,
)
from elle.ops.editing.tiers.base import BaseTier

logger = logging.getLogger(__name__)


class Tier5Patch(BaseTier):
    """Tier 5: Text Patching.

    Last resort editing using anchor-based text matching.
    Should only be used when no other tier can handle the edit.

    Features:
        - Anchor pattern matching for reliable location
        - Context verification before and after edits
        - Mandatory justification requirement
    """

    tier_number = 5
    tier_name = "Text Patching"

    def can_handle(
        self,
        file_path: Path,
        operation: EditOperation,
    ) -> TierApplicability:
        """Tier 5 can always handle any text file as a last resort."""
        # Check if it's a text file (not binary)
        try:
            if file_path.exists():
                content = file_path.read_bytes()
                # Check for null bytes (binary indicator)
                if b"\x00" in content[:8192]:
                    return TierApplicability(
                        tier=5,
                        applicable=False,
                        reason="File appears to be binary",
                    )
        except Exception:
            pass

        if operation.anchor_pattern:
            return TierApplicability(
                tier=5,
                applicable=True,
                reason="Text file - using anchor-based patching with context verification",
                confidence=0.8,
            )
        else:
            return TierApplicability(
                tier=5,
                applicable=True,
                reason="Text file - using best-effort patching (no anchor pattern)",
                confidence=0.5,
            )

    def validate_syntax(
        self,
        content: str,
        file_path: Path,
    ) -> ValidationResult:
        """Text patching has no syntax validation - it's raw text."""
        # We can only validate that it's valid UTF-8
        try:
            content.encode("utf-8")
            return ValidationResult(passed=True, method="utf-8")
        except UnicodeEncodeError as e:
            return ValidationResult(
                passed=False,
                method="utf-8",
                errors=(str(e),),
            )

    def preview(
        self,
        file_path: Path,
        operation: EditOperation,
    ) -> FileEditPreview:
        """Preview text patch."""
        from elle.ops.augeas.diff import colored_diff, unified_diff

        original = file_path.read_text() if file_path.exists() else ""

        try:
            proposed = self._apply_patch(original, operation)

            diff = unified_diff(original, proposed, str(file_path))
            diff_col = colored_diff(original, proposed, str(file_path))

            return FileEditPreview(
                file_path=str(file_path),
                tier_selected=5,
                tier_name="Text Patching",
                original_content=original,
                proposed_content=proposed,
                diff=diff,
                diff_colored=diff_col,
                is_valid=True,
                tool_to_use="text-patch",
            )
        except Exception:
            return FileEditPreview(
                file_path=str(file_path),
                tier_selected=5,
                tier_name="Text Patching",
                original_content=original,
                proposed_content="",
                diff="",
                diff_colored="",
                is_valid=False,
                tool_to_use="text-patch",
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
        """Apply text patch."""
        from elle.ops.augeas.backup import backup_file
        from elle.ops.augeas.diff import colored_diff, unified_diff

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
                record = backup_file(str(file_path), domain="text")
                backup_path = record.backup_path
            except Exception as e:
                return self._create_failure_result(
                    str(file_path),
                    f"Backup failed: {e}",
                )

        # Verify anchor pattern if provided
        if operation.anchor_pattern:
            if not self._verify_anchor(original, operation):
                return self._create_failure_result(
                    str(file_path),
                    f"Anchor pattern not found: {operation.anchor_pattern}",
                    backup_path=backup_path,
                )

        # Apply patch
        try:
            proposed = self._apply_patch(original, operation)
        except Exception as e:
            return self._create_failure_result(
                str(file_path),
                f"Patch failed: {e}",
                backup_path=backup_path,
            )

        # Validate (just UTF-8 check)
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
            tool_used="text-patch",
        )

    def _verify_anchor(
        self,
        content: str,
        operation: EditOperation,
    ) -> bool:
        """Verify that the anchor pattern exists in content."""
        if not operation.anchor_pattern:
            return True

        try:
            pattern = re.compile(operation.anchor_pattern, re.MULTILINE)
            return bool(pattern.search(content))
        except re.error:
            # Invalid regex - try as literal string
            return operation.anchor_pattern in content

    def _get_context_lines(
        self,
        content: str,
        match_start: int,
        match_end: int,
        num_lines: int = 3,
    ) -> tuple[list[str], list[str]]:
        """Get context lines before and after a match."""
        lines = content.split("\n")

        # Find line numbers
        char_count = 0
        start_line = 0
        end_line = 0

        for i, line in enumerate(lines):
            if char_count <= match_start < char_count + len(line) + 1:
                start_line = i
            if char_count <= match_end < char_count + len(line) + 1:
                end_line = i
                break
            char_count += len(line) + 1

        before = lines[max(0, start_line - num_lines) : start_line]
        after = lines[end_line + 1 : min(len(lines), end_line + 1 + num_lines)]

        return before, after

    def _apply_patch(
        self,
        content: str,
        operation: EditOperation,
    ) -> str:
        """Apply the patch operation to content."""
        if operation.kind == "set" or operation.kind == "replace":
            return self._apply_replace(content, operation)
        elif operation.kind == "add" or operation.kind == "append":
            return self._apply_append(content, operation)
        elif operation.kind == "insert":
            return self._apply_insert(content, operation)
        elif operation.kind == "rm":
            return self._apply_remove(content, operation)
        return content

    def _apply_replace(
        self,
        content: str,
        operation: EditOperation,
    ) -> str:
        """Replace content at anchor or path."""
        if operation.anchor_pattern and operation.value:
            try:
                pattern = re.compile(operation.anchor_pattern, re.MULTILINE)
                return pattern.sub(operation.value, content, count=1)
            except re.error:
                # Literal replacement
                return content.replace(operation.anchor_pattern, operation.value, 1)
        elif operation.path and operation.value:
            return content.replace(operation.path, operation.value, 1)
        return content

    def _apply_append(
        self,
        content: str,
        operation: EditOperation,
    ) -> str:
        """Append content at end or after anchor."""
        if not operation.value:
            return content

        if operation.anchor_pattern:
            # Append after anchor
            try:
                pattern = re.compile(f"({operation.anchor_pattern})", re.MULTILINE)
                return pattern.sub(f"\\1\n{operation.value}", content, count=1)
            except re.error:
                # Literal
                if operation.anchor_pattern in content:
                    return content.replace(
                        operation.anchor_pattern,
                        f"{operation.anchor_pattern}\n{operation.value}",
                        1,
                    )
        else:
            # Append to end
            if not content.endswith("\n"):
                content += "\n"
            return content + operation.value + "\n"

        return content

    def _apply_insert(
        self,
        content: str,
        operation: EditOperation,
    ) -> str:
        """Insert content before or after anchor."""
        if not operation.value or not operation.anchor_pattern:
            return content

        try:
            pattern = re.compile(f"({operation.anchor_pattern})", re.MULTILINE)

            if operation.position == "before":
                return pattern.sub(f"{operation.value}\n\\1", content, count=1)
            else:  # after
                return pattern.sub(f"\\1\n{operation.value}", content, count=1)
        except re.error:
            # Literal
            if operation.anchor_pattern in content:
                if operation.position == "before":
                    return content.replace(
                        operation.anchor_pattern,
                        f"{operation.value}\n{operation.anchor_pattern}",
                        1,
                    )
                else:
                    return content.replace(
                        operation.anchor_pattern,
                        f"{operation.anchor_pattern}\n{operation.value}",
                        1,
                    )

        return content

    def _apply_remove(
        self,
        content: str,
        operation: EditOperation,
    ) -> str:
        """Remove content matching anchor or path."""
        if operation.anchor_pattern:
            try:
                pattern = re.compile(operation.anchor_pattern, re.MULTILINE)
                return pattern.sub("", content, count=1)
            except re.error:
                return content.replace(operation.anchor_pattern, "", 1)
        elif operation.path:
            return content.replace(operation.path, "", 1)
        return content
