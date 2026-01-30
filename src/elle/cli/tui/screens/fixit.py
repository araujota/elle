"""Fixit modal screen for the ELLE TUI.

Shows diagnosis and suggestions via FixitView. The user can
navigate between suggestions and apply fixes.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Static

from elle.cli.tui.widgets.fixit_view import FixitView


class FixitScreen(ModalScreen[str | None]):
    """Modal screen for fixit interactions.

    Shows diagnosis and numbered suggestions. The user can select
    a suggestion to apply. Returns the selected suggestion index
    as a string, or None if dismissed.
    """

    DEFAULT_CSS = """
    FixitScreen {
        align: center middle;
    }

    FixitScreen > VerticalScroll {
        width: 85%;
        height: 80%;
        border: solid $warning;
        background: $surface;
        padding: 1 2;
    }

    FixitScreen .fixit-title {
        text-style: bold;
        text-align: center;
        padding: 0 0 1 0;
    }

    FixitScreen .fixit-actions {
        dock: bottom;
        height: 3;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close", show=True),
        Binding("1", "select_1", "Fix 1"),
        Binding("2", "select_2", "Fix 2"),
        Binding("3", "select_3", "Fix 3"),
        Binding("j", "scroll_down", "Down"),
        Binding("k", "scroll_up", "Up"),
    ]

    def __init__(self, fixit_data: dict[str, Any]) -> None:
        """Initialize with fixit data.

        Args:
            fixit_data: Dict with command, exit_code, diagnosis,
                       suggestions, etc.
        """
        super().__init__()
        self._fixit_data = fixit_data
        self._suggestions = fixit_data.get("suggestions", [])

    def compose(self) -> ComposeResult:
        """Compose the fixit layout."""
        with VerticalScroll():
            yield Static("[bold]Fix Diagnosis[/bold]", classes="fixit-title")
            yield FixitView(self._fixit_data)

            # Numbered suggestion buttons
            if self._suggestions:
                yield Static("\n[bold]Select a fix to apply:[/bold]")
                for i, suggestion in enumerate(self._suggestions[:9], 1):
                    desc = suggestion.get("description", suggestion.get("command", f"Fix {i}"))
                    yield Button(f"  [{i}] {desc}", id=f"fix-{i}")

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle suggestion button presses."""
        if event.button.id and event.button.id.startswith("fix-"):
            idx = event.button.id.split("-")[1]
            self.dismiss(idx)

    def action_dismiss_screen(self) -> None:
        """Dismiss the fixit screen."""
        self.dismiss(None)

    def action_select_1(self) -> None:
        """Select suggestion 1."""
        if len(self._suggestions) >= 1:
            self.dismiss("1")

    def action_select_2(self) -> None:
        """Select suggestion 2."""
        if len(self._suggestions) >= 2:
            self.dismiss("2")

    def action_select_3(self) -> None:
        """Select suggestion 3."""
        if len(self._suggestions) >= 3:
            self.dismiss("3")

    def action_scroll_down(self) -> None:
        """Scroll down."""
        scroll = self.query_one(VerticalScroll)
        scroll.scroll_down(animate=False)

    def action_scroll_up(self) -> None:
        """Scroll up."""
        scroll = self.query_one(VerticalScroll)
        scroll.scroll_up(animate=False)
