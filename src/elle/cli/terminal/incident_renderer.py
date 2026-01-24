"""Incident rendering utilities for the REPL.

Provides unicode grid rendering for:
- Incident lists (recent + outcomes)
- Incident details (snapshots, actions, checks)
- Snapshot diffs (before/after comparison)
- Markdown export
"""

from datetime import datetime, timedelta, UTC
from typing import Any

from elle.daemon.incidents.models import (
    IncidentReport,
    IncidentAction,
    IncidentSnapshot,
    SystemSnapshot,
    Fingerprint,
)


# =============================================================================
# Unicode Box Drawing Characters
# =============================================================================

BOX = {
    "tl": "\u250c",  # ┌
    "tr": "\u2510",  # ┐
    "bl": "\u2514",  # └
    "br": "\u2518",  # ┘
    "h": "\u2500",   # ─
    "v": "\u2502",   # │
    "vr": "\u251c",  # ├
    "vl": "\u2524",  # ┤
    "hd": "\u252c",  # ┬
    "hu": "\u2534",  # ┴
    "x": "\u253c",   # ┼
}

# Double line variants for headers
DBOX = {
    "h": "\u2550",   # ═
    "tl": "\u2554",  # ╔
    "tr": "\u2557",  # ╗
    "bl": "\u255a",  # ╚
    "br": "\u255d",  # ╝
    "v": "\u2551",   # ║
}


# =============================================================================
# ANSI Color Codes
# =============================================================================

class Color:
    """ANSI color codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    GRAY = "\033[90m"

    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"


# =============================================================================
# Status & Outcome Badges
# =============================================================================

STATUS_BADGES = {
    "open": f"{Color.BG_YELLOW}{Color.BOLD} OPEN {Color.RESET}",
    "mitigated": f"{Color.BG_BLUE}{Color.BOLD} MITIGATED {Color.RESET}",
    "resolved": f"{Color.BG_GREEN}{Color.BOLD} RESOLVED {Color.RESET}",
    "false_positive": f"{Color.DIM} FALSE POSITIVE {Color.RESET}",
}

OUTCOME_BADGES = {
    "improved": f"{Color.GREEN}\u2714 improved{Color.RESET}",
    "partial": f"{Color.YELLOW}\u25d0 partial{Color.RESET}",
    "no_change": f"{Color.GRAY}\u2500 no change{Color.RESET}",
    "worse": f"{Color.RED}\u2716 worse{Color.RESET}",
    "unknown": f"{Color.DIM}? unknown{Color.RESET}",
}

SEVERITY_COLORS = {
    "critical": Color.RED,
    "error": Color.RED,
    "warning": Color.YELLOW,
    "info": Color.CYAN,
}

DOMAIN_ICONS = {
    "net": "\u26a1",      # ⚡
    "disk": "\u26c1",     # ⛁
    "oom": "\u26a0",      # ⚠
    "docker": "\u2693",   # ⚓
    "auth": "\u26bf",     # ⚿
    "pkg": "\u2615",      # ☕
    "fs": "\u2648",       # ♈
    "service": "\u2699",  # ⚙
    "thermal": "\u2668",  # ♨
    "smart": "\u26c3",    # ⛃
    "other": "\u2022",    # •
}


# =============================================================================
# Utility Functions
# =============================================================================

def _truncate(text: str, max_len: int, ellipsis: str = "\u2026") -> str:
    """Truncate text with ellipsis if too long."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + ellipsis


def _time_ago(dt: datetime) -> str:
    """Convert datetime to human-readable 'time ago' string."""
    now = datetime.now(UTC)
    # Handle naive datetime
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    delta = now - dt

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


def _format_duration(seconds: int | None) -> str:
    """Format duration in seconds to human-readable string."""
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    else:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        return f"{hours}h {mins}m"


def _pad(text: str, width: int, align: str = "left") -> str:
    """Pad text to width with alignment."""
    # Strip ANSI codes for length calculation
    import re
    visible_len = len(re.sub(r'\033\[[0-9;]*m', '', text))
    padding = width - visible_len

    if padding <= 0:
        return text

    if align == "left":
        return text + " " * padding
    elif align == "right":
        return " " * padding + text
    else:  # center
        left = padding // 2
        right = padding - left
        return " " * left + text + " " * right


