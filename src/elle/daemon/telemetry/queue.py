"""Bounded async queues with backpressure handling.

Provides flow control between telemetry producers (watchers, probes)
and consumers (normalizer, processor) to prevent memory exhaustion
during log storms.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Generic, TypeVar

from elle.daemon.telemetry.models import QueueStats

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TelemetryQueue(Generic[T]):
    """Bounded async queue with backpressure handling.

    When the queue is full, oldest items are dropped to make room
    for new items. This ensures the daemon remains responsive
    during high-volume events.
    """

    def __init__(self, maxsize: int = 10000, name: str = "queue"):
        """Initialize the queue.

        Args:
            maxsize: Maximum queue size.
            name: Queue name for logging.
        """
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)
        self._name = name
        self._maxsize = maxsize
        self._dropped = 0
        self._processed = 0
        self._total_enqueued = 0
        self._drop_events: list[float] = []  # timestamps of recent drops
        self._peak_size = 0
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Get queue name."""
        return self._name

    @property
    def size(self) -> int:
        """Get current queue size."""
        return self._queue.qsize()

    @property
    def maxsize(self) -> int:
        """Get maximum queue size."""
        return self._maxsize

    @property
    def dropped(self) -> int:
        """Get total dropped items."""
        return self._dropped

    @property
    def processed(self) -> int:
        """Get total processed items."""
        return self._processed

    @property
    def empty(self) -> bool:
        """Check if queue is empty."""
        return self._queue.empty()

    @property
    def full(self) -> bool:
        """Check if queue is full."""
        return self._queue.full()

    async def put(self, item: T) -> bool:
        """Put an item in the queue.

        If the queue is full, drops the oldest item to make room.

        Args:
            item: Item to put in queue.

        Returns:
            True if item was queued, False if dropped.
        """
        import time

        async with self._lock:
            try:
                self._queue.put_nowait(item)
                self._total_enqueued += 1
                self._peak_size = max(self._peak_size, self._queue.qsize())
                return True
            except asyncio.QueueFull:
                # Drop oldest item to make room
                try:
                    self._queue.get_nowait()
                    self._dropped += 1
                    self._drop_events.append(time.time())
                    # Keep only last 1000 drop timestamps
                    if len(self._drop_events) > 1000:
                        self._drop_events = self._drop_events[-1000:]
                    self._queue.put_nowait(item)
                    self._total_enqueued += 1

                    if self._dropped % 100 == 0:
                        logger.warning(f"Queue '{self._name}' backpressure: {self._dropped} items dropped")
                    return True
                except asyncio.QueueEmpty:
                    return False

    def put_nowait(self, item: T) -> bool:
        """Put an item without waiting.

        Non-async version for use in sync contexts.

        Args:
            item: Item to put in queue.

        Returns:
            True if item was queued, False if dropped.
        """
        try:
            self._queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._dropped += 1
                self._queue.put_nowait(item)
                return True
            except asyncio.QueueEmpty:
                return False

    async def get(self, timeout: float | None = None) -> T | None:
        """Get an item from the queue.

        Args:
            timeout: Optional timeout in seconds.

        Returns:
            Item from queue, or None if timeout.
        """
        try:
            if timeout is not None:
                item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            else:
                item = await self._queue.get()
            self._processed += 1
            return item
        except (TimeoutError, asyncio.TimeoutError):
            return None

    def get_nowait(self) -> T | None:
        """Get an item without waiting.

        Returns:
            Item from queue, or None if empty.
        """
        try:
            item = self._queue.get_nowait()
            self._processed += 1
            return item
        except asyncio.QueueEmpty:
            return None

    async def get_batch(
        self,
        max_items: int = 100,
        timeout: float = 0.1,
    ) -> list[T]:
        """Get multiple items from the queue.

        Waits for at least one item, then collects up to max_items
        or until queue is empty.

        Args:
            max_items: Maximum items to return.
            timeout: Timeout for first item.

        Returns:
            List of items (may be empty on timeout).
        """
        items: list[T] = []

        # Wait for at least one item
        first = await self.get(timeout=timeout)
        if first is not None:
            items.append(first)

            # Collect remaining items without waiting
            while len(items) < max_items:
                item = self.get_nowait()
                if item is None:
                    break
                items.append(item)

        return items

    def task_done(self) -> None:
        """Mark a task as done (for join())."""
        self._queue.task_done()

    async def join(self, timeout: float = 30.0) -> None:
        """Wait for all items to be processed.

        Args:
            timeout: Maximum seconds to wait before raising TimeoutError.
        """
        await asyncio.wait_for(self._queue.join(), timeout=timeout)

    def clear(self) -> int:
        """Clear all items from the queue.

        Returns:
            Number of items cleared.
        """
        count = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                count += 1
            except asyncio.QueueEmpty:
                break
        return count

    def get_stats(self) -> QueueStats:
        """Get queue statistics.

        Returns:
            QueueStats object.
        """
        return QueueStats(
            name=self._name,
            size=self.size,
            max_size=self._maxsize,
            dropped=self._dropped,
            processed=self._processed,
        )

    def get_metrics(self) -> dict[str, Any]:
        """Get detailed queue overflow metrics.

        Returns:
            Dict with drop count, current size, peak size,
            total enqueued, and recent drop rate.
        """
        import time

        now = time.time()
        # Calculate drops in the last 60 seconds
        recent_drops = sum(1 for t in self._drop_events if now - t < 60)

        return {
            "name": self._name,
            "current_size": self.size,
            "max_size": self._maxsize,
            "peak_size": self._peak_size,
            "total_dropped": self._dropped,
            "total_processed": self._processed,
            "total_enqueued": self._total_enqueued,
            "recent_drops_per_minute": recent_drops,
            "utilization_pct": (self.size / self._maxsize * 100) if self._maxsize > 0 else 0,
        }

    def reset_stats(self) -> None:
        """Reset dropped/processed counters."""
        self._dropped = 0
        self._processed = 0
        self._total_enqueued = 0
        self._drop_events.clear()
        self._peak_size = 0


