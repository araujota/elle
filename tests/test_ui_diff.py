"""Tests for elle.cli.ui.diff -- Diff display formatting."""

from __future__ import annotations

from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from elle.cli.ui.diff import (
    render_change_summary,
    render_config_change,
    render_config_changes,
    render_diff,
    render_diff_panel,
    render_inline_diff,
    render_side_by_side_diff,
    render_snapshot_diff,
)

# ---------------------------------------------------------------------------
# render_diff
# ---------------------------------------------------------------------------


class TestRenderDiff:
    def test_basic(self):
        diff_text = "--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+new"
        result = render_diff(diff_text)
        assert isinstance(result, Syntax)

    def test_custom_language(self):
        result = render_diff("some text", language="python")
        assert isinstance(result, Syntax)

    def test_with_title(self):
        result = render_diff("diff", title="My Diff")
        assert isinstance(result, Syntax)


# ---------------------------------------------------------------------------
# render_diff_panel
# ---------------------------------------------------------------------------


class TestRenderDiffPanel:
    def test_basic(self):
        diff_text = "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new"
        result = render_diff_panel(diff_text)
        assert isinstance(result, Panel)

    def test_with_file_path(self):
        result = render_diff_panel("diff", file_path="/etc/config")
        assert isinstance(result, Panel)

    def test_custom_title(self):
        result = render_diff_panel("diff", title="Custom Title")
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# render_inline_diff
# ---------------------------------------------------------------------------


class TestRenderInlineDiff:
    def test_no_changes(self):
        lines = ["a", "b", "c"]
        result = render_inline_diff(lines, lines)
        assert isinstance(result, Text)

    def test_additions(self):
        old = ["a", "b"]
        new = ["a", "b", "c"]
        result = render_inline_diff(old, new)
        assert isinstance(result, Text)

    def test_deletions(self):
        old = ["a", "b", "c"]
        new = ["a", "c"]
        result = render_inline_diff(old, new)
        assert isinstance(result, Text)

    def test_modifications(self):
        old = ["line1", "line2"]
        new = ["line1", "LINE2"]
        result = render_inline_diff(old, new)
        assert isinstance(result, Text)

    def test_mixed_changes(self):
        old = ["header", "old content", "footer"]
        new = ["header", "new content", "extra line", "footer"]
        result = render_inline_diff(old, new)
        assert isinstance(result, Text)

    def test_custom_context(self):
        old = ["a", "b", "c", "d", "e", "f"]
        new = ["a", "b", "X", "d", "e", "f"]
        result = render_inline_diff(old, new, context_lines=1)
        assert isinstance(result, Text)


# ---------------------------------------------------------------------------
# render_side_by_side_diff
# ---------------------------------------------------------------------------


class TestRenderSideBySideDiff:
    def test_equal_content(self):
        content = "line1\nline2"
        result = render_side_by_side_diff(content, content)
        assert isinstance(result, Table)

    def test_replaced_content(self):
        old = "old_line"
        new = "new_line"
        result = render_side_by_side_diff(old, new)
        assert isinstance(result, Table)

    def test_deleted_lines(self):
        old = "line1\nline2\nline3"
        new = "line1"
        result = render_side_by_side_diff(old, new)
        assert isinstance(result, Table)

    def test_inserted_lines(self):
        old = "line1"
        new = "line1\nline2\nline3"
        result = render_side_by_side_diff(old, new)
        assert isinstance(result, Table)

    def test_custom_titles(self):
        result = render_side_by_side_diff("a", "b", old_title="Original", new_title="Modified")
        assert isinstance(result, Table)

    def test_custom_max_width(self):
        result = render_side_by_side_diff("a", "b", max_width=60)
        assert isinstance(result, Table)

    def test_unequal_replace_length(self):
        """Replace block where old and new have different number of lines."""
        old = "a\nb\nc"
        new = "x"
        result = render_side_by_side_diff(old, new)
        assert isinstance(result, Table)


# ---------------------------------------------------------------------------
# render_change_summary
# ---------------------------------------------------------------------------


