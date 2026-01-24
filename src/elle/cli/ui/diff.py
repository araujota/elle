"""Diff Rendering for ELLE.

Displays unified diffs, config changes, and before/after comparisons.
"""

from __future__ import annotations

from typing import Sequence

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from elle.cli.ui.console import console
from elle.cli.ui.theme import Colors, Icons


def render_diff(
    diff_text: str,
    *,
    title: str | None = None,
    language: str = "diff",
) -> Syntax:
    """Render a unified diff with syntax highlighting.

    Args:
        diff_text: Unified diff text
        title: Optional title (not used by Syntax, for reference)
        language: Syntax language (default: diff)

    Returns:
        Rich Syntax object
    """
    return Syntax(
        diff_text,
        language,
        theme="monokai",
        line_numbers=True,
        word_wrap=True,
        background_color="default",
    )


def render_diff_panel(
    diff_text: str,
    *,
    title: str = "Changes",
    file_path: str | None = None,
) -> Panel:
    """Render a diff in a panel.

    Args:
        diff_text: Unified diff text
        title: Panel title
        file_path: Optional file path for subtitle

    Returns:
        Rich Panel object
    """
    syntax = render_diff(diff_text)

    return Panel(
        syntax,
        title=title,
        subtitle=file_path,
        subtitle_align="right",
        border_style=Colors.BORDER,
        padding=(0, 1),
    )


def render_inline_diff(
    old_lines: Sequence[str],
    new_lines: Sequence[str],
    *,
    context_lines: int = 3,
) -> Text:
    """Render an inline diff showing changes.

    Args:
        old_lines: Original lines
        new_lines: Modified lines
        context_lines: Number of context lines around changes

    Returns:
        Rich Text object with colored diff
    """
    import difflib

    differ = difflib.unified_diff(
        list(old_lines),
        list(new_lines),
        lineterm="",
        n=context_lines,
    )

    result = Text()

    for line in differ:
        if line.startswith("+++") or line.startswith("---"):
            result.append(line + "\n", style="bold")
        elif line.startswith("@@"):
            result.append(line + "\n", style="cyan")
        elif line.startswith("+"):
            result.append(line + "\n", style="green")
        elif line.startswith("-"):
            result.append(line + "\n", style="red")
        else:
            result.append(line + "\n")

    return result


def render_side_by_side_diff(
    old_content: str,
    new_content: str,
    *,
    old_title: str = "Before",
    new_title: str = "After",
    max_width: int | None = None,
) -> Table:
    """Render a side-by-side diff comparison.

    Args:
        old_content: Original content
        new_content: Modified content
        old_title: Title for old column
        new_title: Title for new column
        max_width: Maximum width for each column

    Returns:
        Rich Table object
    """
    import difflib

    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()

    # Use SequenceMatcher to find matching blocks
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)

    table = Table(
        show_header=True,
        header_style="bold",
        border_style=Colors.BORDER,
        padding=(0, 1),
    )

    half_width = (max_width or 80) // 2 - 2

    table.add_column(old_title, max_width=half_width, style="")
    table.add_column(new_title, max_width=half_width, style="")

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            # Show some context
            for old, new in zip(old_lines[i1:i2], new_lines[j1:j2]):
                table.add_row(
                    Text(old[:half_width], style="muted"),
                    Text(new[:half_width], style="muted"),
                )
        elif tag == "replace":
            # Show both old and new
            max_len = max(i2 - i1, j2 - j1)
            for k in range(max_len):
                old_line = old_lines[i1 + k] if i1 + k < i2 else ""
                new_line = new_lines[j1 + k] if j1 + k < j2 else ""
                table.add_row(
                    Text(old_line[:half_width], style="red"),
                    Text(new_line[:half_width], style="green"),
                )
        elif tag == "delete":
            for line in old_lines[i1:i2]:
                table.add_row(
                    Text(line[:half_width], style="red"),
                    Text("", style="muted"),
                )
        elif tag == "insert":
            for line in new_lines[j1:j2]:
                table.add_row(
                    Text("", style="muted"),
                    Text(line[:half_width], style="green"),
                )

    return table


def render_change_summary(
    additions: int,
    deletions: int,
    modifications: int = 0,
) -> Text:
    """Render a change summary line.

    Args:
        additions: Number of additions
        deletions: Number of deletions
        modifications: Number of modifications

    Returns:
        Rich Text object
    """
    text = Text()

    if additions > 0:
        text.append(f"+{additions}", style="green")
        text.append(" ")

    if deletions > 0:
        text.append(f"-{deletions}", style="red")
        text.append(" ")

    if modifications > 0:
        text.append(f"~{modifications}", style="yellow")
        text.append(" ")

    if not (additions or deletions or modifications):
        text.append("No changes", style="muted")

    return text


