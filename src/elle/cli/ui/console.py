"""Rich Console singleton for ELLE.

All terminal output should go through these console instances
to ensure consistent styling and proper terminal handling.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.console import Console
from rich.theme import Theme as RichTheme

from elle.cli.ui.theme import Colors

if TYPE_CHECKING:
    from rich.console import RenderableType

# Custom theme mapping ELLE colors to rich styles
_ELLE_THEME = RichTheme(
    {
        # Semantic
        "success": Colors.SUCCESS,
        "warning": Colors.WARNING,
        "error": Colors.ERROR,
        "info": Colors.INFO,
        # Accent
        "accent": Colors.ACCENT,
        "accent.dim": Colors.ACCENT_DIM,
        "highlight": Colors.HIGHLIGHT,
        # Risk
        "risk.low": Colors.RISK_LOW,
        "risk.medium": Colors.RISK_MEDIUM,
        "risk.high": Colors.RISK_HIGH,
        # Severity
        "severity.info": Colors.SEVERITY_INFO,
        "severity.warning": Colors.SEVERITY_WARNING,
        "severity.error": Colors.SEVERITY_ERROR,
        "severity.critical": Colors.SEVERITY_CRITICAL,
        # Outcome
        "outcome.improved": Colors.OUTCOME_IMPROVED,
        "outcome.partial": Colors.OUTCOME_PARTIAL,
        "outcome.no_change": Colors.OUTCOME_NO_CHANGE,
        "outcome.worse": Colors.OUTCOME_WORSE,
        # UI
        "muted": Colors.MUTED,
        "border": Colors.BORDER,
        "border.focus": Colors.BORDER_FOCUS,
        "code": Colors.CODE,
        "path": Colors.PATH,
        "command": Colors.COMMAND,
        # Domains
        "domain.net": Colors.DOMAIN_NET,
        "domain.disk": Colors.DOMAIN_DISK,
        "domain.oom": Colors.DOMAIN_OOM,
        "domain.docker": Colors.DOMAIN_DOCKER,
        "domain.auth": Colors.DOMAIN_AUTH,
        "domain.pkg": Colors.DOMAIN_PKG,
        "domain.service": Colors.DOMAIN_SERVICE,
        "domain.fs": Colors.DOMAIN_FS,
    }
)

# Main console for stdout
console = Console(
    theme=_ELLE_THEME,
    highlight=False,  # We control highlighting explicitly
    soft_wrap=True,
)

# Error console for stderr
err_console = Console(
    theme=_ELLE_THEME,
    stderr=True,
    highlight=False,
    soft_wrap=True,
)


def is_interactive() -> bool:
    """Check if we're in an interactive terminal."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def terminal_width() -> int:
    """Get current terminal width, with sensible default."""
    return console.width or 80


def terminal_height() -> int:
    """Get current terminal height, with sensible default."""
    return console.height or 24


def print_error(message: str, *, prefix: str = "Error") -> None:
    """Print an error message to stderr."""
    err_console.print(f"[error]{prefix}:[/error] {message}")


def print_warning(message: str, *, prefix: str = "Warning") -> None:
    """Print a warning message."""
    console.print(f"[warning]{prefix}:[/warning] {message}")


def print_success(message: str, *, prefix: str | None = None) -> None:
    """Print a success message."""
    if prefix:
        console.print(f"[success]{prefix}:[/success] {message}")
    else:
        console.print(f"[success]{message}[/success]")


def print_info(message: str, *, prefix: str | None = None) -> None:
    """Print an info message."""
    if prefix:
        console.print(f"[info]{prefix}:[/info] {message}")
    else:
        console.print(f"[info]{message}[/info]")


def print_muted(message: str) -> None:
    """Print a muted/dimmed message."""
    console.print(f"[muted]{message}[/muted]")


def rule(title: str = "", *, style: str = "border") -> None:
    """Print a horizontal rule."""
    console.rule(title, style=style)


def clear() -> None:
    """Clear the terminal screen."""
    console.clear()


def print_renderable(renderable: "RenderableType") -> None:
    """Print any rich renderable object."""
    console.print(renderable)
