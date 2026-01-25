"""Reusable UI Components.

Building blocks for consistent visual language across ELLE.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from elle.cli.ui.theme import Icons

# =============================================================================
# Badges
# =============================================================================


def badge(
    text: str,
    style: str = "muted",
    *,
    icon: str | None = None,
) -> Text:
    """Create a styled badge/tag.

    Args:
        text: Badge text
        style: Rich style to apply
        icon: Optional icon prefix

    Returns:
        Rich Text object
    """
    result = Text()
    if icon:
        result.append(f"{icon} ", style=style)
    result.append(f"[{text}]", style=style)
    return result


def status_badge(status: Literal["open", "mitigated", "resolved", "false_positive"]) -> Text:
    """Create a status badge for incidents."""
    config = {
        "open": (Icons.PENDING, "OPEN", "warning"),
        "mitigated": (Icons.IN_PROGRESS, "MITIGATED", "info"),
        "resolved": (Icons.COMPLETE, "RESOLVED", "success"),
        "false_positive": (Icons.ERROR, "FALSE POSITIVE", "muted"),
    }
    icon, label, style = config.get(status, (Icons.BULLET, status.upper(), "muted"))
    return badge(label, style, icon=icon)


def outcome_badge(outcome: Literal["improved", "partial", "no_change", "worse"]) -> Text:
    """Create an outcome badge."""
    config = {
        "improved": (Icons.SUCCESS, "improved", "outcome.improved"),
        "partial": (Icons.IN_PROGRESS, "partial", "outcome.partial"),
        "no_change": (Icons.BULLET, "no change", "outcome.no_change"),
        "worse": (Icons.ERROR, "worse", "outcome.worse"),
    }
    icon, label, style = config.get(outcome, (Icons.BULLET, outcome, "muted"))
    return badge(label, style, icon=icon)


def risk_badge(risk: Literal["low", "medium", "high"]) -> Text:
    """Create a risk level badge."""
    config = {
        "low": (Icons.RISK_LOW, "low", "risk.low"),
        "medium": (Icons.RISK_MEDIUM, "medium", "risk.medium"),
        "high": (Icons.RISK_HIGH, "high", "risk.high"),
    }
    icon, label, style = config.get(risk.lower(), (Icons.BULLET, risk, "muted"))
    return badge(label, style, icon=icon)


def severity_badge(severity: Literal["info", "warning", "error", "critical"]) -> Text:
    """Create a severity badge."""
    config = {
        "info": (Icons.INFO, "INFO", "severity.info"),
        "warning": (Icons.WARNING, "WARNING", "severity.warning"),
        "error": (Icons.ERROR, "ERROR", "severity.error"),
        "critical": (Icons.FIRE, "CRITICAL", "severity.critical"),
    }
    icon, label, style = config.get(severity.lower(), (Icons.BULLET, severity.upper(), "muted"))
    return badge(label, style, icon=icon)


def domain_badge(domain: str) -> Text:
    """Create a domain badge with icon."""
    icon = Icons.domain_icon(domain)
    style = f"domain.{domain}" if domain in ("net", "disk", "oom", "docker", "auth", "pkg", "service", "fs") else "muted"
    return badge(domain.upper(), style, icon=icon)


def privilege_badge(required: bool) -> Text:
    """Create a privilege indicator badge."""
    if required:
        return badge("privilege required", "warning", icon=Icons.LOCK)
    return badge("no privilege", "muted", icon=Icons.KEY)


# =============================================================================
# Key/Value Display
# =============================================================================


def key_value(key: str, value: str, *, key_style: str = "muted", value_style: str = "") -> Text:
    """Create a key: value pair.

    Args:
        key: Label text
        value: Value text
        key_style: Style for the key
        value_style: Style for the value

    Returns:
        Rich Text object
    """
    text = Text()
    text.append(f"{key}: ", style=key_style)
    text.append(value, style=value_style)
    return text


def key_value_table(
    items: list[tuple[str, str]],
    *,
    title: str | None = None,
    key_style: str = "muted",
) -> Table:
    """Create a two-column key-value table.

    Args:
        items: List of (key, value) tuples
        title: Optional table title
        key_style: Style for keys

    Returns:
        Rich Table object
    """
    table = Table(
        show_header=False,
        show_edge=False,
        box=None,
        padding=(0, 1),
        title=title,
        title_style="bold",
    )
    table.add_column(style=key_style, no_wrap=True)
    table.add_column()

    for key, value in items:
        table.add_row(f"{key}:", value)

    return table


# =============================================================================
# Lists
# =============================================================================


def bulleted_list(
    items: list[str],
    *,
    bullet: str = Icons.BULLET,
    style: str = "",
) -> Text:
    """Create a bulleted list.

    Args:
        items: List items
        bullet: Bullet character
        style: Style for items

    Returns:
        Rich Text object
    """
    text = Text()
    for i, item in enumerate(items):
        if i > 0:
            text.append("\n")
        text.append(f"  {bullet} ", style="muted")
        text.append(item, style=style)
    return text


def numbered_list(
    items: list[str],
    *,
    style: str = "",
    start: int = 1,
) -> Text:
    """Create a numbered list.

    Args:
        items: List items
        style: Style for items
        start: Starting number

    Returns:
        Rich Text object
    """
    text = Text()
    for i, item in enumerate(items, start=start):
        if i > start:
            text.append("\n")
        text.append(f"  {i}. ", style="muted")
        text.append(item, style=style)
    return text


def option_list(
    options: list[tuple[str, str]],
    *,
    selected: int | None = None,
) -> Text:
    """Create a selectable option list.

    Args:
        options: List of (key, description) tuples
        selected: Currently selected index (0-based)

    Returns:
        Rich Text object
    """
    text = Text()
    for i, (key, desc) in enumerate(options):
        if i > 0:
            text.append("\n")

        # Selection indicator
        if selected is not None and i == selected:
            text.append(f"  {Icons.CHEVRON} ", style="accent")
        else:
            text.append("    ")

        # Key in brackets
        text.append(f"[{key}]", style="accent")
        text.append(f" {desc}")

    return text


# =============================================================================
# Time Formatting
# =============================================================================


def time_ago(dt: datetime) -> str:
    """Format datetime as human-readable 'time ago'.

    Args:
        dt: Datetime to format

    Returns:
        Human-readable string like "2m ago", "1h ago", "3d ago"
    """
    now = datetime.now()
    delta = now - dt

    if delta < timedelta(seconds=60):
        return "just now"
    elif delta < timedelta(minutes=60):
        mins = int(delta.total_seconds() / 60)
        return f"{mins}m ago"
    elif delta < timedelta(hours=24):
        hours = int(delta.total_seconds() / 3600)
        return f"{hours}h ago"
    elif delta < timedelta(days=7):
        days = delta.days
        return f"{days}d ago"
    elif delta < timedelta(days=30):
        weeks = delta.days // 7
        return f"{weeks}w ago"
    elif delta < timedelta(days=365):
        months = delta.days // 30
        return f"{months}mo ago"
    else:
        years = delta.days // 365
        return f"{years}y ago"


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        Human-readable string like "45s", "3m 20s", "1h 45m"
    """
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        if secs == 0:
            return f"{mins}m"
        return f"{mins}m {secs}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        if mins == 0:
            return f"{hours}h"
        return f"{hours}h {mins}m"


