"""Incident detail modal screen for the ELLE TUI.

Full incident view with header, actions timeline, snapshots,
fingerprint, and narrative. Scrollable with keyboard navigation.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

from elle.cli.tui.widgets.incident_card import IncidentCard


class IncidentDetailScreen(ModalScreen[None]):
    """Modal screen showing full incident details.

    Displays the incident in a scrollable view with keyboard navigation.
    """

    DEFAULT_CSS = """
    IncidentDetailScreen {
        align: center middle;
    }

    IncidentDetailScreen > VerticalScroll {
        width: 90%;
        height: 85%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    IncidentDetailScreen .incident-title {
        text-style: bold;
        padding: 0 0 1 0;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=True),
        Binding("q", "dismiss", "Close"),
        Binding("j", "scroll_down", "Down"),
        Binding("k", "scroll_up", "Up"),
    ]

    def __init__(self, incident: dict[str, Any]) -> None:
        """Initialize with incident data.

        Args:
            incident: Full incident dict with all fields.
        """
        super().__init__()
        self._incident = incident

    def compose(self) -> ComposeResult:
        """Compose the incident detail layout."""
        with VerticalScroll():
            yield IncidentCard(self._incident)

            # Actions timeline if available
            actions = self._incident.get("actions", [])
            if actions:
                yield Static("[bold]Actions Timeline[/bold]", classes="incident-title")
                for action in actions:
                    desc = action.get("description", "")
                    ts = action.get("timestamp", "")
                    yield Static(f"  {ts}  {desc}")

            # Narrative if available
            narrative = self._incident.get("narrative")
            if narrative:
                yield Static("[bold]Narrative[/bold]", classes="incident-title")
                yield Static(narrative)

            # Fingerprint
            fingerprint = self._incident.get("fingerprint")
            if fingerprint:
                yield Static(f"\n[dim]Fingerprint: {fingerprint}[/dim]")

        yield Footer()

    def action_scroll_down(self) -> None:
        """Scroll down."""
        scroll = self.query_one(VerticalScroll)
        scroll.scroll_down(animate=False)

    def action_scroll_up(self) -> None:
        """Scroll up."""
        scroll = self.query_one(VerticalScroll)
        scroll.scroll_up(animate=False)
