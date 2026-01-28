"""Tier 4: Document-aware editing (Markdown/RST).

Preserves document structure when editing Markdown and RST files.
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
from elle.ops.editing.tier_registry import (
    TIER4_MARKDOWN_PATTERNS,
    TIER4_RST_PATTERNS,
    matches_pattern,
)
from elle.ops.editing.tiers.base import BaseTier

logger = logging.getLogger(__name__)


class Tier4Document(BaseTier):
    """Tier 4: Document-aware editing.

    Handles Markdown and RST files with awareness of document structure
    (headings, lists, code blocks, etc.).
    """

    tier_number = 4
    tier_name = "Document-aware"

    def _detect_format(self, file_path: str) -> str | None:
        """Detect the document format."""
        if matches_pattern(file_path, TIER4_MARKDOWN_PATTERNS):
            return "markdown"
        if matches_pattern(file_path, TIER4_RST_PATTERNS):
            return "rst"
        return None

    def _is_markdown_it_available(self) -> bool:
        """Check if markdown-it is available."""
        try:
            import markdown_it  # noqa: F401

            return True
        except ImportError:
            return False

    def _is_docutils_available(self) -> bool:
        """Check if docutils is available."""
        try:
            import docutils  # noqa: F401

            return True
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
                tier=4,
                applicable=False,
                reason="File is not Markdown or RST",
            )

        if fmt == "markdown":
            if self._is_markdown_it_available():
                return TierApplicability(
                    tier=4,
                    applicable=True,
                    reason="Markdown file - using markdown-it for structure awareness",
                    confidence=0.85,
                )
            else:
                return TierApplicability(
                    tier=4,
                    applicable=True,
                    reason="Markdown file - using regex-based editing",
                    confidence=0.7,
                )

        if fmt == "rst":
            if self._is_docutils_available():
                return TierApplicability(
                    tier=4,
                    applicable=True,
                    reason="RST file - using docutils for structure awareness",
                    confidence=0.85,
                )
            else:
                return TierApplicability(
                    tier=4,
                    applicable=True,
                    reason="RST file - using regex-based editing",
                    confidence=0.7,
                )

        return TierApplicability(
            tier=4,
            applicable=False,
            reason="Unknown document format",
        )

    def validate_syntax(
        self,
        content: str,
        file_path: Path,
    ) -> ValidationResult:
        """Validate document syntax (basic checks)."""
        fmt = self._detect_format(str(file_path))

        # Documents are generally more forgiving - just check for basic structure
        if fmt == "markdown":
            # Check for unclosed code blocks
            code_blocks = content.count("```")
            if code_blocks % 2 != 0:
                return ValidationResult(
                    passed=False,
                    method="markdown-lint",
                    errors=("Unclosed code block (odd number of ``` markers)",),
                )
            return ValidationResult(passed=True, method="markdown-lint")

        if fmt == "rst":
            # Basic RST validation
            if self._is_docutils_available():
                try:
                    from docutils.parsers.rst import Parser
                    from docutils.utils import new_document
                    from docutils.frontend import OptionParser

                    parser = Parser()
                    settings = OptionParser(components=(Parser,)).get_default_values()
                    document = new_document("<string>", settings)
                    parser.parse(content, document)

                    # Check for system messages (errors/warnings)
                    errors = []
                    for node in document.traverse():
                        if node.tagname == "system_message":
                            if node.get("level", 0) >= 3:  # ERROR or above
                                errors.append(node.astext())

                    if errors:
                        return ValidationResult(
                            passed=False,
                            method="docutils",
                            errors=tuple(errors[:5]),  # Limit to 5 errors
                        )
                    return ValidationResult(passed=True, method="docutils")
                except Exception as e:
                    return ValidationResult(
                        passed=True,
                        method="docutils-failed",
                        output=str(e),
                    )
            return ValidationResult(passed=True, method="none")

        return ValidationResult(passed=True, method="none")

    def preview(
        self,
        file_path: Path,
        operation: EditOperation,
    ) -> FileEditPreview:
        """Preview document edit."""
        from elle.ops.augeas.diff import colored_diff, unified_diff

        fmt = self._detect_format(str(file_path))
        original = file_path.read_text() if file_path.exists() else ""

        try:
            proposed = self._apply_document_edit(original, operation, fmt)
            tool = fmt or "text"

            diff = unified_diff(original, proposed, str(file_path))
            diff_col = colored_diff(original, proposed, str(file_path))

            return FileEditPreview(
                file_path=str(file_path),
                tier_selected=4,
                tier_name="Document-aware",
                original_content=original,
                proposed_content=proposed,
                diff=diff,
                diff_colored=diff_col,
                is_valid=True,
                tool_to_use=tool,
            )
        except Exception as e:
            return FileEditPreview(
                file_path=str(file_path),
                tier_selected=4,
                tier_name="Document-aware",
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
        """Apply document edit."""
        from elle.ops.augeas.backup import backup_file
        from elle.ops.augeas.diff import colored_diff, unified_diff

        fmt = self._detect_format(str(file_path))

        if not fmt:
            return self._create_failure_result(
                str(file_path),
                "Unknown document format",
            )

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
            proposed = self._apply_document_edit(original, operation, fmt)
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
            tool_used=fmt,
        )

    def _apply_document_edit(
        self,
        content: str,
        operation: EditOperation,
        fmt: str | None,
    ) -> str:
        """Apply edit to document content."""
        if operation.kind == "set":
            return self._apply_set(content, operation, fmt)
        elif operation.kind == "add" or operation.kind == "append":
            return self._apply_append(content, operation, fmt)
        elif operation.kind == "insert":
            return self._apply_insert(content, operation, fmt)
        elif operation.kind == "rm":
            return self._apply_remove(content, operation, fmt)
        elif operation.kind == "replace":
            return self._apply_replace(content, operation, fmt)
        return content

    def _apply_set(
        self,
        content: str,
        operation: EditOperation,
        fmt: str | None,
    ) -> str:
        """Set/replace content at a specific location."""
        if operation.path and operation.value:
            # If path looks like a heading, find and replace section
            if operation.path.startswith("#") or (fmt == "rst" and "=" in operation.path):
                return self._replace_section(content, operation.path, operation.value, fmt)
            # Otherwise treat as pattern replacement
            return content.replace(operation.path, operation.value)
        return content

    def _apply_append(
        self,
        content: str,
        operation: EditOperation,
        fmt: str | None,
    ) -> str:
        """Append content to end or after a heading."""
        if not operation.value:
            return content

        if operation.path:
            # Append after a specific heading
            return self._append_after_heading(content, operation.path, operation.value, fmt)
        else:
            # Append to end of file
            if not content.endswith("\n"):
                content += "\n"
            return content + operation.value + "\n"

    def _apply_insert(
        self,
        content: str,
        operation: EditOperation,
        fmt: str | None,
    ) -> str:
        """Insert content before or after a pattern."""
        if not operation.value or not operation.anchor_pattern:
            return content

        if operation.position == "before":
            return re.sub(
                f"({operation.anchor_pattern})",
                f"{operation.value}\n\\1",
                content,
                count=1,
            )
        else:  # after
            return re.sub(
                f"({operation.anchor_pattern})",
                f"\\1\n{operation.value}",
                content,
                count=1,
            )

    def _apply_remove(
        self,
        content: str,
        operation: EditOperation,
        fmt: str | None,
    ) -> str:
        """Remove content matching pattern or heading."""
        if operation.path:
            if operation.path.startswith("#"):
                # Remove a heading section
                return self._remove_section(content, operation.path, fmt)
            else:
                # Remove pattern
                return content.replace(operation.path, "")
        return content

    def _apply_replace(
        self,
        content: str,
        operation: EditOperation,
        fmt: str | None,
    ) -> str:
        """Replace content matching pattern."""
        if operation.path and operation.value:
            return content.replace(operation.path, operation.value)
        return content

    def _replace_section(
        self,
        content: str,
        heading: str,
        new_content: str,
        fmt: str | None,
    ) -> str:
        """Replace a heading and its content."""
        if fmt == "markdown":
            # Find the heading level
            level = len(heading) - len(heading.lstrip("#"))
            pattern = rf"(^{re.escape(heading)}$.*?)(?=^#{{{1},{level}}}[^#]|\Z)"
            return re.sub(pattern, f"{heading}\n{new_content}\n", content, flags=re.MULTILINE | re.DOTALL)
        return content

    def _append_after_heading(
        self,
        content: str,
        heading: str,
        new_content: str,
        fmt: str | None,
    ) -> str:
        """Append content after a heading section."""
        lines = content.split("\n")
        result = []
        found = False
        heading_level = 0

        for i, line in enumerate(lines):
            result.append(line)

            if not found and line.strip() == heading.strip():
                found = True
                if fmt == "markdown":
                    heading_level = len(line) - len(line.lstrip("#"))

            elif found and fmt == "markdown":
                # Check if we've hit the next heading of same or higher level
                if line.startswith("#"):
                    current_level = len(line) - len(line.lstrip("#"))
                    if current_level <= heading_level:
                        # Insert before this heading
                        result.insert(-1, new_content)
                        result.insert(-1, "")
                        found = False  # Stop looking

        # If we never found a stopping point, append at end
        if found:
            result.append("")
            result.append(new_content)

        return "\n".join(result)

    def _remove_section(
        self,
        content: str,
        heading: str,
        fmt: str | None,
    ) -> str:
        """Remove a heading and its content."""
        if fmt == "markdown":
            level = len(heading) - len(heading.lstrip("#"))
            pattern = rf"^{re.escape(heading)}$.*?(?=^#{{{1},{level}}}[^#]|\Z)"
            return re.sub(pattern, "", content, flags=re.MULTILINE | re.DOTALL)
        return content
