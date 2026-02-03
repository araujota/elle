"""REPL and CLI commands for ELLE Mobile Gateway.

Provides /mobile commands for managing the mobile gateway:
- /mobile up [--port PORT]   Start gateway, display QR
- /mobile down               Stop gateway
- /mobile status             Show gateway status
- /mobile devices            List paired devices
- /mobile revoke <device>    Revoke device access
- /mobile approve <device>   Grant temporary elevation
- /mobile audit [--hours N]  Show audit log
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from elle.common.session import Session

# Type alias for command handlers
MobileHandler = Callable[[list[str], "Session"], tuple[str, bool]]

logger = logging.getLogger(__name__)


# =============================================================================
# Colors (matching terminal renderer)
# =============================================================================


class Colors:
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
    BOLD_RED = "\033[1;31m"
    BOLD_GREEN = "\033[1;32m"
    BOLD_YELLOW = "\033[1;33m"


# =============================================================================
# Command Handlers
# =============================================================================


def handle_mobile_command(user_input: str, session: Session) -> tuple[str, bool]:
    """Handle /mobile commands.

    Args:
        user_input: The full user input string.
        session: Current session state.

    Returns:
        Tuple of (output_string, success).
    """
    # Parse the subcommand
    parts = user_input.strip().split()

    # /mobile alone - show help
    if len(parts) == 1:
        return _mobile_help(), True

    subcommand = parts[1].lower()

    # Route to handler
    handlers: dict[str, MobileHandler] = {
        "help": lambda p, s: (_mobile_help(), True),
        "up": lambda p, s: _mobile_up(p[2:], s),
        "start": lambda p, s: _mobile_up(p[2:], s),
        "down": lambda p, s: _mobile_down(s),
        "stop": lambda p, s: _mobile_down(s),
        "status": lambda p, s: _mobile_status(s),
        "devices": lambda p, s: _mobile_devices(s),
        "list": lambda p, s: _mobile_devices(s),
        "revoke": lambda p, s: _mobile_revoke(p[2] if len(p) > 2 else None, s),
        "approve": lambda p, s: _mobile_approve(p[2:], s),
        "elevate": lambda p, s: _mobile_approve(p[2:], s),
        "audit": lambda p, s: _mobile_audit(p[2:], s),
        "logs": lambda p, s: _mobile_audit(p[2:], s),
    }

    handler = handlers.get(subcommand)
    if handler:
        return handler(parts, session)

    return (
        f"{Colors.RED}Unknown subcommand: {subcommand}{Colors.RESET}\nUse '/mobile help' for available commands.",
        False,
    )


def is_mobile_command(text: str) -> bool:
    """Check if text is a mobile gateway command."""
    patterns = [
        r"^/mobile(\s|$)",
        r"^elle\s+mobile(\s|$)",
    ]
    lower = text.lower().strip()
    return any(re.match(p, lower) for p in patterns)


# =============================================================================
# Subcommand Handlers
# =============================================================================


def _mobile_help() -> str:
    """Generate help text for /mobile commands."""
    return f"""{Colors.BOLD}Mobile Gateway - Secure Remote Access{Colors.RESET}

{Colors.BOLD}Server Commands:{Colors.RESET}
  /mobile up [--port PORT]     Start gateway and display QR code
  /mobile down                 Stop gateway
  /mobile status               Show gateway status

{Colors.BOLD}Device Management:{Colors.RESET}
  /mobile devices              List all paired devices
  /mobile revoke <device_id>   Revoke a device's access
  /mobile approve <device_id> [--ttl 10m]  Grant temporary elevation

{Colors.BOLD}Audit:{Colors.RESET}
  /mobile audit [--hours 24]   Show recent audit log

{Colors.BOLD}Examples:{Colors.RESET}
  /mobile up                   Start with default settings
  /mobile up --port 8379       Start on custom port
  /mobile devices              List paired devices
  /mobile approve abc123 --ttl 30m  Elevate device for 30 minutes
  /mobile revoke abc123        Revoke device access

