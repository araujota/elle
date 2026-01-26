"""Output renderer for ELLE Terminal.

DEPRECATED: This module provides legacy ANSI-based rendering.
New code should use elle.cli.ui instead.

Handles formatting and display of:
- Prompts (elle ▸, elle ⚠ ▸, elle ✓ ▸)
- Shell command output
- System explanations
- Command Plan Previews
- Fixit suggestions
- Status and navigation output
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Re-export from new UI system for backwards compatibility
from elle.cli.ui import (
    Icons,
)

if TYPE_CHECKING:
    from elle.common.models import CommandPlan
    from elle.common.session import Session


# Legacy ANSI color codes - kept for backwards compatibility
class Colors:
    """ANSI escape codes for terminal colors.

    DEPRECATED: Use elle.cli.ui.theme.Colors with Rich markup instead.
    """

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"

    BOLD_RED = "\033[1;31m"
    BOLD_GREEN = "\033[1;32m"
    BOLD_YELLOW = "\033[1;33m"
    BOLD_CYAN = "\033[1;36m"


# Prompt characters - now use Icons from theme
PROMPT_IDLE = Icons.PROMPT_IDLE
PROMPT_WARNING = Icons.PROMPT_WARNING
PROMPT_SUCCESS = Icons.PROMPT_SUCCESS


def render_prompt(
    session: Session,
    has_warnings: bool = False,
) -> str:
    """Render the ELLE prompt based on session state.

    DEPRECATED: The REPL now uses elle.cli.ui.prompt.EllePrompt.

    Prompt variants:
    - elle ▸     idle / no previous command
    - elle ⚠ ▸   system warnings present
    - elle ✓ ▸   last action succeeded

    Args:
        session: Current session state.
        has_warnings: Whether system warnings are present.

    Returns:
        Formatted prompt string with ANSI colors.
    """
    prefix = f"{Colors.BOLD_CYAN}elle{Colors.RESET}"

    if has_warnings:
        indicator = f" {Colors.BOLD_YELLOW}{PROMPT_WARNING}{Colors.RESET}"
    elif session.last_exit == 0:
        indicator = f" {Colors.BOLD_GREEN}{PROMPT_SUCCESS}{Colors.RESET}"
    else:
        indicator = ""

    return f"{prefix}{indicator} {Colors.BOLD}{PROMPT_IDLE}{Colors.RESET} "


def render_prompt_plain(
    session: Session,
    has_warnings: bool = False,
) -> str:
    """Render the ELLE prompt without ANSI colors.

    For environments that don't support colors.

    Args:
        session: Current session state.
        has_warnings: Whether system warnings are present.

    Returns:
        Plain prompt string.
    """
    if has_warnings:
        return f"elle {PROMPT_WARNING} {PROMPT_IDLE} "
    elif session.last_exit == 0:
        return f"elle {PROMPT_SUCCESS} {PROMPT_IDLE} "
    return f"elle {PROMPT_IDLE} "


def render_plan_preview(plan: CommandPlan) -> str:
    """Render a CommandPlan for user confirmation.

    DEPRECATED: Use elle.cli.ui.panels.plan_panel() instead for Rich output.

    Args:
        plan: The command plan to preview.

    Returns:
        Formatted preview string.
    """
    lines = []

    # Explanation
    lines.append(f"{Colors.BOLD}Plan:{Colors.RESET}")
    lines.append(f"  {plan.explanation}")
    lines.append("")

    # Commands
    lines.append(f"{Colors.BOLD}Commands:{Colors.RESET}")
    for cmd in plan.commands:
        lines.append(f"  {Colors.CYAN}{cmd}{Colors.RESET}")
    lines.append("")

    # Rollback
    if plan.rollback:
        lines.append(f"{Colors.BOLD}Rollback:{Colors.RESET}")
        for cmd in plan.rollback:
            lines.append(f"  {Colors.DIM}{cmd}{Colors.RESET}")
        lines.append("")

    # Risks
    if plan.risks:
        lines.append(f"{Colors.BOLD_YELLOW}Risks:{Colors.RESET}")
        for risk in plan.risks:
            lines.append(f"  {Colors.YELLOW}• {risk}{Colors.RESET}")
        lines.append("")

    # Confirmation prompt
    lines.append(f"{Colors.BOLD}Apply? [y/N]{Colors.RESET} ")

    return "\n".join(lines)


def render_error(message: str) -> str:
    """Render an error message.

    Args:
        message: The error message.

    Returns:
        Formatted error string with color.
    """
    return f"{Colors.BOLD_RED}Error:{Colors.RESET} {message}"


def render_success(message: str) -> str:
    """Render a success message.

    Args:
        message: The success message.

    Returns:
        Formatted success string with color.
    """
    return f"{Colors.BOLD_GREEN}{Icons.SUCCESS}{Colors.RESET} {message}"


def render_warning(message: str) -> str:
    """Render a warning message.

    Args:
        message: The warning message.

    Returns:
        Formatted warning string with color.
    """
    return f"{Colors.BOLD_YELLOW}Warning:{Colors.RESET} {message}"


def render_banner(session: Session) -> str:
    """Render the startup banner.

    DEPRECATED: Use elle.cli.ui.banner.print_banner() instead.

    Args:
        session: Current session state.

    Returns:
        Formatted banner string.
    """
    lines = [
        f"{Colors.BOLD_CYAN}ELLE{Colors.RESET} — Ubuntu System Assistant",
        f"{Colors.DIM}Type 'help' for commands or just start typing.{Colors.RESET}",
        "",
    ]
    return "\n".join(lines)
