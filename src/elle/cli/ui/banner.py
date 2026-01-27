"""ELLE Startup Banner.

Displays the branded logo and system status on REPL startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.align import Align
from rich.panel import Panel
from rich.text import Text

from elle.cli.ui.console import console
from elle.cli.ui.theme import Colors, Icons

if TYPE_CHECKING:
    pass

# Hand-crafted ASCII logo - compact and readable
LOGO = r"""
███████╗██╗     ██╗     ███████╗
██╔════╝██║     ██║     ██╔════╝
█████╗  ██║     ██║     █████╗
██╔══╝  ██║     ██║     ██╔══╝
███████╗███████╗███████╗███████╗
╚══════╝╚══════╝╚══════╝╚══════╝
"""

# Smaller logo variant for narrow terminals
LOGO_SMALL = r"""
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
██ ▄▄▄ ██ ██ ██ ▄▄▄ ██
██ ███ ██ ██ ██ ███ ██
██▄▄▄▄▄██▄██▄██▄▄▄▄▄██
▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
"""

# Minimal variant
LOGO_MINIMAL = "ELLE"

TAGLINE = "local-first system intelligence"

VERSION = "0.1.0"


def _get_logo() -> str:
    """Select logo based on terminal width."""
    width = console.width or 80
    if width >= 50:
        return LOGO
    elif width >= 30:
        return LOGO_SMALL
    return LOGO_MINIMAL


def _build_status_line(
    *,
    model: str | None = None,
    daemon_status: str | None = None,
    api_port: int | None = None,
    vault_docs: int | None = None,
    incidents_open: int | None = None,
) -> Text:
    """Build the status subtitle line."""
    parts: list[tuple[str, str]] = []

    # Model
    if model:
        parts.append((f"Model: {model}", "muted"))

    # Daemon + API
    if daemon_status:
        color = "success" if daemon_status == "running" else "warning"
        if daemon_status == "running" and api_port:
            parts.append((f"Daemon: {daemon_status} (API :{api_port})", color))
        else:
            parts.append((f"Daemon: {daemon_status}", color))

    # Vault
    if vault_docs is not None:
        parts.append((f"Vault: {vault_docs:,} docs", "muted"))

    # Open incidents
    if incidents_open is not None and incidents_open > 0:
        parts.append((f"Incidents: {incidents_open} open", "warning"))

    if not parts:
        return Text(TAGLINE, style="muted")

    text = Text()
    for i, (content, style) in enumerate(parts):
        if i > 0:
            text.append(" │ ", style="muted")
        text.append(content, style=style)
    return text


def render_banner(
    *,
    model: str | None = None,
    daemon_status: str | None = None,
    api_port: int | None = None,
    vault_docs: int | None = None,
    incidents_open: int | None = None,
    show_tips: bool = True,
) -> Panel:
    """Render the startup banner as a rich Panel.

    Args:
        model: Current LLM model name
        daemon_status: "running", "stopped", etc.
        api_port: Port the API is listening on (if running)
        vault_docs: Number of indexed man pages
        incidents_open: Number of open incidents
        show_tips: Whether to show helpful tips

    Returns:
        Rich Panel renderable
    """
    logo = _get_logo()
    logo_text = Text(logo.strip(), style=Colors.ACCENT)

    # Build subtitle
    subtitle = _build_status_line(
        model=model,
        daemon_status=daemon_status,
        api_port=api_port,
        vault_docs=vault_docs,
        incidents_open=incidents_open,
    )

    # Centered content
    content = Text()
    content.append_text(logo_text)
    content.append("\n")
    content.append_text(subtitle)

    panel = Panel(
        Align.center(content),
        border_style=Colors.ACCENT_DIM,
        padding=(1, 2),
        subtitle=f"v{VERSION}" if console.width and console.width >= 50 else None,
        subtitle_align="right",
    )

    return panel


def print_banner(
    *,
    model: str | None = None,
    daemon_status: str | None = None,
    api_port: int | None = None,
    vault_docs: int | None = None,
    incidents_open: int | None = None,
    show_tips: bool = True,
) -> None:
    """Print the startup banner to console.

    Args:
        model: Current LLM model name
        daemon_status: "running", "stopped", etc.
        api_port: Port the API is listening on (if running)
        vault_docs: Number of indexed man pages
        incidents_open: Number of open incidents
        show_tips: Whether to show helpful tips
    """
    panel = render_banner(
        model=model,
        daemon_status=daemon_status,
        api_port=api_port,
        vault_docs=vault_docs,
        incidents_open=incidents_open,
        show_tips=show_tips,
    )
    console.print(panel)

    if show_tips:
        _print_startup_tips()


def _print_startup_tips() -> None:
    """Print helpful tips after banner."""
    tips = [
        "Type [accent]/help[/accent] for commands",
        "Type [accent]exit[/accent] or press [accent]Ctrl+D[/accent] to quit",
    ]
    tip_text = " │ ".join(tips)
    console.print(f"[muted]{tip_text}[/muted]")
    console.print()  # Blank line before prompt


def render_welcome_back(
    *,
    last_session: str | None = None,
    pending_reboot: bool = False,
    new_incidents: int = 0,
) -> None:
    """Render a brief welcome back message for returning users.

    Shows important state changes since last session.
    """
    messages: list[str] = []

    if pending_reboot:
        messages.append(f"[warning]{Icons.WARNING} Reboot pending[/warning]")

    if new_incidents > 0:
        messages.append(f"[info]{Icons.INFO} {new_incidents} new incident(s)[/info]")

    if messages:
        for msg in messages:
            console.print(msg)
        console.print()
