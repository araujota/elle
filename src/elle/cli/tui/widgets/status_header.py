"""Status header widget for the ELLE TUI.

Displays model name, daemon status, and open incident count
in a top-docked bar.
"""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from elle.cli.ui.theme import Colors, Icons


class StatusHeader(Static):
    """Top status bar showing system state at a glance."""

    DEFAULT_CSS = """
    StatusHeader {
        dock: top;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    model_name: reactive[str] = reactive("", layout=False)
    daemon_status: reactive[str] = reactive("", layout=False)
    incidents_open: reactive[int] = reactive(0, layout=False)

    def render(self) -> Text:
        """Render the status bar content."""
        text = Text()

        # Model
        if self.model_name:
            text.append("Model: ", style=Colors.MUTED)
            text.append(self.model_name, style=Colors.ACCENT)
        else:
            text.append("Model: ", style=Colors.MUTED)
            text.append("not connected", style=Colors.MUTED)

        text.append(" | ", style=Colors.MUTED)

        # Daemon
        text.append("Daemon: ", style=Colors.MUTED)
        if self.daemon_status == "running":
            text.append(Icons.SUCCESS, style=Colors.SUCCESS)
        elif self.daemon_status == "starting":
            text.append(Icons.IN_PROGRESS, style=Colors.WARNING)
        else:
            text.append(Icons.ERROR, style=Colors.ERROR)

        # Incidents
        if self.incidents_open > 0:
            text.append(" | ", style=Colors.MUTED)
            text.append(f"{Icons.WARNING} {self.incidents_open} incident(s)", style=Colors.WARNING)

        # Right-aligned help hint
        available = self.size.width - text.cell_len - 2
        if available > 8:
            spacer = " " * max(0, available - 4)
            text.append(spacer)
            text.append("F1 ?", style=Colors.MUTED)

        return text

    def refresh_status(
        self,
        *,
        model: str | None = None,
        daemon: str | None = None,
        incidents: int | None = None,
    ) -> None:
        """Update status bar values.

        Args:
            model: LLM model name.
            daemon: Daemon status string.
            incidents: Open incident count.
        """
        if model is not None:
            self.model_name = model
        if daemon is not None:
            self.daemon_status = daemon
        if incidents is not None:
            self.incidents_open = incidents
