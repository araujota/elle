"""Kernel watcher for system telemetry.

Monitors journalctl -k for:
- Disk I/O errors
- Thermal throttling
- NIC link state changes
- OOM killer invocations
"""

import asyncio
import json
import logging
from typing import Any

from elle.daemon.telemetry.queue import TelemetryQueue

logger = logging.getLogger(__name__)


class KernelWatcher:
    """Async watcher for kernel ring buffer events.

    Streams kernel messages via journalctl -k and queues them
    for normalization and processing.
    """

    def __init__(
        self,
        queue: TelemetryQueue[dict[str, Any]],
        shutdown: asyncio.Event,
    ):
        """Initialize the kernel watcher.

        Args:
            queue: Queue to put raw events.
            shutdown: Event to signal shutdown.
        """
        self._queue = queue
        self._shutdown = shutdown
        self._process: asyncio.subprocess.Process | None = None
        self._events_read = 0
        self._errors = 0
        self._running = False

    @property
    def events_read(self) -> int:
        """Get total events read."""
        return self._events_read

    @property
    def errors(self) -> int:
        """Get total parse errors."""
        return self._errors

    @property
    def running(self) -> bool:
        """Check if watcher is running."""
        return self._running

    async def start(self) -> None:
        """Start watching kernel messages.

        Runs until shutdown event is set.
        """
        logger.info("Starting kernel watcher")
        self._running = True

        while not self._shutdown.is_set():
            try:
                await self._watch_loop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Kernel watcher error: {e}")
                self._errors += 1
                # Backoff before retry
                try:
                    await asyncio.wait_for(
                        self._shutdown.wait(),
                        timeout=5.0,
                    )
                    break
                except TimeoutError:
                    continue

        self._running = False
        logger.info(f"Kernel watcher stopped (read {self._events_read} events)")

    async def stop(self) -> None:
        """Stop the watcher and cleanup."""
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=2.0)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
            except ProcessLookupError:
                pass
            self._process = None

    async def _watch_loop(self) -> None:
        """Main watch loop - streams kernel messages."""
        # Start journalctl process for kernel messages
        cmd = [
            "journalctl",
            "-k",  # Kernel messages only
            "-f",  # Follow
            "-o",
            "json",  # JSON output
            "--since",
            "now",  # Only new entries
        ]

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        logger.debug("journalctl -k process started")

        try:
            if self._process.stdout is None:
                return

            # Read lines until shutdown
            while not self._shutdown.is_set():
                try:
                    # Read with timeout to check shutdown
                    line = await asyncio.wait_for(
                        self._process.stdout.readline(),
                        timeout=1.0,
                    )

                    if not line:
                        # Process ended
                        break

                    # Parse JSON
                    try:
                        raw = json.loads(line)
                        raw["_SOURCE"] = "kernel"
                        raw["_TRANSPORT"] = "kernel"
                        await self._queue.put(raw)
                        self._events_read += 1
                    except json.JSONDecodeError:
                        self._errors += 1
                        continue

                except TimeoutError:
                    # Check shutdown and continue
                    continue

        finally:
            await self.stop()


class KernelBatchReader:
    """Read kernel messages in batches for historical analysis.

    Unlike KernelWatcher, this reads a fixed range of messages
    rather than streaming.
    """

    def __init__(self, since: str | None = None, until: str | None = None):
        """Initialize the batch reader.

        Args:
            since: Start time (journalctl format).
            until: End time (journalctl format).
        """
        self._since = since
        self._until = until

    async def read(
        self,
        limit: int = 1000,
        priority: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read kernel messages.

        Args:
            limit: Maximum messages to read.
            priority: Filter by priority (e.g., "0..4" for errors).

        Returns:
            List of raw kernel messages.
        """
        cmd = [
            "journalctl",
            "-k",  # Kernel messages only
            "-o",
            "json",  # JSON output
            "-n",
            str(limit),
        ]

        if self._since:
            cmd.extend(["--since", self._since])
        if self._until:
            cmd.extend(["--until", self._until])
        if priority:
            cmd.extend(["-p", priority])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        stdout, _ = await proc.communicate()
        entries = []

        for line in stdout.decode().split("\n"):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                raw["_SOURCE"] = "kernel"
                raw["_TRANSPORT"] = "kernel"
                entries.append(raw)
            except json.JSONDecodeError:
                continue

        return entries


async def watch_kernel(
    queue: TelemetryQueue[dict[str, Any]],
    shutdown: asyncio.Event,
) -> None:
    """Watch kernel messages for hardware events.

    Convenience function wrapping KernelWatcher.

    Args:
        queue: Queue to put raw events.
        shutdown: Event to signal shutdown.
    """
    watcher = KernelWatcher(queue, shutdown)
    try:
        await watcher.start()
    finally:
        await watcher.stop()


async def read_recent_kernel(
    since: str = "1 hour ago",
    limit: int = 1000,
    priority: str | None = None,
) -> list[dict[str, Any]]:
    """Read recent kernel messages.

    Convenience function for historical analysis.

    Args:
        since: Start time.
        limit: Maximum messages.
        priority: Priority filter.

    Returns:
        List of raw kernel messages.
    """
    reader = KernelBatchReader(since=since)
    return await reader.read(limit=limit, priority=priority)