{Colors.DIM}The mobile gateway provides secure access from mobile devices
via QR-based pairing and mTLS authentication.{Colors.RESET}"""


def _mobile_up(args: list[str], session: Session) -> tuple[str, bool]:
    """Start the mobile gateway and display QR code."""
    try:
        from elle.mobile.config import MobileGatewayConfig, get_mobile_config
        from elle.mobile.models import MobileRole
        from elle.mobile.pairing import QRCODE_AVAILABLE, PairingManager
        from elle.mobile.server import GatewayServer, ServerError

        # Parse arguments
        port = None
        role = MobileRole.MOBILE_READONLY

        i = 0
        while i < len(args):
            if args[i] in ("--port", "-p") and i + 1 < len(args):
                try:
                    port = int(args[i + 1])
                except ValueError:
                    return f"{Colors.RED}Invalid port number: {args[i + 1]}{Colors.RESET}", False
                i += 2
            elif args[i] == "--operator":
                role = MobileRole.MOBILE_OPERATOR
                i += 1
            else:
                i += 1

        # Get config with optional port override
        config = get_mobile_config()
        if port:
            config = MobileGatewayConfig(
                enabled=True,
                bind_host=config.bind_host,
                bind_port=port,
                overlay_host=config.overlay_host,
                pairing_token_ttl_seconds=config.pairing_token_ttl_seconds,
                max_paired_devices=config.max_paired_devices,
                default_elevation_ttl_seconds=config.default_elevation_ttl_seconds,
                max_elevation_ttl_seconds=config.max_elevation_ttl_seconds,
                default_role=config.default_role,
                internal_api_host=config.internal_api_host,
                internal_api_port=config.internal_api_port,
                cert_dir=config.cert_dir,
                db_path=config.db_path,
                audit_db_path=config.audit_db_path,
            )

        server = GatewayServer(config)

        # Start server
        print(f"\n{Colors.DIM}Starting mobile gateway...{Colors.RESET}")
        status = server.start()

        if status is None:
            return f"{Colors.RED}Failed to start gateway: process exited immediately{Colors.RESET}", False

        lines = [
            "",
            f"{Colors.BOLD_GREEN}Mobile Gateway Started{Colors.RESET}",
            "",
            f"  Address: {Colors.CYAN}https://{status.bind_host}:{status.bind_port}{Colors.RESET}",
            f"  PID: {status.pid}",
            "",
        ]

        # Generate pairing QR
        if QRCODE_AVAILABLE:
            pairing = PairingManager(config)
            payload, token = pairing.initiate_pairing(role)

            lines.append(f"{Colors.BOLD}Scan to pair:{Colors.RESET}")
            lines.append("")

            try:
                qr = pairing.generate_qr_terminal(payload)
                lines.append(qr)
            except Exception as e:
                logger.warning("Failed to generate QR: %s", e)
                lines.append(f"  {Colors.DIM}(QR generation failed){Colors.RESET}")

            lines.append("")
            lines.append(f"Token expires in {config.pairing_token_ttl_seconds} seconds")
            lines.append(f"Server fingerprint: {payload.server_fingerprint[:16]}...")
        else:
            lines.append(f"{Colors.YELLOW}Note: Install qrcode for QR pairing:{Colors.RESET}")
            lines.append("  pip install 'elle[mobile]'")

        lines.append("")
        lines.append(f"{Colors.DIM}Use '/mobile status' to check gateway status{Colors.RESET}")
        lines.append(f"{Colors.DIM}Use '/mobile down' to stop the gateway{Colors.RESET}")

        try:
            from elle.cli.agentic.incident_recorder import record_arm_action

            record_arm_action(
                arm_name="mobile_gateway",
                action="gateway_start",
                target=f"{status.bind_host}:{status.bind_port}",
                success=True,
                details={"pid": status.pid, "role": role},
            )
        except Exception:
            pass

        return "\n".join(lines), True

    except ServerError as e:
        return f"{Colors.RED}Failed to start gateway: {e}{Colors.RESET}", False
    except Exception as e:
        logger.exception("Failed to start mobile gateway")
        return f"{Colors.RED}Error starting gateway: {e}{Colors.RESET}", False


def _mobile_down(session: Session) -> tuple[str, bool]:
    """Stop the mobile gateway."""
    try:
        from elle.mobile.server import GatewayServer

        server = GatewayServer()
        stopped = server.stop()

        if stopped:
            try:
                from elle.cli.agentic.incident_recorder import record_arm_action

                record_arm_action(
                    arm_name="mobile_gateway",
                    action="gateway_stop",
                    target="mobile_gateway",
                    success=True,
                )
            except Exception:
                pass
            return f"\n{Colors.GREEN}Mobile gateway stopped{Colors.RESET}\n", True
        else:
            return f"\n{Colors.YELLOW}Gateway was not running{Colors.RESET}\n", True

    except Exception as e:
        logger.exception("Failed to stop mobile gateway")
        return f"{Colors.RED}Error stopping gateway: {e}{Colors.RESET}", False


def _mobile_status(session: Session) -> tuple[str, bool]:
    """Show gateway status."""
    try:
        from elle.mobile.server import GatewayServer

        server = GatewayServer()
        status = server.get_status()

        lines = [
            "",
            f"{Colors.BOLD}Mobile Gateway Status{Colors.RESET}",
            "",
        ]

        if status.running:
            lines.append(f"  Status: {Colors.GREEN}Running{Colors.RESET}")
            lines.append(f"  PID: {status.pid}")
            lines.append(f"  Address: https://{status.bind_host}:{status.bind_port}")
            lines.append(f"  Paired devices: {status.paired_devices}")
            lines.append(f"  Active elevations: {status.active_elevations}")

            if status.uptime_seconds is not None:
                hours = int(status.uptime_seconds // 3600)
                minutes = int((status.uptime_seconds % 3600) // 60)
                lines.append(f"  Uptime: {hours}h {minutes}m")

            if status.started_at:
                lines.append(f"  Started: {status.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            lines.append(f"  Status: {Colors.DIM}Not running{Colors.RESET}")
            lines.append("")
            lines.append(f"  {Colors.DIM}Use '/mobile up' to start the gateway{Colors.RESET}")

        lines.append("")

        return "\n".join(lines), True

    except Exception as e:
        logger.exception("Failed to get gateway status")
        return f"{Colors.RED}Error getting status: {e}{Colors.RESET}", False


def _mobile_devices(session: Session) -> tuple[str, bool]:
    """List all paired devices."""
    try:
        from elle.mobile.elevation import ElevationManager
        from elle.mobile.models import DeviceStatus
        from elle.mobile.store import MobileStore

        store = MobileStore()
        elevation_mgr = ElevationManager(store=store)

        devices = store.list_devices()

        if not devices:
            return (
                f"\n  {Colors.DIM}No paired devices.{Colors.RESET}\n"
                f"\n  Use '/mobile up' to start the gateway and pair a device.\n",
                True,
            )

        lines = [
            "",
            f"{Colors.BOLD}Paired Devices{Colors.RESET}",
            "",
        ]

        for device in devices:
            # Status indicator
            if device.status == DeviceStatus.PAIRED:
                status = f"{Colors.GREEN}paired{Colors.RESET}"
            elif device.status == DeviceStatus.REVOKED:
                status = f"{Colors.RED}revoked{Colors.RESET}"
            else:
                status = f"{Colors.YELLOW}{device.status.value}{Colors.RESET}"

            # Role with elevation check
            elevation_status = elevation_mgr.get_elevation_status(device.device_id)
            if elevation_status.get("elevated"):
                eff_role = elevation_status["effective_role"]
                role_display = f"{Colors.CYAN}{eff_role}{Colors.RESET} (elevated)"
            else:
                role_display = device.role.value

            lines.append(f"  {Colors.BOLD}{device.name}{Colors.RESET}")
            lines.append(f"    ID: {device.device_id[:8]}...")
            lines.append(f"    Status: {status}")
            lines.append(f"    Role: {role_display}")

            if device.last_seen_at:
                lines.append(f"    Last seen: {device.last_seen_at.strftime('%Y-%m-%d %H:%M')}")

            if device.paired_at:
                lines.append(f"    Paired: {device.paired_at.strftime('%Y-%m-%d %H:%M')}")

            lines.append("")

        lines.append(f"{Colors.DIM}Use '/mobile revoke <id>' to revoke access{Colors.RESET}")
        lines.append(f"{Colors.DIM}Use '/mobile approve <id>' to grant elevation{Colors.RESET}")

        return "\n".join(lines), True

    except Exception as e:
        logger.exception("Failed to list devices")
        return f"{Colors.RED}Error listing devices: {e}{Colors.RESET}", False


def _mobile_revoke(device_id: str | None, session: Session) -> tuple[str, bool]:
    """Revoke a device's access."""
    if not device_id:
        return f"{Colors.RED}Usage: /mobile revoke <device_id>{Colors.RESET}", False

    try:
        from elle.mobile.elevation import ElevationManager
        from elle.mobile.models import DeviceStatus
        from elle.mobile.store import MobileStore

        store = MobileStore()
        elevation_mgr = ElevationManager(store=store)

        # Find device (support partial ID match)
        devices = store.list_devices()
        matches = [d for d in devices if d.device_id.startswith(device_id)]

        if not matches:
            return f"{Colors.RED}Device not found: {device_id}{Colors.RESET}", False

        if len(matches) > 1:
            lines = [f"{Colors.YELLOW}Multiple matches:{Colors.RESET}"]
            for d in matches:
                lines.append(f"  {d.device_id} ({d.name})")
            lines.append("")
            lines.append("Please provide a more specific ID.")
            return "\n".join(lines), False

        device = matches[0]

        if device.status == DeviceStatus.REVOKED:
            return f"{Colors.YELLOW}Device already revoked: {device.name}{Colors.RESET}", True

        # Confirm revocation
        try:
            response = input(f"{Colors.YELLOW}Revoke access for '{device.name}'? [y/N]: {Colors.RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return f"{Colors.DIM}Cancelled.{Colors.RESET}", False

        if response not in ("y", "yes"):
            return f"{Colors.DIM}Cancelled.{Colors.RESET}", False

        # Revoke
        store.update_device(device.device_id, status=DeviceStatus.REVOKED)
        elevation_mgr.revoke_elevation(device.device_id)

        # Record to incident vault for audit trail
        try:
            from elle.cli.agentic.incident_recorder import record_arm_action

            record_arm_action(
                arm_name="mobile_gateway",
                action="revoke_device",
                target=device.device_id,
                success=True,
                details={
                    "device_name": device.name,
                    "device_id": device.device_id,
                    "previous_status": device.status.value,
                },
            )
        except Exception as e:
            logger.debug(f"Failed to record revocation to incident vault: {e}")

        return (
            f"\n{Colors.GREEN}Revoked access for: {device.name}{Colors.RESET}\nDevice ID: {device.device_id}\n",
            True,
        )

    except Exception as e:
        logger.exception("Failed to revoke device")
        return f"{Colors.RED}Error revoking device: {e}{Colors.RESET}", False


def _mobile_approve(args: list[str], session: Session) -> tuple[str, bool]:
    """Grant temporary elevation to a device."""
    if not args:
        return (
            f"{Colors.RED}Usage: /mobile approve <device_id> [--ttl 10m]{Colors.RESET}",
            False,
        )

    # Parse arguments
    device_id = args[0]
    ttl_str = "10m"  # Default 10 minutes

    i = 1
    while i < len(args):
        if args[i] in ("--ttl", "-t") and i + 1 < len(args):
            ttl_str = args[i + 1]
            i += 2
        else:
            i += 1

    try:
        from elle.mobile.elevation import ElevationError, ElevationManager, format_ttl, parse_ttl
        from elle.mobile.models import DeviceStatus, MobileRole
        from elle.mobile.store import MobileStore

        store = MobileStore()
        elevation_mgr = ElevationManager(store=store)

        # Find device
        devices = store.list_devices()
        matches = [d for d in devices if d.device_id.startswith(device_id)]

        if not matches:
            return f"{Colors.RED}Device not found: {device_id}{Colors.RESET}", False

        if len(matches) > 1:
            lines = [f"{Colors.YELLOW}Multiple matches:{Colors.RESET}"]
            for d in matches:
                lines.append(f"  {d.device_id} ({d.name})")
            lines.append("")
            lines.append("Please provide a more specific ID.")
            return "\n".join(lines), False

        device = matches[0]

        if device.status != DeviceStatus.PAIRED:
            msg = f"Device not paired: {device.name} ({device.status.value})"
            return f"{Colors.RED}{msg}{Colors.RESET}", False

        # Parse TTL
        try:
            ttl_seconds = parse_ttl(ttl_str)
        except ValueError:
            return f"{Colors.RED}Invalid TTL format: {ttl_str}{Colors.RESET}", False

        # Grant elevation
        elevation = elevation_mgr.grant_elevation(
            device.device_id,
            MobileRole.MOBILE_OPERATOR,
            ttl_seconds,
            granted_by="cli",
        )

        # Record to incident vault for audit trail
        try:
            from elle.cli.agentic.incident_recorder import record_arm_action

            record_arm_action(
                arm_name="mobile_gateway",
                action="elevate_device",
                target=device.device_id,
                success=True,
                details={
                    "device_name": device.name,
                    "device_id": device.device_id,
                    "elevated_role": elevation.elevated_role.value,
                    "ttl_seconds": ttl_seconds,
                    "expires_at": elevation.expires_at.isoformat(),
                },
            )
        except Exception as e:
            logger.debug(f"Failed to record elevation to incident vault: {e}")

        return (
            f"\n{Colors.GREEN}Elevated device: {device.name}{Colors.RESET}\n"
            f"  Role: {elevation.elevated_role.value}\n"
            f"  Expires: {elevation.expires_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"  Duration: {format_ttl(ttl_seconds)}\n",
            True,
        )

    except ElevationError as e:
        return f"{Colors.RED}Elevation failed: {e}{Colors.RESET}", False
    except Exception as e:
        logger.exception("Failed to elevate device")
        return f"{Colors.RED}Error elevating device: {e}{Colors.RESET}", False


def _mobile_audit(args: list[str], session: Session) -> tuple[str, bool]:
    """Show audit log."""
    # Parse arguments
    hours = 24
    limit = 50

    import contextlib

    i = 0
    while i < len(args):
        if args[i] in ("--hours", "-h") and i + 1 < len(args):
            with contextlib.suppress(ValueError):
                hours = int(args[i + 1])
            i += 2
        elif args[i] in ("--limit", "-l") and i + 1 < len(args):
            with contextlib.suppress(ValueError):
                limit = int(args[i + 1])
            i += 2
        else:
            i += 1

    try:
        from elle.mobile.audit import MobileAuditStore

        audit = MobileAuditStore()
        entries = audit.get_recent(hours=hours, limit=limit)

        if not entries:
            return (
                f"\n  {Colors.DIM}No audit entries in the last {hours} hours.{Colors.RESET}\n",
                True,
            )

        lines = [
            "",
            f"{Colors.BOLD}Mobile Gateway Audit Log{Colors.RESET}",
            f"{Colors.DIM}Last {hours} hours, up to {limit} entries{Colors.RESET}",
            "",
        ]

        for entry in entries:
            ts = entry.timestamp.strftime("%m-%d %H:%M:%S")

            # Action color
            if entry.action.value in ("pair", "gateway_start"):
                action = f"{Colors.CYAN}{entry.action.value}{Colors.RESET}"
            elif entry.action.value in ("revoke", "gateway_stop"):
                action = f"{Colors.YELLOW}{entry.action.value}{Colors.RESET}"
            elif entry.action.value == "elevate":
                action = f"{Colors.MAGENTA}{entry.action.value}{Colors.RESET}"
            else:
                action = entry.action.value

            # Status
            if entry.success:
                status = f"{Colors.GREEN}OK{Colors.RESET}"
            else:
                status = f"{Colors.RED}FAIL{Colors.RESET}"

            # Device info
            device_info = ""
            if entry.device_name:
                device_info = f" {entry.device_name}"
            elif entry.device_id:
                device_info = f" {entry.device_id[:8]}..."

            lines.append(f"  {ts}  {action:20}{device_info:20} [{status}]")

            if entry.endpoint:
                lines.append(f"    {Colors.DIM}Endpoint: {entry.endpoint}{Colors.RESET}")

            if entry.error:
                lines.append(f"    {Colors.RED}Error: {entry.error[:50]}{Colors.RESET}")

        lines.append("")

        return "\n".join(lines), True

    except Exception as e:
        logger.exception("Failed to get audit log")
        return f"{Colors.RED}Error getting audit log: {e}{Colors.RESET}", False