# =============================================================================
# Progress/Relevance Indicators
# =============================================================================


def confidence_bar(confidence: float, *, width: int = 10) -> Text:
    """Create a confidence/relevance bar.

    Args:
        confidence: Value between 0.0 and 1.0
        width: Total width of bar

    Returns:
        Rich Text with visual bar
    """
    filled = int(confidence * width)
    empty = width - filled

    # Color based on confidence level
    if confidence >= 0.8:
        style = "success"
    elif confidence >= 0.5:
        style = "warning"
    else:
        style = "error"

    text = Text()
    text.append("█" * filled, style=style)
    text.append("░" * empty, style="muted")
    text.append(f" {confidence:.0%}", style="muted")
    return text


def percentage_bar(
    value: float,
    *,
    width: int = 20,
    threshold_warn: float = 0.7,
    threshold_crit: float = 0.9,
) -> Text:
    """Create a percentage bar with thresholds.

    Args:
        value: Value between 0.0 and 1.0
        width: Total width of bar
        threshold_warn: Warning threshold
        threshold_crit: Critical threshold

    Returns:
        Rich Text with visual bar
    """
    filled = int(value * width)
    empty = width - filled

    # Color based on thresholds
    if value >= threshold_crit:
        style = "error"
    elif value >= threshold_warn:
        style = "warning"
    else:
        style = "success"

    text = Text()
    text.append("█" * filled, style=style)
    text.append("░" * empty, style="muted")
    text.append(f" {value:.1%}", style=style)
    return text


