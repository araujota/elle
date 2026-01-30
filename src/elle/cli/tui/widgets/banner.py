"""Startup banner widget for the ELLE TUI.

Renders the ELLE ASCII logo and tagline inside the conversation view
on application startup.
"""

from __future__ import annotations

from rich.align import Align
from rich.text import Text
from textual.widgets import Static

from elle.cli.ui.banner import LOGO, LOGO_SMALL, TAGLINE, VERSION
from elle.cli.ui.theme import Colors


class BannerWidget(Static):
    """Startup banner showing the ELLE logo."""

    DEFAULT_CSS = """
    BannerWidget {
        width: 1fr;
        height: auto;
        padding: 1 2;
        margin: 1 0;
    }
    """

    def __init__(self, *, width: int = 80) -> None:
        super().__init__()
        self._terminal_width = width

    def render(self) -> Align:
        """Render the banner content."""
        logo = LOGO if self._terminal_width >= 50 else LOGO_SMALL
        logo_text = Text(logo.strip(), style=Colors.ACCENT)

        content = Text()
        content.append_text(logo_text)
        content.append("\n")
        content.append(TAGLINE, style=Colors.MUTED)
        content.append(f"  v{VERSION}", style=Colors.MUTED)

        return Align.center(content)
