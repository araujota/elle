"""Docker Events Watcher - streams Docker container events.

Monitors Docker container lifecycle events and detects crashlooping containers.

Events detected:
- start, stop, die, kill, oom, restart
- crashloop (synthetic): 3+ restarts within 5 minutes
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import subprocess
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from elle.daemon.telemetry.models import TelemetryEvent, TelemetrySeverity
from elle.daemon.telemetry.queue import TelemetryQueue

logger = logging.getLogger(__name__)

# Crashloop detection: 3+ restarts in 5 minutes
CRASHLOOP_THRESHOLD = 3
CRASHLOOP_WINDOW_SEC = 300


class DockerEventsWatcher:
    """Watches Docker container events via `docker events` stream.

    Provides:
    - Real-time container lifecycle events
    - Crashloop detection (3+ restarts in 5 minutes)
    - Container state tracking

    Usage:
        watcher = DockerEventsWatcher(event_queue)
        await watcher.start()  # Blocks until stopped
        await watcher.stop()
    """

    def __init__(
        self,
        event_queue: TelemetryQueue[TelemetryEvent],
        container_filter: str | None = None,
    ) -> None:
        """Initialize the Docker events watcher.

        Args:
            event_queue: Queue to push normalized events to.
            container_filter: Optional container name filter.
        """
        self._queue = event_queue
        self._container_filter = container_filter
        self._running = False
        self._process: asyncio.subprocess.Process | None = None

        # Track restart history for crashloop detection
        # container_name -> list of restart timestamps
        self._restart_history: dict[str, list[float]] = defaultdict(list)

        # Track container states
        self._container_states: dict[str, str] = {}

        # Stats
        self._stats = {
            "events_received": 0,
            "events_published": 0,
            "crashloops_detected": 0,
            "errors": 0,
        }

    async def start(self) -> None:
        """Start watching Docker events.

        Blocks until stop() is called or the process terminates.
        """
        if self._running:
            logger.warning("DockerEventsWatcher already running")
            return

        if not self._is_docker_available():
            logger.error("Docker is not available, cannot start watcher")
            return

        self._running = True
        logger.info("Starting DockerEventsWatcher")

        try:
            await self._run_event_loop()
        except asyncio.CancelledError:
            logger.info("DockerEventsWatcher cancelled")
        finally:
            self._running = False
            await self._cleanup()

    async def stop(self) -> None:
        """Stop the Docker events watcher."""
        self._running = False

        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
            except Exception as e:
                logger.warning(f"Error stopping docker events process: {e}")

        logger.info("DockerEventsWatcher stopped")

    async def _run_event_loop(self) -> None:
        """Main event processing loop."""
        while self._running:
            try:
                # Start docker events process
                cmd = [
                    "docker",
                    "events",
                    "--format",
                    "json",
                    "--filter",
                    "type=container",
                ]

                if self._container_filter:
                    cmd.extend(["--filter", f"container={self._container_filter}"])

                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                # Read events from stdout
                if self._process.stdout:
                    async for line in self._process.stdout:
                        if not self._running:
                            break

                        try:
                            line_str = line.decode().strip()
                            if line_str:
                                await self._process_event(line_str)
                        except Exception as e:
                            logger.exception(f"Error processing Docker event: {e}")
                            self._stats["errors"] += 1

                # Process exited
                if self._running:
                    logger.warning("docker events process exited, restarting in 5s")
                    await asyncio.sleep(5)

            except Exception as e:
                logger.exception(f"Docker events loop error: {e}")
                self._stats["errors"] += 1

                if self._running:
                    await asyncio.sleep(5)

    async def _process_event(self, line: str) -> None:
        """Process a single Docker event JSON line.

        Args:
            line: JSON-encoded Docker event.
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON from docker events: {line[:100]}")
            return

        self._stats["events_received"] += 1

        # Extract event info
        status = data.get("status", "")
        action = data.get("Action", status)
        actor = data.get("Actor", {})
        attributes = actor.get("Attributes", {})
        container_name = attributes.get("name", "unknown")
        container_id = actor.get("ID", "")[:12]

        # Get exit code if available
        exit_code = attributes.get("exitCode")
        if exit_code is not None:
            try:
                exit_code = int(exit_code)
            except ValueError:
                exit_code = None

        # Track container state
        if action in ("start", "stop", "die", "kill", "pause", "unpause"):
            self._container_states[container_name] = action

        # Build raw event data
        raw: dict[str, Any] = {
            "_SOURCE": "docker",
            "_DOCKER_EVENT": action,
            "_DOCKER_CONTAINER_NAME": container_name,
            "_DOCKER_CONTAINER_ID": container_id,
            "PRIORITY": self._get_priority(action),
        }

        if exit_code is not None:
            raw["_DOCKER_EXIT_CODE"] = exit_code

        # Build message
        message = self._build_message(action, container_name, exit_code)
        raw["MESSAGE"] = message

        # Determine severity
        severity = self._get_severity(action, exit_code)

        # Create telemetry event
        event = TelemetryEvent(
            ts=datetime.now(UTC),
            source="probe",  # Using probe since docker isn't in TelemetrySource
            severity=severity,
            category="docker",
            message=message,
            entity=f"container:{container_name}",
            fingerprint=TelemetryEvent.compute_fingerprint("docker", f"container:{container_name}", message),
            raw=raw,
        )

        # Publish to queue
        await self._publish_event(event)

        # Check for crashloop on restart/die events
        if action in ("restart", "die", "start"):
            await self._check_crashloop(container_name, action, raw)

    async def _check_crashloop(
        self,
        container_name: str,
        action: str,
        base_raw: dict[str, Any],
    ) -> None:
        """Check if a container is crashlooping.

        Args:
            container_name: Container name.
            action: Current action.
            base_raw: Base raw event data.
        """
        now = time.time()

        # Track restart timestamps
        if action in ("restart", "start"):
            # Check if this was a restart (died recently)
            history = self._restart_history[container_name]
            if action == "restart" or (action == "start" and self._container_states.get(container_name) == "die"):
                history.append(now)

        # Clean old entries
        cutoff = now - CRASHLOOP_WINDOW_SEC
        history = [ts for ts in self._restart_history[container_name] if ts > cutoff]
        self._restart_history[container_name] = history

        # Check for crashloop
        if len(history) >= CRASHLOOP_THRESHOLD:
            self._stats["crashloops_detected"] += 1

            # Create crashloop event
            raw = dict(base_raw)
            raw["_DOCKER_EVENT"] = "crashloop"
            raw["_DOCKER_RESTART_COUNT"] = len(history)
            raw["PRIORITY"] = "2"  # Critical
            raw["MESSAGE"] = (
                f"Container {container_name} is crashlooping ({len(history)} restarts in {CRASHLOOP_WINDOW_SEC}s)"
            )

            event = TelemetryEvent(
                ts=datetime.now(UTC),
                source="probe",
                severity="critical",
                category="docker",
                message=raw["MESSAGE"],
                entity=f"container:{container_name}",
                fingerprint=TelemetryEvent.compute_fingerprint("docker", f"container:{container_name}", "crashloop"),
                raw=raw,
            )

            await self._publish_event(event)

            # Reset history to avoid repeat alerts
            self._restart_history[container_name] = [now]

    async def _publish_event(self, event: TelemetryEvent) -> None:
        """Publish an event to the queue.

        Args:
            event: TelemetryEvent to publish.
        """
        try:
            self._queue.put_nowait(event)
            self._stats["events_published"] += 1
        except asyncio.QueueFull:
            logger.warning("Docker events queue full, dropping event")
            self._stats["errors"] += 1

    def _build_message(
        self,
        action: str,
        container_name: str,
        exit_code: int | None,
    ) -> str:
        """Build human-readable message for an event.

        Args:
            action: Docker event action.
            container_name: Container name.
            exit_code: Exit code if available.

        Returns:
            Human-readable message.
        """
        if action == "die":
            if exit_code is not None:
                return f"Container {container_name} died (exit={exit_code})"
            return f"Container {container_name} died"
        elif action == "oom":
            return f"Container {container_name} killed by OOM"
        elif action == "kill":
            return f"Container {container_name} was killed"
        elif action == "restart":
            return f"Container {container_name} restarted"
        elif action == "start":
            return f"Container {container_name} started"
        elif action == "stop":
            return f"Container {container_name} stopped"
        else:
            return f"Container {container_name}: {action}"

    def _get_priority(self, action: str) -> str:
        """Get journald priority for an action.

        Args:
            action: Docker event action.

        Returns:
            Priority string (0-7).
        """
        if action in ("oom", "crashloop"):
            return "2"  # Critical
        elif action in ("die", "kill"):
            return "3"  # Error
        elif action in ("restart", "stop"):
            return "4"  # Warning
        else:
            return "6"  # Info

    def _get_severity(
        self,
        action: str,
        exit_code: int | None,
    ) -> TelemetrySeverity:
        """Get severity for an event.

        Args:
            action: Docker event action.
            exit_code: Exit code if available.

        Returns:
            Severity literal.
        """
        if action == "oom":
            return "critical"
        elif action in ("die", "kill"):
            if exit_code is not None and exit_code != 0:
                return "error"
            return "warning"
        elif action == "restart":
            return "warning"
        else:
            return "info"

    def _is_docker_available(self) -> bool:
        """Check if Docker is available."""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    async def _cleanup(self) -> None:
        """Clean up resources."""
        if self._process and self._process.returncode is None:
            with contextlib.suppress(Exception):
                self._process.terminate()

    @property
    def is_running(self) -> bool:
        """Check if watcher is running."""
        return self._running

    @property
    def stats(self) -> dict[str, int]:
        """Get watcher statistics."""
        return self._stats.copy()

    def get_container_state(self, name: str) -> str | None:
        """Get the last known state of a container.

        Args:
            name: Container name.

        Returns:
            Last known state or None.
        """
        return self._container_states.get(name)

    def get_restart_count(self, name: str) -> int:
        """Get recent restart count for a container.

        Args:
            name: Container name.

        Returns:
            Number of restarts in the crashloop window.
        """
        now = time.time()
        cutoff = now - CRASHLOOP_WINDOW_SEC
        history = self._restart_history.get(name, [])
        return len([ts for ts in history if ts > cutoff])
