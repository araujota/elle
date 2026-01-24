"""ELLE Terminal REPL - Main interactive loop.

Entry point for ELLE Terminal Mode. Manages the interactive session,
prompt_toolkit input, and dispatches all input through the shared engine.

Features:
- Branded startup banner with system status
- Status bar showing model, daemon, vault stats
- Multi-phase spinners for long operations
- Rich panels for fixit, plans, incidents
- Slash command completion
- History search (Ctrl+R)

Usage:
    $ elle
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from elle.cli.engine import Engine, EngineAction, get_engine
from elle.cli.ui import (
    EllePrompt,
    Icons,
    clear,
    console,
    print_banner,
    print_error,
    print_muted,
    render_welcome_back,
)
from elle.common.session import Session, create_session

if TYPE_CHECKING:
    pass

# History file location
HISTORY_FILE = Path.home() / ".elle_history"


class REPL:
    """Interactive Read-Eval-Print Loop for ELLE.

    Manages the terminal session, prompt_toolkit configuration, and
    routes all input through the shared engine.

    The REPL is just one interface to the engine - the same engine
    is used for one-shot commands, ensuring consistent behavior.
    """

    def __init__(
        self,
        engine: Engine | None = None,
        session: Session | None = None,
    ) -> None:
        """Initialize the REPL.

        Args:
            engine: The engine to use. Defaults to shared instance.
            session: Initial session state. Defaults to fresh session.
        """
        self.engine = engine or get_engine()
        self.session = session or create_session()
        self._running = False

        # Initialize prompt with history
        self.prompt = EllePrompt(
            history_file=HISTORY_FILE,
            show_status_bar=True,
        )

    def run(self) -> int:
        """Run the interactive REPL loop.

        Returns:
            Exit code (0 for normal exit).
        """
        self._running = True

        # Print startup banner
        self._show_banner()

        # Show reboot/incident status
        self._show_startup_notices()

        try:
            while self._running:
                try:
                    # Get input via prompt_toolkit
                    user_input = self.prompt.prompt()

                    if not user_input.strip():
                        continue

                    # Process through engine
                    result = self.engine.process(user_input, self.session)

                    # Update session
                    self.session = result.session

                    # Handle output - use Rich console for rendering
                    if result.output:
                        # Check if it's a renderable or plain text
                        if hasattr(result.output, "__rich__") or hasattr(
                            result.output, "__rich_console__"
                        ):
                            console.print(result.output)
                        else:
                            console.print(result.output)

                    # Update prompt state based on result
                    if result.success:
                        self.prompt.set_state("success")
                    elif result.action == EngineAction.EXIT:
                        self.prompt.set_state("idle")
                    else:
                        self.prompt.set_state("error")

                    # Handle action signals
                    match result.action:
                        case EngineAction.EXIT:
                            self._running = False
                        case EngineAction.CLEAR:
                            clear()

                except KeyboardInterrupt:
                    # Ctrl+C - print newline and reset state
                    console.print()
                    self.prompt.set_state("idle")
                    continue
                except EOFError:
                    # Ctrl+D - exit
                    console.print("\n[muted]Goodbye.[/muted]")
                    self._running = False

        except Exception as e:
            print_error(f"Unexpected error: {e}")
            return 1

        return 0

    def _show_banner(self) -> None:
        """Display the startup banner."""
        # Collect system status
        model = self._get_model_name()
        daemon_status = self._get_daemon_status()
        vault_docs = self._get_vault_docs()
        incidents_open = self._get_open_incidents()

        # Update prompt status bar
        self.prompt.update_status(
            model=model,
            daemon_status=daemon_status,
            vault_docs=vault_docs,
            incidents_open=incidents_open,
        )

        # Print banner
        print_banner(
            model=model,
            daemon_status=daemon_status,
            vault_docs=vault_docs,
            incidents_open=incidents_open,
        )

    def _show_startup_notices(self) -> None:
        """Show important notices on startup (reboots, incidents)."""
        # Check for pending reboot
        pending_reboot = self._check_pending_reboot()

        # Check for new incidents since last session
        new_incidents = self._get_new_incidents()

        if pending_reboot or new_incidents:
            render_welcome_back(
                pending_reboot=pending_reboot,
                new_incidents=new_incidents,
            )

    def _get_model_name(self) -> str | None:
        """Get the current LLM model name."""
        try:
            from elle.rag.llm import get_llm

            llm = get_llm()
            if llm.is_available():
                return llm.model_name
        except Exception:
            pass
        return None

    def _get_daemon_status(self) -> str | None:
        """Get the daemon status."""
        try:
            # Check if daemon is running via PID file or socket
            pid_file = Path("/run/elle/elled.pid")
            if pid_file.exists():
                return "running"
            return "stopped"
        except Exception:
            pass
        return None

    def _get_vault_docs(self) -> int | None:
        """Get the number of indexed Man Vault documents."""
        try:
            from elle.daemon.manvault import get_status

            status = get_status()
            return status.total_docs if status else None
        except Exception:
            pass
        return None

    def _get_open_incidents(self) -> int:
        """Get the count of open incidents."""
        try:
            from elle.daemon.incidents import get_open_count

            return get_open_count()
        except Exception:
            pass
        return 0

    def _check_pending_reboot(self) -> bool:
        """Check if a reboot is pending."""
        try:
            from elle.daemon.reboot import get_reboot_manager

            manager = get_reboot_manager()
            return manager.get_pending() is not None
        except Exception:
            pass
        return False

    def _get_new_incidents(self) -> int:
        """Get count of incidents since last session."""
        # TODO: Track last session time and query incidents
        return 0


def run() -> None:
    """Launch the ELLE Terminal REPL.

    This is the main entry point, called by the 'elle' command.
    """
    repl = REPL()
    sys.exit(repl.run())


# Also support running as module
if __name__ == "__main__":
    run()
