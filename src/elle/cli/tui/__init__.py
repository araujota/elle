"""ELLE Full-Screen TUI.

Textual-based terminal user interface for ELLE.

Usage:
    from elle.cli.tui import launch_tui
    launch_tui()
"""

from __future__ import annotations


def launch_tui() -> None:
    """Launch the ELLE full-screen TUI application."""
    from elle.cli.tui.app import ElleApp

    app = ElleApp()
    app.run()
