#!/usr/bin/env python3
"""ELLE UI Demo - Showcases all UI components.

Run with: python -m elle.cli.ui.demo
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from elle.cli.ui import (
    bulleted_list,
    clear,
    command_display,
    confidence_bar,
    config_diff_panel,
    # Console
    console,
    disk_status_table,
    domain_badge,
    empty_state,
    event_table,
    # Panels
    fixit_panel,
    fixit_phases,
    format_duration,
    incident_panel,
    # Tables
    incident_table,
    key_value,
    key_value_table,
    numbered_list,
    option_list,
    outcome_badge,
    path_display,
    percentage_bar,
    plan_panel,
    # Banner
    print_banner,
    print_error,
    print_info,
    print_muted,
    print_success,
    print_warning,
    privilege_badge,
    risk_badge,
    rule,
    search_results_panel,
    service_status_table,
    severity_badge,
    # Spinner
    spinner,
    status_badge,
    time_ago,
    tip,
)


def demo_banner() -> None:
    """Demo the startup banner."""
    rule("Startup Banner", style="bold cyan")
    console.print()

    print_banner(
        model="Qwen2.5-7B",
        daemon_status="running",
        vault_docs=2341,
        incidents_open=2,
    )


def demo_messages() -> None:
    """Demo message types."""
    rule("Message Types", style="bold cyan")
    console.print()

    print_error("Something went wrong")
    print_warning("This might cause issues")
    print_success("Operation completed")
    print_info("Here's some information")
    print_muted("This is less important")
    console.print()


def demo_badges() -> None:
    """Demo all badge types."""
    rule("Badges", style="bold cyan")
    console.print()

    console.print("Status badges:")
    for status in ["open", "mitigated", "resolved", "false_positive"]:
        console.print("  ", status_badge(status), f" - {status}")  # type: ignore[arg-type]

    console.print("\nOutcome badges:")
    for outcome in ["improved", "partial", "no_change", "worse"]:
        console.print("  ", outcome_badge(outcome), f" - {outcome}")  # type: ignore[arg-type]

    console.print("\nRisk badges:")
    for risk in ["low", "medium", "high"]:
        console.print("  ", risk_badge(risk), f" - {risk}")  # type: ignore[arg-type]

    console.print("\nSeverity badges:")
    for sev in ["info", "warning", "error", "critical"]:
        console.print("  ", severity_badge(sev), f" - {sev}")  # type: ignore[arg-type]

    console.print("\nDomain badges:")
    for domain in ["net", "disk", "oom", "docker", "auth", "pkg", "service", "fs"]:
        console.print("  ", domain_badge(domain), f" - {domain}")

    console.print("\nPrivilege badges:")
    console.print("  ", privilege_badge(True))
    console.print("  ", privilege_badge(False))
    console.print()


def demo_lists() -> None:
    """Demo list components."""
    rule("Lists", style="bold cyan")
    console.print()

    console.print("Bulleted list:")
    console.print(bulleted_list(["First item", "Second item", "Third item"]))

    console.print("\nNumbered list:")
    console.print(numbered_list(["Step one", "Step two", "Step three"]))

    console.print("\nOption list:")
    console.print(option_list([
        ("a", "Option A - Do something"),
        ("b", "Option B - Do something else"),
        ("c", "Option C - Cancel"),
    ], selected=1))
    console.print()


def demo_key_value() -> None:
    """Demo key-value displays."""
    rule("Key-Value Displays", style="bold cyan")
    console.print()

    console.print(key_value("Status", "Running", value_style="success"))
    console.print(key_value("Exit Code", "1", value_style="error"))
    console.print()

    console.print(key_value_table([
        ("OS", "Ubuntu 24.04 LTS"),
        ("Kernel", "6.8.0-40-generic"),
        ("Uptime", "3d 14h 22m"),
        ("Load", "0.42 0.38 0.35"),
    ], title="System Info"))
    console.print()


def demo_progress() -> None:
    """Demo progress indicators."""
    rule("Progress Indicators", style="bold cyan")
    console.print()

    console.print("Confidence bars:")
    for conf in [0.95, 0.75, 0.50, 0.25]:
        console.print("  ", confidence_bar(conf))

    console.print("\nPercentage bars (disk usage):")
    for pct in [0.45, 0.72, 0.91, 0.98]:
        console.print("  ", percentage_bar(pct))
    console.print()


def demo_time() -> None:
    """Demo time formatting."""
    rule("Time Formatting", style="bold cyan")
    console.print()

    console.print("Time ago:")
    now = datetime.now()
    for delta in [
        timedelta(seconds=30),
        timedelta(minutes=5),
        timedelta(hours=2),
        timedelta(days=3),
        timedelta(weeks=2),
    ]:
        console.print(f"  {time_ago(now - delta)}")

    console.print("\nDuration formatting:")
    for secs in [45, 185, 3725, 86520]:
        console.print(f"  {secs}s = {format_duration(secs)}")
    console.print()


def demo_code() -> None:
    """Demo code/command display."""
    rule("Code Display", style="bold cyan")
    console.print()

    console.print("Command display:")
    console.print("  ", command_display("apt update && apt upgrade -y"))
    console.print("  ", command_display("systemctl restart nginx", prefix="#"))

    console.print("\nPath display:")
    console.print("  ", path_display("/etc/ssh/sshd_config"))
    console.print("  ", path_display("/var/lib/elle/manvault.db"))
    console.print()


def demo_misc() -> None:
    """Demo miscellaneous components."""
    rule("Miscellaneous Components", style="bold cyan")
    console.print()

    console.print("Tip:")
    console.print("  ", tip("Type /help to see available commands"))

    console.print("\nEmpty state:")
    console.print(empty_state("No incidents found", suggestion="Check back later"))
    console.print()


def demo_spinners() -> None:
    """Demo spinner components."""
    rule("Spinners", style="bold cyan")
    console.print()

    console.print("Simple spinner (2 seconds):")
    with spinner("Processing", done_message="Done!", show_elapsed=True):
        time.sleep(2)

    console.print("\nMulti-phase spinner:")
    with fixit_phases() as s:
        for i in range(5):
            time.sleep(0.5)
            if i < 4:
                s.advance()
    console.print("[success]Fix complete![/success]")
    console.print()


def demo_fixit_panel() -> None:
    """Demo fixit panel."""
    rule("Fixit Panel", style="bold cyan")
    console.print()

    panel = fixit_panel(
        command="apt update",
        exit_code=1,
        diagnosis="The apt lock file is held by another process.",
        root_cause="Another package manager (apt, snap, or unattended-upgrades) is currently running.",
        confidence=0.92,
        suggestions=[
            {
                "command": "sudo rm /var/lib/apt/lists/lock",
                "explanation": "Remove the stale lock file",
                "risk": "low",
            },
            {
                "command": "sudo kill $(lsof -t /var/lib/dpkg/lock-frontend)",
                "explanation": "Kill the process holding the lock",
                "risk": "medium",
            },
        ],
        verification_commands=["apt update"],
        requires_privilege=True,
        incident_id="inc_abc123",
        learning_resources=["man apt(8)", "man dpkg(1)"],
    )
    console.print(panel)
    console.print()


def demo_plan_panel() -> None:
    """Demo plan panel."""
    rule("Plan Panel", style="bold cyan")
    console.print()

    panel = plan_panel(
        title="Configure SSH Key Authentication",
        explanation="This plan will disable password authentication and enable SSH key-only login for improved security.",
        steps=[
            {
                "description": "Backup current sshd_config",
                "command": "cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak",
                "verification": "File exists",
                "can_rollback": True,
            },
            {
                "description": "Disable password authentication",
                "command": "sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config",
                "verification": "grep -q 'PasswordAuthentication no'",
                "can_rollback": True,
            },
            {
                "description": "Restart SSH service",
                "command": "systemctl restart sshd",
                "verification": "systemctl is-active sshd",
                "can_rollback": False,
            },
        ],
        risks=[
            "You may be locked out if SSH keys are not properly configured",
            "Existing password-based sessions will continue until logout",
        ],
        rollback_steps=[
            {"description": "Restore backup config"},
            {"description": "Restart SSH service"},
        ],
        requires_privilege=True,
        current_step=1,  # Show step 2 as in-progress
    )
    console.print(panel)
    console.print()


def demo_incident_panel() -> None:
    """Demo incident panel."""
    rule("Incident Panel", style="bold cyan")
    console.print()

    panel = incident_panel(
        incident_id="inc_20240115_001",
        title="OOM killer terminated nginx worker",
        domain="oom",
        status="mitigated",
        severity="error",
        created_at=datetime.now() - timedelta(hours=2, minutes=15),
        summary="The OOM killer terminated an nginx worker process due to memory pressure. Server was experiencing high traffic.",
        symptoms=[
            "nginx worker process terminated unexpectedly",
            "Memory usage peaked at 98%",
            "Swap usage at 100%",
        ],
        root_cause="Insufficient memory limits for nginx worker processes under high traffic",
        outcome="improved",
        actions_count=3,
        similar_incidents=2,
    )
    console.print(panel)
    console.print()


def demo_search_panel() -> None:
    """Demo search results panel."""
    rule("Search Results Panel", style="bold cyan")
    console.print()

    panel = search_results_panel(
        query="mount nfs share",
        results=[
            {"title": "mount(8)", "snippet": "mount a filesystem - mount -t nfs server:/path /mnt", "relevance": 0.95},
            {"title": "nfs(5)", "snippet": "NFS mount options and configuration", "relevance": 0.87},
            {"title": "fstab(5)", "snippet": "static file system information, including NFS mounts", "relevance": 0.72},
        ],
        source="Man Vault",
    )
    console.print(panel)
    console.print()


def demo_config_diff() -> None:
    """Demo config diff panel."""
    rule("Config Diff Panel", style="bold cyan")
    console.print()

    diff_text = """--- /etc/ssh/sshd_config.orig
