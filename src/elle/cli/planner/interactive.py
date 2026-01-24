"""Interactive UI for the System Task Planner.

Provides REPL-mode interaction for:
- Plan preview with approval flow
- Step-by-step execution with progress
- Rollback on failure
- Outcome confirmation
"""

import sys
from enum import Enum, auto

from elle.cli.planner.models import (
    CommandPlan,
    PlanOutcome,
    PlanResult,
)
from elle.cli.planner.renderer import (
    render_box,
    render_check_result,
    render_confirmation_prompt,
    render_execution_summary,
    render_plan,
    render_step_progress,
    render_step_result,
    risk_badge,
)
from elle.cli.planner.service import get_planner_service
from elle.cli.planner.verifier import can_auto_approve
from elle.cli.terminal.renderer import Colors
from elle.common.session import Session


class InteractiveState(Enum):
    """States for interactive plan execution."""

    PREVIEW = auto()
    CONFIRM = auto()
    EXECUTING = auto()
    CHECKING = auto()
    ROLLBACK_PROMPT = auto()
    ROLLING_BACK = auto()
    COMPLETE = auto()


class PlannerInteractive:
    """Interactive UI for plan approval and execution."""

    def __init__(self, result: PlanResult, session: Session) -> None:
        """Initialize interactive planner.

        Args:
            result: PlanResult from planning pipeline.
            session: Current session state.
        """
        self.result = result
        self.session = session
        self.state = InteractiveState.PREVIEW
        self.auto_approve = False

    def render_preview(self) -> str:
        """Render plan preview.

        Returns:
            Preview string.
        """
        if self.result.plan is None:
            return f"{Colors.RED}No plan generated{Colors.RESET}"

        lines = []
        lines.append(render_plan(self.result.plan, self.result.verification))

        # Verification status
        if self.result.verification:
            if self.result.verification.is_valid:
                # Check for auto-approval
                can_auto, reason = can_auto_approve(self.result.plan)
                self.auto_approve = can_auto
                if can_auto:
                    lines.append(
                        f"{Colors.GREEN}Low-risk operation - can proceed automatically{Colors.RESET}"
                    )
            else:
                lines.append(f"{Colors.RED}Plan has validation errors:{Colors.RESET}")
                for error in self.result.verification.errors:
                    lines.append(f"  {Colors.RED}!{Colors.RESET} {error}")

        return "\n".join(lines)

    def render_confirmation(self) -> str:
        """Render confirmation prompt.

        Returns:
            Confirmation prompt string.
        """
        if self.result.plan is None:
            return ""
        return render_confirmation_prompt(self.result.plan, self.auto_approve)

    def handle_input(self, user_input: str) -> tuple[str | None, bool]:
        """Handle user input during interactive session.

        Args:
            user_input: User's response.

        Returns:
            Tuple of (output message, should_continue).
        """
        inp = user_input.strip().lower()

        if self.state == InteractiveState.PREVIEW:
            return self._handle_preview_input(inp)
        elif self.state == InteractiveState.CONFIRM:
            return self._handle_confirm_input(inp)
        elif self.state == InteractiveState.ROLLBACK_PROMPT:
            return self._handle_rollback_prompt(inp)

        return None, False

    def _handle_preview_input(self, inp: str) -> tuple[str | None, bool]:
        """Handle input during preview state."""
        if self.result.verification and not self.result.verification.is_valid:
            # Plan has errors, can't proceed
            self.result = self.result.with_outcome(PlanOutcome.CANCELLED)
            return f"{Colors.YELLOW}Plan cancelled due to validation errors{Colors.RESET}", False

        # Move to confirmation
        self.state = InteractiveState.CONFIRM
        return self.render_confirmation(), True

    def _handle_confirm_input(self, inp: str) -> tuple[str | None, bool]:
        """Handle input during confirmation state."""
        if inp in ("", "y", "yes") or (self.auto_approve and inp == ""):
            # Approved - will execute
            return None, False  # Signal to execute
        elif inp in ("n", "no"):
            self.result = self.result.with_outcome(PlanOutcome.CANCELLED)
            return f"{Colors.YELLOW}Plan cancelled{Colors.RESET}", False
        elif inp == "r":
            # Review again
            self.state = InteractiveState.PREVIEW
            return self.render_preview(), True
        else:
            return "Please enter 'y' to proceed, 'n' to cancel, or 'r' to review", True

    def _handle_rollback_prompt(self, inp: str) -> tuple[str | None, bool]:
        """Handle input during rollback prompt."""
        if inp in ("y", "yes"):
            return None, False  # Signal to rollback
        elif inp in ("n", "no"):
            return None, False  # Skip rollback
        else:
            return "Rollback changes? [y/n]: ", True