# =============================================================================
# Incident List Rendering
# =============================================================================

def render_incident_list(
    incidents: list[IncidentReport],
    title: str = "Recent Incidents",
    show_header: bool = True,
) -> str:
    """Render a list of incidents as a unicode table.

    Example output:
    ╔══════════════════════════════════════════════════════════════════════════╗
    ║  Recent Incidents                                                         ║
    ╚══════════════════════════════════════════════════════════════════════════╝
    ┌────────────┬────────┬──────────────────────────────────┬─────────┬────────┐
    │ ID         │ Domain │ Title                            │ Outcome │ When   │
    ├────────────┼────────┼──────────────────────────────────┼─────────┼────────┤
    │ abc123def4 │ ⚙ svc  │ nginx.service failed to start    │ ✔ impr… │ 2h ago │
    │ xyz789abc0 │ ⛁ disk │ /dev/sda low space warning       │ ? unkn… │ 1d ago │
    └────────────┴────────┴──────────────────────────────────┴─────────┴────────┘
    """
    lines = []

    # Column widths
    id_w = 10
    domain_w = 8
    title_w = 36
    outcome_w = 12
    when_w = 10

    total_w = id_w + domain_w + title_w + outcome_w + when_w + 6  # 6 for borders

    # Header box
    if show_header:
        lines.append(f"{DBOX['tl']}{DBOX['h'] * (total_w - 2)}{DBOX['tr']}")
        lines.append(f"{DBOX['v']}  {Color.BOLD}{_pad(title, total_w - 4)}{Color.RESET}{DBOX['v']}")
        lines.append(f"{DBOX['bl']}{DBOX['h'] * (total_w - 2)}{DBOX['br']}")

    if not incidents:
        lines.append(f"{Color.DIM}  No incidents found.{Color.RESET}")
        return "\n".join(lines)

    # Table header
    h_line = (
        f"{BOX['tl']}{BOX['h'] * id_w}{BOX['hd']}"
        f"{BOX['h'] * domain_w}{BOX['hd']}"
        f"{BOX['h'] * title_w}{BOX['hd']}"
        f"{BOX['h'] * outcome_w}{BOX['hd']}"
        f"{BOX['h'] * when_w}{BOX['tr']}"
    )
    lines.append(h_line)

    header = (
        f"{BOX['v']}{Color.BOLD}{_pad(' ID', id_w)}{Color.RESET}{BOX['v']}"
        f"{Color.BOLD}{_pad(' Domain', domain_w)}{Color.RESET}{BOX['v']}"
        f"{Color.BOLD}{_pad(' Title', title_w)}{Color.RESET}{BOX['v']}"
        f"{Color.BOLD}{_pad(' Outcome', outcome_w)}{Color.RESET}{BOX['v']}"
        f"{Color.BOLD}{_pad(' When', when_w)}{Color.RESET}{BOX['v']}"
    )
    lines.append(header)

    sep_line = (
        f"{BOX['vr']}{BOX['h'] * id_w}{BOX['x']}"
        f"{BOX['h'] * domain_w}{BOX['x']}"
        f"{BOX['h'] * title_w}{BOX['x']}"
        f"{BOX['h'] * outcome_w}{BOX['x']}"
        f"{BOX['h'] * when_w}{BOX['vl']}"
    )
    lines.append(sep_line)

    # Rows
    for inc in incidents:
        icon = DOMAIN_ICONS.get(inc.domain, "\u2022")
        domain_str = f" {icon} {inc.domain[:4]}"

        outcome_str = OUTCOME_BADGES.get(inc.outcome, f"{Color.DIM}?{Color.RESET}")
        outcome_short = _truncate(outcome_str, outcome_w - 1)

        row = (
            f"{BOX['v']} {_pad(inc.incident_id[:9], id_w - 1)}{BOX['v']}"
            f"{_pad(domain_str, domain_w)}{BOX['v']}"
            f" {_pad(_truncate(inc.title, title_w - 2), title_w - 1)}{BOX['v']}"
            f" {_pad(outcome_short, outcome_w - 1)}{BOX['v']}"
            f" {_pad(_time_ago(inc.updated_at), when_w - 1)}{BOX['v']}"
        )
        lines.append(row)

    # Bottom border
    b_line = (
        f"{BOX['bl']}{BOX['h'] * id_w}{BOX['hu']}"
        f"{BOX['h'] * domain_w}{BOX['hu']}"
        f"{BOX['h'] * title_w}{BOX['hu']}"
        f"{BOX['h'] * outcome_w}{BOX['hu']}"
        f"{BOX['h'] * when_w}{BOX['br']}"
    )
    lines.append(b_line)

    lines.append(f"\n{Color.DIM}  Showing {len(incidents)} incident(s). Use 'incident <id>' for details.{Color.RESET}")

    return "\n".join(lines)


