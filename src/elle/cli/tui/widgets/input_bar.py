"""Input bar widget for the ELLE TUI.

Command input with slash-command completion using suggestions
from the existing prompt.py SLASH_COMMANDS list, plus up/down
arrow navigation through command history.
"""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.events import Key
from textual.suggester import SuggestFromList
from textual.widgets import Input

from elle.cli.ui.prompt import SLASH_COMMANDS

# Build suggestion list from existing slash commands
_SLASH_SUGGESTIONS = [cmd for cmd, _desc in SLASH_COMMANDS]

# History file location (same as old REPL)
_HISTORY_FILE = Path.home() / ".elle_history"
_MAX_HISTORY = 500


class ElleInput(Input):
    """Command input bar for the ELLE TUI.

    Features:
    - Slash-command suggestions from SLASH_COMMANDS
    - Placeholder text
    - Submits on Enter, clears input
    - Up/down arrow to cycle through command history
    - Persistent history file at ~/.elle_history
    """

    DEFAULT_CSS = """
    ElleInput {
        dock: bottom;
        height: 3;
        padding: 0 1;
        border-top: solid $accent;
    }
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(
            placeholder="Ask ELLE anything...",
            suggester=SuggestFromList(_SLASH_SUGGESTIONS, case_sensitive=False),
            name=name,
            id=id,
            classes=classes,
        )
        self._history: list[str] = []
        self._history_index: int = -1
        self._saved_input: str = ""
        self._load_history()

    def _load_history(self) -> None:
        """Load command history from file."""
        try:
            if _HISTORY_FILE.exists():
                lines = _HISTORY_FILE.read_text().strip().splitlines()
                self._history = lines[-_MAX_HISTORY:]
        except Exception:
            pass

    def _save_history_entry(self, command: str) -> None:
        """Append a command to the history file.

        Args:
            command: The command to save.
        """
        self._history.append(command)

        # Trim in-memory history
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]

        try:
            _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with _HISTORY_FILE.open("a") as f:
                f.write(command + "\n")
        except Exception:
            pass

    @on(Input.Submitted)
    def on_submitted(self, event: Input.Submitted) -> None:
        """Save to history and clear input after submission."""
        command = event.value.strip()
        if command:
            self._save_history_entry(command)
        self._history_index = -1
        self._saved_input = ""
        self.value = ""

    def on_key(self, event: Key) -> None:
        """Handle up/down arrow for history navigation."""
        if event.key == "up":
            if not self._history:
                return
            if self._history_index == -1:
                self._saved_input = self.value
                self._history_index = len(self._history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
            self.value = self._history[self._history_index]
            self.cursor_position = len(self.value)
            event.prevent_default()

        elif event.key == "down":
            if self._history_index == -1:
                return
            if self._history_index < len(self._history) - 1:
                self._history_index += 1
                self.value = self._history[self._history_index]
            else:
                self._history_index = -1
                self.value = self._saved_input
            self.cursor_position = len(self.value)
            event.prevent_default()
