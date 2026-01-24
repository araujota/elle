"""ELLE Terminal REPL - Main interactive loop.

Entry point for ELLE Terminal Mode. Manages the interactive session,
readline history, and dispatches all input through the shared engine.

Usage:
    $ elle
    ELLE — Ubuntu System Assistant
    Type 'help' for commands or just start typing.

    elle ▸ help
    ...
"""

from __future__ import annotations

import contextlib
import os
import readline
import sys
from pathlib import Path

from elle.cli.engine import Engine, EngineAction, get_engine
from elle.cli.terminal.renderer import render_banner, render_prompt
from elle.common.session import Session, create_session

# History file location
HISTORY_FILE = Path.home() / ".elle_history"
HISTORY_MAX_LENGTH = 1000


class REPL:
    """Interactive Read-Eval-Print Loop for ELLE.

    Manages the terminal session, readline configuration, and
    routes all input through the shared engine.

    The REPL is just one interface to the engine - the same engine
    is used for one-shot commands, ensuring consistent behavior.
    """

    def __init__(
        self,
        engine: Engine | None = None,
        session: Session | None = None,
        use_colors: bool = True,
    ) -> None:
        """Initialize the REPL.

        Args:
            engine: The engine to use. Defaults to shared instance.
            session: Initial session state. Defaults to fresh session.
            use_colors: Whether to use ANSI colors. Auto-detected if not set.
        """
        self.engine = engine or get_engine()
        self.session = session or create_session()
        self.use_colors = use_colors and self._supports_color()
        self._running = False

    def run(self) -> int:
        """Run the interactive REPL loop.

        Returns:
            Exit code (0 for normal exit).
        """
        self._setup_readline()
        self._running = True

        # Print banner
        print(render_banner(self.session))

        # Show recent reboot status if any
        self._show_reboot_status()

        try:
            while self._running:
                try:
                    # Get input
                    prompt = render_prompt(self.session)
                    user_input = input(prompt)

                    # Process through engine
                    result = self.engine.process(user_input, self.session)

                    # Update session
                    self.session = result.session

                    # Handle output
                    if result.output:
                        print(result.output)

                    # Handle action signals
                    match result.action:
                        case EngineAction.EXIT:
                            self._running = False
                        case EngineAction.CLEAR:
                            self._clear_screen()

                except KeyboardInterrupt:
                    # Ctrl+C - print newline and continue
                    print()
                    continue
                except EOFError:
                    # Ctrl+D - exit
                    print("\nGoodbye.")
                    self._running = False

        finally:
            self._save_history()

        return 0

    def _setup_readline(self) -> None:
        """Configure readline for history and completion."""
        # Set up history file
        with contextlib.suppress(FileNotFoundError):
            readline.read_history_file(HISTORY_FILE)

        readline.set_history_length(HISTORY_MAX_LENGTH)

        # Enable basic completion
        readline.parse_and_bind("tab: complete")

        # Use Emacs-style key bindings
        readline.parse_and_bind("set editing-mode emacs")

        # Handle ANSI escape sequences properly in prompt
        # This tells readline which characters are non-printing
        if self.use_colors:
            readline.parse_and_bind("set enable-bracketed-paste on")

    def _save_history(self) -> None:
        """Save command history to file."""
        with contextlib.suppress(OSError):
            readline.write_history_file(HISTORY_FILE)

    def _clear_screen(self) -> None:
        """Clear the terminal screen."""
        if sys.platform == "win32":
            os.system("cls")
        else:
            # Use ANSI escape sequence
            print("\033[2J\033[H", end="")

    @staticmethod
    def _supports_color() -> bool:
        """Check if the terminal supports ANSI colors.

        Returns:
            True if colors are supported.
        """
        # Check if stdout is a TTY
        if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
            return False

        # Check TERM environment variable
        term = os.environ.get("TERM", "")
        if term == "dumb":
            return False

        # Check NO_COLOR environment variable (standard)
        return not os.environ.get("NO_COLOR")

    def _show_reboot_status(self) -> None:
        """Show recent reboot status on startup.

        Displays active reboot operations or recent issues.
        """
        try:
            from elle.cli.reboot.commands import render_recent_reboots_summary

            summary = render_recent_reboots_summary()
            if summary:
                print(summary)
        except ImportError:
            # Reboot module not available
            pass
        except Exception:
            # Silently ignore errors - don't disrupt REPL startup
            pass


def run() -> None:
    """Launch the ELLE Terminal REPL.

    This is the main entry point, called by the 'elle' command.
    """
    repl = REPL()
    sys.exit(repl.run())


# Also support running as module
if __name__ == "__main__":
    run()
