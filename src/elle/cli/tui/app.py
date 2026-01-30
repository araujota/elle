"""ELLE TUI Application.

The main Textual application class that owns the Engine and Session,
manages screens, and handles global key bindings.
"""

from __future__ import annotations

from textual.app import App
from textual.binding import Binding

from elle.cli.tui.screens.main import MainScreen


class ElleApp(App):
    """The ELLE full-screen terminal application."""

    TITLE = "ELLE"
    SUB_TITLE = "local-first system intelligence"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True, priority=True),
        Binding("ctrl+s", "toggle_sidebar", "Sidebar", show=True),
        Binding("ctrl+l", "clear", "Clear", show=True),
        Binding("f1", "help", "Help", show=True),
    ]

    SCREENS = {
        "main": MainScreen,
    }

    def on_mount(self) -> None:
        """Push the main screen and check for first-run setup."""
        self.push_screen("main")
        self._check_first_run()

    def _check_first_run(self) -> None:
        """Check if this is the first run and show setup if needed."""
        try:
            from elle.cli.setup import is_first_run

            if is_first_run():
                from elle.cli.tui.screens.setup import SetupScreen

                self.push_screen(SetupScreen())
        except Exception:
            pass

    def action_reconfigure(self) -> None:
        """Open the setup wizard for reconfiguration."""
        from elle.cli.tui.screens.setup import SetupScreen

        self.push_screen(SetupScreen(reconfigure=True))

    def action_toggle_sidebar(self) -> None:
        """Toggle the system sidebar visibility."""
        screen = self.screen
        if isinstance(screen, MainScreen):
            screen.toggle_sidebar()

    def action_clear(self) -> None:
        """Clear the conversation view."""
        screen = self.screen
        if hasattr(screen, "action_clear_conversation"):
            screen.action_clear_conversation()

    def action_help(self) -> None:
        """Show help information."""
        screen = self.screen
        if isinstance(screen, MainScreen) and screen.bridge is not None:
            screen.bridge.submit("/help")
