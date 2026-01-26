"""Gateway process management for ELLE Mobile Gateway.

This module handles starting and stopping the mobile gateway as a
separate process. The gateway runs independently from the main
ELLE daemon for isolation.
"""

import contextlib
import json
import logging
import os
import signal
import ssl
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from elle.mobile.config import MobileGatewayConfig, get_mobile_config
from elle.mobile.crypto import MobileCrypto
from elle.mobile.models import GatewayStatus
from elle.mobile.store import MobileStore

logger = logging.getLogger(__name__)


# PID file location
PID_FILE = Path("/var/run/elle/mobile_gateway.pid")
STATE_FILE = Path("/var/lib/elle/mobile_gateway_state.json")


class ServerError(Exception):
    """Error during server operation."""

    pass


class GatewayServer:
    """Manages the mobile gateway server process."""

    def __init__(self, config: MobileGatewayConfig | None = None):
        """Initialize server manager.

        Args:
            config: Mobile gateway configuration.
        """
        self.config = config or get_mobile_config()
        self.crypto = MobileCrypto(self.config)
        self.store = MobileStore(self.config)

    def start(self) -> GatewayStatus:
        """Start the gateway server.

        Returns:
            Gateway status after starting.

        Raises:
            ServerError: If server cannot be started.
        """
        # Check if already running
        status = self.get_status()
        if status.running:
            raise ServerError(f"Gateway already running (PID {status.pid})")

        # Ensure certificates exist
        cert_paths = self.crypto.ensure_certificates()

        # Ensure PID directory exists
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Build uvicorn command
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "elle.mobile.gateway:create_mobile_gateway",
            "--factory",
            "--host",
            self.config.bind_host,
            "--port",
            str(self.config.bind_port),
            "--ssl-keyfile",
            str(cert_paths.server_key),
            "--ssl-certfile",
            str(cert_paths.server_cert),
            "--ssl-ca-certs",
            str(cert_paths.ca_cert),
            "--ssl-cert-reqs",
            str(ssl.CERT_OPTIONAL),  # Allow pairing without cert
        ]

        logger.info("Starting gateway: %s", " ".join(cmd))

        # Start subprocess
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            # Write PID file
            PID_FILE.write_text(str(process.pid))

            # Write state file
            state = {
                "pid": process.pid,
                "started_at": datetime.utcnow().isoformat(),
                "bind_host": self.config.bind_host,
                "bind_port": self.config.bind_port,
            }
            STATE_FILE.write_text(json.dumps(state))

            # Wait briefly and check if process is still running
            time.sleep(0.5)
            if process.poll() is not None:
                raise ServerError(f"Gateway process exited immediately (code {process.returncode})")

            logger.info(
                "Gateway started on %s:%d (PID %d)",
                self.config.bind_host,
                self.config.bind_port,
                process.pid,
            )

            return self.get_status()

        except Exception as e:
            # Clean up PID file if we fail
            if PID_FILE.exists():
                PID_FILE.unlink()
            if STATE_FILE.exists():
                STATE_FILE.unlink()
            raise ServerError(f"Failed to start gateway: {e}") from e

    def stop(self) -> bool:
        """Stop the gateway server.

        Returns:
            True if server was stopped, False if not running.

        Raises:
            ServerError: If server cannot be stopped.
        """
        status = self.get_status()
        if not status.running:
            return False

        pid = status.pid
        try:
            # Send SIGTERM
            os.kill(pid, signal.SIGTERM)

            # Wait for process to exit
            for _ in range(50):  # 5 seconds max
                time.sleep(0.1)
                try:
                    os.kill(pid, 0)  # Check if process exists
                except OSError:
                    break
            else:
                # Process didn't exit, send SIGKILL
                logger.warning("Gateway didn't stop gracefully, sending SIGKILL")
                os.kill(pid, signal.SIGKILL)

            logger.info("Gateway stopped (was PID %d)", pid)

        except ProcessLookupError:
            logger.info("Gateway process already gone")

        except Exception as e:
            raise ServerError(f"Failed to stop gateway: {e}") from e

        finally:
            # Clean up PID and state files
            if PID_FILE.exists():
                PID_FILE.unlink()
            if STATE_FILE.exists():
                STATE_FILE.unlink()

        return True

    def restart(self) -> GatewayStatus:
        """Restart the gateway server.

        Returns:
            Gateway status after restarting.
        """
        self.stop()
        time.sleep(0.5)
        return self.start()

    def get_status(self) -> GatewayStatus:
        """Get current gateway status.

        Returns:
            GatewayStatus with current state.
        """
        # Check PID file
        if not PID_FILE.exists():
            return GatewayStatus(running=False)

        try:
            pid = int(PID_FILE.read_text().strip())
        except (ValueError, OSError):
            return GatewayStatus(running=False)

        # Check if process is running
        try:
            os.kill(pid, 0)
        except OSError:
            # Process not running, clean up stale files
            if PID_FILE.exists():
                PID_FILE.unlink()
            if STATE_FILE.exists():
                STATE_FILE.unlink()
            return GatewayStatus(running=False)

        # Load state
        state = {}
        if STATE_FILE.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                state = json.loads(STATE_FILE.read_text())

        # Calculate uptime
        uptime_seconds = None
        started_at = None
        if "started_at" in state:
            started_at = datetime.fromisoformat(state["started_at"])
            uptime_seconds = (datetime.utcnow() - started_at).total_seconds()

        # Get device counts
        paired_count = self.store.count_devices()
        active_elevations = len(self.store.list_active_elevations())

        return GatewayStatus(
            running=True,
            pid=pid,
            bind_host=state.get("bind_host"),
            bind_port=state.get("bind_port"),
            paired_devices=paired_count,
            active_elevations=active_elevations,
            uptime_seconds=uptime_seconds,
            started_at=started_at,
        )


def run_gateway_foreground(config: MobileGatewayConfig | None = None) -> None:
    """Run the gateway in the foreground (for development/testing).

    Args:
        config: Mobile gateway configuration.
    """
    import uvicorn

    config = config or get_mobile_config()
    crypto = MobileCrypto(config)
    cert_paths = crypto.ensure_certificates()

    uvicorn.run(
        "elle.mobile.gateway:create_mobile_gateway",
        factory=True,
        host=config.bind_host,
        port=config.bind_port,
        ssl_keyfile=str(cert_paths.server_key),
        ssl_certfile=str(cert_paths.server_cert),
        ssl_ca_certs=str(cert_paths.ca_cert),
        ssl_cert_reqs=ssl.CERT_OPTIONAL,
        log_level="info",
    )


def format_status(status: GatewayStatus) -> str:
    """Format gateway status for display.

    Args:
        status: Gateway status to format.

    Returns:
        Formatted status string.
    """
    if not status.running:
        return "Mobile Gateway: Not running"

    lines = [
        "Mobile Gateway: Running",
        f"  PID: {status.pid}",
        f"  Address: {status.bind_host}:{status.bind_port}",
        f"  Paired devices: {status.paired_devices}",
        f"  Active elevations: {status.active_elevations}",
    ]

    if status.uptime_seconds is not None:
        hours = int(status.uptime_seconds // 3600)
        minutes = int((status.uptime_seconds % 3600) // 60)
        lines.append(f"  Uptime: {hours}h {minutes}m")

    if status.started_at:
        lines.append(f"  Started: {status.started_at.isoformat()}")

    return "\n".join(lines)
