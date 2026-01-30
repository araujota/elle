"""Incident list widget for the ELLE TUI.

Renders a table of incidents using the existing tables.incident_table()
function. Supports click/select to open incident detail.
"""

from __future__ import annotations

from typing import Any

from rich.console import RenderableType
from textual.message import Message
from textual.widgets import Static

from elle.cli.ui.tables import incident_table


class IncidentListView(Static):
    """Widget that renders a table of incidents.

    Wraps tables.incident_table() to reuse the existing Rich rendering.
    Posts IncidentSelected message when an incident is clicked.
    """

    DEFAULT_CSS = """
    IncidentListView {
        width: 1fr;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    """

    class IncidentSelected(Message):
        """Posted when the user selects an incident from the list."""

        def __init__(self, incident_id: str) -> None:
            super().__init__()
            self.incident_id = incident_id

    def __init__(
        self,
        incidents: list[dict[str, Any]],
        *,
        title: str | None = None,
        **kwargs: object,
    ) -> None:
        """Initialize with a list of incidents.

        Args:
            incidents: List of incident dicts.
            title: Optional table title.
        """
        super().__init__(**kwargs)
        self._incidents = incidents
        self._title = title

    def render(self) -> RenderableType:
        """Render the incident table."""
        return incident_table(
            self._incidents,
            title=self._title,
        )