# =============================================================================
# Section Headers
# =============================================================================


def section_header(
    title: str,
    *,
    icon: str | None = None,
    style: str = "bold",
) -> Text:
    """Create a section header.

    Args:
        title: Header text
        icon: Optional icon prefix
        style: Text style

    Returns:
        Rich Text object
    """
    text = Text()
    if icon:
        text.append(f"{icon} ", style=style)
    text.append(title, style=style)
    return text


def section_rule(title: str = "", *, style: str = "border") -> Rule:
    """Create a section divider rule.

    Args:
        title: Optional centered title
        style: Rule style

    Returns:
        Rich Rule object
    """
    return Rule(title, style=style)


# =============================================================================
# Code/Command Display
# =============================================================================


def code_inline(code: str) -> Text:
    """Format inline code.

    Args:
        code: Code text

    Returns:
        Rich Text with code styling
    """
    return Text(code, style="code")


def command_display(command: str, *, prefix: str = "$") -> Text:
    """Format a shell command.

    Args:
        command: Command text
        prefix: Shell prompt prefix

    Returns:
        Rich Text with command styling
    """
    text = Text()
    text.append(f"{prefix} ", style="muted")
    text.append(command, style="command")
    return text


def path_display(path: str) -> Text:
    """Format a file path.

    Args:
        path: File path

    Returns:
        Rich Text with path styling
    """
    return Text(path, style="path")


# =============================================================================
# Compound Components
# =============================================================================


def confirmation_prompt(
    message: str,
    *,
    default: bool = False,
    risk: Literal["low", "medium", "high"] | None = None,
) -> Text:
    """Create a confirmation prompt.

    Args:
        message: Prompt message
        default: Default answer
        risk: Optional risk level to display

    Returns:
        Rich Text object
    """
    text = Text()

    if risk:
        text.append_text(risk_badge(risk))
        text.append(" ")

    text.append(message)
    text.append(" ")

    if default:
        text.append("[Y/n]", style="muted")
    else:
        text.append("[y/N]", style="muted")

    text.append(" ")
    return text


def tip(message: str, *, prefix: str = "Tip") -> Text:
    """Create a tip/hint message.

    Args:
        message: Tip text
        prefix: Prefix label

    Returns:
        Rich Text object
    """
    text = Text()
    text.append(f"{Icons.LIGHTBULB} ", style="info")
    text.append(f"{prefix}: ", style="info")
    text.append(message, style="muted")
    return text


def empty_state(message: str, *, suggestion: str | None = None) -> Panel:
    """Create an empty state panel.

    Args:
        message: Main message
        suggestion: Optional suggestion

    Returns:
        Rich Panel object
    """
    content = Text()
    content.append(message, style="muted")
    if suggestion:
        content.append("\n\n")
        content.append_text(tip(suggestion, prefix="Try"))

    return Panel(
        content,
        border_style="muted",
        padding=(1, 2),
    )


# =============================================================================
# Docker Environment Variable Components
# =============================================================================


def env_var_line(
    name: str,
    *,
    required: bool = False,
    sensitive: bool = False,
    default: str | None = None,
    description: str | None = None,
    has_saved: bool = False,
) -> Text:
    """Format a single environment variable line.

    Args:
        name: Variable name
        required: Whether the variable is required
        sensitive: Whether it's a sensitive value (password, secret)
        default: Default value if any
        description: Variable description
        has_saved: Whether there's a saved value

    Returns:
        Rich Text object
    """
    text = Text()
    text.append(f"  {Icons.BULLET} ", style="muted")
    text.append(name, style="bold")

    # Annotations
    annotations: list[str] = []
    if required:
        annotations.append("required")
    if sensitive:
        annotations.append("sensitive")
    if default:
        annotations.append(f"default: {default}")
    if has_saved:
        annotations.append("saved")

    if annotations:
        text.append(f" ({', '.join(annotations)})", style="dim")

    if description:
        text.append(f"\n    {description}", style="dim")

    return text


