"""Non-interactive text renderer for Fixit.

Renders fixit results as plain text output for one-shot mode
and fallback display. Supports ANSI colors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from elle.cli.terminal.renderer import Colors

if TYPE_CHECKING:
    from elle.cli.fixit.models import FixitResult, VerifiedFixCommand


# Risk level colors
RISK_COLORS = {
    "safe": Colors.GREEN,
    "moderate": Colors.YELLOW,
    "high": Colors.RED,
}


def render_fixit_result(result: FixitResult) -> str:
    """Render a fixit result as text output.

    Suitable for one-shot mode and non-interactive display.

    Args:
        result: The fixit result to render.

    Returns:
        Formatted text string with ANSI colors.
    """
    lines: list[str] = []

    # Header
    lines.append(_render_header(result))
    lines.append("")

    # Diagnosis
    if result.analysis:
        lines.extend(_render_diagnosis(result))
        lines.append("")

    # Suggestions
    if result.verified_suggestions:
        lines.extend(_render_suggestions(result))
        lines.append("")

    # Alternative approach
    if result.analysis and result.analysis.alternative_approach:
        lines.extend(_render_alternative(result))
        lines.append("")

    # Learn more
    if result.analysis and result.analysis.learn_more:
        lines.extend(_render_learn_more(result))
        lines.append("")

    # Verification errors
    if result.verification_errors:
        lines.extend(_render_errors(result))
        lines.append("")

    # Footer
    lines.extend(_render_footer(result))

    return "\n".join(lines)


def _render_header(result: FixitResult) -> str:
    """Render the header section."""
    lines = [
        f"{Colors.BOLD}Fixit Analysis{Colors.RESET}",
        f"{Colors.DIM}{'─' * 60}{Colors.RESET}",
    ]

    failure = result.context.failure
    lines.append(f"Failed: {Colors.CYAN}{failure.command}{Colors.RESET}")
    lines.append(f"Exit code: {failure.exit_code}")

    if result.used_fallback:
        lines.append(f"{Colors.DIM}(using rule-based analysis){Colors.RESET}")

    return "\n".join(lines)


def _render_diagnosis(result: FixitResult) -> list[str]:
    """Render the diagnosis section."""
    if not result.analysis:
        return []

    diag = result.analysis.diagnosis
    lines = []

    # Category with confidence
    confidence_str = f"{diag.confidence * 100:.0f}%"
    lines.append(
        f"{Colors.BOLD}Diagnosis:{Colors.RESET} "
        f"{diag.error_category.replace('_', ' ')} "
        f"{Colors.DIM}(confidence: {confidence_str}){Colors.RESET}"
    )

    # Summary
    lines.append(f"  {diag.summary}")

    # Root cause
    if diag.root_cause and diag.root_cause != diag.summary:
        lines.append("")
        lines.append(f"{Colors.BOLD}Root cause:{Colors.RESET}")
        lines.append(f"  {diag.root_cause}")

    return lines


def _render_suggestions(result: FixitResult) -> list[str]:
    """Render the suggestions section."""
    lines = [f"{Colors.BOLD}Suggested Fixes:{Colors.RESET}"]

    safe_suggestions = [s for s in result.verified_suggestions if s.is_safe]

    if not safe_suggestions:
        lines.append(
            f"  {Colors.DIM}No verified fixes available.{Colors.RESET}"
        )
        return lines

    for i, verified in enumerate(safe_suggestions, 1):
        lines.extend(_render_single_suggestion(i, verified))
        if i < len(safe_suggestions):
            lines.append("")

    return lines


def _render_single_suggestion(idx: int, verified: VerifiedFixCommand) -> list[str]:
    """Render a single suggestion."""
    fix = verified.fix
    lines = []

    # Number and explanation
    lines.append(f"  {Colors.BOLD}[{idx}]{Colors.RESET} {fix.explanation}")

    # Command with syntax highlighting
    lines.append(f"      {Colors.CYAN}{fix.command}{Colors.RESET}")

    # Risk and privilege info
    risk_color = RISK_COLORS.get(fix.risk_level, Colors.RESET)
    risk_str = f"{risk_color}Risk: {fix.risk_level}{Colors.RESET}"

    extras = [risk_str]
    if fix.requires_privilege:
        extras.append(f"{Colors.YELLOW}Requires: privilege{Colors.RESET}")

    lines.append(f"      {' | '.join(extras)}")

    # Verification warnings
    if verified.verification.warnings:
        for warning in verified.verification.warnings:
            lines.append(f"      {Colors.DIM}Warning: {warning}{Colors.RESET}")

    # Verification command
    if fix.verification:
        lines.append(f"      {Colors.DIM}Verify: {fix.verification}{Colors.RESET}")

    return lines


def _render_alternative(result: FixitResult) -> list[str]:
    """Render the alternative approach section."""
    if not result.analysis or not result.analysis.alternative_approach:
        return []

    return [
        f"{Colors.BOLD}Alternative approach:{Colors.RESET}",
        f"  {result.analysis.alternative_approach}",
    ]


def _render_learn_more(result: FixitResult) -> list[str]:
    """Render the learn more section."""
    if not result.analysis or not result.analysis.learn_more:
        return []

    lines = [f"{Colors.BOLD}Learn more:{Colors.RESET}"]
    for item in result.analysis.learn_more:
        lines.append(f"  - {item}")

    return lines


def _render_errors(result: FixitResult) -> list[str]:
    """Render verification errors."""
    lines = [f"{Colors.YELLOW}Verification warnings:{Colors.RESET}"]
    for error in result.verification_errors:
        lines.append(f"  {Colors.DIM}- {error}{Colors.RESET}")

    return lines


def _render_footer(result: FixitResult) -> list[str]:
    """Render the footer section."""
    lines = [f"{Colors.DIM}{'─' * 60}{Colors.RESET}"]

    safe_count = len([s for s in result.verified_suggestions if s.is_safe])

    if safe_count > 0:
        lines.append(
            f"{Colors.DIM}To apply a fix, copy the command above and run it.{Colors.RESET}"
        )
    else:
        lines.append(
            f"{Colors.DIM}No verified fixes available. "
            f"Check the error output for more details.{Colors.RESET}"
        )

    if result.incident_id:
        lines.append(
            f"{Colors.DIM}Incident ID: {result.incident_id}{Colors.RESET}"
        )

    return lines


def render_action_result(
    command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    success: bool,
) -> str:
    """Render the result of executing a fix command.

    Args:
        command: The command that was executed.
        exit_code: Exit code.
        stdout: Standard output.
        stderr: Standard error.
        success: Whether it succeeded.

    Returns:
        Formatted result string.
    """
    lines = []

    if success:
        lines.append(f"{Colors.GREEN}Command succeeded{Colors.RESET}")
    else:
        lines.append(f"{Colors.RED}Command failed (exit {exit_code}){Colors.RESET}")

    if stdout:
        lines.append(stdout.rstrip())

    if stderr and not success:
        lines.append(f"{Colors.RED}{stderr.rstrip()}{Colors.RESET}")

    return "\n".join(lines)


def render_outcome_prompt() -> str:
    """Render the outcome selection prompt.

    Returns:
        Prompt string asking about outcome.
    """
    return (
        f"\n{Colors.BOLD}Did this fix the problem?{Colors.RESET}\n"
        f"  [y] Yes, it's fixed\n"
        f"  [p] Partially fixed\n"
        f"  [n] No change\n"
        f"  [w] Made it worse\n"
        f"  [s] Skip (don't record)\n"
        f"\n{Colors.BOLD}>{Colors.RESET} "
    )


def render_compact_result(result: FixitResult) -> str:
    """Render a compact summary of the fixit result.

    Suitable for inline display after automatic analysis.

    Args:
        result: The fixit result.

    Returns:
        Single-line or few-line summary.
    """
    if not result.analysis:
        return f"{Colors.YELLOW}Unable to analyze the error.{Colors.RESET}"

    diag = result.analysis.diagnosis
    safe_count = len([s for s in result.verified_suggestions if s.is_safe])

    lines = [
        f"{Colors.BOLD}Diagnosis:{Colors.RESET} "
        f"{diag.error_category.replace('_', ' ')} - {diag.summary}"
    ]

    if safe_count > 0:
        lines.append(
            f"{Colors.DIM}({safe_count} fix suggestion{'s' if safe_count > 1 else ''} available){Colors.RESET}"
        )

    return "\n".join(lines)
