"""CLI commands for capability browsing and management.

Provides handlers for:
- /capabilities - List capabilities by domain or show detail
- /autonomy - Configure autonomy settings

These commands surface ELLE's capability system to users,
allowing them to explore what operations ELLE can perform
and configure how autonomously they execute.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from elle.capabilities.autonomy import (
    AutonomyLevel,
    AutonomyStatus,
    ExecutionStats,
    get_autonomy_engine,
)
from elle.capabilities.models import CapabilityDomain, CapabilitySpec, RiskLevel
from elle.capabilities.registry import get_registry
from elle.cli.engine import EngineResult
from elle.common.session import Session

logger = logging.getLogger(__name__)

# Console for rich output
console = Console()


# =============================================================================
# Risk Level Styling
# =============================================================================

RISK_COLORS: dict[RiskLevel, str] = {
    "none": "green",
    "low": "blue",
    "medium": "yellow",
    "high": "red",
    "critical": "bold red",
}

RISK_ICONS: dict[RiskLevel, str] = {
    "none": " ",
    "low": " ",
    "medium": " ",
    "high": " ",
    "critical": " ",
}


def risk_text(risk: RiskLevel) -> Text:
    """Create styled text for a risk level."""
    return Text(risk, style=RISK_COLORS.get(risk, "white"))


def risk_bar(counts: dict[RiskLevel, int], width: int = 10) -> Text:
    """Create a visual bar showing risk distribution."""
    total = sum(counts.values())
    if total == 0:
        return Text("-" * width, style="dim")

    # Calculate proportions
    result = Text()
    for risk in ["none", "low", "medium", "high", "critical"]:
        count = counts.get(risk, 0)  # type: ignore
        if count > 0:
            chars = max(1, int((count / total) * width))
            result.append(" " * chars, style=f"on {RISK_COLORS.get(risk, 'white')}")  # type: ignore

    return result


# =============================================================================
# Autonomy Styling
# =============================================================================


def autonomy_badge(status: AutonomyStatus) -> Text:
    """Create styled badge for autonomy status."""
    if status.can_run_autonomously:
        if status.source == "earned":
            return Text("earned", style="bold green")
        elif status.source == "manual_grant":
            return Text("granted", style="green")
        else:
            return Text("auto", style="cyan")
    else:
        if status.source == "manual_revoke":
            return Text("blocked", style="red")
        else:
            return Text("confirm", style="yellow")


def format_time_ago(dt: datetime | None) -> str:
    """Format a datetime as relative time."""
    if dt is None:
        return "never"

    # If dt is naive, assume UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    delta = datetime.now(timezone.utc) - dt
    if delta < timedelta(minutes=1):
        return "just now"
    elif delta < timedelta(hours=1):
        mins = int(delta.total_seconds() / 60)
        return f"{mins}m ago"
    elif delta < timedelta(days=1):
        hours = int(delta.total_seconds() / 3600)
        return f"{hours}h ago"
    elif delta < timedelta(days=7):
        days = delta.days
        return f"{days}d ago"
    else:
        return dt.strftime("%Y-%m-%d")


# =============================================================================
# Capability Command Handler
# =============================================================================


async def handle_capabilities_command(input_text: str, session: Session) -> EngineResult:
    """Handle /capabilities command.

    Patterns:
        /capabilities              -> List all domains with counts
        /cap                       -> Alias for above
        /capabilities service      -> List all service.* capabilities
        /capabilities service.restart -> Show single capability detail
        /capabilities search <query> -> Search capabilities

    Args:
        input_text: The raw input after the command prefix.
        session: Current session state.

    Returns:
        EngineResult with formatted output.
    """
    from elle.cli.engine import EngineAction, EngineResult

    # Strip command prefix
    args = input_text.strip()
    for prefix in ["/capabilities", "/cap"]:
        if args.startswith(prefix):
            args = args[len(prefix) :].strip()
            break

    # No args -> show domain summary
    if not args:
        output = _render_domain_summary()
        return EngineResult(
            output=output,
            session=session,
            action=EngineAction.CONTINUE,
            success=True,
        )

    # "search <query>" -> search capabilities
    if args.startswith("search "):
        query = args[7:].strip()
        output = _render_search_results(query)
        return EngineResult(
            output=output,
            session=session,
            action=EngineAction.CONTINUE,
            success=True,
        )

    # Check if arg is a domain
    registry = get_registry()
    domains = registry.list_domains()

    # Try to match domain
    for domain in domains:
        if args.lower() == domain.lower():
            output = _render_domain_capabilities(domain)
            return EngineResult(
                output=output,
                session=session,
                action=EngineAction.CONTINUE,
                success=True,
            )

    # Try to match full capability name
    cap = registry.get(args)
    if cap:
        output = _render_capability_detail(cap.spec)
        return EngineResult(
            output=output,
            session=session,
            action=EngineAction.CONTINUE,
            success=True,
        )

    # Try to match partial name (domain prefix)
    domain_prefix = args.split(".")[0] if "." in args else args
    for domain in domains:
        if domain_prefix.lower() == domain.lower():
            output = _render_domain_capabilities(domain)
            return EngineResult(
                output=output,
                session=session,
                action=EngineAction.CONTINUE,
                success=True,
            )

    # Search as fallback
    output = _render_search_results(args)
    return EngineResult(
        output=output,
        session=session,
        action=EngineAction.CONTINUE,
        success=True,
    )


def _render_domain_summary() -> str:
    """Render summary of all capability domains."""
    registry = get_registry()
    autonomy = get_autonomy_engine()
    prefs = autonomy.store.get_preferences()

    # Get all capabilities organized by domain
    all_caps = registry.list_all()
    domains: dict[CapabilityDomain, list[CapabilitySpec]] = defaultdict(list)
    for cap in all_caps:
        domains[cap.domain].append(cap)

    # Sort domains by capability count
    sorted_domains = sorted(domains.items(), key=lambda x: len(x[1]), reverse=True)

    # Build summary text
    lines = []

    # Header
    total_caps = len(all_caps)
    total_domains = len(sorted_domains)
    header = Panel(
        f"[bold]ELLE[/bold] can perform [cyan]{total_caps}[/cyan] typed operations across [cyan]{total_domains}[/cyan] domains.\n"
        "Capabilities are policy-governed, auditable actions.",
        title="ELLE Capabilities",
        border_style="blue",
    )
    with console.capture() as capture:
        console.print(header)
    lines.append(capture.get())

    # Domain table
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Domain", style="cyan", width=12)
    table.add_column("Count", justify="right", width=6)
    table.add_column("Risk Distribution", width=25)
    table.add_column("Autonomy", width=18)

    for domain, caps in sorted_domains:
        # Count risk levels
        risk_counts: dict[RiskLevel, int] = defaultdict(int)
        auto_count = 0
        for cap in caps:
            risk_counts[cap.risk] += 1
            status = autonomy.get_autonomy_status(cap.name, cap.risk)
            if status.can_run_autonomously:
                auto_count += 1

        # Risk distribution text
        risk_parts = []
        for risk in ["low", "medium", "high"]:
            count = risk_counts.get(risk, 0)  # type: ignore
            if count > 0:
                risk_parts.append(f"{count} {risk}")
        risk_text_str = ", ".join(risk_parts) if risk_parts else "none"

        # Risk bar
        bar = risk_bar(risk_counts)

        # Autonomy status
        autonomy_text = f"{auto_count}/{len(caps)} autonomous"

        table.add_row(
            domain,
            str(len(caps)),
            Text.assemble(bar, " ", risk_text_str),
            autonomy_text,
        )

    with console.capture() as capture:
        console.print(table)
    lines.append(capture.get())

    # Footer with autonomy status
    footer = f"\nAutonomy Level: [cyan]{prefs.base_level.value}[/cyan] | "
    footer += f"Earned Autonomy: [cyan]{'enabled' if prefs.earned_autonomy.enabled else 'disabled'}[/cyan]"
    with console.capture() as capture:
        console.print(footer)
    lines.append(capture.get())

    return "".join(lines)


def _render_domain_capabilities(domain: CapabilityDomain) -> str:
    """Render capabilities in a specific domain."""
    registry = get_registry()
    autonomy = get_autonomy_engine()

    caps = registry.list_by_domain(domain)
    if not caps:
        return f"No capabilities found in domain: {domain}"

    lines = []

    # Header
    domain_desc = _get_domain_description(domain)
    header = Panel(
        domain_desc,
        title=f"{domain.title()} Capabilities",
        border_style="blue",
    )
    with console.capture() as capture:
        console.print(header)
    lines.append(capture.get())

    # Capability table
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Capability", style="cyan", width=24)
    table.add_column("Risk", width=8)
    table.add_column("Autonomy", width=10)
    table.add_column("Success", width=12)
    table.add_column("Uses", justify="right", width=6)

    for cap in sorted(caps, key=lambda c: c.name):
        status = autonomy.get_autonomy_status(cap.name, cap.risk)
        stats = status.stats or ExecutionStats(capability_name=cap.name)

        # Success rate
        if stats.total_executions > 0:
            success_text = f"{stats.success_rate:.0%} ({stats.successful_executions})"
        else:
            success_text = "-"

        table.add_row(
            cap.name,
            risk_text(cap.risk),
            autonomy_badge(status),
            success_text,
            str(stats.total_executions) if stats.total_executions > 0 else "-",
        )

    with console.capture() as capture:
        console.print(table)
    lines.append(capture.get())

    return "".join(lines)


def _render_capability_detail(spec: CapabilitySpec) -> str:
    """Render detailed information about a single capability."""
    autonomy = get_autonomy_engine()
    status = autonomy.get_autonomy_status(spec.name, spec.risk)
    stats = status.stats or ExecutionStats(capability_name=spec.name)

    lines = []

    # Build panel content
    content_lines = []

    # Summary
    content_lines.append(f"[dim]{spec.summary}[/dim]\n")

    # Metadata row
    content_lines.append(
        f"[bold]Domain:[/bold] {spec.domain}    "
        f"[bold]Risk:[/bold] {spec.risk}    "
        f"[bold]Trust:[/bold] {spec.trust_level}    "
        f"[bold]Privileged:[/bold] {'yes' if spec.requires_privilege else 'no'}"
    )

    # Side effects
    if spec.side_effects:
        content_lines.append("\n[bold]SIDE EFFECTS[/bold]")
        for effect in spec.side_effects:
            reversible = "[green]reversible[/green]" if effect.reversible else "[red]permanent[/red]"
            content_lines.append(f"  {effect.kind}: {effect.target} ({reversible})")

    # Autonomy status section
    content_lines.append("\n[bold]AUTONOMY STATUS[/bold]")
    if status.can_run_autonomously:
        content_lines.append(f"  Status: [green]Autonomous[/green] ({status.source})")
    else:
        content_lines.append("  Status: [yellow]Requires confirmation[/yellow]")
    content_lines.append(f"  Reason: {status.reason}")

    if status.earned_progress is not None:
        progress_bar = _progress_bar(status.earned_progress)
        content_lines.append(f"  Earned progress: {progress_bar} {status.earned_progress:.0%}")

    # Execution history
    content_lines.append("\n[bold]EXECUTION HISTORY[/bold]")
    if stats.total_executions > 0:
        content_lines.append(
            f"  Total: {stats.total_executions} | "
            f"Success: {stats.successful_executions} ({stats.success_rate:.0%}) | "
            f"Failed: {stats.failed_executions} ({1 - stats.success_rate:.0%})"
        )
        content_lines.append(f"  Last used: {format_time_ago(stats.last_executed_at)}")
    else:
        content_lines.append("  No executions recorded")

    # Dependencies
    if spec.dependencies:
        content_lines.append("\n[bold]DEPENDENCIES[/bold]")
        content_lines.append(f"  {', '.join(spec.dependencies)}")

    content = "\n".join(content_lines)

    panel = Panel(
        content,
        title=spec.name,
        border_style="blue",
    )
    with console.capture() as capture:
        console.print(panel)
    lines.append(capture.get())

    return "".join(lines)


def _render_search_results(query: str) -> str:
    """Render search results for capabilities."""
    registry = get_registry()
    results = registry.search(query)

    if not results:
        return f"No capabilities found matching: {query}"

    lines = [f"Found {len(results)} capabilities matching '{query}':\n"]

    for spec in results[:20]:  # Limit to 20 results
        lines.append(f"  [cyan]{spec.name}[/cyan] - {spec.summary}")

    if len(results) > 20:
        lines.append(f"\n  ... and {len(results) - 20} more")

    return "\n".join(lines)


def _progress_bar(progress: float, width: int = 10) -> str:
    """Create a simple progress bar."""
    filled = int(progress * width)
    empty = width - filled
    return "[" + "|" * filled + " " * empty + "]"


def _get_domain_description(domain: CapabilityDomain) -> str:
    """Get a description for a capability domain."""
    descriptions: dict[CapabilityDomain, str] = {
        "service": "Operations for managing systemd services",
        "file": "File system operations (read, write, delete, permissions)",
        "config": "Configuration file editing and management",
        "network": "Network configuration and diagnostics",
        "package": "Package installation and management",
        "docker": "Container operations (Docker, Podman)",
        "auth": "Authentication and authorization management",
        "notification": "Desktop and push notifications",
        "incident": "Incident vault management",
    }
    return descriptions.get(domain, f"Operations in the {domain} domain")


# =============================================================================
# Autonomy Command Handler
# =============================================================================


async def handle_autonomy_command(input_text: str, session: Session) -> EngineResult:
    """Handle /autonomy command.

    Patterns:
        /autonomy                  -> Show current configuration
        /autonomy status           -> Same as above
        /autonomy set <level>      -> Set base autonomy level
        /autonomy earn on|off      -> Enable/disable earned autonomy
        /autonomy grant <cap>      -> Manually grant autonomy
        /autonomy revoke <cap>     -> Revoke autonomy

    Args:
        input_text: The raw input after the command prefix.
        session: Current session state.

    Returns:
        EngineResult with formatted output.
    """
    from elle.cli.engine import EngineAction, EngineResult

    # Strip command prefix
    args = input_text.strip()
    if args.startswith("/autonomy"):
        args = args[9:].strip()

    autonomy = get_autonomy_engine()

    # No args or "status" -> show current config
    if not args or args == "status":
        output = _render_autonomy_status()
        return EngineResult(
            output=output,
            session=session,
            action=EngineAction.CONTINUE,
            success=True,
        )

    # "set <level>" -> set base level
    if args.startswith("set "):
        level_str = args[4:].strip().lower().replace("-", "_")
        try:
            level = AutonomyLevel(level_str)
            autonomy.store.set_base_level(level)
            output = f"Autonomy level set to: [cyan]{level.value}[/cyan]"
            try:
                from elle.cli.agentic.incident_recorder import record_arm_action

                record_arm_action(
                    arm_name="autonomy_config",
                    action="set_level",
                    target=level.value,
                    success=True,
                )
            except Exception:
                logger.debug("Failed to record autonomy action", exc_info=True)
        except ValueError:
            valid = ", ".join(l.value for l in AutonomyLevel)
            output = f"Invalid level: {level_str}. Valid levels: {valid}"
        return EngineResult(
            output=output,
            session=session,
            action=EngineAction.CONTINUE,
            success=True,
        )

    # "earn on|off" -> enable/disable earned autonomy
    if args.startswith("earn "):
        value = args[5:].strip().lower()
        if value in ("on", "enable", "true", "1"):
            autonomy.store.set_earned_autonomy_enabled(True)
            output = "Earned autonomy [green]enabled[/green]"
        elif value in ("off", "disable", "false", "0"):
            autonomy.store.set_earned_autonomy_enabled(False)
            output = "Earned autonomy [yellow]disabled[/yellow]"
        else:
            output = f"Invalid value: {value}. Use 'on' or 'off'"
        if value in ("on", "enable", "true", "1", "off", "disable", "false", "0"):
            try:
                from elle.cli.agentic.incident_recorder import record_arm_action

                record_arm_action(
                    arm_name="autonomy_config",
                    action="earn_toggle",
                    target=value,
                    success=True,
                )
            except Exception:
                logger.debug("Failed to record earn toggle", exc_info=True)
        return EngineResult(
            output=output,
            session=session,
            action=EngineAction.CONTINUE,
            success=True,
        )

    # "grant <capability>" -> manually grant autonomy
    if args.startswith("grant "):
        cap_name = args[6:].strip()
        registry = get_registry()
        if registry.get(cap_name):
            autonomy.store.set_override(cap_name, True, reason="manual")
            output = f"Granted autonomous execution to [cyan]{cap_name}[/cyan]"
            try:
                from elle.cli.agentic.incident_recorder import record_arm_action

                record_arm_action(
                    arm_name="autonomy_config",
                    action="grant",
                    target=cap_name,
                    success=True,
                )
            except Exception:
                logger.debug("Failed to record autonomy action", exc_info=True)
        else:
            output = f"Capability not found: {cap_name}"
        return EngineResult(
            output=output,
            session=session,
            action=EngineAction.CONTINUE,
            success=True,
        )

    # "revoke <capability>" -> revoke autonomy
    if args.startswith("revoke "):
        cap_name = args[7:].strip()
        registry = get_registry()
        if registry.get(cap_name):
            autonomy.store.set_override(cap_name, False, reason="manual")
            output = f"Revoked autonomy from [cyan]{cap_name}[/cyan]"
            try:
                from elle.cli.agentic.incident_recorder import record_arm_action

                record_arm_action(
                    arm_name="autonomy_config",
                    action="revoke",
                    target=cap_name,
                    success=True,
                )
            except Exception:
                logger.debug("Failed to record autonomy action", exc_info=True)
        else:
            output = f"Capability not found: {cap_name}"
        return EngineResult(
            output=output,
            session=session,
            action=EngineAction.CONTINUE,
            success=True,
        )

    # "overrides" -> list all overrides
    if args == "overrides":
        output = _render_overrides()
        return EngineResult(
            output=output,
            session=session,
            action=EngineAction.CONTINUE,
            success=True,
        )

    # Unknown subcommand
    output = _render_autonomy_help()
    return EngineResult(
        output=output,
        session=session,
        action=EngineAction.CONTINUE,
        success=True,
    )


def _render_autonomy_status() -> str:
    """Render current autonomy configuration."""
    autonomy = get_autonomy_engine()
    prefs = autonomy.store.get_preferences()
    overrides = autonomy.store.list_overrides()

    lines = []

    # Header panel
    content_lines = [
        f"[bold]Base Level:[/bold] [cyan]{prefs.base_level.value}[/cyan]",
        f"  Allows automatic execution for: {_level_description(prefs.base_level)}",
        "",
        f"[bold]Earned Autonomy:[/bold] {'[green]enabled[/green]' if prefs.earned_autonomy.enabled else '[yellow]disabled[/yellow]'}",
    ]

    if prefs.earned_autonomy.enabled:
        content_lines.extend(
            [
                f"  Min executions: {prefs.earned_autonomy.min_executions}",
                f"  Min success rate: {prefs.earned_autonomy.min_success_rate:.0%}",
                f"  Cooldown after failure: {prefs.earned_autonomy.cooldown_after_failure} runs",
            ]
        )

    if overrides:
        content_lines.append("")
        content_lines.append(f"[bold]Overrides:[/bold] {len(overrides)} active")
        grants = sum(1 for o in overrides if o.autonomous)
        revokes = len(overrides) - grants
        content_lines.append(f"  {grants} grants, {revokes} revokes")

    content = "\n".join(content_lines)
    panel = Panel(content, title="Autonomy Configuration", border_style="blue")

    with console.capture() as capture:
        console.print(panel)
    lines.append(capture.get())

    return "".join(lines)


def _render_overrides() -> str:
    """Render list of all autonomy overrides."""
    autonomy = get_autonomy_engine()
    overrides = autonomy.store.list_overrides()

    if not overrides:
        return "No autonomy overrides configured."

    lines = ["Autonomy Overrides:\n"]

    for override in overrides:
        status = "[green]granted[/green]" if override.autonomous else "[red]revoked[/red]"
        reason = f"({override.reason})"
        lines.append(f"  [cyan]{override.capability_name}[/cyan]: {status} {reason}")

    return "\n".join(lines)


def _render_autonomy_help() -> str:
    """Render help for autonomy command."""
    return """Autonomy Configuration Commands:

  /autonomy                    Show current configuration
  /autonomy status             Same as above
  /autonomy set <level>        Set base autonomy level
                               Levels: readonly, low-risk, medium-risk, high-risk, full-auto
  /autonomy earn on|off        Enable/disable earned autonomy
  /autonomy grant <capability> Manually grant autonomy to a capability
  /autonomy revoke <capability> Revoke autonomy from a capability
  /autonomy overrides          List all manual overrides
