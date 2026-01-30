"""Incident card widget for the ELLE TUI.

Renders a single incident as a Rich panel using the existing
panels.incident_panel() function.
"""

from __future__ import annotations

from typing import Any

from rich.console import RenderableType
from textual.widgets import Static

from elle.cli.ui.panels import incident_panel


class IncidentCard(Static):
    """Widget that renders a single incident as a panel.

    Wraps panels.incident_panel() to reuse the existing Rich rendering.
    """

    DEFAULT_CSS = """
    IncidentCard {
        width: 1fr;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, incident: dict[str, Any], **kwargs: object) -> None:
        """Initialize with incident data.

        Args:
            incident: Dict with keys: incident_id, title, domain, status,
                     severity, created_at, summary, symptoms, root_cause, outcome.
        """
        super().__init__(**kwargs)
        self._incident = incident

    def render(self) -> RenderableType:
        """Render the incident panel."""
        return incident_panel(
            incident_id=self._incident.get("incident_id", "unknown"),
            title=self._incident.get("title", "Untitled"),
            domain=self._incident.get("domain", "unknown"),
            status=self._incident.get("status", "open"),
            severity=self._incident.get("severity", "info"),
            created_at=self._incident.get("created_at"),
            summary=self._incident.get("summary"),
            symptoms=self._incident.get("symptoms"),
            root_cause=self._incident.get("root_cause"),
            outcome=self._incident.get("outcome"),
        )