def env_var_panel(
    image: str,
    required_vars: list[tuple[str, str | None, bool]],
    optional_vars: list[tuple[str, str | None, bool]] | None = None,
    *,
    saved_names: set[str] | None = None,
) -> Panel:
    """Create a panel showing Docker environment variables.

    Args:
        image: Docker image name
        required_vars: List of (name, description, sensitive) tuples for required vars
        optional_vars: List of (name, description, sensitive) tuples for optional vars
        saved_names: Set of variable names that have saved values

    Returns:
        Rich Panel object
    """
    saved = saved_names or set()
    content = Text()

    if required_vars:
        content.append("Required:\n", style="bold")
        for name, description, sensitive in required_vars:
            content.append_text(env_var_line(
                name,
                required=True,
                sensitive=sensitive,
                description=description,
                has_saved=name in saved,
            ))
            content.append("\n")
        content.append("\n")

    if optional_vars:
        content.append("Optional:\n", style="bold")
        for name, description, sensitive in optional_vars:
            content.append_text(env_var_line(
                name,
                required=False,
                sensitive=sensitive,
                description=description,
                has_saved=name in saved,
            ))
            content.append("\n")

    return Panel(
        content,
        title="[bold cyan]Docker Environment Variables[/bold cyan]",
        subtitle=f"[dim]{image}[/dim]",
        border_style="cyan",
        padding=(1, 2),
    )


def env_var_summary_table(
    values: list[tuple[str, str, bool, bool]],
) -> Table:
    """Create a summary table of entered environment variable values.

    Args:
        values: List of (name, value, sensitive, from_saved) tuples

    Returns:
        Rich Table object
    """
    table = Table(
        show_header=True,
        header_style="bold",
        border_style="dim",
        padding=(0, 1),
    )
    table.add_column("Variable", style="cyan")
    table.add_column("Value")
    table.add_column("Source", style="dim")

    for name, value, sensitive, from_saved in values:
        display_value = "********" if sensitive else value
        source = "saved" if from_saved else "entered"
        table.add_row(name, display_value, source)

    return table


def env_var_summary_panel(
    values: list[tuple[str, str, bool, bool]],
) -> Panel:
    """Create a summary panel for environment variable values.

    Args:
        values: List of (name, value, sensitive, from_saved) tuples

    Returns:
        Rich Panel object
    """
    table = env_var_summary_table(values)

    return Panel(
        table,
        title="[bold]Summary[/bold]",
        border_style="green",
        padding=(1, 2),
    )


def env_var_action_bar(
    has_saved: bool = False,
    show_confirm: bool = False,
) -> Text:
    """Create an action bar for environment variable prompting.

    Args:
        has_saved: Whether saved values are available
        show_confirm: Whether to show confirm/edit options instead

    Returns:
        Rich Text object
    """
    text = Text()

    if show_confirm:
        text.append("[", style="dim")
        text.append("c", style="cyan")
        text.append("]", style="dim")
        text.append(" Confirm  ")

        text.append("[", style="dim")
        text.append("e", style="cyan")
        text.append("]", style="dim")
        text.append(" Edit  ")

        text.append("[", style="dim")
        text.append("q", style="cyan")
        text.append("]", style="dim")
        text.append(" Cancel")
    else:
        if has_saved:
            text.append("[", style="dim")
            text.append("u", style="cyan")
            text.append("]", style="dim")
            text.append(" Use saved  ")

        text.append("[", style="dim")
        text.append("e", style="cyan")
        text.append("]", style="dim")
        text.append(" Enter  ")

        text.append("[", style="dim")
        text.append("s", style="cyan")
        text.append("]", style="dim")
        text.append(" Skip  ")

        text.append("[", style="dim")
        text.append("?", style="cyan")
        text.append("]", style="dim")
        text.append(" Explain  ")

        text.append("[", style="dim")
        text.append("q", style="cyan")
        text.append("]", style="dim")
        text.append(" Cancel")

    return text
