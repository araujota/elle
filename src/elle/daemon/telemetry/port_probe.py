"""Port Listener Probe - detects processes listening on network ports.

Periodically scans /proc/net/tcp* and /proc/net/udp* to detect:
- New listeners appearing on sensitive ports
- Unexpected processes binding to ports
- Changes to the listening port baseline
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, NamedTuple

from elle.daemon.telemetry.models import TelemetryEvent

logger = logging.getLogger(__name__)

# Sensitive ports to monitor
SENSITIVE_PORTS = {
    22: "ssh",
    80: "http",
    443: "https",
    3306: "mysql",
    5432: "postgres",
    6379: "redis",
    27017: "mongodb",
    8080: "http-alt",
    8443: "https-alt",
    25: "smtp",
    143: "imap",
    993: "imaps",
    110: "pop3",
    995: "pop3s",
}

# Trusted processes for sensitive ports
TRUSTED_PROCESSES = {
    22: {"sshd", "dropbear"},
    80: {"nginx", "apache2", "httpd", "caddy", "traefik"},
    443: {"nginx", "apache2", "httpd", "caddy", "traefik"},
    3306: {"mysqld", "mariadbd"},
    5432: {"postgres", "postmaster"},
    6379: {"redis-server", "redis"},
    27017: {"mongod"},
}


class Listener(NamedTuple):
    """Information about a listening socket."""
    port: int
    proto: str
    address: str
    pid: int | None
    comm: str
    is_wildcard: bool


class PortListenerProbe:
    """Probes for new network port listeners.

    Scans /proc/net/tcp* and /proc/net/udp* periodically to detect:
    - New listeners appearing since last scan
    - Untrusted processes on sensitive ports
    - Changes to listening ports

    Usage:
        probe = PortListenerProbe(event_queue)
        await probe.start()  # Runs in background
        await probe.stop()
    """

    def __init__(
        self,
        event_queue: asyncio.Queue[TelemetryEvent],
        scan_interval_sec: int = 60,
        alert_on_sensitive: bool = True,
        alert_on_new: bool = True,
    ) -> None:
        """Initialize the port listener probe.

        Args:
            event_queue: Queue to push events to.
            scan_interval_sec: Seconds between scans.
            alert_on_sensitive: Alert when untrusted process on sensitive port.
            alert_on_new: Alert on any new listener.
        """
        self._queue = event_queue
        self._scan_interval = scan_interval_sec
        self._alert_on_sensitive = alert_on_sensitive
        self._alert_on_new = alert_on_new
        self._running = False

        # Baseline listeners (port, proto, address) -> Listener
        self._baseline: dict[tuple[int, str, str], Listener] = {}

        # Stats
        self._stats = {
            "scans_completed": 0,
            "new_listeners_detected": 0,
            "sensitive_alerts": 0,
            "errors": 0,
        }

    async def start(self) -> None:
        """Start the probe background task.

        Blocks until stop() is called.
        """
        if self._running:
            logger.warning("PortListenerProbe already running")
            return

        self._running = True
        logger.info(f"Starting PortListenerProbe (interval: {self._scan_interval}s)")

        # Initial scan to establish baseline
        await self._scan_and_update_baseline()

        try:
            while self._running:
                await asyncio.sleep(self._scan_interval)

                if not self._running:
                    break

                try:
                    await self._scan_and_check()
                except Exception as e:
                    logger.exception(f"Port scan error: {e}")
                    self._stats["errors"] += 1

        except asyncio.CancelledError:
            logger.info("PortListenerProbe cancelled")
        finally:
            self._running = False

    async def stop(self) -> None:
        """Stop the probe."""
        self._running = False
        logger.info("PortListenerProbe stopped")

    async def run_now(self) -> list[TelemetryEvent]:
        """Run a scan immediately and return any events.

        Returns:
            List of generated events.
        """
        events: list[TelemetryEvent] = []

        # Temporarily capture events
        original_queue = self._queue
        capture_queue: asyncio.Queue[TelemetryEvent] = asyncio.Queue()
        self._queue = capture_queue

        try:
            await self._scan_and_check()

            # Collect events
            while not capture_queue.empty():
                events.append(capture_queue.get_nowait())

        finally:
            self._queue = original_queue

        return events

    async def _scan_and_update_baseline(self) -> None:
        """Perform initial scan and establish baseline."""
        listeners = await self._get_all_listeners()

        self._baseline = {
            (l.port, l.proto, l.address): l
            for l in listeners
        }

        logger.info(f"Established baseline with {len(self._baseline)} listeners")

    async def _scan_and_check(self) -> None:
        """Scan for listeners and check for changes."""
        self._stats["scans_completed"] += 1

        listeners = await self._get_all_listeners()
        current = {
            (l.port, l.proto, l.address): l
            for l in listeners
        }

        # Check for new listeners
        for key, listener in current.items():
            if key not in self._baseline:
                self._stats["new_listeners_detected"] += 1

                # Check if sensitive port with untrusted process
                is_sensitive_alert = False
                if listener.port in SENSITIVE_PORTS:
                    trusted = TRUSTED_PROCESSES.get(listener.port, set())
                    if listener.comm not in trusted:
                        is_sensitive_alert = True
                        self._stats["sensitive_alerts"] += 1

                # Generate event
                if is_sensitive_alert and self._alert_on_sensitive:
                    await self._emit_sensitive_port_alert(listener)
                elif self._alert_on_new:
                    await self._emit_new_listener_event(listener)

        # Check for removed listeners (optional - could log for debugging)
        for key in self._baseline:
            if key not in current:
                logger.debug(f"Listener removed: port {key[0]}/{key[1]}")

        # Update baseline
        self._baseline = current

    async def _get_all_listeners(self) -> list[Listener]:
        """Get all current network listeners.

        Returns:
            List of Listener objects.
        """
        listeners: list[Listener] = []

        # Scan TCP
        for path in ["/proc/net/tcp", "/proc/net/tcp6"]:
            listeners.extend(await self._scan_proc_net(path, "tcp"))

        # Scan UDP
        for path in ["/proc/net/udp", "/proc/net/udp6"]:
            listeners.extend(await self._scan_proc_net(path, "udp"))

        return listeners

    async def _scan_proc_net(self, path: str, proto: str) -> list[Listener]:
        """Scan a /proc/net file for listeners.

        Args:
            path: Path to /proc/net/tcp or similar.
            proto: Protocol (tcp/udp).

        Returns:
            List of Listener objects.
        """
        listeners: list[Listener] = []
        p = Path(path)

        if not p.exists():
            return listeners

        try:
            content = p.read_text()
            lines = content.strip().split("\n")[1:]  # Skip header

            for line in lines:
                parts = line.split()
                if len(parts) < 10:
                    continue

                # Parse local address
                local = parts[1]
                addr_hex, port_hex = local.split(":")
                port = int(port_hex, 16)

                if port == 0:
                    continue

                # Parse state (for TCP, 0A = LISTEN)
                state = parts[3]
                if proto == "tcp" and state != "0A":
                    continue

                # Parse address
                address = self._hex_to_ip(addr_hex)

                # Skip loopback
                if address in ("127.0.0.1", "::1"):
                    continue

                is_wildcard = address in ("0.0.0.0", "::")

                # Get inode and find process
                inode = parts[9]
                pid, comm = await self._get_process_for_inode(inode)

                listeners.append(Listener(
                    port=port,
                    proto=proto,
                    address=address,
                    pid=pid,
                    comm=comm,
                    is_wildcard=is_wildcard,
                ))

        except Exception as e:
            logger.debug(f"Error scanning {path}: {e}")

        return listeners

    def _hex_to_ip(self, hex_str: str) -> str:
        """Convert hex address to IP string."""
        if len(hex_str) == 8:
            # IPv4
            octets = [str(int(hex_str[i:i+2], 16)) for i in range(0, 8, 2)]
            return ".".join(reversed(octets))
        elif len(hex_str) == 32:
            # IPv6
            if hex_str == "00000000000000000000000000000000":
                return "::"
            if hex_str == "00000000000000000000000001000000":
                return "::1"
            # Return simplified form for display
            return "[::]"
        return hex_str

    async def _get_process_for_inode(self, inode: str) -> tuple[int | None, str]:
        """Get PID and process name for a socket inode.

        Args:
            inode: Socket inode number.

        Returns:
            Tuple of (pid, process_name).
        """
        if inode == "0":
            return None, "kernel"

        socket_link = f"socket:[{inode}]"
        proc_path = Path("/proc")

        try:
            for pid_dir in proc_path.iterdir():
                if not pid_dir.name.isdigit():
                    continue

                fd_dir = pid_dir / "fd"
                if not fd_dir.exists():
                    continue

                try:
                    for fd in fd_dir.iterdir():
                        try:
                            target = os.readlink(str(fd))
                            if target == socket_link:
                                pid = int(pid_dir.name)

                                # Get process name
                                comm_file = pid_dir / "comm"
                                if comm_file.exists():
                                    comm = comm_file.read_text().strip()
                                else:
                                    comm = "unknown"

                                return pid, comm

                        except (OSError, PermissionError):
                            continue

                except (OSError, PermissionError):
                    continue

        except Exception:
            pass

        return None, "unknown"

    async def _emit_new_listener_event(self, listener: Listener) -> None:
        """Emit event for a new listener.

        Args:
            listener: The new listener.
        """
        message = f"New listener on port {listener.port}/{listener.proto}: {listener.comm} (PID {listener.pid})"

        raw: dict[str, Any] = {
            "_SOURCE": "probe",
            "_PROBE": "port_listener",
            "port": listener.port,
            "proto": listener.proto,
            "address": listener.address,
            "pid": listener.pid,
            "comm": listener.comm,
            "is_wildcard": listener.is_wildcard,
            "is_sensitive": listener.port in SENSITIVE_PORTS,
            "MESSAGE": message,
            "PRIORITY": "6",  # Info
        }

        event = TelemetryEvent(
            ts=datetime.now(UTC),
            source="probe",
            severity="info",
            category="net",
            message=message,
            entity=f"port:{listener.port}/{listener.proto}",
            fingerprint=TelemetryEvent.compute_fingerprint(
                "net", f"port:{listener.port}", f"new_listener:{listener.comm}"
            ),
            raw=raw,
        )

        await self._publish_event(event)

    async def _emit_sensitive_port_alert(self, listener: Listener) -> None:
        """Emit alert for untrusted process on sensitive port.

        Args:
            listener: The suspicious listener.
        """
        port_name = SENSITIVE_PORTS.get(listener.port, str(listener.port))
        message = f"Unauthorized process on {port_name} port {listener.port}: {listener.comm} (PID {listener.pid})"

        raw: dict[str, Any] = {
            "_SOURCE": "probe",
            "_PROBE": "port_listener",
            "port": listener.port,
            "proto": listener.proto,
            "address": listener.address,
            "pid": listener.pid,
            "comm": listener.comm,
            "is_wildcard": listener.is_wildcard,
            "is_sensitive": True,
            "expected_processes": list(TRUSTED_PROCESSES.get(listener.port, set())),
            "MESSAGE": message,
            "PRIORITY": "3",  # Error
        }

        event = TelemetryEvent(
            ts=datetime.now(UTC),
            source="probe",
            severity="error",
            category="net",
            message=message,
            entity=f"port:{listener.port}/{listener.proto}",
            fingerprint=TelemetryEvent.compute_fingerprint(
                "net", f"port:{listener.port}", f"unauthorized:{listener.comm}"
            ),
            raw=raw,
        )

        await self._publish_event(event)

    async def _publish_event(self, event: TelemetryEvent) -> None:
        """Publish an event to the queue.

        Args:
            event: TelemetryEvent to publish.
        """
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Port probe events queue full, dropping event")
            self._stats["errors"] += 1

    @property
    def is_running(self) -> bool:
        """Check if probe is running."""
        return self._running

    @property
    def stats(self) -> dict[str, int]:
        """Get probe statistics."""
        return self._stats.copy()

    def get_baseline(self) -> list[Listener]:
        """Get current baseline listeners.

        Returns:
            List of baseline Listener objects.
        """
        return list(self._baseline.values())

    def get_sensitive_listeners(self) -> list[Listener]:
        """Get listeners on sensitive ports.

        Returns:
            List of Listener objects on sensitive ports.
        """
        return [
            l for l in self._baseline.values()
            if l.port in SENSITIVE_PORTS
        ]
