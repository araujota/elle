"""TelemetrydWatcher - Connects to elled-telemetryd for pre-normalized events.

This module provides the interface to receive telemetry from the C daemon.
Events arrive PRE-NORMALIZED (already have category, entity, fingerprint set)
and can be directly stored without Python processing.

The C daemon (elled-telemetryd) handles:
- Journal/kernel log streaming (sd-journal API)
- Docker container events with crashloop detection
- Inotify file monitoring with diff generation
- eBPF tracing (OOM, exec, block I/O, TCP, capabilities)
- Periodic probes (PSI, memory, disk, network, thermal, package, port)
- Event normalization with 58 category patterns
- Deduplication with 60-second window
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from elle.daemon.telemetry.models import TelemetryEvent, TelemetrySource
from elle.daemon.telemetry.queue import TelemetryQueue

logger = logging.getLogger(__name__)

# Default socket path for telemetryd
TELEMETRYD_SOCKET = Path("/run/elle/telemetry.sock")

# Reconnection settings
RECONNECT_DELAY = 2.0  # seconds
MAX_RECONNECT_DELAY = 60.0  # seconds

# Health monitoring settings
HEALTH_CHECK_INTERVAL = 30.0  # seconds between health checks
STALE_THRESHOLD = 60.0  # seconds without events before considered unhealthy
MAX_RESTART_ATTEMPTS = 5  # maximum restart attempts before giving up
RESTART_COOLDOWN = 300.0  # seconds to wait before resetting restart counter

# Log rate limiting
LOG_RATE_LIMIT_SECONDS = 60.0  # minimum seconds between repeated log messages


def is_telemetryd_available(socket_path: Path | None = None) -> bool:
    """Check if elled-telemetryd is available and connectable.

    Args:
        socket_path: Path to the Unix socket.

    Returns:
        True if socket exists and is connectable.
    """
    path = socket_path or TELEMETRYD_SOCKET

    if not path.exists():
        return False

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect(str(path))
        sock.close()
        return True
    except OSError:
        return False


class TelemetrydWatcher:
    """Watches elled-telemetryd for pre-normalized events.

    This watcher connects to the unified C telemetry daemon and receives
    events that are already normalized (category, entity, fingerprint set).

    Events can be directly queued for storage without Python normalization.
    """

    def __init__(
        self,
        event_queue: TelemetryQueue[TelemetryEvent],
        shutdown: asyncio.Event,
        socket_path: Path | None = None,
    ):
        """Initialize the telemetryd watcher.

        Args:
            event_queue: Queue for normalized events (TelemetryEvent).
            shutdown: Event to signal shutdown.
            socket_path: Path to Unix socket (default: /run/elle/telemetry.sock).
        """
        self._event_queue = event_queue
        self._shutdown = shutdown
        self._socket_path = socket_path or TELEMETRYD_SOCKET

        # State
        self._running = False
        self._connected = False
        self._started_at: datetime | None = None
        self._total_events = 0
        self._total_errors = 0

        # Health monitoring
        self._last_event_time: datetime | None = None
        self._restart_count = 0
        self._last_restart_time: datetime | None = None
        self._health_task: asyncio.Task[None] | None = None

        # Connection
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

        # Log rate limiting: tracks last log time and suppressed count per key
        self._log_last_time: dict[str, float] = {}
        self._log_suppressed: dict[str, int] = {}

    @property
    def running(self) -> bool:
        """Check if watcher is running."""
        return self._running

    @property
    def connected(self) -> bool:
        """Check if connected to telemetryd socket."""
        return self._connected

    @property
    def total_events(self) -> int:
        """Get total events processed."""
        return self._total_events

    @property
    def last_event_age_seconds(self) -> float | None:
        """Get seconds since last event received."""
        if self._last_event_time is None:
            return None
        return (datetime.now(timezone.utc) - self._last_event_time).total_seconds()

    @property
    def is_healthy(self) -> bool:
        """Check if the watcher is healthy.

        Healthy means:
        - Running
        - Connected
        - Received an event within STALE_THRESHOLD seconds
        """
        if not self._running or not self._connected:
            return False
        if self._last_event_time is None:
            return True  # Just started, no events yet is OK
        age = self.last_event_age_seconds
        if age is None:
            return True
        return age < STALE_THRESHOLD

    @property
    def restart_count(self) -> int:
        """Get number of restart attempts."""
        return self._restart_count

    def _should_log(self, key: str) -> bool:
        """Check if a log message should be emitted (rate-limited).

        Returns True at most once per LOG_RATE_LIMIT_SECONDS per key.
        When returning True after suppression, logs the suppressed count.
        """
        now = time.monotonic()
        last = self._log_last_time.get(key, 0.0)
        if now - last < LOG_RATE_LIMIT_SECONDS:
            self._log_suppressed[key] = self._log_suppressed.get(key, 0) + 1
            return False
        suppressed = self._log_suppressed.pop(key, 0)
        if suppressed > 0:
            logger.info(f"[{key}] suppressed {suppressed} repeated messages")
        self._log_last_time[key] = now
        return True

    async def check_available(self) -> bool:
        """Check if telemetryd socket is available.

        Returns:
            True if socket exists and is connectable.
        """
        if not self._socket_path.exists():
            return False

        if not self._socket_path.is_socket():
            return False

        # Try to connect briefly
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._socket_path)),
                timeout=1.0,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    async def start(self) -> None:
        """Start watching telemetryd socket.

        Raises:
            RuntimeError: If telemetryd is not available.
        """
        logger.info(f"Connecting to telemetryd at {self._socket_path}")
        self._running = True
        self._started_at = datetime.now(timezone.utc)

        # Check if telemetryd is available
        if not await self.check_available():
            self._running = False
            raise RuntimeError(
                f"elled-telemetryd is not available at {self._socket_path}. "
                "Start it with: sudo systemctl start elled-telemetryd"
            )

        logger.info("Connected to elled-telemetryd")

        # Start health monitoring task
        self._health_task = asyncio.create_task(self._health_check_loop())

        try:
            await self._watch_loop()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Telemetryd watcher error: {e}")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the watcher and cleanup."""
        logger.info("Stopping telemetryd watcher")
        self._running = False

        # Cancel health check task
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None

        await self._disconnect()
        logger.info(f"Telemetryd watcher stopped (events: {self._total_events}, restarts: {self._restart_count})")

    async def _health_check_loop(self) -> None:
        """Monitor telemetryd health and attempt recovery if needed.

        Runs continuously while the watcher is running, checking health
        at regular intervals and attempting recovery if needed.
        """
        logger.debug("Health check loop started")

        while self._running:
            try:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL)

                if not self._running:
                    break

                if not await self._check_health():
                    logger.warning("Telemetryd appears unhealthy, attempting recovery")
                    await self._attempt_recovery()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Health check error: {e}")

        logger.debug("Health check loop stopped")

    async def _check_health(self) -> bool:
        """Check if telemetryd is functioning properly.

        Returns:
            True if healthy, False if recovery is needed.
        """
        # Check basic connection state
        if self._reader is None or self._reader.at_eof():
            logger.debug("Health check: reader is None or at EOF")
            return False

        if not self._connected:
            logger.debug("Health check: not connected")
            return False

        # Check for stale events
        if self._last_event_time:
            stale_seconds = (datetime.now(timezone.utc) - self._last_event_time).total_seconds()
            if stale_seconds > STALE_THRESHOLD:
                logger.debug(f"Health check: no events for {stale_seconds:.1f}s (threshold: {STALE_THRESHOLD}s)")
                # Try sending a ping to confirm connection is alive
                if not await self._send_ping():
                    return False

        return True

    async def _send_ping(self) -> bool:
        """Send a ping to verify connection is alive.

        Returns:
            True if ping succeeded, False otherwise.
        """
        # telemetryd doesn't have a ping command, so we just check
        # if the socket is still writable
        if self._writer is None:
            return False

        try:
            # Try to write an empty line (telemetryd ignores it)
            self._writer.write(b"\n")
            await asyncio.wait_for(self._writer.drain(), timeout=5.0)
            return True
        except Exception as e:
            logger.debug(f"Ping failed: {e}")
            return False

    async def _attempt_recovery(self) -> None:
        """Attempt to recover telemetryd connection.

        Recovery steps:
        1. Check if we've exceeded max restart attempts
        2. Disconnect from the current (broken) connection
        3. Try to restart the telemetryd service
        4. Reconnect to the socket
        5. Create an incident if recovery fails
        """
        # Reset restart counter after cooldown period
        if self._last_restart_time:
            elapsed = (datetime.now(timezone.utc) - self._last_restart_time).total_seconds()
            if elapsed > RESTART_COOLDOWN:
                logger.info(f"Restart cooldown passed ({elapsed:.0f}s), resetting counter")
                self._restart_count = 0

        # Check max restarts
        if self._restart_count >= MAX_RESTART_ATTEMPTS:
            logger.error(f"Max telemetryd restart attempts ({MAX_RESTART_ATTEMPTS}) reached")
            await self._create_health_incident(
                "Telemetryd unrecoverable",
                f"Failed to recover telemetryd after {MAX_RESTART_ATTEMPTS} attempts",
            )
            return

        self._restart_count += 1
        self._last_restart_time = datetime.now(timezone.utc)
        logger.info(f"Recovery attempt {self._restart_count}/{MAX_RESTART_ATTEMPTS}")

        # Disconnect current connection
        await self._disconnect()

        # Try to restart telemetryd service
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl",
                "restart",
                "elled-telemetryd",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=30.0)

            if proc.returncode == 0:
                logger.info("Successfully restarted elled-telemetryd service")
            else:
                logger.warning(f"systemctl restart failed with code {proc.returncode}")

        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("systemctl restart timed out")
        except Exception as e:
            logger.warning(f"Failed to restart telemetryd service: {e}")

        # Wait for service to start
        await asyncio.sleep(2.0)

        # Attempt reconnection
        if await self._connect():
            logger.info("Successfully recovered telemetryd connection")
            # Reset last event time to avoid immediate re-trigger
            self._last_event_time = datetime.now(timezone.utc)
        else:
            logger.warning("Failed to reconnect after service restart")

    async def _create_health_incident(
        self,
        title: str,
        summary: str,
    ) -> None:
        """Create an incident for a health failure.

        Args:
            title: Incident title.
            summary: Incident summary.
        """
        try:
            from elle.daemon.incidents.store import create_incident_draft

            create_incident_draft(
                title=title,
                domain="telemetry",
                severity="critical",
                trigger_source="health_monitor",
            )
            logger.info(f"Created health incident: {title}")

        except ImportError:
            logger.debug("Incident store not available")
        except Exception as e:
            logger.warning(f"Failed to create health incident: {e}")

    async def _connect(self) -> bool:
        """Connect to the telemetryd socket.

        Returns:
            True if connected, False otherwise.
        """
        if not self._socket_path.exists():
            return False

        try:
            self._reader, self._writer = await asyncio.open_unix_connection(str(self._socket_path))
            self._connected = True
            logger.info(f"Connected to telemetryd at {self._socket_path}")
            return True

        except Exception as e:
            logger.warning(f"Failed to connect to telemetryd: {e}")
            return False

    async def _disconnect(self) -> None:
        """Disconnect from the socket."""
        self._connected = False

        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None

        self._reader = None

    async def _watch_loop(self) -> None:
        """Main loop connecting to socket and reading events."""
        reconnect_delay = RECONNECT_DELAY

        while not self._shutdown.is_set():
            try:
                # Attempt connection
                if not self._connected:
                    if not await self._connect():
                        # Connection failed, wait before retry
                        if self._should_log("reconnect"):
                            logger.warning(
                                f"Cannot connect to telemetryd, retrying in {reconnect_delay}s. "
                                "Restart with: sudo systemctl restart elled-telemetryd"
                            )
                        await asyncio.wait_for(
                            self._shutdown.wait(),
                            timeout=reconnect_delay,
                        )
                        # Exponential backoff
                        reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY)
                        continue

                    # Connection successful, reset backoff
                    reconnect_delay = RECONNECT_DELAY

                # Read events
                await self._read_events()

            except (TimeoutError, asyncio.TimeoutError):
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._should_log("watch_loop_error"):
                    logger.error(f"Watch loop error: {e}")
                await self._disconnect()
                await asyncio.sleep(reconnect_delay)

    async def _read_events(self) -> None:
        """Read and process pre-normalized events from socket."""
        if not self._reader:
            return

        while not self._shutdown.is_set() and self._connected:
            try:
                # Read line with timeout
                line = await asyncio.wait_for(
                    self._reader.readline(),
                    timeout=1.0,
                )

                if not line:
                    # EOF - socket closed
                    logger.warning("Telemetryd socket closed")
                    await self._disconnect()
                    return

                # Parse NDJSON and convert to TelemetryEvent
                try:
                    raw = json.loads(line.decode("utf-8"))
                    event = self._convert_to_event(raw)
                    if event:
                        self._queue_event(event)
                except json.JSONDecodeError as e:
                    if self._should_log("invalid_json"):
                        logger.warning(f"Invalid JSON from telemetryd: {e}")
                    self._total_errors += 1

            except (TimeoutError, asyncio.TimeoutError):
                continue
            except Exception as e:
                if self._should_log("read_error"):
                    logger.error(f"Read error: {e}")
                await self._disconnect()
                return

    def _convert_to_event(self, raw: dict[str, Any]) -> TelemetryEvent | None:
        """Convert raw telemetryd event to TelemetryEvent.

        Telemetryd events are PRE-NORMALIZED with these fields:
        - ts: timestamp in nanoseconds
        - source: "journal"|"kernel"|"probe"|"ebpf"|"docker"|"inotify"
        - severity: "debug"|"info"|"notice"|"warning"|"error"|"critical"
        - category: "oom"|"disk"|"net"|etc.
        - message: Human-readable message
        - entity: Optional entity string like "service:nginx"
        - fingerprint: SHA256[:16] hex string
        - raw: Optional source-specific data

        Args:
            raw: Raw event dict from telemetryd NDJSON.

        Returns:
            TelemetryEvent or None if conversion fails.
        """
        try:
            # Convert nanosecond timestamp to datetime
            ts_ns = raw.get("ts", 0)
            if ts_ns:
                ts = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
            else:
                ts = datetime.now(timezone.utc)

            # Map source string to source type
            raw_source = raw.get("source", "journal")
            valid_sources = {"journal", "kernel", "probe", "ebpf", "docker", "inotify"}
            source: TelemetrySource = cast(
                TelemetrySource,
                raw_source if raw_source in valid_sources else "journal",
            )

            return TelemetryEvent(
                ts=ts,
                source=source,
                severity=raw.get("severity", "info"),
                category=raw.get("category", "other"),
                message=raw.get("message", ""),
                entity=raw.get("entity"),
                fingerprint=raw.get("fingerprint", ""),
                raw=raw.get("raw", {}),
            )

        except Exception as e:
            if self._should_log("convert_event"):
                logger.warning(f"Failed to convert event: {e}")
            self._total_errors += 1
            return None

    def _queue_event(self, event: TelemetryEvent) -> None:
        """Queue an event for processing.

        Args:
            event: TelemetryEvent to queue.
        """
        try:
            self._event_queue.put_nowait(event)
            self._total_events += 1
            # Update last event time for health monitoring
            self._last_event_time = datetime.now(timezone.utc)
        except Exception as e:
            logger.warning(f"Failed to queue event: {e}")
            self._total_errors += 1
