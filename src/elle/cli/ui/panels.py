"""Display Panels for Fixit, Plans, and Incidents.

Rich Panel-based components for major ELLE features.
Each panel follows consistent structure:
- Header with title and icon
- Body with main content
- Footer with actions/metadata
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from rich.box import ROUNDED
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from elle.cli.ui.components import (
    bulleted_list,
    command_display,
    confidence_bar,
    confirmation_prompt,
    domain_badge,
    key_value_table,
    outcome_badge,
    privilege_badge,
    risk_badge,
    section_header,
    section_rule,
    severity_badge,
    status_badge,
    time_ago,
    tip,
)
from elle.cli.ui.theme import Colors, Icons, Theme

if TYPE_CHECKING:
    pass


# =============================================================================
# Fixit Panel
# =============================================================================


def fixit_panel(
    *,
    command: str,
    exit_code: int,
    diagnosis: str,
    root_cause: str | None = None,
    confidence: float | None = None,
    suggestions: Sequence[dict[str, str]] | None = None,
    verification_commands: Sequence[str] | None = None,
    requires_privilege: bool = False,
    incident_id: str | None = None,
    learning_resources: Sequence[str] | None = None,
    # NEW: Provenance fields for rationale display
    man_citations: Sequence[dict[str, str | float]] | None = None,
    incident_citations: Sequence[dict[str, str | float]] | None = None,
    confidence_breakdown: dict[str, float] | None = None,
    rationale_summary: str | None = None,
    show_rationale: bool = True,
) -> Panel:
    """Create a Fixit diagnosis panel.

    Args:
        command: Failed command
        exit_code: Exit code of failed command
        diagnosis: Main diagnosis text
        root_cause: Root cause explanation
        confidence: Confidence level (0-1) - legacy, prefer confidence_breakdown
        suggestions: List of fix suggestions with 'command', 'explanation', 'risk'
        verification_commands: Commands to verify the fix
        requires_privilege: Whether fix requires elevated privileges
        incident_id: Associated incident ID
        learning_resources: Related man pages or docs
        man_citations: Man page citations for rationale (optional)
        incident_citations: Prior incident citations for rationale (optional)
        confidence_breakdown: Confidence breakdown for rationale (optional)
        rationale_summary: Summary text for rationale (optional)
        show_rationale: Whether to show the rationale panel (default True)

    Returns:
        Rich Panel object
    """
    content_parts: list[RenderableType] = []

    # Failed command header
    header = Text()
    header.append(f"{Icons.ERROR} ", style="error")
    header.append("Command failed: ", style="bold")
    header.append(command, style="command")
    header.append(f" (exit {exit_code})", style="muted")
    content_parts.append(header)
    content_parts.append(Text())

    # Diagnosis
    content_parts.append(section_header("Diagnosis", icon=Icons.MAGNIFIER))
    content_parts.append(Text(diagnosis))

    # Root cause
    if root_cause:
        content_parts.append(Text())
        content_parts.append(section_header("Root Cause"))
        content_parts.append(Text(root_cause, style="muted"))

    # Confidence (legacy single value)
    if confidence is not None and confidence_breakdown is None:
        content_parts.append(Text())
        conf_text = Text()
        conf_text.append("Confidence: ")
        conf_text.append_text(confidence_bar(confidence))
        content_parts.append(conf_text)

    # Rationale panel with provenance
    has_provenance = man_citations or incident_citations or confidence_breakdown
    if has_provenance and show_rationale:
        content_parts.append(Text())
        content_parts.append(rationale_panel(
            man_citations=man_citations,
            incident_citations=incident_citations,
            confidence=confidence_breakdown,
            summary=rationale_summary,
        ))

    # Suggestions
    if suggestions:
        content_parts.append(Text())
        content_parts.append(section_rule("Suggested Fixes"))

        for i, suggestion in enumerate(suggestions, 1):
            cmd = suggestion.get("command", "")
            explanation = suggestion.get("explanation", "")
            risk = suggestion.get("risk", "low")

            fix_text = Text()
            fix_text.append(f"\n{i}. ", style="bold")
            fix_text.append_text(risk_badge(risk))  # type: ignore[arg-type]
            fix_text.append("\n   ")
            fix_text.append_text(command_display(cmd))
            if explanation:
                fix_text.append(f"\n   {explanation}", style="muted")

            content_parts.append(fix_text)

    # Verification
    if verification_commands:
        content_parts.append(Text())
        content_parts.append(section_header("Verification", icon=Icons.SUCCESS))
        for cmd in verification_commands:
            content_parts.append(command_display(cmd))

    # Learning resources (if no provenance shown, show docs the old way)
    if learning_resources and not has_provenance:
        content_parts.append(Text())
        content_parts.append(section_header("Documentation", icon=Icons.BOOK))
        content_parts.append(bulleted_list(list(learning_resources)))

    # Footer metadata
    footer_parts: list[str] = []
    if requires_privilege:
        footer_parts.append(f"{Icons.LOCK} Privilege required")
    if incident_id:
        footer_parts.append(f"Incident: {incident_id}")

    subtitle = " │ ".join(footer_parts) if footer_parts else None

    return Panel(
        Group(*content_parts),
        title=f"{Icons.WRENCH} Fixit",
        title_align="left",
        subtitle=subtitle,
        subtitle_align="right",
        border_style=Colors.WARNING,
        box=ROUNDED,
        padding=(1, 2),
    )


def fixit_compact(
    *,
    command: str,
    fix_command: str,
    risk: Literal["low", "medium", "high"] = "low",
) -> Text:
    """Compact single-line fixit suggestion.

    Args:
        command: Failed command
        fix_command: Suggested fix
        risk: Risk level

    Returns:
        Rich Text object
    """
    text = Text()
    text.append(f"{Icons.WRENCH} Fix for ", style="muted")
    text.append(command, style="command")
    text.append(": ", style="muted")
    text.append_text(risk_badge(risk))
    text.append(" ")
    text.append(fix_command, style="bold")
    return text


# =============================================================================
# Rationale Panel (Provenance Display)
# =============================================================================


def rationale_panel(
    *,
    man_citations: Sequence[dict[str, str | float]] | None = None,
    incident_citations: Sequence[dict[str, str | float]] | None = None,
    confidence: dict[str, float] | None = None,
    summary: str | None = None,
) -> Panel:
    """Create a rationale panel showing provenance for a decision.

    Displays man page citations, prior incident citations, and confidence
    breakdown to explain why ELLE is suggesting this solution.

    Args:
        man_citations: List of man page citations with 'name', 'section', 'snippet', 'relevance_score'
        incident_citations: List of incident citations with 'title', 'outcome', 'similarity_score'
        confidence: Confidence breakdown with 'overall', 'from_man_vault', 'from_incident_vault', 'from_llm'
        summary: Optional summary text (e.g., "Suggested because man systemctl(1) + 2 similar incidents")

    Returns:
        Rich Panel object
    """
    content_parts: list[RenderableType] = []

    # Summary line
    if summary:
        summary_text = Text()
        summary_text.append(f"{Icons.LIGHTBULB} ", style="info")
        summary_text.append(summary, style="muted")
        content_parts.append(summary_text)
        content_parts.append(Text())

    # Man page citations
    if man_citations:
        content_parts.append(section_header("Documentation", icon=Icons.BOOK))
        for citation in man_citations[:3]:  # Show top 3
            name = citation.get("name", "")
            section = citation.get("section", "")
            snippet = citation.get("snippet", "")
            score = float(citation.get("relevance_score", 0))

            cite_text = Text()
            cite_text.append(f"  {Icons.CITATION} ", style="info")
            cite_text.append(f"{name}({section})", style="bold")
            cite_text.append(" ")
            cite_text.append_text(confidence_bar(score, width=5))
            content_parts.append(cite_text)

            if snippet:
                # Show first 80 chars of snippet
                snippet_preview = snippet[:80] + "..." if len(snippet) > 80 else snippet
                snip_text = Text()
                snip_text.append("    ", style="")
                snip_text.append(snippet_preview, style="muted")
                content_parts.append(snip_text)

        content_parts.append(Text())

    # Prior incident citations
    if incident_citations:
        content_parts.append(section_header("Prior Incidents", icon=Icons.HISTORY))
        for citation in incident_citations[:3]:  # Show top 3
            title = citation.get("title", "")
            outcome = citation.get("outcome", "unknown")
            score = float(citation.get("similarity_score", 0))
            successful = citation.get("successful_actions", [])

            cite_text = Text()
            cite_text.append(f"  {Icons.CITATION} ", style="info")
            cite_text.append(title[:40], style="")
            if len(title) > 40:
                cite_text.append("...", style="muted")
            cite_text.append(" ")
            cite_text.append_text(outcome_badge(outcome))  # type: ignore[arg-type]
            cite_text.append(" ")
            cite_text.append_text(confidence_bar(score, width=5))
            content_parts.append(cite_text)

            # Show successful commands from that incident
            if successful and isinstance(successful, (list, tuple)):
                for cmd in list(successful)[:2]:
                    cmd_text = Text()
                    cmd_text.append("    ", style="")
                    cmd_text.append(f"{Icons.SUCCESS} ", style="success")
                    cmd_text.append(str(cmd)[:50], style="command")
                    content_parts.append(cmd_text)

        content_parts.append(Text())

    # Confidence breakdown
    if confidence:
        content_parts.append(section_header("Confidence", icon=Icons.BRAIN))
        overall = float(confidence.get("overall", 0))
        from_man = float(confidence.get("from_man_vault", 0))
        from_inc = float(confidence.get("from_incident_vault", 0))
        from_llm = float(confidence.get("from_llm", 0))

        # Overall confidence bar
        overall_text = Text()
        overall_text.append("  Overall: ")
        overall_text.append_text(confidence_bar(overall, width=10))
        content_parts.append(overall_text)

        # Breakdown by source
        sources = []
        if from_man > 0:
            sources.append(f"man vault: {from_man:.0%}")
        if from_inc > 0:
            sources.append(f"incidents: {from_inc:.0%}")
        if from_llm > 0:
            sources.append(f"LLM: {from_llm:.0%}")

        if sources:
            breakdown_text = Text()
            breakdown_text.append("  Sources: ", style="muted")
            breakdown_text.append(" + ".join(sources), style="muted")
            content_parts.append(breakdown_text)

    return Panel(
        Group(*content_parts),
        title=f"{Icons.BRAIN} Rationale",
        title_align="left",
        border_style=Colors.INFO,
        box=ROUNDED,
        padding=(0, 1),
    )


# =============================================================================
# Plan Panel
# =============================================================================


def plan_panel(
    *,
    title: str,
    explanation: str,
    steps: Sequence[dict[str, str | bool | None]],
    risks: Sequence[str] | None = None,
    rollback_steps: Sequence[dict[str, str]] | None = None,
    requires_privilege: bool = False,
    current_step: int | None = None,
    # NEW: Provenance fields for rationale display
    man_citations: Sequence[dict[str, str | float]] | None = None,
    incident_citations: Sequence[dict[str, str | float]] | None = None,
    confidence: dict[str, float] | None = None,
    rationale_summary: str | None = None,
    show_rationale: bool = True,
) -> Panel:
    """Create an execution plan panel.

    Args:
        title: Plan title
        explanation: Plan explanation
        steps: List of steps with 'description', 'command', 'verification', 'can_rollback'
        risks: List of risk warnings
        rollback_steps: Rollback procedure steps
        requires_privilege: Whether plan requires elevated privileges
        current_step: Currently executing step (0-indexed, None if not executing)
        man_citations: Man page citations for rationale (optional)
        incident_citations: Prior incident citations for rationale (optional)
        confidence: Confidence breakdown for rationale (optional)
        rationale_summary: Summary text for rationale (optional)
        show_rationale: Whether to show the rationale panel (default True)

    Returns:
        Rich Panel object
    """
    content_parts: list[RenderableType] = []

    # Explanation
    content_parts.append(Text(explanation))
    content_parts.append(Text())

    # Rationale panel (if provenance data provided and show_rationale is True)
    has_provenance = man_citations or incident_citations or confidence
    if has_provenance and show_rationale:
        content_parts.append(rationale_panel(
            man_citations=man_citations,
            incident_citations=incident_citations,
            confidence=confidence,
            summary=rationale_summary,
        ))
        content_parts.append(Text())

    # Risk warnings
    if risks:
        content_parts.append(section_header("Risks", icon=Icons.WARNING, style="warning"))
        for risk in risks:
            risk_text = Text()
            risk_text.append(f"  {Icons.WARNING} ", style="warning")
            risk_text.append(risk)
            content_parts.append(risk_text)
        content_parts.append(Text())

    # Steps
    content_parts.append(section_rule("Steps"))

    for i, step in enumerate(steps):
        desc = step.get("description", "")
        cmd = step.get("command")
        verification = step.get("verification")
        can_rollback = step.get("can_rollback", True)

        step_text = Text()

        # Step number with status indicator
        if current_step is not None:
            if i < current_step:
                step_text.append(f"  {Icons.SUCCESS} ", style="success")
            elif i == current_step:
                step_text.append(f"  {Icons.IN_PROGRESS} ", style="accent")
            else:
                step_text.append(f"  {Icons.PENDING} ", style="muted")
        else:
            step_text.append(f"  {i + 1}. ", style="bold")

        step_text.append(str(desc))

        if not can_rollback:
            step_text.append(" ", style="")
            step_text.append("[no rollback]", style="warning")

        content_parts.append(step_text)

        # Command
        if cmd:
            cmd_text = Text()
            cmd_text.append("     ")
            cmd_text.append_text(command_display(str(cmd)))
            content_parts.append(cmd_text)

        # Verification
        if verification:
            ver_text = Text()
            ver_text.append("     ", style="")
            ver_text.append(f"verify: {verification}", style="muted")
            content_parts.append(ver_text)

    # Rollback procedure
    if rollback_steps:
        content_parts.append(Text())
        content_parts.append(section_header("Rollback", icon=Icons.ARROW_LEFT, style="muted"))
        for i, step in enumerate(rollback_steps, 1):
            rb_text = Text()
            rb_text.append(f"  {i}. ", style="muted")
            rb_text.append(step.get("description", ""), style="muted")
            content_parts.append(rb_text)

    # Footer
    footer_parts: list[str] = []
    if requires_privilege:
        footer_parts.append(f"{Icons.LOCK} Privilege required")
    footer_parts.append(f"{len(steps)} step(s)")

    subtitle = " │ ".join(footer_parts)

    return Panel(
        Group(*content_parts),
        title=f"{Icons.BULLET} {title}",
        title_align="left",
        subtitle=subtitle,
        subtitle_align="right",
        border_style=Colors.ACCENT,
        box=ROUNDED,
        padding=(1, 2),
    )


def plan_step_result(
    step_num: int,
    description: str,
    *,
    success: bool,
    output: str | None = None,
    error: str | None = None,
) -> Text:
    """Render result of a plan step execution.

    Args:
        step_num: Step number
        description: Step description
        success: Whether step succeeded
        output: Command output (if any)
        error: Error message (if failed)

    Returns:
        Rich Text object
    """
    text = Text()

    if success:
        text.append(f"{Icons.SUCCESS} ", style="success")
        text.append(f"Step {step_num}: ", style="bold")
        text.append(description)
    else:
        text.append(f"{Icons.ERROR} ", style="error")
        text.append(f"Step {step_num} failed: ", style="bold")
        text.append(description)

    if error:
        text.append(f"\n  {error}", style="error")

    return text


# =============================================================================
# Incident Panel
# =============================================================================


def incident_panel(
    *,
    incident_id: str,
    title: str,
    domain: str,
    status: Literal["open", "mitigated", "resolved", "false_positive"],
    severity: Literal["info", "warning", "error", "critical"],
    created_at: datetime,
    summary: str | None = None,
    symptoms: Sequence[str] | None = None,
    root_cause: str | None = None,
    outcome: Literal["improved", "partial", "no_change", "worse"] | None = None,
    actions_count: int = 0,
    similar_incidents: int = 0,
) -> Panel:
    """Create an incident detail panel.

    Args:
        incident_id: Unique incident ID
        title: Incident title
        domain: Incident domain (net, disk, etc.)
        status: Current status
        severity: Severity level
        created_at: Creation timestamp
        summary: Incident summary
        symptoms: List of observed symptoms
        root_cause: Identified root cause
        outcome: Resolution outcome
        actions_count: Number of actions taken
        similar_incidents: Number of similar incidents found

    Returns:
        Rich Panel object
    """
    content_parts: list[RenderableType] = []

    # Header badges
    header = Text()
    header.append_text(domain_badge(domain))
    header.append(" ")
    header.append_text(severity_badge(severity))
    header.append(" ")
    header.append_text(status_badge(status))
    if outcome:
        header.append(" ")
        header.append_text(outcome_badge(outcome))
    content_parts.append(header)
    content_parts.append(Text())

    # Summary
    if summary:
        content_parts.append(Text(summary))
        content_parts.append(Text())

    # Symptoms
    if symptoms:
        content_parts.append(section_header("Symptoms", icon=Icons.WARNING))
        content_parts.append(bulleted_list(list(symptoms)))
        content_parts.append(Text())

    # Root cause
    if root_cause:
        content_parts.append(section_header("Root Cause", icon=Icons.MAGNIFIER))
        content_parts.append(Text(root_cause, style="muted"))
        content_parts.append(Text())

    # Metadata
    meta_items = [
        ("Created", time_ago(created_at)),
        ("Actions", str(actions_count)),
    ]
    if similar_incidents > 0:
        meta_items.append(("Similar", str(similar_incidents)))

    content_parts.append(key_value_table(meta_items))

    return Panel(
        Group(*content_parts),
        title=f"{Icons.domain_icon(domain)} {title}",
        title_align="left",
        subtitle=f"ID: {incident_id}",
        subtitle_align="right",
        border_style=Theme.severity_color(severity),
        box=ROUNDED,
        padding=(1, 2),
    )


def incident_compact(
    *,
    incident_id: str,
    title: str,
    domain: str,
    status: Literal["open", "mitigated", "resolved", "false_positive"],
    created_at: datetime,
) -> Text:
    """Compact single-line incident summary.

    Args:
        incident_id: Incident ID
        title: Incident title
        domain: Incident domain
        status: Current status
        created_at: Creation timestamp

    Returns:
        Rich Text object
    """
    text = Text()
    text.append(f"{Icons.domain_icon(domain)} ", style=f"domain.{domain}")
    text.append(f"[{incident_id[:8]}] ", style="muted")
    text.append(title)
    text.append(f" ({time_ago(created_at)})", style="muted")
    return text


# =============================================================================
# Search Results Panel
# =============================================================================


def search_results_panel(
    query: str,
    results: Sequence[dict[str, str | float]],
    *,
    source: str = "Man Vault",
) -> Panel:
    """Create a search results panel.

    Args:
        query: Search query
        results: List of results with 'title', 'snippet', 'relevance'
        source: Data source name

    Returns:
        Rich Panel object
    """
    content_parts: list[RenderableType] = []

    if not results:
        content_parts.append(Text("No results found.", style="muted"))
        content_parts.append(Text())
        content_parts.append(tip("Try different keywords or check spelling"))
    else:
        for i, result in enumerate(results, 1):
            title = result.get("title", "Untitled")
            snippet = result.get("snippet", "")
            relevance = float(result.get("relevance", 0))

            result_text = Text()
            result_text.append(f"{i}. ", style="bold")
            result_text.append(str(title), style="accent")
            result_text.append(" ")
            result_text.append_text(confidence_bar(relevance, width=5))
            content_parts.append(result_text)

            if snippet:
                snippet_text = Text()
                snippet_text.append("   ")
                snippet_text.append(str(snippet)[:100], style="muted")
                if len(str(snippet)) > 100:
                    snippet_text.append("...", style="muted")
                content_parts.append(snippet_text)

            content_parts.append(Text())

    return Panel(
        Group(*content_parts),
        title=f"{Icons.MAGNIFIER} Search: {query}",
        title_align="left",
        subtitle=f"{len(results)} result(s) from {source}",
        subtitle_align="right",
        border_style=Colors.BORDER,
        box=ROUNDED,
        padding=(1, 2),
    )


# =============================================================================
# Config Diff Panel
# =============================================================================


def config_diff_panel(
    path: str,
    diff_text: str,
    *,
    operations: Sequence[dict[str, str]] | None = None,
    requires_privilege: bool = False,
    validation_command: str | None = None,
    risks: Sequence[str] | None = None,
) -> Panel:
    """Create a config change preview panel.

    Args:
        path: Target file path
        diff_text: Unified diff text
        operations: List of operations with 'kind', 'path', 'value'
        requires_privilege: Whether change requires elevated privileges
        validation_command: Command to validate the change
        risks: List of risk warnings

    Returns:
        Rich Panel object
    """
    content_parts: list[RenderableType] = []

    # Operations summary
    if operations:
        content_parts.append(section_header("Changes"))
        for op in operations:
            kind = op.get("kind", "set")
            op_path = op.get("path", "")
            value = op.get("value", "")

            op_text = Text()
            if kind == "set":
                op_text.append("  + ", style="success")
            elif kind == "rm":
                op_text.append("  - ", style="error")
            else:
                op_text.append("  ~ ", style="warning")

            op_text.append(op_path, style="path")
            if value:
                op_text.append(" = ", style="muted")
                op_text.append(value)
            content_parts.append(op_text)

        content_parts.append(Text())

    # Diff
    content_parts.append(section_rule("Diff"))
    content_parts.append(
        Syntax(diff_text, "diff", theme="monokai", line_numbers=False, word_wrap=True)
    )

    # Risks
    if risks:
        content_parts.append(Text())
        content_parts.append(section_header("Risks", icon=Icons.WARNING, style="warning"))
        for risk in risks:
            risk_text = Text()
            risk_text.append(f"  {Icons.WARNING} ", style="warning")
            risk_text.append(risk)
            content_parts.append(risk_text)

    # Validation
    if validation_command:
        content_parts.append(Text())
        content_parts.append(section_header("Validation", icon=Icons.SUCCESS))
        content_parts.append(command_display(validation_command))

    # Footer
    footer_parts: list[str] = []
    if requires_privilege:
        footer_parts.append(f"{Icons.LOCK} Privilege required")

    subtitle = " │ ".join(footer_parts) if footer_parts else None

    return Panel(
        Group(*content_parts),
        title=f"Config: {path}",
        title_align="left",
        subtitle=subtitle,
        subtitle_align="right",
        border_style=Colors.ACCENT,
        box=ROUNDED,
        padding=(1, 2),
    )


# =============================================================================
# Confirmation Panels
# =============================================================================


def apply_confirmation(
    *,
    action: str,
    risk: Literal["low", "medium", "high"] = "low",
    requires_privilege: bool = False,
) -> Text:
    """Create an apply confirmation prompt.

    Args:
        action: Description of action to confirm
        risk: Risk level
        requires_privilege: Whether action needs privileges

    Returns:
        Rich Text object
    """
    text = Text()
    text.append_text(risk_badge(risk))

    if requires_privilege:
        text.append(" ")
        text.append_text(privilege_badge(True))

    text.append(f"\n{action}\n")
    text.append_text(confirmation_prompt("Apply?", default=(risk == "low"), risk=risk))

    return text


def rollback_confirmation(*, reason: str) -> Text:
    """Create a rollback confirmation prompt.

    Args:
        reason: Reason for rollback

    Returns:
        Rich Text object
    """
    text = Text()
    text.append(f"{Icons.WARNING} ", style="warning")
    text.append("Rollback: ", style="bold")
    text.append(reason)
    text.append("\n")
    text.append_text(confirmation_prompt("Rollback changes?", default=False))

    return text
