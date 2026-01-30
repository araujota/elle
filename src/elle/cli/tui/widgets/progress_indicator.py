"""Progress indicator widgets for the ELLE TUI.

Spinner and progress bar wrappers for long-running operations
like model pulls and service startups.
"""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from elle.cli.ui.theme import Colors


class SpinnerWidget(Static):
    """Simple spinner with a message."""

    DEFAULT_CSS = """
    SpinnerWidget {
        width: 1fr;
        height: 1;
        padding: 0 2;
    }
    """

    _spinner_frames = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    message: reactive[str] = reactive("Loading...", layout=False)
    _frame: reactive[int] = reactive(0, layout=False)

    def on_mount(self) -> None:
        """Start the spinner animation timer."""
        self.set_interval(0.1, self._advance_frame)

    def _advance_frame(self) -> None:
        """Advance to the next spinner frame."""
        self._frame = (self._frame + 1) % len(self._spinner_frames)
        self.update(self._render_spinner())

    def _render_spinner(self) -> Text:
        """Render the spinner with message."""
        text = Text()
        text.append(self._spinner_frames[self._frame], style=Colors.ACCENT)
        text.append(f" {self.message}", style=Colors.MUTED)
        return text


class ProgressBarWidget(Static):
    """Progress bar with percentage display."""

    DEFAULT_CSS = """
    ProgressBarWidget {
        width: 1fr;
        height: 1;
        padding: 0 2;
    }
    """

    progress: reactive[float] = reactive(0.0, layout=False)
    message: reactive[str] = reactive("", layout=False)

    def render(self) -> Text:
        """Render the progress bar."""
        bar_width = 20
        filled = int(bar_width * self.progress)
        empty = bar_width - filled

        text = Text()
        text.append("[", style=Colors.MUTED)
        text.append("=" * filled, style=Colors.ACCENT)
        text.append("-" * empty, style=Colors.MUTED)
        text.append("]", style=Colors.MUTED)
        text.append(f" {self.progress:.0%}", style=Colors.ACCENT)

        if self.message:
            text.append(f"  {self.message}", style=Colors.MUTED)

        return text
