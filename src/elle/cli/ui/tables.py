"""Data Tables for ELLE.

Rich Table-based components for displaying lists and tabular data.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from rich.box import MINIMAL, ROUNDED, SIMPLE
from rich.table import Table
from rich.text import Text

from elle.cli.ui.components import (
    confidence_bar,
    domain_badge,
    outcome_badge,
    percentage_bar,
    severity_badge,
    status_badge,
    time_ago,
)
from elle.cli.ui.theme import Colors, Icons, Theme

# =============================================================================
# Incident Table
# =============================================================================


def incident_table(
    incidents: Sequence[dict[str, Any]],
    *,
    title: str | None = None,
    show_outcome: bool = True,
    max_title_width: int = 40,
) -> Table:
    """Create an incident list table.

    Args:
        incidents: List of incident dicts with keys:
            - incident_id: str
            - title: str
            - domain: str
            - status: str
            - severity: str
            - created_at: datetime
            - outcome: str (optional)
        title: Optional table title
        show_outcome: Whether to show outcome column
        max_title_width: Maximum width for title column

    Returns:
        Rich Table object
    """
    table = Table(
        title=title,
        box=ROUNDED,
        border_style=Colors.BORDER,
        show_header=True,
        header_style="bold",
        row_styles=["", "dim"],
        padding=(0, 1),
    )

    # Columns
    table.add_column("ID", style="muted", width=10)
    table.add_column("Domain", width=8)
    table.add_column("Title", max_width=max_title_width)
    table.add_column("Status", width=12)
    table.add_column("Severity", width=10)
    if show_outcome:
        table.add_column("Outcome", width=12)
    table.add_column("Created", style="muted", width=10)

    for incident in incidents:
        incident_id = str(incident.get("incident_id", ""))[:8]
        title_text = str(incident.get("title", ""))
        domain = str(incident.get("domain", ""))
        status = incident.get("status", "open")
        severity = incident.get("severity", "info")
        created_at = incident.get("created_at")
        outcome = incident.get("outcome")

        # Truncate title if needed
        if len(title_text) > max_title_width:
            title_text = title_text[: max_title_width - 3] + "..."

        row: list[str | Text] = [
            incident_id,
            domain_badge(domain),
            title_text,
            status_badge(status),
            severity_badge(severity),
        ]

        if show_outcome:
            if outcome:
                row.append(outcome_badge(outcome))
            else:
                row.append(Text("-", style="muted"))

        if isinstance(created_at, datetime):
            row.append(time_ago(created_at))
        else:
            row.append(str(created_at) if created_at else "-")

        table.add_row(*row)

    return table


def incident_table_compact(
    incidents: Sequence[dict[str, Any]],
    *,
    max_rows: int = 5,
) -> Table:
    """Create a compact incident table (fewer columns).

    Args:
        incidents: List of incident dicts
        max_rows: Maximum number of rows to show

    Returns:
        Rich Table object
    """
    table = Table(
        box=SIMPLE,
        show_header=False,
        padding=(0, 1),
    )

    table.add_column(width=3)  # Icon
    table.add_column()  # Title + ID
    table.add_column(width=10, justify="right")  # Time

    for incident in incidents[:max_rows]:
        domain = str(incident.get("domain", ""))
        title_text = str(incident.get("title", ""))[:50]
        incident_id = str(incident.get("incident_id", ""))[:8]
        created_at = incident.get("created_at")

        icon = Text(Icons.domain_icon(domain), style=f"domain.{domain}")

        title_col = Text()
        title_col.append(title_text)
        title_col.append(f" [{incident_id}]", style="muted")

        time_col = time_ago(created_at) if isinstance(created_at, datetime) else "-"

        table.add_row(icon, title_col, time_col)

    if len(incidents) > max_rows:
        remaining = len(incidents) - max_rows
        table.add_row(
            "",
            Text(f"... and {remaining} more", style="muted"),
            "",
        )

    return table


# =============================================================================
# Event Table
# =============================================================================


def event_table(
    events: Sequence[dict[str, Any]],
    *,
    title: str | None = None,
    max_message_width: int = 50,
) -> Table:
    """Create a telemetry event table.

    Args:
        events: List of event dicts with keys:
            - ts: datetime
            - source: str
            - severity: str
            - category: str
            - message: str
        title: Optional table title
        max_message_width: Maximum width for message column

    Returns:
        Rich Table object
    """
    table = Table(
        title=title,
        box=ROUNDED,
        border_style=Colors.BORDER,
        show_header=True,
        header_style="bold",
        padding=(0, 1),
    )

    table.add_column("Time", style="muted", width=10)
    table.add_column("Source", width=8)
    table.add_column("Sev", width=4)
    table.add_column("Category", width=10)
    table.add_column("Message", max_width=max_message_width)

    for event in events:
        ts = event.get("ts")
        source = str(event.get("source", ""))
        severity = str(event.get("severity", "info"))
        category = str(event.get("category", ""))
        message = str(event.get("message", ""))

        # Truncate message
        if len(message) > max_message_width:
            message = message[: max_message_width - 3] + "..."

        # Severity indicator
        sev_icon = {
            "info": Icons.INFO,
            "warning": Icons.WARNING,
            "error": Icons.ERROR,
            "critical": Icons.FIRE,
        }.get(severity, Icons.BULLET)

        sev_text = Text(sev_icon, style=Theme.severity_color(severity))

        time_str = time_ago(ts) if isinstance(ts, datetime) else str(ts) if ts else "-"

        table.add_row(time_str, source, sev_text, category, message)

    return table


# =============================================================================
# Man Vault Search Results
# =============================================================================


def manvault_results_table(
    results: Sequence[dict[str, Any]],
    *,
    title: str | None = None,
) -> Table:
    """Create a Man Vault search results table.

    Args:
        results: List of result dicts with keys:
            - name: str (command name)
            - section: str (man section)
            - snippet: str (matched text snippet)
            - relevance: float (0-1)
        title: Optional table title

    Returns:
        Rich Table object
    """
    table = Table(
        title=title,
        box=ROUNDED,
        border_style=Colors.BORDER,
        show_header=True,
        header_style="bold",
        padding=(0, 1),
    )

    table.add_column("#", style="muted", width=3)
    table.add_column("Command", style="accent", width=15)
    table.add_column("Section", style="muted", width=8)
    table.add_column("Relevance", width=15)
    table.add_column("Snippet", max_width=50)

    for i, result in enumerate(results, 1):
        name = str(result.get("name", ""))
        section = str(result.get("section", ""))
        snippet = str(result.get("snippet", ""))[:50]
        relevance = float(result.get("relevance", 0))

        table.add_row(
            str(i),
            name,
            f"({section})",
            confidence_bar(relevance, width=8),
            snippet,
        )

    return table


# =============================================================================
# System Status Tables
# =============================================================================


def disk_status_table(
    disks: Sequence[dict[str, Any]],
    *,
    title: str = "Disk Usage",
) -> Table:
    """Create a disk status table.

    Args:
        disks: List of disk dicts with keys:
            - mount: str
            - used_pct: float (0-1)
            - avail_gb: float
            - total_gb: float (optional)
        title: Table title

    Returns:
        Rich Table object
    """
    table = Table(
        title=title,
        box=SIMPLE,
        show_header=True,
        header_style="bold dim",
        padding=(0, 1),
    )

    table.add_column("Mount", style="path")
    table.add_column("Usage", width=25)
    table.add_column("Available", justify="right")

    for disk in disks:
        mount = str(disk.get("mount", "/"))
        used_pct = float(disk.get("used_pct", 0))
        avail_gb = float(disk.get("avail_gb", 0))

        table.add_row(
            mount,
            percentage_bar(used_pct, width=15),
            f"{avail_gb:.1f} GB",
        )

    return table


def service_status_table(
    services: Sequence[dict[str, Any]],
    *,
    title: str = "Services",
) -> Table:
    """Create a service status table.

    Args:
        services: List of service dicts with keys:
            - name: str
            - active: bool
            - failed: bool
        title: Table title

    Returns:
        Rich Table object
    """
    table = Table(
        title=title,
        box=SIMPLE,
        show_header=True,
        header_style="bold dim",
        padding=(0, 1),
    )

    table.add_column("Service")
    table.add_column("Status", width=12)

    for service in services:
        name = str(service.get("name", ""))
        active = service.get("active", False)
        failed = service.get("failed", False)

        if failed:
            status = Text(f"{Icons.ERROR} failed", style="error")
        elif active:
            status = Text(f"{Icons.SUCCESS} active", style="success")
        else:
            status = Text(f"{Icons.PENDING} inactive", style="muted")

        table.add_row(name, status)

    return table


def interface_status_table(
    interfaces: Sequence[dict[str, Any]],
    *,
    title: str = "Network Interfaces",
) -> Table:
    """Create a network interface status table.

    Args:
        interfaces: List of interface dicts with keys:
            - name: str
            - state: str (up/down)
            - ip: str (optional)
            - errors: int (optional)
        title: Table title

    Returns:
        Rich Table object
    """
    table = Table(
        title=title,
        box=SIMPLE,
        show_header=True,
        header_style="bold dim",
        padding=(0, 1),
    )

    table.add_column("Interface")
    table.add_column("State", width=8)
    table.add_column("IP Address")
    table.add_column("Errors", justify="right", width=8)

    for iface in interfaces:
        name = str(iface.get("name", ""))
        state = str(iface.get("state", "down")).lower()
        ip = str(iface.get("ip", "-"))
        errors = iface.get("errors", 0)

        if state == "up":
            state_text = Text(f"{Icons.SUCCESS} up", style="success")
        else:
            state_text = Text(f"{Icons.PENDING} down", style="muted")

        error_text = Text(str(errors), style="error" if errors > 0 else "muted")

        table.add_row(name, state_text, ip, error_text)

    return table


# =============================================================================
# Command History Table
# =============================================================================


def command_history_table(
    commands: Sequence[dict[str, Any]],
    *,
    title: str | None = "Recent Commands",
    max_rows: int = 10,
) -> Table:
    """Create a command history table.

    Args:
        commands: List of command dicts with keys:
            - command: str
            - exit_code: int
            - timestamp: datetime (optional)
        title: Table title
        max_rows: Maximum rows to show

    Returns:
        Rich Table object
    """
    table = Table(
        title=title,
        box=SIMPLE,
        show_header=True,
        header_style="bold dim",
        padding=(0, 1),
    )

    table.add_column("#", style="muted", width=4)
    table.add_column("Command", max_width=60)
    table.add_column("Exit", width=6, justify="right")
    table.add_column("Time", style="muted", width=10)

    for i, cmd in enumerate(commands[:max_rows], 1):
        command = str(cmd.get("command", ""))[:60]
        exit_code = cmd.get("exit_code", 0)
        timestamp = cmd.get("timestamp")

        # Exit code styling
        if exit_code == 0:
            exit_text = Text(str(exit_code), style="success")
        else:
            exit_text = Text(str(exit_code), style="error")

        time_str = time_ago(timestamp) if isinstance(timestamp, datetime) else "-"

        table.add_row(str(i), command, exit_text, time_str)

    return table


# =============================================================================
# Options/Selection Table
# =============================================================================


def options_table(
    options: Sequence[tuple[str, str]],
    *,
    selected: int | None = None,
    title: str | None = None,
) -> Table:
    """Create a selectable options table.

    Args:
        options: List of (key, description) tuples
        selected: Currently selected index (0-based)
        title: Table title

    Returns:
        Rich Table object
    """
    table = Table(
        title=title,
        box=MINIMAL,
        show_header=False,
        padding=(0, 1),
    )

    table.add_column(width=3)  # Selection indicator
    table.add_column(width=5)  # Key
    table.add_column()  # Description

    for i, (key, desc) in enumerate(options):
        if selected is not None and i == selected:
            indicator = Text(Icons.CHEVRON, style="accent")
            key_text = Text(f"[{key}]", style="accent bold")
        else:
            indicator = Text("")
            key_text = Text(f"[{key}]", style="accent")

        table.add_row(indicator, key_text, desc)

    return table


# =============================================================================
# Snapshot Comparison Table
# =============================================================================


def snapshot_diff_table(
    changes: Sequence[dict[str, Any]],
    *,
    title: str = "System Changes",
) -> Table:
    """Create a system snapshot comparison table.

    Args:
        changes: List of change dicts with keys:
            - field: str
            - before: str
            - after: str
            - severity: str (optional)
        title: Table title

    Returns:
        Rich Table object
    """
    table = Table(
        title=title,
        box=ROUNDED,
        border_style=Colors.BORDER,
        show_header=True,
        header_style="bold",
        padding=(0, 1),
    )

    table.add_column("Field")
    table.add_column("Before", style="muted")
    table.add_column("", width=3)  # Arrow
    table.add_column("After")

    for change in changes:
        field = str(change.get("field", ""))
        before = str(change.get("before", "-"))
        after = str(change.get("after", "-"))
        severity = str(change.get("severity", "info"))

        arrow = Text(Icons.ARROW_RIGHT, style=Theme.severity_color(severity))
        after_text = Text(after, style=Theme.severity_color(severity))

        table.add_row(field, before, arrow, after_text)

    return table