"""


def _level_description(level: AutonomyLevel) -> str:
    """Get a description of what risk levels are allowed."""
    descriptions = {
        AutonomyLevel.READONLY: "none (all require confirmation)",
        AutonomyLevel.LOW_RISK: "none and low risk",
        AutonomyLevel.MEDIUM_RISK: "none, low, and medium risk",
        AutonomyLevel.HIGH_RISK: "none, low, medium, and high risk",
        AutonomyLevel.FULL_AUTO: "all risk levels (dangerous!)",
    }
    return descriptions.get(level, "unknown")


# =============================================================================
# Natural Language Routing
# =============================================================================


async def handle_capability_query(input_text: str, session: Session) -> EngineResult:
    """Handle natural language queries about capabilities.

    Patterns recognized:
        - "what can you do" / "tell me about your capabilities"
        - "what X capabilities do you have" (where X is a domain)
        - "explain the X.Y capability"
        - "what can run automatically"

    Args:
        input_text: The natural language query.
        session: Current session state.

    Returns:
        EngineResult with formatted output.
    """
    from elle.cli.engine import EngineAction, EngineResult

    text = input_text.lower().strip()

    # General capability questions
    if any(
        phrase in text
        for phrase in [
            "what can you do",
            "tell me about your capabilities",
            "tell me what you can do",
            "list capabilities",
            "show capabilities",
        ]
    ):
        output = _render_domain_summary()
        return EngineResult(
            output=output,
            session=session,
            action=EngineAction.CONTINUE,
            success=True,
        )

    # Autonomy questions
    if any(phrase in text for phrase in ["what can run automatically", "what runs autonomously", "autonomy status"]):
        output = _render_autonomy_status()
        return EngineResult(
            output=output,
            session=session,
            action=EngineAction.CONTINUE,
            success=True,
        )

    # Domain-specific questions: "what service capabilities", "service capabilities"
    registry = get_registry()
    for domain in registry.list_domains():
        if f"{domain} capabilities" in text or f"capabilities for {domain}" in text:
            output = _render_domain_capabilities(domain)
            return EngineResult(
                output=output,
                session=session,
                action=EngineAction.CONTINUE,
                success=True,
            )

    # Fallback to search
    # Extract potential search terms
    search_terms = []
    for word in text.split():
        if word not in [
            "what",
            "which",
            "how",
            "can",
            "you",
            "do",
            "about",
            "the",
            "a",
            "an",
            "capabilities",
            "capability",
        ]:
            search_terms.append(word)

    if search_terms:
        query = " ".join(search_terms)
        output = _render_search_results(query)
    else:
        output = _render_domain_summary()

    return EngineResult(
        output=output,
        session=session,
        action=EngineAction.CONTINUE,
        success=True,
    )