+++ /etc/ssh/sshd_config
@@ -32,7 +32,7 @@
 #LoginGraceTime 2m
-PermitRootLogin yes
+PermitRootLogin no
 #StrictModes yes
@@ -56,7 +56,7 @@
 # Change to yes to enable challenge-response passwords
-PasswordAuthentication yes
+PasswordAuthentication no
"""

    panel = config_diff_panel(
        path="/etc/ssh/sshd_config",
        diff_text=diff_text,
        operations=[
            {"kind": "set", "path": "PermitRootLogin", "value": "no"},
            {"kind": "set", "path": "PasswordAuthentication", "value": "no"},
        ],
        requires_privilege=True,
        validation_command="sshd -t",
        risks=["Ensure you have SSH key access before applying"],
    )
    console.print(panel)
    console.print()


def demo_incident_table() -> None:
    """Demo incident table."""
    rule("Incident Table", style="bold cyan")
    console.print()

    incidents = [
        {
            "incident_id": "inc_20240115_001",
            "title": "OOM killer terminated nginx worker",
            "domain": "oom",
            "status": "resolved",
            "severity": "error",
            "created_at": datetime.now() - timedelta(hours=2),
            "outcome": "improved",
        },
        {
            "incident_id": "inc_20240115_002",
            "title": "Disk usage exceeded 95% on /var",
            "domain": "disk",
            "status": "open",
            "severity": "warning",
            "created_at": datetime.now() - timedelta(minutes=30),
            "outcome": None,
        },
        {
            "incident_id": "inc_20240114_003",
            "title": "SSH authentication failures from 192.168.1.100",
            "domain": "auth",
            "status": "mitigated",
            "severity": "warning",
            "created_at": datetime.now() - timedelta(days=1),
            "outcome": "partial",
        },
    ]

    table = incident_table(incidents, title="Recent Incidents")
    console.print(table)
    console.print()


def demo_event_table() -> None:
    """Demo event table."""
    rule("Event Table", style="bold cyan")
    console.print()

    events = [
        {"ts": datetime.now() - timedelta(minutes=5), "source": "journal", "severity": "error", "category": "oom", "message": "Out of memory: Killed process 12345 (nginx)"},
        {"ts": datetime.now() - timedelta(minutes=15), "source": "kernel", "severity": "warning", "category": "disk", "message": "EXT4-fs warning: mounting fs with errors"},
        {"ts": datetime.now() - timedelta(hours=1), "source": "probe", "severity": "info", "category": "network", "message": "Interface eth0 link up at 1000 Mbps"},
    ]

    table = event_table(events, title="Recent Events")
    console.print(table)
    console.print()


def demo_status_tables() -> None:
    """Demo system status tables."""
    rule("System Status Tables", style="bold cyan")
    console.print()

    disks = [
        {"mount": "/", "used_pct": 0.45, "avail_gb": 55.2},
        {"mount": "/home", "used_pct": 0.72, "avail_gb": 128.5},
        {"mount": "/var", "used_pct": 0.91, "avail_gb": 8.2},
    ]
    console.print(disk_status_table(disks))
    console.print()

    services = [
        {"name": "nginx", "active": True, "failed": False},
        {"name": "postgresql", "active": True, "failed": False},
        {"name": "redis", "active": False, "failed": True},
        {"name": "docker", "active": True, "failed": False},
    ]
    console.print(service_status_table(services))
    console.print()


def main() -> None:
    """Run all demos."""
    clear()

    demos = [
        ("Banner", demo_banner),
        ("Messages", demo_messages),
        ("Badges", demo_badges),
        ("Lists", demo_lists),
        ("Key-Value", demo_key_value),
        ("Progress", demo_progress),
        ("Time", demo_time),
        ("Code", demo_code),
        ("Misc", demo_misc),
        ("Spinners", demo_spinners),
        ("Fixit Panel", demo_fixit_panel),
        ("Plan Panel", demo_plan_panel),
        ("Incident Panel", demo_incident_panel),
        ("Search Panel", demo_search_panel),
        ("Config Diff", demo_config_diff),
        ("Incident Table", demo_incident_table),
        ("Event Table", demo_event_table),
        ("Status Tables", demo_status_tables),
    ]

    console.print("[bold cyan]ELLE UI Component Demo[/bold cyan]")
    console.print("[muted]Showcasing all UI components[/muted]\n")

    for name, demo_fn in demos:
        try:
            demo_fn()
        except Exception as e:
            console.print(f"[error]Error in {name}:[/error] {e}")
            console.print()

    console.print("[bold green]Demo complete![/bold green]")


if __name__ == "__main__":
    main()
