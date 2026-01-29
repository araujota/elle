"""Daemon lifecycle and visibility commands.

Provides commands for managing and inspecting the elled daemon:
- `daemon status`   - Show full daemon health
- `daemon explain`  - Describe what daemon monitors
- `daemon start`    - Start elled via systemctl
- `daemon stop`     - Stop elled
- `daemon restart`  - Restart elled
- `daemon logs`     - Stream daemon logs
- `daemon actions`  - Show recent autonomous actions

This module surfaces the daemon's state to users, ensuring visibility
into what ELLE is observing and doing autonomously.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Models
# =============================================================================


class DaemonHealth(BaseModel):
    """Health status of the daemon."""

    model_config = ConfigDict(frozen=True)

    running: bool = Field(description="Whether daemon process is running")
    pid: int | None = Field(default=None, description="Process ID if running")
    uptime_seconds: int = Field(default=0, description="Seconds since daemon started")
    api_reachable: bool = Field(default=False, description="Whether API responds")
    last_error: str | None = Field(default=None, description="Last error if any")


class DaemonStatus(BaseModel):
    """Full daemon status."""

    model_config = ConfigDict(frozen=True)

    health: DaemonHealth
    version: str = Field(default="unknown")

    # Event processing
    events_processed_total: int = Field(default=0)
    events_per_minute: float = Field(default=0.0)

    # Incidents
    incidents_open: int = Field(default=0)
    incidents_total: int = Field(default=0)
    last_incident_at: datetime | None = Field(default=None)

    # Watchers
    active_watchers: tuple[str, ...] = Field(default_factory=tuple)
    watcher_status: dict[str, str] = Field(default_factory=dict)

    # Recent actions
    last_autonomous_action: str | None = Field(default=None)
    last_autonomous_action_at: datetime | None = Field(default=None)

    # Resources
    memory_mb: float = Field(default=0.0)
    cpu_percent: float = Field(default=0.0)


class AutonomousAction(BaseModel):
    """Record of an autonomous action taken by the daemon."""

    model_config = ConfigDict(frozen=True)

    action_id: str
    capability_name: str
    incident_id: str | None = None
    triggered_by: str  # 'daemon' | 'user'
    input_summary: str
    success: bool
    executed_at: datetime
    duration_ms: int = 0


# =============================================================================
# Display Classes
# =============================================================================


class DaemonStatusDisplay:
    """Rich display of daemon status."""

    def render(self, status: DaemonStatus) -> str:
        """Render daemon status as formatted text.

        Args:
            status: The daemon status to render.

        Returns:
            Formatted status string.
        """
        lines = []

        # Health header
        if status.health.running:
            uptime = self._format_uptime(status.health.uptime_seconds)
            lines.append(f"DAEMON: RUNNING (PID {status.health.pid}, up {uptime})")
        else:
            lines.append("DAEMON: NOT RUNNING")
            if status.health.last_error:
                lines.append(f"  Last error: {status.health.last_error}")
            lines.append("")
            lines.append("Start with: elle daemon start")
            return "\n".join(lines)

        lines.append(f"  Version: {status.version}")
        lines.append(f"  API: {'reachable' if status.health.api_reachable else 'unreachable'}")
        lines.append(f"  Memory: {status.memory_mb:.1f} MB")
        lines.append(f"  CPU: {status.cpu_percent:.1f}%")
        lines.append("")

        # Event processing
        lines.append("EVENT PROCESSING:")
        lines.append(f"  Total events: {status.events_processed_total:,}")
        lines.append(f"  Rate: {status.events_per_minute:.1f}/min")
        lines.append("")

        # Incidents
        lines.append("INCIDENTS:")
        lines.append(f"  Open: {status.incidents_open}")
        lines.append(f"  Total: {status.incidents_total}")
        if status.last_incident_at:
            ago = self._format_time_ago(status.last_incident_at)
            lines.append(f"  Last: {ago}")
        lines.append("")

        # Watchers
        lines.append("ACTIVE WATCHERS:")
        if status.active_watchers:
            for watcher in status.active_watchers:
                watcher_state = status.watcher_status.get(watcher, "unknown")
                lines.append(f"  {watcher}: {watcher_state}")
        else:
            lines.append("  (none)")
        lines.append("")

        # Last autonomous action
        if status.last_autonomous_action:
            lines.append("LAST AUTONOMOUS ACTION:")
            lines.append(f"  {status.last_autonomous_action}")
            if status.last_autonomous_action_at:
                ago = self._format_time_ago(status.last_autonomous_action_at)
                lines.append(f"  When: {ago}")

        return "\n".join(lines)

    def _format_uptime(self, seconds: int) -> str:
        """Format uptime as human-readable string."""
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m {seconds % 60}s"
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"

    def _format_time_ago(self, dt: datetime) -> str:
        """Format datetime as 'X ago' string."""
        from datetime import timezone

        now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
        delta = now - dt
        seconds = int(delta.total_seconds())

        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"


class DaemonExplainer:
    """Explains what the daemon monitors and why."""

    def explain(self) -> str:
        """Generate explanation of daemon observation.

        Returns:
            Explanation text.
        """
        lines = [
            "ELLE DAEMON (elled)",
            "",
            "The daemon is the observation layer of ELLE. It passively monitors",
            "the system and converts signals into actionable insights.",
            "",
            "WHAT IT MONITORS:",
            "",
            "  Journal Watcher",
            "    Streams systemd journal in real-time",
            "    Detects: service failures, auth events, kernel messages",
            "",
            "  Kernel Watcher",
            "    Monitors /dev/kmsg for kernel events",
            "    Detects: OOM kills, hardware errors, driver issues",
            "",
            "  Inotify Watcher",
            "    Watches critical config files for changes",
            "    Detects: unauthorized config modifications",
            "",
            "  Docker Watcher",
            "    Monitors container lifecycle events",
            "    Detects: crashes, restarts, OOM, healthcheck failures",
            "",
            "  Port Probe",
            "    Periodically checks listening ports",
            "    Detects: unexpected listeners, service availability",
            "",
            "  Package Probe",
            "    Monitors dpkg/apt for package changes",
            "    Detects: new installs, upgrades (triggers auto-learning)",
            "",
            "WHAT IT DOES WITH SIGNALS:",
            "",
            "  1. Events are normalized and fingerprinted",
            "  2. Related events are correlated into incidents",
            "  3. Incidents trigger automatic diagnosis (if enabled)",
            "  4. Actions are recorded for learning",
            "",
            "AUTONOMOUS ACTIONS:",
            "",
            "  By default, the daemon only observes. It will not take action",
            "  unless explicitly enabled via /autonomy command.",
            "",
            "  When ELLE_AUTO_REMEDIATE_CRITICAL=true:",
            "    - Critical incidents trigger automatic diagnosis",
            "    - Capabilities may execute with user confirmation",
            "",
            "Use 'daemon status' to see current state.",
            "Use 'daemon actions' to see recent autonomous actions.",
        ]
        return "\n".join(lines)


# =============================================================================
# Command Handlers
# =============================================================================


async def handle_daemon_command(args: str) -> str:
    """Handle /daemon or 'daemon' commands.

    Usage:
        daemon                  - Show status (default)
        daemon status           - Show full daemon health
        daemon explain          - Describe what daemon monitors
        daemon start            - Start elled via systemctl
        daemon stop             - Stop elled
        daemon restart          - Restart elled
        daemon logs             - Show recent daemon logs
        daemon actions [N]      - Show last N autonomous actions

    Args:
        args: Command arguments.

    Returns:
        Response string.
    """
    args = args.strip()

    if not args or args == "status":
        return await _handle_status()

    parts = args.split(None, 1)
    subcommand = parts[0].lower()
    subargs = parts[1] if len(parts) > 1 else ""

    handlers = {
        "status": lambda _: _handle_status(),
        "explain": lambda _: _handle_explain(),
        "start": lambda _: _handle_start(),
        "stop": lambda _: _handle_stop(),
        "restart": lambda _: _handle_restart(),
        "logs": lambda _: _handle_logs(),
        "actions": _handle_actions,
        "help": lambda _: _handle_help(),
    }

    handler = handlers.get(subcommand)
    if handler:
        result = handler(subargs)
        if asyncio.iscoroutine(result):
            return await result
        return result

    return f"Unknown daemon subcommand: {subcommand}\n\n{_handle_help('')}"


def _handle_help(_: str) -> str:
    """Get help text for daemon commands."""
    return """
