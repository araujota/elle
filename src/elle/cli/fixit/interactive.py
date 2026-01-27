"""Interactive navigable UI for Fixit in REPL mode.

Provides a state machine-based interface for:
- Browsing fix suggestions
- Viewing detailed explanations
- Applying fixes
- Recording outcomes

Uses box-drawing characters for visual structure and
single-keypress navigation for fluid interaction.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from enum import Enum, auto
from typing import TYPE_CHECKING

from elle.cli.fixit.models import FixitAction, FixitOutcome, VerifiedFixCommand
from elle.cli.terminal.renderer import Colors

if TYPE_CHECKING:
    from elle.cli.fixit.models import FixitResult
    from elle.common.session import Session


# =============================================================================
# Box Drawing Characters
# =============================================================================

BOX_TOP_LEFT = "┌"
BOX_TOP_RIGHT = "┐"
BOX_BOTTOM_LEFT = "└"
BOX_BOTTOM_RIGHT = "┘"
BOX_HORIZONTAL = "─"
BOX_VERTICAL = "│"
BOX_T_LEFT = "├"
BOX_T_RIGHT = "┤"


# =============================================================================
# State Machine
# =============================================================================


class InteractiveState(Enum):
    """States for the interactive UI."""

    LIST = auto()  # Main list view
    DETAIL = auto()  # Single fix detail view
    CONFIRM = auto()  # Confirmation before apply
    OUTCOME = auto()  # Asking about outcome


class FixitInteractive:
    """Interactive navigable UI for fixit in REPL mode.

    Manages state transitions and rendering for the
    interactive fix selection interface.
    """

    WIDTH = 60  # Terminal width for box drawing

    def __init__(self, result: FixitResult) -> None:
        """Initialize the interactive UI.

        Args:
            result: The fixit result to display.
        """
        self.result = result
        self.state = InteractiveState.LIST
        self.selected_index: int | None = None
        self._safe_suggestions = [s for s in result.verified_suggestions if s.is_safe]

    @property
    def safe_suggestions(self) -> list[VerifiedFixCommand]:
        """Get safe suggestions for display."""
        return self._safe_suggestions

    def render(self) -> str:
        """Render the current state.

        Returns:
            Formatted string for terminal display.
        """
        if self.state == InteractiveState.LIST:
            return self._render_list()
        elif self.state == InteractiveState.DETAIL:
            return self._render_detail()
        elif self.state == InteractiveState.CONFIRM:
            return self._render_confirm()
        elif self.state == InteractiveState.OUTCOME:
            return self._render_outcome()
        return ""

    def handle_input(self, key: str) -> tuple[str | None, bool]:
        """Handle keyboard input.

        Args:
            key: The key pressed.

        Returns:
            Tuple of (command_to_execute, should_continue).
            command_to_execute is None if no command should run.
            should_continue is False to exit the interactive loop.
        """
        if self.state == InteractiveState.LIST:
            return self._handle_list_input(key)
        elif self.state == InteractiveState.DETAIL:
            return self._handle_detail_input(key)
        elif self.state == InteractiveState.CONFIRM:
            return self._handle_confirm_input(key)
        elif self.state == InteractiveState.OUTCOME:
            return self._handle_outcome_input(key)
        return None, False

    # =========================================================================
    # List View
    # =========================================================================

    def _render_list(self) -> str:
        """Render the main list view."""
        lines = []

        # Top border with title
        title = " Fixit Analysis "
        pad = (self.WIDTH - len(title) - 2) // 2
        lines.append(
            f"{BOX_TOP_LEFT}{BOX_HORIZONTAL * pad}{title}"
            f"{BOX_HORIZONTAL * (self.WIDTH - pad - len(title) - 2)}{BOX_TOP_RIGHT}"
        )

        # Failed command info
        failure = self.result.context.failure
        lines.append(self._box_line(f"Failed: {Colors.CYAN}{failure.command}{Colors.RESET}"))
        lines.append(self._box_line(f"Exit: {failure.exit_code}"))
        lines.append(self._box_line(""))

        # Diagnosis
        if self.result.analysis:
            diag = self.result.analysis.diagnosis
            conf = f"{diag.confidence * 100:.0f}%"
            lines.append(
                self._box_line(
                    f"{Colors.BOLD}Diagnosis:{Colors.RESET} "
                    f"{diag.error_category.replace('_', ' ')} "
                    f"{Colors.DIM}(confidence: {conf}){Colors.RESET}"
                )
            )
            lines.append(self._box_line(diag.summary))

        # Separator
        lines.append(self._box_separator())

        # Suggestions header
        lines.append(self._box_line(f"{Colors.BOLD}Suggested Fixes:{Colors.RESET}"))
        lines.append(self._box_line(""))

        # Suggestion list
        if not self.safe_suggestions:
            lines.append(self._box_line(f"  {Colors.DIM}No verified fixes available.{Colors.RESET}"))
        else:
            for i, verified in enumerate(self.safe_suggestions, 1):
                fix = verified.fix
                risk_color = {
                    "safe": Colors.GREEN,
                    "moderate": Colors.YELLOW,
                    "high": Colors.RED,
                }.get(fix.risk_level, "")

                lines.append(self._box_line(f"  {Colors.BOLD}[{i}]{Colors.RESET} {fix.explanation[:45]}"))
                lines.append(self._box_line(f"      {Colors.CYAN}{fix.command[:50]}{Colors.RESET}"))

                extras = [f"{risk_color}Risk: {fix.risk_level}{Colors.RESET}"]
                if fix.requires_privilege:
                    extras.append(f"{Colors.YELLOW}Requires: privilege{Colors.RESET}")
                lines.append(self._box_line(f"      {' | '.join(extras)}"))
                lines.append(self._box_line(""))

        # Bottom separator and controls
        lines.append(self._box_separator())

        # Controls
        if self.safe_suggestions:
            num_range = f"1-{len(self.safe_suggestions)}"
            controls = f"[{num_range}] Apply fix  [v] View details  [q] Skip"
        else:
            controls = "[q] Exit"

        lines.append(self._box_line(controls))

        # Bottom border
        lines.append(f"{BOX_BOTTOM_LEFT}{BOX_HORIZONTAL * (self.WIDTH - 2)}{BOX_BOTTOM_RIGHT}")

        return "\n".join(lines)

    def _handle_list_input(self, key: str) -> tuple[str | None, bool]:
        """Handle input in list view."""
        # Number key - select and confirm
        if key.isdigit():
            idx = int(key) - 1
            if 0 <= idx < len(self.safe_suggestions):
                self.selected_index = idx
                self.state = InteractiveState.CONFIRM
                return None, True

        # v + number - view details
        if key == "v":
            # Wait for next key
            return None, True

        # q or Enter - skip
        if key in ("q", "Q", "\r", "\n"):
            return None, False

        return None, True

    # =========================================================================
    # Detail View
    # =========================================================================

    def _render_detail(self) -> str:
        """Render the detail view for a single fix."""
        if self.selected_index is None:
            return self._render_list()

        verified = self.safe_suggestions[self.selected_index]
        fix = verified.fix
        lines = []

        # Title
        title = f" Fix #{self.selected_index + 1}: {fix.explanation[:30]} "
        pad = (self.WIDTH - len(title) - 2) // 2
        lines.append(
            f"{BOX_TOP_LEFT}{BOX_HORIZONTAL * pad}{title}"
            f"{BOX_HORIZONTAL * (self.WIDTH - pad - len(title) - 2)}{BOX_TOP_RIGHT}"
        )

        # Command
        lines.append(self._box_line(f"{Colors.BOLD}Command:{Colors.RESET}"))
        lines.append(self._box_line(f"  {Colors.CYAN}{fix.command}{Colors.RESET}"))
        lines.append(self._box_line(""))

        # Risk
        risk_color = {
            "safe": Colors.GREEN,
            "moderate": Colors.YELLOW,
            "high": Colors.RED,
        }.get(fix.risk_level, "")
        lines.append(self._box_line(f"{Colors.BOLD}Risk:{Colors.RESET} {risk_color}{fix.risk_level}{Colors.RESET}"))

        # Privilege
        if fix.requires_privilege:
            lines.append(
                self._box_line(
                    f"{Colors.BOLD}Privilege:{Colors.RESET} {Colors.YELLOW}Required (Polkit elevation){Colors.RESET}"
                )
            )
        lines.append(self._box_line(""))

        # Explanation
        lines.append(self._box_line(f"{Colors.BOLD}Explanation:{Colors.RESET}"))
        # Wrap explanation text
        for line in self._wrap_text(fix.explanation, self.WIDTH - 6):
            lines.append(self._box_line(f"  {line}"))
        lines.append(self._box_line(""))

        # Verification
        if fix.verification:
            lines.append(self._box_line(f"{Colors.BOLD}Verify with:{Colors.RESET}"))
            lines.append(self._box_line(f"  {Colors.DIM}{fix.verification}{Colors.RESET}"))
            lines.append(self._box_line(""))

        # Warnings
        if verified.verification.warnings:
            lines.append(self._box_line(f"{Colors.YELLOW}Warnings:{Colors.RESET}"))
            for warning in verified.verification.warnings:
                lines.append(self._box_line(f"  - {warning}"))
            lines.append(self._box_line(""))

        # Controls
        lines.append(self._box_separator())
        lines.append(self._box_line("[Enter] Apply this fix  [b] Back to list  [q] Skip all"))
        lines.append(f"{BOX_BOTTOM_LEFT}{BOX_HORIZONTAL * (self.WIDTH - 2)}{BOX_BOTTOM_RIGHT}")

        return "\n".join(lines)

    def _handle_detail_input(self, key: str) -> tuple[str | None, bool]:
        """Handle input in detail view."""
        # Enter - confirm
        if key in ("\r", "\n"):
            self.state = InteractiveState.CONFIRM
            return None, True

        # b - back to list
        if key in ("b", "B"):
            self.state = InteractiveState.LIST
            return None, True

        # q - skip all
        if key in ("q", "Q"):
            return None, False

        return None, True

    # =========================================================================
    # Confirm View
    # =========================================================================

    def _render_confirm(self) -> str:
        """Render confirmation prompt."""
        if self.selected_index is None:
            return self._render_list()

        fix = self.safe_suggestions[self.selected_index].fix
        lines = [
            "",
            f"{Colors.BOLD}Apply this fix?{Colors.RESET}",
            f"  {Colors.CYAN}{fix.command}{Colors.RESET}",
            "",
            "[y] Yes, apply  [n] No, go back",
            "",
        ]
        return "\n".join(lines)

    def _handle_confirm_input(self, key: str) -> tuple[str | None, bool]:
        """Handle input in confirm view."""
        if key in ("y", "Y"):
            # Return command to execute
            if self.selected_index is not None:
                fix = self.safe_suggestions[self.selected_index].fix
                self.state = InteractiveState.OUTCOME
                return fix.command, True

        if key in ("n", "N"):
            self.state = InteractiveState.LIST
            return None, True

        return None, True

    # =========================================================================
    # Outcome View
    # =========================================================================

    def _render_outcome(self) -> str:
        """Render outcome question."""
        lines = [
            "",
            f"{Colors.BOLD}Did this fix the problem?{Colors.RESET}",
            "",
            "  [y] Yes, it's fixed",
            "  [p] Partially fixed",
            "  [n] No change",
            "  [w] Made it worse",
            "  [s] Skip (don't record)",
            "",
        ]
        return "\n".join(lines)

    def _handle_outcome_input(self, key: str) -> tuple[str | None, bool]:
        """Handle input in outcome view."""
        # These signal the outcome and exit
        outcome_map = {
            "y": FixitOutcome.IMPROVED,
            "Y": FixitOutcome.IMPROVED,
            "p": FixitOutcome.PARTIAL,
            "P": FixitOutcome.PARTIAL,
            "n": FixitOutcome.NO_CHANGE,
            "N": FixitOutcome.NO_CHANGE,
            "w": FixitOutcome.WORSE,
            "W": FixitOutcome.WORSE,
            "s": FixitOutcome.SKIPPED,
            "S": FixitOutcome.SKIPPED,
        }

        if key in outcome_map:
            self._selected_outcome = outcome_map[key]
            return None, False

        return None, True

    @property
    def selected_outcome(self) -> FixitOutcome:
        """Get the selected outcome."""
        return getattr(self, "_selected_outcome", FixitOutcome.SKIPPED)

    # =========================================================================
    # Helpers
    # =========================================================================

    def _box_line(self, content: str) -> str:
        """Create a line within the box."""
        # Strip ANSI codes for length calculation
        import re

        plain = re.sub(r"\033\[[0-9;]*m", "", content)
        padding = self.WIDTH - len(plain) - 4
        if padding < 0:
            padding = 0
        return f"{BOX_VERTICAL} {content}{' ' * padding} {BOX_VERTICAL}"

    def _box_separator(self) -> str:
        """Create a separator line."""
        return f"{BOX_T_LEFT}{BOX_HORIZONTAL * (self.WIDTH - 2)}{BOX_T_RIGHT}"

    def _wrap_text(self, text: str, width: int) -> list[str]:
        """Wrap text to specified width."""
        words = text.split()
        lines: list[str] = []
        current: list[str] = []
        current_len = 0

        for word in words:
            if current_len + len(word) + 1 > width:
                lines.append(" ".join(current))
                current = [word]
                current_len = len(word)
            else:
                current.append(word)
                current_len += len(word) + 1

        if current:
            lines.append(" ".join(current))

        return lines


# =============================================================================
# Keypress Reading
# =============================================================================


def get_keypress() -> str:
    """Read a single keypress without requiring Enter.

    Returns:
        The character pressed.
    """
    try:
        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except (ImportError, termios.error, AttributeError):
        # Fallback to regular input
        return input()[0] if input() else "q"


# =============================================================================
# Main Interactive Loop
# =============================================================================


def run_interactive_fixit(
    result: FixitResult,
    session: Session,
    execute_callback: Callable[[FixitResult, str, Session], tuple[FixitResult, FixitAction]] | None = None,
) -> FixitResult:
    """Run the interactive fixit session in REPL mode.

    Args:
        result: The fixit result to display.
        session: Current session state.
        execute_callback: Function to execute commands. If None, uses service.

    Returns:
        Updated FixitResult with actions and outcome.
    """
    # If no suggestions, just render and return
    safe_suggestions = [s for s in result.verified_suggestions if s.is_safe]
    if not safe_suggestions:
        from elle.cli.fixit.renderer import render_fixit_result

        print(render_fixit_result(result))
        return result

    ui = FixitInteractive(result)

    # Get execute callback
    if execute_callback is None:
        from elle.cli.fixit.service import get_fixit_service

        service = get_fixit_service()
        execute_callback = service.execute_fix

    while True:
        # Clear screen and render
        print("\033[2J\033[H", end="")  # Clear screen, move to top
        print(ui.render())

        # Get input
        try:
            key = get_keypress()
        except KeyboardInterrupt:
            break

        # Handle input
        command, should_continue = ui.handle_input(key)

        if command:
            # Execute the command
            print(f"\n{Colors.DIM}Executing: {command}{Colors.RESET}\n")
            result, action = execute_callback(result, command, session)

            # Show result
            if action.success:
                print(f"{Colors.GREEN}Command succeeded{Colors.RESET}")
            else:
                print(f"{Colors.RED}Command failed (exit {action.exit_code}){Colors.RESET}")

            if action.stdout:
                print(action.stdout.rstrip())
            if action.stderr and not action.success:
                print(f"{Colors.RED}{action.stderr.rstrip()}{Colors.RESET}")

            print()
            input("Press Enter to continue...")

        if not should_continue:
            break

    # Finalize with outcome
    result = result.with_outcome(ui.selected_outcome)

    # Record to incident vault
    if result.incident_id:
        try:
            from elle.cli.fixit.service import get_fixit_service

            service = get_fixit_service()
            service.finalize_incident(result.incident_id, result)
        except Exception:
            pass

    return result
