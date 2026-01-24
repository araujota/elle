"""Event Router - telemetry subscription and event routing.

Subscribes to telemetry event queues and routes events to the
ReactiveEngine for processing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from elle.daemon.telemetry.models import TelemetryEvent
    from elle.reactive.engine import ReactiveEngine

logger = logging.getLogger(__name__)


class EventRouter:
    """Routes telemetry events to the ReactiveEngine.

    Subscribes to event queues from telemetry sources (journal, kernel,
    probe, ebpf) and forwards events to the engine for processing.

    Usage:
        router = EventRouter(engine)
        await router.start(telemetry_queues)
    """

    def __init__(
        self,
        engine: ReactiveEngine,
        max_concurrent: int = 10,
    ) -> None:
        """Initialize the event router.

        Args:
            engine: The ReactiveEngine for processing events.
            max_concurrent: Maximum concurrent event processing tasks.
        """
        self.engine = engine
        self.max_concurrent = max_concurrent
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._stats = RouterStats()

    async def start(
        self,
        telemetry_queues: dict[str, asyncio.Queue[TelemetryEvent]],
    ) -> None:
        """Start consuming from all telemetry sources.

        Blocks until stop() is called.

        Args:
            telemetry_queues: Dict mapping source names to event queues.
        """
        if self._running:
            logger.warning("EventRouter already running")
            return

        self._running = True
        logger.info(f"Starting EventRouter with {len(telemetry_queues)} sources")

        # Create a consumer task for each source
        self._tasks = [
            asyncio.create_task(
                self._consume(source, queue),
                name=f"router-{source}",
            )
            for source, queue in telemetry_queues.items()
        ]

        try:
            # Wait for all consumers to complete (or be cancelled)
            await asyncio.gather(*self._tasks, return_exceptions=True)
        except asyncio.CancelledError:
            logger.info("EventRouter cancelled")
        finally:
            self._running = False

    async def stop(self) -> None:
        """Stop the event router and all consumer tasks."""
        self._running = False

        for task in self._tasks:
            if not task.done():
                task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        self._tasks = []
        logger.info("EventRouter stopped")

    async def _consume(
        self,
        source: str,
        queue: asyncio.Queue[TelemetryEvent],
    ) -> None:
        """Consume events from a single source queue.

        Args:
            source: Source name for logging.
            queue: Event queue to consume from.
        """
        logger.debug(f"Starting consumer for source: {source}")

        while self._running:
            try:
                # Use timeout to allow periodic check of _running flag
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            # Process event with concurrency limit
            try:
                async with self._semaphore:
                    await self._process_event(event)
            except Exception as e:
                logger.exception(f"Error processing event from {source}: {e}")
                self._stats.errors += 1

    async def _process_event(self, event: TelemetryEvent) -> None:
        """Process a single event through the engine.

        Args:
            event: The telemetry event to process.
        """
        self._stats.events_received += 1

        try:
            records = await self.engine.process_event(event)
            self._stats.events_processed += 1

            for record in records:
                if record.success:
                    self._stats.functions_triggered += 1
                else:
                    self._stats.functions_failed += 1

        except Exception as e:
            logger.exception(f"Engine error processing event {event.event_id}: {e}")
            self._stats.errors += 1
            raise

    @property
    def is_running(self) -> bool:
        """Check if the router is running."""
        return self._running

    @property
    def stats(self) -> RouterStats:
        """Get current router statistics."""
        return self._stats


class RouterStats:
    """Statistics for the event router."""

    def __init__(self) -> None:
        """Initialize statistics counters."""
        self.events_received = 0
        self.events_processed = 0
        self.functions_triggered = 0
        self.functions_failed = 0
        self.errors = 0

    def to_dict(self) -> dict[str, int]:
        """Convert stats to a dictionary."""
        return {
            "events_received": self.events_received,
            "events_processed": self.events_processed,
            "functions_triggered": self.functions_triggered,
            "functions_failed": self.functions_failed,
            "errors": self.errors,
        }

    def reset(self) -> None:
        """Reset all statistics to zero."""
        self.events_received = 0
        self.events_processed = 0
        self.functions_triggered = 0
        self.functions_failed = 0
        self.errors = 0


# =============================================================================
# Standalone event routing function
# =============================================================================


async def route_event(
    event: TelemetryEvent,
    engine: ReactiveEngine | None = None,
) -> list:
    """Route a single event through the reactive engine.

    Convenience function for routing events without setting up
    the full router infrastructure.

    Args:
        event: The telemetry event to route.
        engine: ReactiveEngine instance (uses global if None).

    Returns:
        List of ExecutionRecords.
    """
    if engine is None:
        from elle.reactive.engine import get_engine

        engine = get_engine()

    return await engine.process_event(event)