daemon - Daemon lifecycle and visibility commands

Usage:
  daemon status           Show full daemon health
  daemon explain          Describe what daemon monitors
  daemon start            Start elled via systemctl
  daemon stop             Stop elled
  daemon restart          Restart elled
  daemon logs             Show recent daemon logs
  daemon actions [N]      Show last N autonomous actions (default: 10)
  daemon help             Show this help

The daemon (elled) owns all passive system observation:
- Journal streaming (service failures, auth events)
- Kernel monitoring (OOM, hardware errors)
- File watching (config changes)
- Docker events (container lifecycle)
- Port/package probing

Use 'daemon explain' for detailed information about what is monitored.
""".strip()


async def _handle_status() -> str:
    """Handle 'daemon status' command."""
    try:
        status = await _get_daemon_status()
        display = DaemonStatusDisplay()
        return display.render(status)
    except Exception as e:
        logger.error(f"Error getting daemon status: {e}")
        return f"Error getting daemon status: {e}"


def _handle_explain(_: str) -> str:
    """Handle 'daemon explain' command."""
    explainer = DaemonExplainer()
    return explainer.explain()


def _handle_start(_: str) -> str:
    """Handle 'daemon start' command."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "start", "elled"],
            capture_output=True,
            text=True,
            timeout=10.0,
        )

        if result.returncode == 0:
            return "Daemon started successfully.\n\nUse 'daemon status' to verify."
        else:
            # Try system-level if user-level fails
            result = subprocess.run(
                ["sudo", "systemctl", "start", "elled"],
                capture_output=True,
                text=True,
                timeout=10.0,
            )
            if result.returncode == 0:
                return "Daemon started successfully (system-level).\n\nUse 'daemon status' to verify."

            return f"Failed to start daemon:\n{result.stderr or result.stdout}"

    except subprocess.TimeoutExpired:
        return "Timeout starting daemon. Check systemctl manually."
    except FileNotFoundError:
        return "systemctl not found. Cannot start daemon on this system."
    except Exception as e:
        return f"Error starting daemon: {e}"