class TestRenderChangeSummary:
    def test_additions_only(self):
        result = render_change_summary(3, 0, 0)
        assert isinstance(result, Text)
        assert "+3" in result.plain

    def test_deletions_only(self):
        result = render_change_summary(0, 2, 0)
        assert isinstance(result, Text)
        assert "-2" in result.plain

    def test_modifications_only(self):
        result = render_change_summary(0, 0, 1)
        assert isinstance(result, Text)
        assert "~1" in result.plain

    def test_mixed(self):
        result = render_change_summary(1, 2, 3)
        assert isinstance(result, Text)

    def test_no_changes(self):
        result = render_change_summary(0, 0, 0)
        assert isinstance(result, Text)
        assert "No changes" in result.plain


# ---------------------------------------------------------------------------
# render_config_change
# ---------------------------------------------------------------------------


class TestRenderConfigChange:
    def test_set_operation(self):
        result = render_config_change("/path/key", "old_val", "new_val", operation="set")
        assert isinstance(result, Text)

    def test_rm_operation(self):
        result = render_config_change("/path/key", "old_val", None, operation="rm")
        assert isinstance(result, Text)

    def test_insert_operation(self):
        result = render_config_change("/path/key", None, "new_val", operation="ins")
        assert isinstance(result, Text)

    def test_rm_with_old_value(self):
        result = render_config_change("/path/key", "val", None)
        assert isinstance(result, Text)

    def test_ins_with_new_value(self):
        result = render_config_change("/path/key", None, "val")
        assert isinstance(result, Text)

    def test_modify_shows_both(self):
        result = render_config_change("/path/key", "before", "after", operation="set")
        assert isinstance(result, Text)

    def test_modify_empty_values(self):
        result = render_config_change("/path/key", "", "", operation="set")
        assert isinstance(result, Text)


# ---------------------------------------------------------------------------
# render_config_changes
# ---------------------------------------------------------------------------


class TestRenderConfigChanges:
    def test_basic(self):
        changes = [
            {"path": "/etc/foo", "old_value": None, "new_value": "bar", "operation": "ins"},
            {"path": "/etc/baz", "old_value": "old", "new_value": None, "operation": "rm"},
            {"path": "/etc/qux", "old_value": "a", "new_value": "b", "operation": "set"},
        ]
        result = render_config_changes(changes)
        assert isinstance(result, Panel)

    def test_custom_title(self):
        result = render_config_changes([], title="My Changes")
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# render_snapshot_diff
# ---------------------------------------------------------------------------


class TestRenderSnapshotDiff:
    def test_numeric_increase(self):
        before = {"cpu_load": "0.5", "mem_used": "4.0"}
        after = {"cpu_load": "1.5", "mem_used": "4.0"}
        result = render_snapshot_diff(before, after)
        assert isinstance(result, Table)

    def test_numeric_decrease(self):
        before = {"disk_pressure": "0.9"}
        after = {"disk_pressure": "0.5"}
        result = render_snapshot_diff(before, after)
        assert isinstance(result, Table)

    def test_non_numeric_change(self):
        before = {"status": "running"}
        after = {"status": "stopped"}
        result = render_snapshot_diff(before, after)
        assert isinstance(result, Table)

    def test_no_changes(self):
        data = {"a": "1", "b": "2"}
        result = render_snapshot_diff(data, data)
        assert isinstance(result, Table)

    def test_field_filter(self):
        before = {"a": "1", "b": "2"}
        after = {"a": "X", "b": "Y"}
        result = render_snapshot_diff(before, after, fields=["a"])
        assert isinstance(result, Table)

    def test_missing_field(self):
        before = {"a": "1"}
        after = {"b": "2"}
        result = render_snapshot_diff(before, after)
        assert isinstance(result, Table)

    def test_pressure_field_color(self):
        """Fields like mem_pressure, disk_pressure, cpu_load use red for increases."""
        before = {"mem_pressure": "0.5"}
        after = {"mem_pressure": "0.9"}
        result = render_snapshot_diff(before, after)
        assert isinstance(result, Table)

    def test_equal_numeric_values(self):
        """Values that are equal numerically but field changed from '-'."""
        before = {"x": "-"}
        after = {"x": "0"}
        result = render_snapshot_diff(before, after)
        assert isinstance(result, Table)