def render_config_change(
    path: str,
    old_value: str | None,
    new_value: str | None,
    *,
    operation: str = "set",
) -> Text:
    """Render a single config change.

    Args:
        path: Config path (e.g., /etc/ssh/sshd_config/PermitRootLogin)
        old_value: Previous value (None if new)
        new_value: New value (None if deleted)
        operation: Operation type (set, rm, ins, mv)

    Returns:
        Rich Text object
    """
    text = Text()

    if operation == "rm" or new_value is None:
        text.append(f"- ", style="red")
        text.append(path, style="path")
        if old_value:
            text.append(f" = {old_value}", style="red")
    elif operation == "ins" or old_value is None:
        text.append(f"+ ", style="green")
        text.append(path, style="path")
        if new_value:
            text.append(f" = {new_value}", style="green")
    else:
        text.append(f"~ ", style="yellow")
        text.append(path, style="path")
        text.append(f"\n    ", style="")
        text.append(f"{old_value or '(empty)'}", style="red")
        text.append(f" {Icons.ARROW_RIGHT} ", style="muted")
        text.append(f"{new_value or '(empty)'}", style="green")

    return text


def render_config_changes(
    changes: Sequence[dict],
    *,
    title: str = "Configuration Changes",
) -> Panel:
    """Render a list of config changes in a panel.

    Args:
        changes: List of change dicts with keys:
            - path: str
            - old_value: str | None
            - new_value: str | None
            - operation: str
        title: Panel title

    Returns:
        Rich Panel object
    """
    content_parts: list[RenderableType] = []

    # Summary
    additions = sum(1 for c in changes if c.get("operation") == "ins" or c.get("old_value") is None)
    deletions = sum(1 for c in changes if c.get("operation") == "rm" or c.get("new_value") is None)
    modifications = len(changes) - additions - deletions

    summary = render_change_summary(additions, deletions, modifications)
    content_parts.append(summary)
    content_parts.append(Text())

    # Individual changes
    for change in changes:
        content_parts.append(
            render_config_change(
                path=str(change.get("path", "")),
                old_value=change.get("old_value"),
                new_value=change.get("new_value"),
                operation=str(change.get("operation", "set")),
            )
        )
        content_parts.append(Text())

    return Panel(
        Group(*content_parts),
        title=title,
        border_style=Colors.BORDER,
        padding=(1, 2),
    )


def render_snapshot_diff(
    before: dict,
    after: dict,
    *,
    fields: Sequence[str] | None = None,
) -> Table:
    """Render a system snapshot comparison.

    Args:
        before: Before snapshot dict
        after: After snapshot dict
        fields: Optional list of fields to compare (None = all)

    Returns:
        Rich Table object
    """
    table = Table(
        title="System State Changes",
        show_header=True,
        header_style="bold",
        border_style=Colors.BORDER,
        padding=(0, 1),
    )

    table.add_column("Field")
    table.add_column("Before")
    table.add_column("", width=3)
    table.add_column("After")
    table.add_column("Change")

    # Determine fields to compare
    all_fields = set(before.keys()) | set(after.keys())
    if fields:
        all_fields = all_fields & set(fields)

    for field in sorted(all_fields):
        before_val = before.get(field, "-")
        after_val = after.get(field, "-")

        # Skip if unchanged
        if before_val == after_val:
            continue

        # Determine change direction and style
        change_text = Text()
        style = "muted"

        try:
            before_num = float(before_val) if before_val != "-" else 0
            after_num = float(after_val) if after_val != "-" else 0
            diff = after_num - before_num

            if diff > 0:
                change_text.append(f"+{diff:.1f}", style="red")
                style = "red" if field in ("mem_pressure", "disk_pressure", "cpu_load") else "green"
            elif diff < 0:
                change_text.append(f"{diff:.1f}", style="green")
                style = "green"
            else:
                change_text.append("=", style="muted")
        except (ValueError, TypeError):
            change_text.append("changed", style="yellow")
            style = "yellow"

        table.add_row(
            field,
            Text(str(before_val), style="muted"),
            Text(Icons.ARROW_RIGHT, style=style),
            Text(str(after_val), style=style),
            change_text,
        )

    return table
