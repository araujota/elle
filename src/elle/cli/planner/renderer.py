"""Rendering for the System Task Planner.

Provides text output formatting for plans with:
- Unicode box drawing
- Risk-colored output
- Step-by-step display
- Execution progress
"""

from elle.cli.planner.models import (
    CheckResult,
    CommandPlan,
    PlanOutcome,
    PlanResult,
    PlanVerification,
    RiskLevel,
    StepResult,
)
from elle.cli.terminal.renderer import Colors

# =============================================================================
# Box Drawing Characters
# =============================================================================

BOX_CHARS = {
    "top_left": "┌",
    "top_right": "┐",
    "bottom_left": "└",
    "bottom_right": "┘",
    "horizontal": "─",
    "vertical": "│",
    "t_down": "┬",
    "t_up": "┴",
    "t_right": "├",
    "t_left": "┤",
    "cross": "┼",
}

# Compact box variants
COMPACT_BOX = {
    "top_left": "╭",
    "top_right": "╮",
    "bottom_left": "╰",
    "bottom_right": "╯",
    "horizontal": "─",
    "vertical": "│",
}


# =============================================================================
# Risk Colors
# =============================================================================


def risk_color(risk: RiskLevel) -> str:
    """Get color code for risk level.

    Args:
        risk: Risk level.

    Returns:
        ANSI color code.
    """
    colors = {
        "low": Colors.GREEN,
        "medium": Colors.YELLOW,
        "high": Colors.RED,
    }
    return colors.get(risk, Colors.RESET)


def risk_badge(risk: RiskLevel) -> str:
    """Get colored risk badge.

    Args:
        risk: Risk level.

    Returns:
        Colored badge string.
    """
    color = risk_color(risk)
    return f"{color}[{risk.upper()}]{Colors.RESET}"


# =============================================================================
# Box Rendering
# =============================================================================