def _handle_stop(_: str) -> str:
    """Handle 'daemon stop' command."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "stop", "elled"],
            capture_output=True,
            text=True,
            timeout=10.0,
        )

        if result.returncode == 0:
            return "Daemon stopped."
        else:
            # Try system-level
            result = subprocess.run(
                ["sudo", "systemctl", "stop", "elled"],
                capture_output=True,
                text=True,
                timeout=10.0,
            )
            if result.returncode == 0:
                return "Daemon stopped (system-level)."

            return f"Failed to stop daemon:\n{result.stderr or result.stdout}"

    except subprocess.TimeoutExpired:
        return "Timeout stopping daemon."
    except FileNotFoundError:
        return "systemctl not found."
    except Exception as e:
        return f"Error stopping daemon: {e}"


def _handle_restart(_: str) -> str:
    """Handle 'daemon restart' command."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "restart", "elled"],
            capture_output=True,
            text=True,
            timeout=15.0,
        )

        if result.returncode == 0:
            return "Daemon restarted successfully.\n\nUse 'daemon status' to verify."
        else:
            # Try system-level
            result = subprocess.run(
                ["sudo", "systemctl", "restart", "elled"],
                capture_output=True,
                text=True,
                timeout=15.0,
            )
            if result.returncode == 0:
                return "Daemon restarted successfully (system-level)."

            return f"Failed to restart daemon:\n{result.stderr or result.stdout}"

    except subprocess.TimeoutExpired:
        return "Timeout restarting daemon."
    except FileNotFoundError:
        return "systemctl not found."
    except Exception as e:
        return f"Error restarting daemon: {e}"


