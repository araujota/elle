"""Agent progress panel widget for the ELLE TUI.

Displays live stage progression during agent loop execution.
Shows completed stages with duration, active stage with spinner,
and pending stages dimmed.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.widgets import Static

from elle.cli.ui.theme import Colors, Icons


class _StageRecord:
    """Track state for a single agent stage."""

    __slots__ = ("name", "description", "start_time", "end_time", "status")

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self.start_time = time.monotonic()
        self.end_time: float | None = None
        self.status: str = "running"  # running | completed | failed

    @property
    def duration(self) -> float | None:
        if self.end_time is not None:
            return self.end_time - self.start_time
        return None

    def complete(self) -> None:
        self.end_time = time.monotonic()
        self.status = "completed"


class AgentProgressPanel(Static):
    """Live progress display for agent loop stages.

    Shows a vertical list of stages with status indicators:
    - Completed stages show a checkmark and duration
    - The active stage shows a spinner
    - Tool calls are shown indented under the active stage
    """

    DEFAULT_CSS = """
    AgentProgressPanel {
        width: 1fr;
        height: auto;
        padding: 0 2 1 4;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._stages: list[_StageRecord] = []
        self._tool_info: str = ""

    def add_stage(self, stage: str, description: str) -> None:
        """Add a new stage, completing the previous one.

        Args:
            stage: Stage identifier (retrieve, hypothesize, act, verify, record).
            description: Human-readable description.
        """
        # Complete previous stage
        if self._stages and self._stages[-1].status == "running":
            self._stages[-1].complete()

        self._stages.append(_StageRecord(stage, description))
        self._tool_info = ""
        self.update(self._render())

    def set_tool_info(self, tool_name: str) -> None:
        """Set the current tool being called.

        Args:
            tool_name: Name of the tool being executed.
        """
        self._tool_info = tool_name
        self.update(self._render())

    def clear_tool_info(self) -> None:
        """Clear tool info after tool completes."""
        self._tool_info = ""
        self.update(self._render())

    def finish(self) -> None:
        """Mark all stages as complete."""
        if self._stages and self._stages[-1].status == "running":
            self._stages[-1].complete()
        self._tool_info = ""
        self.update(self._render())

    def _render(self) -> Text:  # type: ignore[override]
        """Render the progress panel."""
        text = Text()

        for record in self._stages:
            if record.status == "completed":
                duration = record.duration
                dur_str = f"{duration:.1f}s" if duration is not None else ""
                text.append(f"  {Icons.SUCCESS} ", style=Colors.SUCCESS)
                text.append(record.name.upper().ljust(12), style=f"bold {Colors.SUCCESS}")
                text.append(record.description, style=Colors.MUTED)
                if dur_str:
                    text.append(f"  {dur_str}", style=Colors.MUTED)
                text.append("\n")
            elif record.status == "running":
                text.append(f"  {Icons.IN_PROGRESS} ", style=Colors.ACCENT)
                text.append(record.name.upper().ljust(12), style=f"bold {Colors.ACCENT}")
                text.append(record.description, style="")
                text.append("  ...", style=Colors.MUTED)
                text.append("\n")

                # Show tool info if present
                if self._tool_info:
                    text.append(f"      {Icons.CHEVRON} ", style=Colors.MUTED)
                    text.append(self._tool_info, style=Colors.ACCENT_DIM)
                    text.append("\n")

        return text