def render_box(
    title: str,
    content: list[str],
    width: int = 70,
    title_color: str = Colors.BOLD,
    border_color: str = Colors.DIM,
    rounded: bool = True,
) -> str:
    """Render content in a unicode box.

    Args:
        title: Box title.
        content: Lines of content.
        width: Box width.
        title_color: Color for title.
        border_color: Color for border.
        rounded: Use rounded corners.

    Returns:
        Rendered box as string.
    """
    box = COMPACT_BOX if rounded else BOX_CHARS
    inner_width = width - 2

    lines = []

    # Top border with title
    title_display = f" {title} "
    padding = inner_width - len(title) - 2
    left_pad = padding // 2
    right_pad = padding - left_pad

    lines.append(
        f"{border_color}{box['top_left']}"
        f"{box['horizontal'] * left_pad}"
        f"{Colors.RESET}{title_color}{title_display}{Colors.RESET}"
        f"{border_color}{box['horizontal'] * right_pad}"
        f"{box['top_right']}{Colors.RESET}"
    )

    # Content lines
    for line in content:
        # Strip ANSI codes for length calculation
        visible_len = len(_strip_ansi(line))
        padding = inner_width - visible_len
        if padding < 0:
            # Truncate line
            line = line[: inner_width - 3] + "..."
            padding = 0
        lines.append(
            f"{border_color}{box['vertical']}{Colors.RESET}"
            f"{line}{' ' * padding}"
            f"{border_color}{box['vertical']}{Colors.RESET}"
        )

    # Bottom border
    lines.append(
        f"{border_color}{box['bottom_left']}{box['horizontal'] * inner_width}{box['bottom_right']}{Colors.RESET}"
    )

    return "\n".join(lines)


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from text.

    Args:
        text: Text with ANSI codes.

    Returns:
        Text without ANSI codes.
    """
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# =============================================================================
# Plan Rendering
# =============================================================================


def render_plan(plan: CommandPlan, verification: PlanVerification | None = None) -> str:
    """Render a command plan for preview.

    Args:
        plan: The command plan.
        verification: Optional verification results.

    Returns:
        Formatted plan string.
    """
    lines = []

    # Plan header
    risk_badge_str = risk_badge(plan.overall_risk)
    priv_str = f" {Colors.YELLOW}[PRIVILEGES REQUIRED]{Colors.RESET}" if plan.requires_privilege else ""

    header_content = [
        f"{plan.explanation}",
        "",
        f"Risk: {risk_badge_str}{priv_str}",
        f"Steps: {plan.step_count}  Checks: {len(plan.checks)}  Rollback: {'Yes' if plan.has_rollback else 'No'}",
    ]

    if plan.grounded_in:
        header_content.append(f"Grounded in: {', '.join(plan.grounded_in)}")

    lines.append(
        render_box(
            f"Plan: {plan.title}",
            header_content,
            title_color=Colors.BOLD_CYAN,
        )
    )
    lines.append("")

    # Steps
    lines.append(f"{Colors.BOLD}Steps:{Colors.RESET}")
    for i, step in enumerate(plan.steps, 1):
        step_risk = risk_badge(step.risk_level)
        priv = f" {Colors.DIM}(privileged){Colors.RESET}" if step.requires_privilege else ""

        lines.append(f"  {Colors.CYAN}{i}.{Colors.RESET} {step_risk}{priv}")
        lines.append(f"     {Colors.DIM}${Colors.RESET} {step.command}")
        lines.append(f"     {Colors.DIM}{step.explanation}{Colors.RESET}")
        lines.append("")

    # Validation checks
    if plan.checks:
        lines.append(f"{Colors.BOLD}Validation Checks:{Colors.RESET}")
        for i, check in enumerate(plan.checks, 1):
            lines.append(f"  {i}. {check.description}")
            lines.append(f"     {Colors.DIM}${Colors.RESET} {check.command}")
        lines.append("")

    # Rollback
    if plan.rollback:
        lines.append(f"{Colors.BOLD}Rollback (if needed):{Colors.RESET}")
        for i, rb in enumerate(plan.rollback, 1):
            lines.append(f"  {i}. {rb.explanation}")
            lines.append(f"     {Colors.DIM}${Colors.RESET} {rb.command}")
        lines.append("")

    # Risks
    if plan.risks:
        lines.append(f"{Colors.BOLD_YELLOW}Risks:{Colors.RESET}")
        for risk in plan.risks:
            lines.append(f"  {Colors.YELLOW}!{Colors.RESET} {risk}")
        lines.append("")

    # Verification warnings
    if verification and verification.warnings:
        lines.append(f"{Colors.BOLD_YELLOW}Warnings:{Colors.RESET}")
        for warning in verification.warnings:
            lines.append(f"  {Colors.YELLOW}!{Colors.RESET} {warning}")
        lines.append("")

    # Verification errors
    if verification and verification.errors:
        lines.append(f"{Colors.BOLD_RED}Errors:{Colors.RESET}")
        for error in verification.errors:
            lines.append(f"  {Colors.RED}!{Colors.RESET} {error}")
        lines.append("")

    return "\n".join(lines)


def render_confirmation_prompt(plan: CommandPlan, auto_approve: bool = False) -> str:
    """Render confirmation prompt for plan approval.

    Args:
        plan: The command plan.
        auto_approve: Whether auto-approval is available.

    Returns:
        Confirmation prompt string.
    """
    if auto_approve:
        return f"{Colors.DIM}This is a low-risk operation. Press Enter to continue or 'n' to cancel.{Colors.RESET}"

    risk = plan.overall_risk
    if risk == "high":
        return (
            f"{Colors.BOLD_RED}This is a HIGH-RISK operation.{Colors.RESET}\n"
            f"Type 'yes' to proceed, 'r' to review, or 'n' to cancel: "
        )
    elif risk == "medium":
        return (
            f"{Colors.YELLOW}This operation will modify your system.{Colors.RESET}\n"
            f"Press 'y' to proceed, 'r' to review, or 'n' to cancel: "
        )
    else:
        return "Proceed? [Y/n/r(review)]: "


def render_step_progress(step_index: int, total: int, command: str) -> str:
    """Render step execution progress.

    Args:
        step_index: Current step (0-based).
        total: Total steps.
        command: Command being executed.

    Returns:
        Progress string.
    """
    return f"{Colors.CYAN}[{step_index + 1}/{total}]{Colors.RESET} Executing: {Colors.DIM}${Colors.RESET} {command}"


def render_step_result(result: StepResult, step_explanation: str = "") -> str:
    """Render a step execution result.

    Args:
        result: The step result.
        step_explanation: Optional explanation.

    Returns:
        Result string.
    """
    if result.success:
        status = f"{Colors.GREEN}OK{Colors.RESET}"
    else:
        status = f"{Colors.RED}FAILED (exit {result.exit_code}){Colors.RESET}"

    lines = [f"  [{status}] {Colors.DIM}${Colors.RESET} {result.command}"]

    # Show stderr on failure
    if not result.success and result.stderr:
        stderr_lines = result.stderr.strip().split("\n")[:3]
        for line in stderr_lines:
            lines.append(f"    {Colors.RED}{line}{Colors.RESET}")

    return "\n".join(lines)


def render_check_result(result: CheckResult) -> str:
    """Render a validation check result.

    Args:
        result: The check result.

    Returns:
        Check result string.
    """
    if result.passed:
        status = f"{Colors.GREEN}PASS{Colors.RESET}"
    else:
        status = f"{Colors.RED}FAIL{Colors.RESET}"

    return f"  [{status}] {result.command}"


def render_execution_summary(result: PlanResult) -> str:
    """Render execution summary.

    Args:
        result: The plan result.

    Returns:
        Summary string.
    """
    lines = []

    # Outcome box
    outcome_colors = {
        PlanOutcome.SUCCESS: Colors.BOLD_GREEN,
        PlanOutcome.PARTIAL: Colors.BOLD_YELLOW,
        PlanOutcome.FAILED: Colors.BOLD_RED,
        PlanOutcome.ROLLED_BACK: Colors.YELLOW,
        PlanOutcome.CANCELLED: Colors.DIM,
        PlanOutcome.ERROR: Colors.BOLD_RED,
    }
    color = outcome_colors.get(result.outcome, Colors.RESET)
    outcome_text = result.outcome.value.upper().replace("_", " ")

    content = [
        f"Outcome: {color}{outcome_text}{Colors.RESET}",
        "",
        f"Steps: {result.steps_succeeded} succeeded, {result.steps_failed} failed",
    ]

    if result.check_results:
        content.append(f"Checks: {result.checks_passed} passed, {result.checks_failed} failed")

    if result.rollback_results:
        rb_success = sum(1 for r in result.rollback_results if r.success)
        content.append(f"Rollback: {rb_success}/{len(result.rollback_results)} steps completed")

    if result.incident_id:
        content.append(f"{Colors.DIM}Incident: {result.incident_id[:8]}...{Colors.RESET}")

    lines.append(render_box("Execution Summary", content, title_color=color))

    return "\n".join(lines)


def render_plan_result(result: PlanResult) -> str:
    """Render complete plan result (for one-shot mode).

    Args:
        result: The plan result.

    Returns:
        Complete result string.
    """
    lines = []

    if result.plan is None:
        lines.append(f"{Colors.RED}Failed to generate plan{Colors.RESET}")
        return "\n".join(lines)

    # Plan preview
    lines.append(render_plan(result.plan, result.verification))

    # Verification status
    if result.verification:
        if result.verification.is_valid:
            lines.append(f"{Colors.GREEN}Plan verified successfully{Colors.RESET}")
        else:
            lines.append(f"{Colors.RED}Plan verification failed:{Colors.RESET}")
            for error in result.verification.errors:
                lines.append(f"  {Colors.RED}!{Colors.RESET} {error}")
        lines.append("")

    # Execution results
    if result.step_results:
        lines.append(f"{Colors.BOLD}Execution Results:{Colors.RESET}")
        for i, step_result in enumerate(result.step_results):
            explanation = ""
            if result.plan and i < len(result.plan.steps):
                explanation = result.plan.steps[i].explanation
            lines.append(render_step_result(step_result, explanation))
        lines.append("")

    # Check results
    if result.check_results:
        lines.append(f"{Colors.BOLD}Validation Results:{Colors.RESET}")
        for check_result in result.check_results:
            lines.append(render_check_result(check_result))
        lines.append("")

    # Summary
    if result.outcome != PlanOutcome.CANCELLED:
        lines.append(render_execution_summary(result))

    return "\n".join(lines)
