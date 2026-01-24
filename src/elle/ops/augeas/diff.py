"""Diff generation for config file changes.

Provides unified diff, side-by-side diff, and colored terminal output
for previewing configuration changes before applying.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


# ANSI color codes
class Colors:
    """ANSI color codes for terminal output."""

    RESET = "\033[0m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # Background colors
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"


class DiffStats(BaseModel):
    """Statistics about a diff."""

    model_config = ConfigDict(frozen=True)

    additions: int = Field(ge=0, default=0, description="Lines added")
    deletions: int = Field(ge=0, default=0, description="Lines removed")
    modifications: int = Field(ge=0, default=0, description="Lines modified")
    unchanged: int = Field(ge=0, default=0, description="Lines unchanged")

    @property
    def total_changes(self) -> int:
        """Total number of changed lines."""
        return self.additions + self.deletions + self.modifications


class DiffResult(BaseModel):
    """Result of a diff operation."""

    model_config = ConfigDict(frozen=True)

    unified: str = Field(default="", description="Unified diff format")
    colored: str = Field(default="", description="ANSI colored diff")
    side_by_side: str = Field(default="", description="Side-by-side view")
    stats: DiffStats = Field(default_factory=DiffStats)
    has_changes: bool = Field(default=False, description="Whether there are any changes")


class DiffGenerator:
    """Generate human-readable diffs for config file changes.

    Supports unified diff (patch format), side-by-side comparison,
    and ANSI-colored output for terminal display.

    Usage:
        generator = DiffGenerator()
        result = generator.diff(original, modified, "config.txt")
        print(result.colored)
    """

    def __init__(
        self,
        context_lines: int = 3,
        width: int = 80,
        tab_size: int = 4,
    ) -> None:
        """Initialize the diff generator.

        Args:
            context_lines: Lines of context around changes in unified diff.
            width: Terminal width for side-by-side view.
            tab_size: Tab expansion size.
        """
        self.context_lines = context_lines
        self.width = width
        self.tab_size = tab_size

    def diff(
        self,
        original: str,
        modified: str,
        file_path: str | Path | None = None,
    ) -> DiffResult:
        """Generate a complete diff result.

        Args:
            original: Original file content.
            modified: Modified file content.
            file_path: Optional file path for headers.

        Returns:
            DiffResult with all diff formats and statistics.
        """
        # Split into lines, keeping line endings
        original_lines = original.splitlines(keepends=True)
        modified_lines = modified.splitlines(keepends=True)

        # Ensure trailing newlines for proper diff output
        if original_lines and not original_lines[-1].endswith("\n"):
            original_lines[-1] += "\n"
        if modified_lines and not modified_lines[-1].endswith("\n"):
            modified_lines[-1] += "\n"

        # Generate file labels
        if file_path:
            from_file = f"a/{file_path}"
            to_file = f"b/{file_path}"
        else:
            from_file = "a/original"
            to_file = "b/modified"

        # Generate unified diff
        unified = self.unified(original_lines, modified_lines, from_file, to_file)

        # Check if there are any changes
        has_changes = original != modified

        # Generate colored version
        colored = self.colored(unified) if has_changes else ""

        # Generate side-by-side view
        side_by_side = self.side_by_side_lines(
            original_lines, modified_lines
        ) if has_changes else ""

        # Calculate statistics
        stats = self._calculate_stats(original_lines, modified_lines)

        return DiffResult(
            unified=unified,
            colored=colored,
            side_by_side=side_by_side,
            stats=stats,
            has_changes=has_changes,
        )

    def unified(
        self,
        original_lines: list[str],
        modified_lines: list[str],
        from_file: str = "a/original",
        to_file: str = "b/modified",
    ) -> str:
        """Generate unified diff format.

        Args:
            original_lines: Original file lines.
            modified_lines: Modified file lines.
            from_file: Label for original file.
            to_file: Label for modified file.

        Returns:
            Unified diff string.
        """
        diff = difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=from_file,
            tofile=to_file,
            n=self.context_lines,
        )
        return "".join(diff)

    def colored(self, diff: str) -> str:
        """Add ANSI colors to a unified diff.

        Args:
            diff: Unified diff string.

        Returns:
            Colored diff string.
        """
        if not diff:
            return ""

        lines = []
        for line in diff.splitlines(keepends=True):
            if line.startswith("+++") or line.startswith("---"):
                # File headers - bold
                lines.append(f"{Colors.BOLD}{line}{Colors.RESET}")
            elif line.startswith("+"):
                # Additions - green
                lines.append(f"{Colors.GREEN}{line}{Colors.RESET}")
            elif line.startswith("-"):
                # Deletions - red
                lines.append(f"{Colors.RED}{line}{Colors.RESET}")
            elif line.startswith("@@"):
                # Hunk headers - cyan
                lines.append(f"{Colors.CYAN}{line}{Colors.RESET}")
            else:
                # Context lines - normal
                lines.append(line)

        return "".join(lines)

    def side_by_side_lines(
        self,
        original_lines: list[str],
        modified_lines: list[str],
    ) -> str:
        """Generate side-by-side diff view.

        Args:
            original_lines: Original file lines.
            modified_lines: Modified file lines.

        Returns:
            Side-by-side diff string.
        """
        # Calculate column width
        col_width = (self.width - 3) // 2  # -3 for separator " | "

        # Use difflib.Differ for detailed comparison
        differ = difflib.Differ()
        diff = list(differ.compare(original_lines, modified_lines))

        lines = []
        header = f"{'Original':<{col_width}} | {'Modified':<{col_width}}"
        separator = "-" * col_width + "-+-" + "-" * col_width
        lines.append(header)
        lines.append(separator)

        left_line = ""
        right_line = ""

        for line in diff:
            code = line[0]
            text = line[2:].rstrip("\n")

            if code == " ":
                # Unchanged line
                left = self._truncate(text, col_width)
                right = self._truncate(text, col_width)
                lines.append(f"{left:<{col_width}} | {right:<{col_width}}")
            elif code == "-":
                # Removed line (show on left)
                left = self._truncate(text, col_width)
                lines.append(
                    f"{Colors.RED}{left:<{col_width}}{Colors.RESET} | "
                    f"{Colors.DIM}{'':<{col_width}}{Colors.RESET}"
                )
            elif code == "+":
                # Added line (show on right)
                right = self._truncate(text, col_width)
                lines.append(
                    f"{Colors.DIM}{'':<{col_width}}{Colors.RESET} | "
                    f"{Colors.GREEN}{right:<{col_width}}{Colors.RESET}"
                )
            elif code == "?":
                # Hint line (for character-level changes) - skip
                continue

        return "\n".join(lines)

    def inline(self, original: str, modified: str) -> str:
        """Generate inline diff with word-level highlighting.

        Shows changes within lines using character-level diff.

        Args:
            original: Original content.
            modified: Modified content.

        Returns:
            Inline diff with highlighted changes.
        """
        original_lines = original.splitlines()
        modified_lines = modified.splitlines()

        matcher = difflib.SequenceMatcher(None, original_lines, modified_lines)
        lines = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for line in original_lines[i1:i2]:
                    lines.append(f"  {line}")
            elif tag == "replace":
                # Show character-level diff for replaced lines
                for old_line, new_line in zip(
                    original_lines[i1:i2],
                    modified_lines[j1:j2],
                    strict=False,
                ):
                    char_diff = self._char_diff(old_line, new_line)
                    lines.append(f"{Colors.YELLOW}~ {char_diff}{Colors.RESET}")
                # Handle remaining lines if counts differ
                for line in original_lines[i1 + (j2 - j1):i2]:
                    lines.append(f"{Colors.RED}- {line}{Colors.RESET}")
                for line in modified_lines[j1 + (i2 - i1):j2]:
                    lines.append(f"{Colors.GREEN}+ {line}{Colors.RESET}")
            elif tag == "delete":
                for line in original_lines[i1:i2]:
                    lines.append(f"{Colors.RED}- {line}{Colors.RESET}")
            elif tag == "insert":
                for line in modified_lines[j1:j2]:
                    lines.append(f"{Colors.GREEN}+ {line}{Colors.RESET}")

        return "\n".join(lines)

    def summary(self, original: str, modified: str) -> str:
        """Generate a brief summary of changes.

        Args:
            original: Original content.
            modified: Modified content.

        Returns:
            Human-readable summary string.
        """
        stats = self._calculate_stats(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
        )

        if not stats.total_changes:
            return "No changes"

        parts = []
        if stats.additions:
            parts.append(f"{Colors.GREEN}+{stats.additions}{Colors.RESET} added")
        if stats.deletions:
            parts.append(f"{Colors.RED}-{stats.deletions}{Colors.RESET} removed")
        if stats.modifications:
            parts.append(f"{Colors.YELLOW}~{stats.modifications}{Colors.RESET} modified")

        return ", ".join(parts)

    # =========================================================================
    # Private Helpers
    # =========================================================================

    def _truncate(self, text: str, max_width: int) -> str:
        """Truncate text to fit within width.

        Args:
            text: Text to truncate.
            max_width: Maximum width.

        Returns:
            Truncated text with ellipsis if needed.
        """
        # Expand tabs
        text = text.expandtabs(self.tab_size)

        if len(text) <= max_width:
            return text

        return text[: max_width - 3] + "..."

    def _char_diff(self, old: str, new: str) -> str:
        """Generate character-level diff between two lines.

        Args:
            old: Old line content.
            new: New line content.

        Returns:
            Line with character changes highlighted.
        """
        matcher = difflib.SequenceMatcher(None, old, new)
        result = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                result.append(new[j1:j2])
            elif tag == "replace":
                result.append(f"{Colors.BG_RED}{old[i1:i2]}{Colors.RESET}")
                result.append(f"{Colors.BG_GREEN}{new[j1:j2]}{Colors.RESET}")
            elif tag == "delete":
                result.append(f"{Colors.BG_RED}{old[i1:i2]}{Colors.RESET}")
            elif tag == "insert":
                result.append(f"{Colors.BG_GREEN}{new[j1:j2]}{Colors.RESET}")

        return "".join(result)

    def _calculate_stats(
        self,
        original_lines: list[str],
        modified_lines: list[str],
    ) -> DiffStats:
        """Calculate diff statistics.

        Args:
            original_lines: Original file lines.
            modified_lines: Modified file lines.

        Returns:
            DiffStats with line counts.
        """
        matcher = difflib.SequenceMatcher(None, original_lines, modified_lines)

        additions = 0
        deletions = 0
        modifications = 0
        unchanged = 0

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                unchanged += i2 - i1
            elif tag == "replace":
                # Count as modifications (min) + additions/deletions (excess)
                old_count = i2 - i1
                new_count = j2 - j1
                modifications += min(old_count, new_count)
                if new_count > old_count:
                    additions += new_count - old_count
                else:
                    deletions += old_count - new_count
            elif tag == "delete":
                deletions += i2 - i1
            elif tag == "insert":
                additions += j2 - j1

        return DiffStats(
            additions=additions,
            deletions=deletions,
            modifications=modifications,
            unchanged=unchanged,
        )


# =============================================================================
# Convenience Functions
# =============================================================================


def generate_diff(
    original: str,
    modified: str,
    file_path: str | Path | None = None,
    context_lines: int = 3,
) -> DiffResult:
    """Generate a complete diff result (convenience function).

    Args:
        original: Original file content.
        modified: Modified file content.
        file_path: Optional file path for headers.
        context_lines: Lines of context in unified diff.

    Returns:
        DiffResult with all diff formats.
    """
    generator = DiffGenerator(context_lines=context_lines)
    return generator.diff(original, modified, file_path)


def unified_diff(
    original: str,
    modified: str,
    file_path: str | Path | None = None,
) -> str:
    """Generate unified diff string (convenience function).

    Args:
        original: Original file content.
        modified: Modified file content.
        file_path: Optional file path for headers.

    Returns:
        Unified diff string.
    """
    result = generate_diff(original, modified, file_path)
    return result.unified


def colored_diff(
    original: str,
    modified: str,
    file_path: str | Path | None = None,
) -> str:
    """Generate colored diff string (convenience function).

    Args:
        original: Original file content.
        modified: Modified file content.
        file_path: Optional file path for headers.

    Returns:
        ANSI-colored diff string.
    """
    result = generate_diff(original, modified, file_path)
    return result.colored


def has_changes(original: str, modified: str) -> bool:
    """Check if there are any changes between two strings.

    Args:
        original: Original content.
        modified: Modified content.

    Returns:
        True if content differs.
    """
    return original != modified