class PriorityQueue(Generic[T]):
    """Priority queue for urgent events.

    Critical severity events get higher priority than
    warning/info events.
    """

    def __init__(self, maxsize: int = 5000, name: str = "priority_queue"):
        """Initialize the priority queue.

        Args:
            maxsize: Maximum queue size.
            name: Queue name for logging.
        """
        # Use separate queues for different priorities
        self._critical: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize // 4)
        self._high: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize // 4)
        self._normal: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize // 2)
        self._name = name
        self._maxsize = maxsize
        self._dropped = 0
        self._processed = 0

    @property
    def size(self) -> int:
        """Get total queue size."""
        return self._critical.qsize() + self._high.qsize() + self._normal.qsize()

    async def put(self, item: T, priority: str = "normal") -> bool:
        """Put an item with priority.

        Args:
            item: Item to put in queue.
            priority: 'critical', 'high', or 'normal'.

        Returns:
            True if queued, False if dropped.
        """
        if priority == "critical":
            queue = self._critical
        elif priority == "high":
            queue = self._high
        else:
            queue = self._normal

        try:
            queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            self._dropped += 1
            return False

    async def get(self, timeout: float | None = None) -> T | None:
        """Get item by priority (critical first).

        Args:
            timeout: Optional timeout in seconds.

        Returns:
            Item from queue, or None if timeout.
        """
        # Check critical first
        try:
            item = self._critical.get_nowait()
            self._processed += 1
            return item
        except asyncio.QueueEmpty:
            pass

        # Then high priority
        try:
            item = self._high.get_nowait()
            self._processed += 1
            return item
        except asyncio.QueueEmpty:
            pass

        # Finally normal priority (with wait)
        try:
            if timeout is not None:
                item = await asyncio.wait_for(self._normal.get(), timeout=timeout)
            else:
                item = await self._normal.get()
            self._processed += 1
            return item
        except (TimeoutError, asyncio.TimeoutError, asyncio.QueueEmpty):
            return None

    def get_stats(self) -> QueueStats:
        """Get queue statistics."""
        return QueueStats(
            name=self._name,
            size=self.size,
            max_size=self._maxsize,
            dropped=self._dropped,
            processed=self._processed,
        )


class BatchProcessor(Generic[T]):
    """Processes queue items in batches for efficiency.

    Collects items from a source queue and processes them
    in batches with configurable size and timing.
    """

    def __init__(
        self,
        source: TelemetryQueue[T],
        batch_size: int = 100,
        flush_interval: float = 1.0,
    ):
        """Initialize the batch processor.

        Args:
            source: Source queue to read from.
            batch_size: Maximum batch size.
            flush_interval: Seconds between flushes.
        """
        self._source = source
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._batch: list[T] = []
        self._last_flush = asyncio.get_event_loop().time()

    async def get_batch(self) -> list[T]:
        """Get a batch of items.

        Returns when batch is full or flush interval elapsed.

        Returns:
            List of items.
        """
        while True:
            # Check if batch is ready
            now = asyncio.get_event_loop().time()
            time_since_flush = now - self._last_flush

            if len(self._batch) >= self._batch_size:
                batch = self._batch
                self._batch = []
                self._last_flush = now
                return batch

            if self._batch and time_since_flush >= self._flush_interval:
                batch = self._batch
                self._batch = []
                self._last_flush = now
                return batch

            # Calculate timeout
            timeout = max(0.1, self._flush_interval - time_since_flush)

            # Get more items
            items = await self._source.get_batch(
                max_items=self._batch_size - len(self._batch),
                timeout=timeout,
            )
            self._batch.extend(items)

            if not items:
                # Timeout with items - return partial batch
                if self._batch:
                    batch = self._batch
                    self._batch = []
                    self._last_flush = now
                    return batch


def create_queues(
    raw_size: int = 10000, event_size: int = 5000
) -> tuple[
    TelemetryQueue[dict[str, Any]],
    TelemetryQueue[Any],
]:
    """Create the standard daemon queues.

    Args:
        raw_size: Size for raw event queue.
        event_size: Size for normalized event queue.

    Returns:
        Tuple of (raw_queue, event_queue).
    """
    raw_queue: TelemetryQueue[dict[str, Any]] = TelemetryQueue(
        maxsize=raw_size,
        name="raw",
    )
    event_queue: TelemetryQueue[Any] = TelemetryQueue(
        maxsize=event_size,
        name="events",
    )
    return raw_queue, event_queue
