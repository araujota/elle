"""Diff view widget for the ELLE TUI.

Renders diff output using the existing diff.render_diff_panel()
function from the cli/ui module.
"""

from __future__ import annotations

from rich.console import RenderableType
from textual.widgets import Static

from elle.cli.ui.diff import render_diff_panel


class DiffView(Static):
    """Widget that renders a diff panel.

    Wraps diff.render_diff_panel() to reuse the existing Rich rendering.
    """

    DEFAULT_CSS = """
    DiffView {
        width: 1fr;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    """

    def __init__(
        self,
        diff_text: str,
        *,
        title: str = "Changes",
        **kwargs: object,
    ) -> None:
        """Initialize with diff content.

        Args:
            diff_text: The unified diff text.
            title: Title for the diff panel.
        """
        super().__init__(**kwargs)
        self._diff_text = diff_text
        self._title = title

    def render(self) -> RenderableType:
        """Render the diff panel."""
        return render_diff_panel(self._diff_text, title=self._title)
