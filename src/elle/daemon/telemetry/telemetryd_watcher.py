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

import asyncio
import json
import logging
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elle.daemon.telemetry.models import TelemetryEvent
from elle.daemon.telemetry.queue import TelemetryQueue

logger = logging.getLogger(__name__)

# Default socket path for telemetryd
TELEMETRYD_SOCKET = Path("/run/elle/telemetry.sock")

# Reconnection settings
RECONNECT_DELAY = 2.0  # seconds
MAX_RECONNECT_DELAY = 60.0  # seconds


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

        # Connection
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

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
        self._started_at = datetime.now(UTC)

        # Check if telemetryd is available
        if not await self.check_available():
            self._running = False
            raise RuntimeError(
                f"elled-telemetryd is not available at {self._socket_path}. "
                "Start it with: sudo systemctl start elled-telemetryd"
            )

        logger.info("Connected to elled-telemetryd")

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
        await self._disconnect()
        logger.info(f"Telemetryd watcher stopped (events: {self._total_events})")

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

            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
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
                    logger.warning(f"Invalid JSON from telemetryd: {e}")
                    self._total_errors += 1

            except TimeoutError:
                continue
            except Exception as e:
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
                ts = datetime.fromtimestamp(ts_ns / 1e9, tz=UTC)
            else:
                ts = datetime.now(UTC)

            # Map source string to source type
            source_map = {
                "journal": "journal",
                "kernel": "kernel",
                "probe": "probe",
                "ebpf": "ebpf",
                "docker": "docker",
                "inotify": "inotify",
            }
            source = source_map.get(raw.get("source", ""), "journal")

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
        except Exception as e:
            logger.warning(f"Failed to queue event: {e}")
            self._total_errors += 1