# =============================================================================
# Incident Detail Rendering
# =============================================================================

def render_incident_detail(
    incident: IncidentReport,
    actions: list[IncidentAction] | None = None,
    snapshots: dict[str, IncidentSnapshot] | None = None,
) -> str:
    """Render detailed view of a single incident.

    Shows:
    - Header with status badge
    - What happened (symptoms)
    - What we did (actions)
    - Result (outcome + verification)
    - Snapshot diff (if pre/post available)
    """
    lines = []

    # Header
    status_badge = STATUS_BADGES.get(incident.status, incident.status)
    severity_color = SEVERITY_COLORS.get(incident.severity, "")
    domain_icon = DOMAIN_ICONS.get(incident.domain, "\u2022")

    lines.append("")
    lines.append(f"  {DBOX['h'] * 70}")
    lines.append(f"  {Color.BOLD}{incident.title}{Color.RESET}")
    lines.append(f"  {status_badge}  {severity_color}{incident.severity.upper()}{Color.RESET}  {domain_icon} {incident.domain}")
    lines.append(f"  {Color.DIM}ID: {incident.incident_id}{Color.RESET}")
    lines.append(f"  {Color.DIM}Created: {incident.created_at.strftime('%Y-%m-%d %H:%M:%S')} ({_time_ago(incident.created_at)}){Color.RESET}")
    lines.append(f"  {DBOX['h'] * 70}")
    lines.append("")

    # What Happened
    lines.append(f"  {Color.BOLD}{Color.CYAN}\u2139 WHAT HAPPENED{Color.RESET}")
    lines.append(f"  {BOX['h'] * 30}")

    if incident.summary:
        lines.append(f"  {incident.summary}")
        lines.append("")

    if incident.symptoms:
        lines.append(f"  {Color.DIM}Symptoms:{Color.RESET}")
        for symptom in incident.symptoms:
            lines.append(f"    \u2022 {symptom}")
        lines.append("")

    if incident.suspected_causes:
        lines.append(f"  {Color.DIM}Suspected causes:{Color.RESET}")
        for cause in incident.suspected_causes:
            lines.append(f"    \u2022 {cause}")
        lines.append("")

    if incident.root_cause:
        lines.append(f"  {Color.GREEN}Root cause:{Color.RESET} {incident.root_cause}")
        lines.append("")

    # What We Did
    if actions:
        lines.append(f"  {Color.BOLD}{Color.MAGENTA}\u26a1 WHAT WE DID{Color.RESET}")
        lines.append(f"  {BOX['h'] * 30}")

        for i, action in enumerate(actions, 1):
            success_icon = f"{Color.GREEN}\u2714{Color.RESET}" if action.success else f"{Color.RED}\u2716{Color.RESET}"
            duration = _format_duration(action.duration_ms // 1000 if action.duration_ms else None)

            lines.append(f"  {i}. {success_icon} [{action.kind}] {Color.CYAN}{action.command}{Color.RESET}")
            if action.exit_code is not None and action.exit_code != 0:
                lines.append(f"     {Color.DIM}Exit code: {action.exit_code}{Color.RESET}")
            if duration != "-":
                lines.append(f"     {Color.DIM}Duration: {duration}{Color.RESET}")
        lines.append("")

    # Result
    lines.append(f"  {Color.BOLD}{Color.YELLOW}\u2605 RESULT{Color.RESET}")
    lines.append(f"  {BOX['h'] * 30}")

    outcome_badge = OUTCOME_BADGES.get(incident.outcome, f"{Color.DIM}unknown{Color.RESET}")
    lines.append(f"  Outcome: {outcome_badge}")

    if incident.time_to_mitigate_sec:
        lines.append(f"  Time to mitigate: {_format_duration(incident.time_to_mitigate_sec)}")
    if incident.time_to_resolve_sec:
        lines.append(f"  Time to resolve: {_format_duration(incident.time_to_resolve_sec)}")

    if incident.verification_steps:
        lines.append(f"\n  {Color.DIM}Verification:{Color.RESET}")
        for step in incident.verification_steps:
            lines.append(f"    \u2714 {step}")

    lines.append("")

    # Snapshot Diff
    if snapshots:
        pre = snapshots.get("pre")
        post = snapshots.get("post")
        if pre and post:
            diff = render_snapshot_diff(pre.snapshot, post.snapshot)
            if diff:
                lines.append(f"  {Color.BOLD}{Color.BLUE}\u0394 STATE CHANGES{Color.RESET}")
                lines.append(f"  {BOX['h'] * 30}")
                lines.append(diff)
                lines.append("")

    # Fingerprint summary
    if incident.fingerprint:
        lines.append(f"  {Color.DIM}Fingerprint: disk={incident.fingerprint.disk_pressure:.0%} mem={incident.fingerprint.mem_pressure:.0%} cpu={incident.fingerprint.cpu_pressure:.1f}{Color.RESET}")

    return "\n".join(lines)


# =============================================================================
# Snapshot Diff Rendering
# =============================================================================

def render_snapshot_diff(
    pre: SystemSnapshot,
    post: SystemSnapshot,
) -> str:
    """Render diff between pre and post snapshots.

    Shows top changed metrics:
    - Memory usage change
    - Disk usage changes
    - Interface state changes
    - Service state changes
    """
    changes = []

    # Memory
    pre_mem_pct = 100 * (1 - pre.mem_available_mb / pre.mem_total_mb) if pre.mem_total_mb else 0
    post_mem_pct = 100 * (1 - post.mem_available_mb / post.mem_total_mb) if post.mem_total_mb else 0
    mem_delta = post_mem_pct - pre_mem_pct

    if abs(mem_delta) > 5:  # Only show if > 5% change
        arrow = "\u2191" if mem_delta > 0 else "\u2193"
        color = Color.RED if mem_delta > 0 else Color.GREEN
        changes.append(f"    Memory: {pre_mem_pct:.0f}% {arrow} {post_mem_pct:.0f}% ({color}{mem_delta:+.0f}%{Color.RESET})")

    # Disk changes
    pre_disks = {d.get("mount"): d.get("used_pct", 0) for d in pre.disks}
    post_disks = {d.get("mount"): d.get("used_pct", 0) for d in post.disks}

    for mount in set(pre_disks.keys()) | set(post_disks.keys()):
        pre_pct = pre_disks.get(mount, 0)
        post_pct = post_disks.get(mount, 0)
        delta = post_pct - pre_pct

        if abs(delta) > 2:  # Only show if > 2% change
            arrow = "\u2191" if delta > 0 else "\u2193"
            color = Color.RED if delta > 0 else Color.GREEN
            changes.append(f"    Disk {mount}: {pre_pct:.0f}% {arrow} {post_pct:.0f}% ({color}{delta:+.0f}%{Color.RESET})")

    # Interface changes
    pre_ifaces = {i.get("name"): i.get("state") for i in pre.interfaces}
    post_ifaces = {i.get("name"): i.get("state") for i in post.interfaces}

    for iface in set(pre_ifaces.keys()) | set(post_ifaces.keys()):
        pre_state = pre_ifaces.get(iface, "unknown")
        post_state = post_ifaces.get(iface, "unknown")

        if pre_state != post_state:
            if post_state == "up":
                changes.append(f"    Interface {iface}: {pre_state} \u2192 {Color.GREEN}{post_state}{Color.RESET}")
            elif post_state == "down":
                changes.append(f"    Interface {iface}: {pre_state} \u2192 {Color.RED}{post_state}{Color.RESET}")
            else:
                changes.append(f"    Interface {iface}: {pre_state} \u2192 {post_state}")

    # Service changes
    pre_svcs = {s.get("name"): (s.get("active"), s.get("failed")) for s in pre.services}
    post_svcs = {s.get("name"): (s.get("active"), s.get("failed")) for s in post.services}

    for svc in set(pre_svcs.keys()) | set(post_svcs.keys()):
        pre_active, pre_failed = pre_svcs.get(svc, (None, None))
        post_active, post_failed = post_svcs.get(svc, (None, None))

        if (pre_active, pre_failed) != (post_active, post_failed):
            pre_status = "failed" if pre_failed else ("active" if pre_active else "inactive")
            post_status = "failed" if post_failed else ("active" if post_active else "inactive")

            if post_status == "active":
                changes.append(f"    Service {svc}: {pre_status} \u2192 {Color.GREEN}{post_status}{Color.RESET}")
            elif post_status == "failed":
                changes.append(f"    Service {svc}: {pre_status} \u2192 {Color.RED}{post_status}{Color.RESET}")
            else:
                changes.append(f"    Service {svc}: {pre_status} \u2192 {post_status}")

    if not changes:
        return f"    {Color.DIM}No significant state changes detected.{Color.RESET}"

    return "\n".join(changes)


# =============================================================================
# Markdown Export
# =============================================================================

def render_incident_markdown(
    incident: IncidentReport,
    actions: list[IncidentAction] | None = None,
    snapshots: dict[str, IncidentSnapshot] | None = None,
) -> str:
    """Export incident as markdown for sharing.

    Returns a complete markdown document suitable for pasting
    into issues, wikis, or documentation.
    """
    lines = []

    # Title
    lines.append(f"# {incident.title}")
    lines.append("")
    lines.append(f"**ID:** `{incident.incident_id}`  ")
    lines.append(f"**Status:** {incident.status}  ")
    lines.append(f"**Severity:** {incident.severity}  ")
    lines.append(f"**Domain:** {incident.domain}  ")
    lines.append(f"**Outcome:** {incident.outcome}  ")
    lines.append(f"**Created:** {incident.created_at.isoformat()}  ")
    lines.append(f"**Updated:** {incident.updated_at.isoformat()}  ")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    if incident.summary:
        lines.append(incident.summary)
    else:
        lines.append("_No summary provided._")
    lines.append("")

    # Symptoms
    if incident.symptoms:
        lines.append("## Symptoms")
        lines.append("")
        for symptom in incident.symptoms:
            lines.append(f"- {symptom}")
        lines.append("")

    # Suspected Causes
    if incident.suspected_causes:
        lines.append("## Suspected Causes")
        lines.append("")
        for cause in incident.suspected_causes:
            lines.append(f"- {cause}")
        lines.append("")

    # Root Cause
    if incident.root_cause:
        lines.append("## Root Cause")
        lines.append("")
        lines.append(incident.root_cause)
        lines.append("")

    # Actions Taken
    if actions:
        lines.append("## Actions Taken")
        lines.append("")
        lines.append("| # | Type | Command | Success | Exit Code |")
        lines.append("|---|------|---------|---------|-----------|")
        for i, action in enumerate(actions, 1):
            success = "\u2714" if action.success else "\u2716"
            exit_code = action.exit_code if action.exit_code is not None else "-"
            cmd = action.command.replace("|", "\\|") if action.command else "-"
            lines.append(f"| {i} | {action.kind} | `{cmd}` | {success} | {exit_code} |")
        lines.append("")

    # Verification
    if incident.verification_steps:
        lines.append("## Verification")
        lines.append("")
        for step in incident.verification_steps:
            lines.append(f"- [x] {step}")
        lines.append("")

    # Timing
    if incident.time_to_mitigate_sec or incident.time_to_resolve_sec:
        lines.append("## Timing")
        lines.append("")
        if incident.time_to_mitigate_sec:
            lines.append(f"- **Time to mitigate:** {_format_duration(incident.time_to_mitigate_sec)}")
        if incident.time_to_resolve_sec:
            lines.append(f"- **Time to resolve:** {_format_duration(incident.time_to_resolve_sec)}")
        lines.append("")

    # Snapshots
    if snapshots:
        pre = snapshots.get("pre")
        post = snapshots.get("post")

        if pre:
            lines.append("## Pre-Action State")
            lines.append("")
            lines.append(_snapshot_to_markdown(pre.snapshot))
            lines.append("")

        if post:
            lines.append("## Post-Action State")
            lines.append("")
            lines.append(_snapshot_to_markdown(post.snapshot))
            lines.append("")

    # Fingerprint
    if incident.fingerprint:
        lines.append("## Fingerprint")
        lines.append("")
        lines.append("```json")
        lines.append(f"disk_pressure: {incident.fingerprint.disk_pressure:.2f}")
        lines.append(f"mem_pressure: {incident.fingerprint.mem_pressure:.2f}")
        lines.append(f"swap_pressure: {incident.fingerprint.swap_pressure:.2f}")
        lines.append(f"cpu_pressure: {incident.fingerprint.cpu_pressure:.2f}")
        if incident.fingerprint.entities:
            lines.append(f"entities: {', '.join(incident.fingerprint.entities)}")
        lines.append("```")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"_Generated by ELLE on {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC_")

    return "\n".join(lines)