def run_interactive_planner(result: PlanResult, session: Session) -> PlanResult:
    """Run interactive plan approval and execution.

    Args:
        result: PlanResult from planning pipeline.
        session: Current session state.

    Returns:
        Final PlanResult after execution.
    """
    if result.plan is None:
        print(f"{Colors.RED}No plan was generated{Colors.RESET}")
        return result.with_outcome(PlanOutcome.ERROR)

    if result.verification and not result.verification.is_valid:
        print(f"{Colors.RED}Plan failed verification:{Colors.RESET}")
        for error in result.verification.errors:
            print(f"  {Colors.RED}!{Colors.RESET} {error}")
        return result.with_outcome(PlanOutcome.CANCELLED)

    ui = PlannerInteractive(result, session)

    # Show preview
    print(ui.render_preview())
    print()

    # Get confirmation
    can_auto, _ = can_auto_approve(result.plan)

    if can_auto:
        prompt = f"{Colors.DIM}Press Enter to proceed or 'n' to cancel:{Colors.RESET} "
    elif result.plan.overall_risk == "high":
        prompt = (
            f"{Colors.BOLD_RED}HIGH-RISK operation.{Colors.RESET} "
            f"Type 'yes' to proceed, or 'n' to cancel: "
        )
    else:
        prompt = "Proceed? [Y/n]: "

    try:
        response = input(prompt).strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return result.with_outcome(PlanOutcome.CANCELLED)

    # Handle response
    if response in ("n", "no"):
        print(f"\n{Colors.YELLOW}Cancelled{Colors.RESET}")
        return result.with_outcome(PlanOutcome.CANCELLED)

    if result.plan.overall_risk == "high" and response != "yes":
        print(f"\n{Colors.YELLOW}Cancelled (must type 'yes' for high-risk){Colors.RESET}")
        return result.with_outcome(PlanOutcome.CANCELLED)

    # Execute plan
    print()
    print(f"{Colors.BOLD}Executing plan...{Colors.RESET}")
    print()

    service = get_planner_service()
    result = service.execute_plan(result, session)

    # Show step results
    if result.plan:
        for i, step_result in enumerate(result.step_results):
            explanation = ""
            if i < len(result.plan.steps):
                explanation = result.plan.steps[i].explanation
            print(render_step_result(step_result, explanation))

    # Show check results
    if result.check_results:
        print()
        print(f"{Colors.BOLD}Validation:{Colors.RESET}")
        for check_result in result.check_results:
            print(render_check_result(check_result))

    # Handle failure with rollback option
    if result.outcome == PlanOutcome.FAILED and result.plan and result.plan.has_rollback:
        print()
        try:
            response = input(
                f"{Colors.YELLOW}Execution failed. Rollback changes? [Y/n]:{Colors.RESET} "
            ).strip().lower()

            if response not in ("n", "no"):
                print()
                print(f"{Colors.BOLD}Rolling back...{Colors.RESET}")
                result = service.rollback_plan(result, session)

                for rb_result in result.rollback_results:
                    print(render_step_result(rb_result))

        except (KeyboardInterrupt, EOFError):
            print()

    # Finalize
    result = service.finalize(result)

    # Show summary
    print()
    print(render_execution_summary(result))

    return result


def run_noninteractive_planner(
    result: PlanResult,
    session: Session,
    auto_approve: bool = False,
) -> PlanResult:
    """Run non-interactive plan execution (for scripts/one-shot).

    Args:
        result: PlanResult from planning pipeline.
        session: Current session state.
        auto_approve: Whether to auto-approve the plan.

    Returns:
        Final PlanResult.
    """
    from elle.cli.planner.renderer import render_plan_result

    if result.plan is None:
        return result.with_outcome(PlanOutcome.ERROR)

    if result.verification and not result.verification.is_valid:
        return result.with_outcome(PlanOutcome.CANCELLED)

    # Check if we can auto-approve
    can_auto, reason = can_auto_approve(result.plan)

    if not auto_approve and not can_auto:
        # Can't execute without approval
        return result

    service = get_planner_service()
    result = service.execute_plan(result, session)
    result = service.finalize(result)

    return result
