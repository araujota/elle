"""Fixit view widget for the ELLE TUI.

Renders fixit diagnosis and suggestions using the existing
panels.fixit_panel() function.
"""

from __future__ import annotations

from typing import Any

from rich.console import RenderableType
from textual.widgets import Static

from elle.cli.ui.panels import fixit_panel


class FixitView(Static):
    """Widget that renders a fixit diagnosis panel.

    Wraps panels.fixit_panel() to reuse the existing Rich rendering.
    Shows diagnosis, suggestions with confidence bars, and verification commands.
    """

    DEFAULT_CSS = """
    FixitView {
        width: 1fr;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, fixit_data: dict[str, Any], **kwargs: object) -> None:
        """Initialize with fixit data.

        Args:
            fixit_data: Dict with keys: command, exit_code, diagnosis,
                       root_cause, confidence, suggestions, man_citations,
                       incident_citations, confidence_breakdown, rationale_summary.
        """
        super().__init__(**kwargs)
        self._fixit_data = fixit_data

    def render(self) -> RenderableType:
        """Render the fixit panel."""
        return fixit_panel(
            command=self._fixit_data.get("command", ""),
            exit_code=self._fixit_data.get("exit_code", 1),
            diagnosis=self._fixit_data.get("diagnosis", ""),
            root_cause=self._fixit_data.get("root_cause"),
            confidence=self._fixit_data.get("confidence"),
            suggestions=self._fixit_data.get("suggestions"),
            man_citations=self._fixit_data.get("man_citations"),
            incident_citations=self._fixit_data.get("incident_citations"),
            confidence_breakdown=self._fixit_data.get("confidence_breakdown"),
            rationale_summary=self._fixit_data.get("rationale_summary"),
        )