def _snapshot_to_markdown(snapshot: SystemSnapshot) -> str:
    """Convert snapshot to markdown summary."""
    lines = []

    lines.append(f"- **OS:** {snapshot.os}")
    lines.append(f"- **Kernel:** {snapshot.kernel}")
    lines.append(f"- **Uptime:** {_format_duration(snapshot.uptime_sec)}")
    lines.append(f"- **Load Average:** {snapshot.cpu_load[0]:.2f}, {snapshot.cpu_load[1]:.2f}, {snapshot.cpu_load[2]:.2f}")

    mem_pct = 100 * (1 - snapshot.mem_available_mb / snapshot.mem_total_mb) if snapshot.mem_total_mb else 0
    lines.append(f"- **Memory:** {mem_pct:.0f}% used ({snapshot.mem_available_mb}MB available / {snapshot.mem_total_mb}MB total)")

    if snapshot.disks:
        lines.append("- **Disks:**")
        for disk in snapshot.disks:
            mount = disk.get("mount", "?")
            used_pct = disk.get("used_pct", 0)
            avail_gb = disk.get("avail_gb", 0)
            lines.append(f"  - `{mount}`: {used_pct:.0f}% used, {avail_gb:.1f}GB available")

    if snapshot.interfaces:
        lines.append("- **Interfaces:**")
        for iface in snapshot.interfaces:
            name = iface.get("name", "?")
            state = iface.get("state", "unknown")
            lines.append(f"  - `{name}`: {state}")

    return "\n".join(lines)


