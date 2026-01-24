"""Tests for the diff generator."""

from __future__ import annotations

import pytest

from elle.ops.augeas.diff import (
    Colors,
    DiffGenerator,
    DiffResult,
    DiffStats,
    colored_diff,
    generate_diff,
    has_changes,
    unified_diff,
)


class TestDiffStats:
    """Tests for DiffStats model."""

    def test_total_changes_empty(self) -> None:
        """Empty stats should have zero total changes."""
        stats = DiffStats()
        assert stats.total_changes == 0

    def test_total_changes_calculation(self) -> None:
        """Total changes should sum additions, deletions, and modifications."""
        stats = DiffStats(additions=5, deletions=3, modifications=2)
        assert stats.total_changes == 10

    def test_frozen(self) -> None:
        """DiffStats should be immutable."""
        stats = DiffStats(additions=1)
        with pytest.raises(Exception):  # ValidationError for frozen model
            stats.additions = 2  # type: ignore[misc]


class TestDiffGenerator:
    """Tests for DiffGenerator."""

    def test_diff_identical_content(self) -> None:
        """Identical content should have no changes."""
        generator = DiffGenerator()
        result = generator.diff("hello\nworld\n", "hello\nworld\n")

        assert not result.has_changes
        assert result.stats.total_changes == 0

    def test_diff_added_lines(self) -> None:
        """Added lines should be detected."""
        generator = DiffGenerator()
        original = "line1\nline2\n"
        modified = "line1\nline2\nline3\n"

        result = generator.diff(original, modified)

        assert result.has_changes
        assert result.stats.additions >= 1
        assert "+line3" in result.unified

    def test_diff_removed_lines(self) -> None:
        """Removed lines should be detected."""
        generator = DiffGenerator()
        original = "line1\nline2\nline3\n"
        modified = "line1\nline2\n"

        result = generator.diff(original, modified)

        assert result.has_changes
        assert result.stats.deletions >= 1
        assert "-line3" in result.unified

    def test_diff_modified_lines(self) -> None:
        """Modified lines should be detected."""
        generator = DiffGenerator()
        original = "line1\nold_value\nline3\n"
        modified = "line1\nnew_value\nline3\n"

        result = generator.diff(original, modified)

        assert result.has_changes
        assert "-old_value" in result.unified
        assert "+new_value" in result.unified

    def test_diff_with_file_path(self) -> None:
        """File path should appear in diff headers."""
        generator = DiffGenerator()
        result = generator.diff("old\n", "new\n", "/etc/config")

        assert "a//etc/config" in result.unified
        assert "b//etc/config" in result.unified

    def test_colored_output(self) -> None:
        """Colored output should contain ANSI codes."""
        generator = DiffGenerator()
        result = generator.diff("old\n", "new\n")

        # Should contain color codes for additions and deletions
        assert Colors.GREEN in result.colored
        assert Colors.RED in result.colored
        assert Colors.RESET in result.colored

    def test_colored_no_changes(self) -> None:
        """Identical content should have empty colored output."""
        generator = DiffGenerator()
        result = generator.diff("same\n", "same\n")

        assert result.colored == ""

    def test_side_by_side(self) -> None:
        """Side-by-side view should be generated."""
        generator = DiffGenerator(width=80)
        result = generator.diff("left\n", "right\n")

        assert "Original" in result.side_by_side
        assert "Modified" in result.side_by_side

    def test_summary(self) -> None:
        """Summary should describe changes."""
        generator = DiffGenerator()

        # No changes
        summary = generator.summary("same", "same")
        assert summary == "No changes"

        # With changes
        summary = generator.summary("old", "new")
        assert "added" in summary or "removed" in summary or "modified" in summary

    def test_context_lines(self) -> None:
        """Context lines setting should be respected."""
        generator = DiffGenerator(context_lines=5)
        original = "1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n"
        modified = "1\n2\n3\n4\nX\n6\n7\n8\n9\n10\n"

        result = generator.diff(original, modified)

        # Should have context around the change
        assert "1" in result.unified
        assert "X" in result.unified


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_generate_diff(self) -> None:
        """generate_diff should return DiffResult."""
        result = generate_diff("old\n", "new\n", "/test/file")

        assert isinstance(result, DiffResult)
        assert result.has_changes

    def test_unified_diff(self) -> None:
        """unified_diff should return string."""
        diff = unified_diff("old\n", "new\n")

        assert isinstance(diff, str)
        assert "-old" in diff
        assert "+new" in diff

    def test_colored_diff(self) -> None:
        """colored_diff should return colored string."""
        diff = colored_diff("old\n", "new\n")

        assert isinstance(diff, str)
        assert Colors.RED in diff or Colors.GREEN in diff

    def test_has_changes_true(self) -> None:
        """has_changes should return True for different content."""
        assert has_changes("old", "new") is True

    def test_has_changes_false(self) -> None:
        """has_changes should return False for identical content."""
        assert has_changes("same", "same") is False


class TestDiffResult:
    """Tests for DiffResult model."""

    def test_default_values(self) -> None:
        """DiffResult should have sensible defaults."""
        result = DiffResult()

        assert result.unified == ""
        assert result.colored == ""
        assert result.side_by_side == ""
        assert result.has_changes is False
        assert isinstance(result.stats, DiffStats)

    def test_frozen(self) -> None:
        """DiffResult should be immutable."""
        result = DiffResult(has_changes=True)

        with pytest.raises(Exception):
            result.has_changes = False  # type: ignore[misc]