def _handle_logs(_: str) -> str:
    """Handle 'daemon logs' command."""
    try:
        # Get recent logs from journalctl
        result = subprocess.run(
            ["journalctl", "--user", "-u", "elled", "-n", "50", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10.0,
        )

        if result.returncode == 0 and result.stdout.strip():
            return f"Recent daemon logs:\n\n{result.stdout}"

        # Try system-level
        result = subprocess.run(
            ["journalctl", "-u", "elled", "-n", "50", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10.0,
        )

        if result.returncode == 0 and result.stdout.strip():
            return f"Recent daemon logs:\n\n{result.stdout}"

        return "No daemon logs found. Is the daemon installed as a systemd service?"

    except subprocess.TimeoutExpired:
        return "Timeout reading logs."
    except FileNotFoundError:
        return "journalctl not found."
    except Exception as e:
        return f"Error reading logs: {e}"


async def _handle_actions(args: str) -> str:
    """Handle 'daemon actions' command."""
    limit = 10
    if args:
        try:
            limit = int(args)
        except ValueError:
            return f"Invalid number: {args}"

    try:
        actions = await _get_autonomous_actions(limit)

        if not actions:
            return "No autonomous actions recorded.\n\nThe daemon only observes by default."

        lines = [f"LAST {len(actions)} AUTONOMOUS ACTIONS:", ""]

        for action in actions:
            status = "[OK]" if action.success else "[FAIL]"
            lines.append(f"{status} {action.capability_name}")
            lines.append(f"    {action.input_summary}")
            lines.append(f"    {action.executed_at.strftime('%Y-%m-%d %H:%M:%S')}")
            if action.incident_id:
                lines.append(f"    Incident: {action.incident_id[:8]}...")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error getting autonomous actions: {e}")
        return f"Error getting autonomous actions: {e}"


# =============================================================================
# Data Fetching
# =============================================================================


async def _get_daemon_status() -> DaemonStatus:
    """Fetch daemon status from the daemon API or system.

    Returns:
        DaemonStatus with current daemon state.
    """
    try:
        from elle.cli.daemon_client import get_daemon_client

        client = get_daemon_client()

        # Check if daemon is reachable
        is_available = await client.is_daemon_available()

        if is_available:
            # Fetch full status from daemon API
            status_data = await client._request("GET", "/v1/status")
            if status_data:
                return DaemonStatus(
                    health=DaemonHealth(
                        running=True,
                        pid=status_data.get("pid"),
                        uptime_seconds=status_data.get("uptime_seconds", 0),
                        api_reachable=True,
                    ),
                    version=status_data.get("version", "unknown"),
                    events_processed_total=status_data.get("events_processed_total", 0),
                    events_per_minute=status_data.get("events_per_minute", 0.0),
                    incidents_open=status_data.get("incidents_open", 0),
                    incidents_total=status_data.get("incidents_total", 0),
                    active_watchers=tuple(status_data.get("active_watchers", [])),
                    watcher_status=status_data.get("watcher_status", {}),
                    last_autonomous_action=status_data.get("last_autonomous_action"),
                    memory_mb=status_data.get("memory_mb", 0.0),
                    cpu_percent=status_data.get("cpu_percent", 0.0),
                )

    except Exception as e:
        logger.debug(f"Failed to get status from daemon API: {e}")

    # Fallback: check if process is running via systemctl
    health = await _check_daemon_health()
    return DaemonStatus(health=health)


async def _check_daemon_health() -> DaemonHealth:
    """Check daemon health via systemctl.

    Returns:
        DaemonHealth with process state.
    """
    try:
        # Check user-level service first
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "elled"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )

        if result.stdout.strip() == "active":
            # Get PID
            pid_result = subprocess.run(
                ["systemctl", "--user", "show", "elled", "--property=MainPID", "--value"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            pid = int(pid_result.stdout.strip()) if pid_result.stdout.strip() else None

            return DaemonHealth(
                running=True,
                pid=pid,
                api_reachable=False,  # Will be updated if API check succeeds
            )

        # Try system-level
        result = subprocess.run(
            ["systemctl", "is-active", "elled"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )

        if result.stdout.strip() == "active":
            pid_result = subprocess.run(
                ["systemctl", "show", "elled", "--property=MainPID", "--value"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            pid = int(pid_result.stdout.strip()) if pid_result.stdout.strip() else None

            return DaemonHealth(running=True, pid=pid, api_reachable=False)

        return DaemonHealth(running=False, last_error=result.stderr.strip() or None)

    except Exception as e:
        return DaemonHealth(running=False, last_error=str(e))


async def _get_autonomous_actions(limit: int = 10) -> list[AutonomousAction]:
    """Fetch recent autonomous actions from the daemon.

    Args:
        limit: Maximum number of actions to fetch.

    Returns:
        List of autonomous actions.
    """
    try:
        from elle.cli.daemon_client import get_daemon_client

        client = get_daemon_client()
        response = await client._request("GET", f"/v1/autonomous-actions?limit={limit}")

        if response and "actions" in response:
            return [
                AutonomousAction(
                    action_id=a["action_id"],
                    capability_name=a["capability_name"],
                    incident_id=a.get("incident_id"),
                    triggered_by=a["triggered_by"],
                    input_summary=a.get("input_summary", ""),
                    success=a["success"],
                    executed_at=datetime.fromisoformat(a["executed_at"]),
                    duration_ms=a.get("duration_ms", 0),
                )
                for a in response["actions"]
            ]

    except Exception as e:
        logger.debug(f"Failed to get autonomous actions: {e}")

    return []


# =============================================================================
# Synchronous wrapper
# =============================================================================


def handle_daemon_command_sync(args: str) -> str:
    """Synchronous version of handle_daemon_command."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(handle_daemon_command(args))