# =============================================================================
# Search Results Rendering
# =============================================================================

def render_search_results(
    query: str,
    results: list[tuple[IncidentReport, float]],
) -> str:
    """Render incident search results with relevance scores."""
    lines = []

    lines.append(f"  {Color.BOLD}Search results for: {Color.CYAN}\"{query}\"{Color.RESET}")
    lines.append(f"  {BOX['h'] * 50}")
    lines.append("")

    if not results:
        lines.append(f"  {Color.DIM}No incidents found matching your query.{Color.RESET}")
        return "\n".join(lines)

    for inc, score in results:
        icon = DOMAIN_ICONS.get(inc.domain, "\u2022")
        outcome = OUTCOME_BADGES.get(inc.outcome, "")

        # Score bar (0-100%)
        bar_len = int(score * 10)
        bar = f"{Color.GREEN}{'█' * bar_len}{Color.DIM}{'░' * (10 - bar_len)}{Color.RESET}"

        lines.append(f"  {bar} {score:.0%}  {icon} {Color.BOLD}{inc.incident_id[:8]}{Color.RESET}")
        lines.append(f"       {inc.title}")
        lines.append(f"       {outcome}  {Color.DIM}{_time_ago(inc.updated_at)}{Color.RESET}")
        lines.append("")

    lines.append(f"  {Color.DIM}Use 'incident <id>' for full details.{Color.RESET}")

    return "\n".join(lines)
