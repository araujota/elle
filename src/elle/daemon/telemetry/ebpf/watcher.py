"""EBPFWatcher - Main async watcher for eBPF telemetry events.

Orchestrates loading, attaching, and polling of multiple BCC programs,
converting events to raw dicts for the telemetry pipeline.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from elle.daemon.telemetry.ebpf.capabilities import can_use_ebpf, get_kernel_version_string
from elle.daemon.telemetry.ebpf.models import (
    EBPF_EVENT_CATEGORIES,
    EBPF_EVENT_SEVERITIES,
    EBPFRawEvent,
    EBPFWatcherStatus,
)
from elle.daemon.telemetry.queue import TelemetryQueue

logger = logging.getLogger(__name__)


class EBPFWatcher:
    """Async watcher for eBPF telemetry events.

    Manages multiple BCC programs and polls their ring buffers
    for events, converting them to raw dicts for the normalizer.
    """

    def __init__(
        self,
        queue: TelemetryQueue[dict[str, Any]],
        shutdown: asyncio.Event,
        programs: list[str] | None = None,
    ):
        """Initialize the eBPF watcher.

        Args:
            queue: Queue to put raw events.
            shutdown: Event to signal shutdown.
            programs: List of program names to load (default: all).
        """
        self._queue = queue
        self._shutdown = shutdown
        self._requested_programs = programs or ["oom", "block_io", "process", "thermal", "net_drops"]

        # State
        self._programs: list[Any] = []  # BCCProgram instances
        self._running = False
        self._started_at: datetime | None = None
        self._total_events = 0

        # Capability check
        self._can_use, self._reason = can_use_ebpf()

    @property
    def running(self) -> bool:
        """Check if watcher is running."""
        return self._running

    @property
    def total_events(self) -> int:
        """Get total events processed."""
        return self._total_events

    def get_status(self) -> EBPFWatcherStatus:
        """Get current watcher status.

        Returns:
            EBPFWatcherStatus object.
        """
        from elle.daemon.telemetry.ebpf.capabilities import (
            check_capabilities,
            get_missing_capabilities,
        )

        check = check_capabilities()

        program_statuses = tuple(p.get_status() for p in self._programs)

        uptime = 0
        if self._started_at:
            uptime = int((datetime.now(UTC) - self._started_at).total_seconds())

        return EBPFWatcherStatus(
            enabled=self._can_use,
            capabilities_ok=check.available,
            missing_capabilities=tuple(get_missing_capabilities()),
            programs=program_statuses,
            total_events=self._total_events,
            uptime_sec=uptime,
            backend="bcc",
            kernel_version=get_kernel_version_string(),
        )

    async def start(self) -> None:
        """Start watching eBPF events.

        Runs until shutdown event is set.
        """
        if not self._can_use:
            logger.warning(f"eBPF not available: {self._reason}")
            return

        logger.info("Starting eBPF watcher")
        self._running = True
        self._started_at = datetime.now(UTC)

        try:
            # Load and attach programs
            await self._load_programs()

            if not self._programs:
                logger.warning("No eBPF programs loaded, watcher stopping")
                self._running = False
                return

            # Setup ring buffers
            for program in self._programs:
                program.set_event_callback(self._handle_event)
                program.setup_ring_buffer()

            # Main polling loop
            await self._watch_loop()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"eBPF watcher error: {e}")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the watcher and cleanup."""
        logger.info("Stopping eBPF watcher")

        # Unload all programs
        for program in self._programs:
            try:
                program.unload()
            except Exception as e:
                logger.debug(f"Error unloading {program.name}: {e}")

        self._programs = []
        self._running = False

        logger.info(f"eBPF watcher stopped (events: {self._total_events})")

    async def _load_programs(self) -> None:
        """Load requested BCC programs."""
        program_factories = self._get_program_factories()

        for name in self._requested_programs:
            if name not in program_factories:
                logger.warning(f"Unknown eBPF program: {name}")
                continue

            try:
                factory = program_factories[name]
                program = factory()

                if not program.load():
                    logger.warning(f"Failed to load eBPF program: {name}")
                    continue

                if not program.attach():
                    logger.warning(f"Failed to attach eBPF program: {name}")
                    program.unload()
                    continue

                self._programs.append(program)
                logger.info(f"Loaded eBPF program: {name}")

            except Exception as e:
                logger.error(f"Error loading eBPF program {name}: {e}")

        logger.info(f"Loaded {len(self._programs)} eBPF programs")

    def _get_program_factories(self) -> dict[str, Any]:
        """Get factory functions for each program type.

        Returns:
            Dict mapping program name to factory function.
        """
        factories = {}

        # OOM program
        try:
            from elle.daemon.telemetry.ebpf.bcc.oom import create_oom_program
            factories["oom"] = create_oom_program
        except ImportError as e:
            logger.debug(f"OOM program not available: {e}")

        # Block I/O program
        try:
            from elle.daemon.telemetry.ebpf.bcc.block_io import create_block_io_program
            factories["block_io"] = create_block_io_program
        except ImportError as e:
            logger.debug(f"Block I/O program not available: {e}")

        # Process program
        try:
            from elle.daemon.telemetry.ebpf.bcc.process import create_process_program
            factories["process"] = create_process_program
        except ImportError as e:
            logger.debug(f"Process program not available: {e}")

        # Thermal program
        try:
            from elle.daemon.telemetry.ebpf.bcc.thermal import create_thermal_program
            factories["thermal"] = create_thermal_program
        except ImportError as e:
            logger.debug(f"Thermal program not available: {e}")

        # Network drops program
        try:
            from elle.daemon.telemetry.ebpf.bcc.net_drops import create_net_drops_program
            factories["net_drops"] = create_net_drops_program
        except ImportError as e:
            logger.debug(f"Network drops program not available: {e}")

        return factories

    async def _watch_loop(self) -> None:
        """Main loop polling ring buffers."""
        poll_interval = 0.1  # 100ms

        while not self._shutdown.is_set():
            try:
                # Poll all programs
                for program in self._programs:
                    try:
                        program.poll(timeout_ms=10)
                    except Exception as e:
                        logger.debug(f"Poll error in {program.name}: {e}")

                # Small sleep to prevent busy-waiting
                await asyncio.sleep(poll_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Watch loop error: {e}")
                await asyncio.sleep(1)

    def _handle_event(self, event: EBPFRawEvent) -> None:
        """Handle an event from a BCC program.

        Converts the EBPFRawEvent to a raw dict for the normalizer.

        Args:
            event: The eBPF event to handle.
        """
        self._total_events += 1

        # Convert to raw dict format expected by normalizer
        raw = self._event_to_raw_dict(event)

        # Queue for processing (non-blocking)
        try:
            self._queue.put_nowait(raw)
        except Exception as e:
            logger.debug(f"Failed to queue eBPF event: {e}")

    def _event_to_raw_dict(self, event: EBPFRawEvent) -> dict[str, Any]:
        """Convert EBPFRawEvent to raw dict for normalizer.

        Args:
            event: The eBPF event.

        Returns:
            Raw dict with journald-compatible fields.
        """
        # Get category and severity from mappings
        category = EBPF_EVENT_CATEGORIES.get(event.event_type, "other")
        severity = EBPF_EVENT_SEVERITIES.get(event.event_type, "info")

        # Map severity to journald priority
        severity_to_priority = {
            "debug": 7,
            "info": 6,
            "notice": 5,
            "warning": 4,
            "error": 3,
            "critical": 2,
        }
        priority = severity_to_priority.get(severity, 6)

        # Format message
        message = self._format_message(event)

        # Build raw dict
        raw: dict[str, Any] = {
            "_SOURCE": "ebpf",
            "_EBPF_EVENT_TYPE": event.event_type,
            "_EBPF_CATEGORY": category,
            "MESSAGE": message,
            "PRIORITY": str(priority),
            "_EBPF_TS_NS": event.ts_ns,
            "_EBPF_CPU": event.cpu,
        }

        # Add process context if available
        if event.pid is not None:
            raw["_PID"] = str(event.pid)
        if event.tgid is not None:
            raw["_TGID"] = str(event.tgid)
        if event.uid is not None:
            raw["_UID"] = str(event.uid)
        if event.comm:
            raw["_COMM"] = event.comm

        # Add event-specific data
        raw.update({f"_EBPF_{k.upper()}": v for k, v in event.data.items()})

        return raw

    def _format_message(self, event: EBPFRawEvent) -> str:
        """Format a human-readable message from an eBPF event.

        Args:
            event: The eBPF event.

        Returns:
            Formatted message string.
        """
        from elle.daemon.telemetry.ebpf.models import EBPFEventType

        if event.event_type == EBPFEventType.OOM_KILL:
            comm = event.comm or "unknown"
            pid = event.pid or 0
            return f"OOM killer invoked, killed process {comm} (PID {pid})"

        elif event.event_type == EBPFEventType.BLOCK_RQ_ERROR:
            dev = event.data.get("dev", "unknown")
            error = event.data.get("error", 0)
            return f"Block I/O error on device {dev} (error {error})"

        elif event.event_type == EBPFEventType.NET_DROP:
            reason = event.data.get("reason", "unknown")
            ifname = event.data.get("ifname", "unknown")
            return f"Network packet dropped on {ifname}: {reason}"

        elif event.event_type == EBPFEventType.PROCESS_EXIT:
            comm = event.comm or "unknown"
            pid = event.pid or 0
            exit_code = event.data.get("exit_code", 0)
            signal = event.data.get("signal", 0)
            if signal > 0:
                return f"Process {comm} (PID {pid}) killed by signal {signal}"
            return f"Process {comm} (PID {pid}) exited with code {exit_code}"

        elif event.event_type == EBPFEventType.SIGNAL_DELIVER:
            comm = event.comm or "unknown"
            pid = event.pid or 0
            signal = event.data.get("signal", 0)
            return f"Signal {signal} delivered to {comm} (PID {pid})"

        elif event.event_type == EBPFEventType.THERMAL_ZONE_TRIP:
            zone = event.data.get("zone", "unknown")
            trip_type = event.data.get("trip_type", "unknown")
            temp = event.data.get("temp", 0)
            return f"Thermal zone {zone} trip ({trip_type}) at {temp}C"

        elif event.event_type == EBPFEventType.CPU_FREQUENCY:
            cpu = event.data.get("cpu", 0)
            freq = event.data.get("freq", 0)
            return f"CPU {cpu} frequency changed to {freq} kHz"

        else:
            return f"eBPF event: {event.event_type}"


async def watch_ebpf(
    queue: TelemetryQueue[dict[str, Any]],
    shutdown: asyncio.Event,
    programs: list[str] | None = None,
) -> None:
    """Watch eBPF events and queue them.

    Convenience function wrapping EBPFWatcher.

    Args:
        queue: Queue to put raw events.
        shutdown: Event to signal shutdown.
        programs: Optional list of programs to load.
    """
    watcher = EBPFWatcher(queue, shutdown, programs)
    try:
        await watcher.start()
    finally:
        await watcher.stop()
