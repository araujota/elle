"""Tests for telemetry queue with backpressure handling."""

from __future__ import annotations

import asyncio

import pytest

from elle.daemon.telemetry.queue import (
    BatchProcessor,
    PriorityQueue,
    TelemetryQueue,
    create_queues,
)


class TestTelemetryQueue:
    """Tests for TelemetryQueue."""

    @pytest.fixture
    def queue(self):
        return TelemetryQueue[int](maxsize=5, name="test")

    @pytest.mark.asyncio
    async def test_basic_put_get(self, queue):
        await queue.put(1)
        await queue.put(2)
        result = await queue.get(timeout=1.0)
        assert result == 1
        result = await queue.get(timeout=1.0)
        assert result == 2

    @pytest.mark.asyncio
    async def test_queue_size(self, queue):
        assert queue.size == 0
        await queue.put(1)
        assert queue.size == 1
        await queue.put(2)
        assert queue.size == 2

    @pytest.mark.asyncio
    async def test_queue_empty(self, queue):
        assert queue.empty is True
        await queue.put(1)
        assert queue.empty is False

    @pytest.mark.asyncio
    async def test_queue_full(self, queue):
        # Fill the queue
        for i in range(5):
            await queue.put(i)
        assert queue.full is True

    @pytest.mark.asyncio
    async def test_backpressure_drops_oldest(self, queue):
        # Fill the queue
        for i in range(5):
            await queue.put(i)

        # Add one more - should drop oldest
        await queue.put(100)

        assert queue.dropped == 1
        # First item should be 1 (not 0, which was dropped)
        result = await queue.get(timeout=1.0)
        assert result == 1

    @pytest.mark.asyncio
    async def test_get_timeout(self, queue):
        result = await asyncio.wait_for(queue.get(timeout=0.1), timeout=5.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_nowait_empty(self, queue):
        result = queue.get_nowait()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_nowait_with_item(self, queue):
        await queue.put(42)
        result = queue.get_nowait()
        assert result == 42

    @pytest.mark.asyncio
    async def test_get_batch(self, queue):
        # Add items
        for i in range(3):
            await queue.put(i)

        # Get batch
        items = await queue.get_batch(max_items=10, timeout=0.1)
        assert len(items) == 3
        assert items == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_get_batch_respects_max(self, queue):
        # Add many items
        for i in range(5):
            await queue.put(i)

        # Get batch with limit
        items = await queue.get_batch(max_items=2, timeout=0.1)
        assert len(items) == 2
        assert items == [0, 1]

    @pytest.mark.asyncio
    async def test_get_batch_timeout_empty(self, queue):
        items = await asyncio.wait_for(queue.get_batch(max_items=10, timeout=0.1), timeout=5.0)
        assert items == []

    @pytest.mark.asyncio
    async def test_processed_counter(self, queue):
        await queue.put(1)
        await queue.put(2)

        assert queue.processed == 0
        await queue.get(timeout=1.0)
        assert queue.processed == 1
        await queue.get(timeout=1.0)
        assert queue.processed == 2

    @pytest.mark.asyncio
    async def test_clear(self, queue):
        for i in range(3):
            await queue.put(i)

        cleared = queue.clear()
        assert cleared == 3
        assert queue.size == 0
        assert queue.empty is True

    def test_get_stats(self, queue):
        stats = queue.get_stats()
        assert stats.name == "test"
        assert stats.size == 0
        assert stats.max_size == 5
        assert stats.dropped == 0
        assert stats.processed == 0

    @pytest.mark.asyncio
    async def test_stats_utilization(self, queue):
        # Empty queue
        stats = queue.get_stats()
        assert stats.utilization == 0.0

        # Half full
        await queue.put(1)
        await queue.put(2)
        stats = queue.get_stats()
        assert stats.utilization == 0.4  # 2/5

        # Full
        for i in range(3):
            await queue.put(i + 10)
        stats = queue.get_stats()
        assert stats.utilization == 1.0

    def test_reset_stats(self, queue):
        # Simulate some activity
        queue.put_nowait(1)
        queue.get_nowait()
        queue.put_nowait(2)
        queue.put_nowait(3)

        queue.reset_stats()

        assert queue.dropped == 0
        assert queue.processed == 0


class TestCreateQueues:
    """Tests for queue factory function."""

    def test_create_default_queues(self):
        raw, events = create_queues()
        assert raw.name == "raw"
        assert events.name == "events"
        assert raw.maxsize == 10000
        assert events.maxsize == 5000

    def test_create_custom_queues(self):
        raw, events = create_queues(raw_size=100, event_size=50)
        assert raw.maxsize == 100
        assert events.maxsize == 50


class TestConcurrentAccess:
    """Tests for concurrent queue access."""

    @pytest.mark.asyncio
    async def test_concurrent_producers(self):
        queue = TelemetryQueue[int](maxsize=1000, name="concurrent")

        async def producer(start: int, count: int):
            for i in range(count):
                await queue.put(start + i)

        # Run multiple producers concurrently
        await asyncio.gather(
            producer(0, 100),
            producer(100, 100),
            producer(200, 100),
        )

        assert queue.size == 300

    @pytest.mark.asyncio
    async def test_concurrent_producer_consumer(self):
        queue = TelemetryQueue[int](maxsize=100, name="prodcons")
        produced = []
        consumed = []
        stop = asyncio.Event()

        async def producer():
            for i in range(50):
                await queue.put(i)
                produced.append(i)
                await asyncio.sleep(0.001)
            stop.set()

        async def consumer():
            while not stop.is_set() or not queue.empty:
                item = await queue.get(timeout=0.5)
                if item is not None:
                    consumed.append(item)
                elif stop.is_set():
                    # Producer finished and get timed out: drain remaining
                    while not queue.empty:
                        item = queue.get_nowait()
                        if item is not None:
                            consumed.append(item)
                    break

        await asyncio.wait_for(
            asyncio.gather(producer(), consumer()),
            timeout=10.0,
        )

        assert len(produced) == 50
        assert sorted(consumed) == list(range(50))


# =============================================================================
# Additional TelemetryQueue tests for uncovered branches
# =============================================================================


class TestTelemetryQueuePutNowait:
    """Tests for put_nowait (sync path)."""

    def test_put_nowait_success(self):
        """put_nowait adds item to non-full queue."""
        q = TelemetryQueue[int](maxsize=5, name="sync")
        result = q.put_nowait(42)
        assert result is True
        assert q.size == 1

    def test_put_nowait_backpressure(self):
        """put_nowait drops oldest when full."""
        q = TelemetryQueue[int](maxsize=2, name="sync")
        q.put_nowait(1)
        q.put_nowait(2)
        assert q.full is True

        # Should drop oldest (1) and add 3
        result = q.put_nowait(3)
        assert result is True
        assert q.dropped == 1
        # First item should be 2
        item = q.get_nowait()
        assert item == 2

    def test_put_nowait_multiple_drops(self):
        """Multiple put_nowait calls track drop count."""
        q = TelemetryQueue[int](maxsize=1, name="sync")
        q.put_nowait(1)
        q.put_nowait(2)
        q.put_nowait(3)
        q.put_nowait(4)
        assert q.dropped == 3
        item = q.get_nowait()
        assert item == 4


class TestTelemetryQueueGetNoWait:
    """Additional get_nowait tests."""

    def test_processed_counter_incremented(self):
        """get_nowait increments processed counter."""
        q = TelemetryQueue[str](maxsize=5, name="test")
        q.put_nowait("a")
        q.put_nowait("b")
        assert q.processed == 0

        q.get_nowait()
        assert q.processed == 1
        q.get_nowait()
        assert q.processed == 2


class TestTelemetryQueueGetNoTimeout:
    """Test get() without timeout (waits indefinitely)."""

    @pytest.mark.asyncio
    async def test_get_without_timeout(self):
        """get() without timeout waits for an item."""
        q = TelemetryQueue[int](maxsize=5, name="wait")

        async def delayed_put():
            await asyncio.sleep(0.05)
            await q.put(99)

        asyncio.create_task(delayed_put())
        item = await asyncio.wait_for(q.get(), timeout=2.0)
        assert item == 99
        assert q.processed == 1


class TestTelemetryQueueTaskDone:
    """Tests for task_done and join."""

    @pytest.mark.asyncio
    async def test_task_done_and_join(self):
        """task_done + join works correctly."""
        q = TelemetryQueue[int](maxsize=5, name="join")
        await q.put(1)
        await q.put(2)

        async def consumer():
            for _ in range(2):
                item = await q.get(timeout=1.0)
                assert item is not None
                q.task_done()

        async def wait_join():
            await q.join()

        await asyncio.wait_for(
            asyncio.gather(consumer(), wait_join()),
            timeout=5.0,
        )


class TestTelemetryQueueClear:
    """Additional clear tests."""

    def test_clear_empty_queue(self):
        """Clearing an empty queue returns 0."""
        q = TelemetryQueue[int](maxsize=5, name="test")
        cleared = q.clear()
        assert cleared == 0


class TestTelemetryQueueDropLogging:
    """Test drop logging at interval boundaries."""

    @pytest.mark.asyncio
    async def test_drop_count_logging_boundary(self):
        """Logging happens every 100 drops."""
        q = TelemetryQueue[int](maxsize=1, name="log_test")

        # Fill queue first
        await q.put(0)

        # Drop 100 items to hit the logging boundary
        for i in range(1, 101):
            await q.put(i)

        assert q.dropped == 100


class TestTelemetryQueueGetBatchEdgeCases:
    """Edge cases for get_batch."""

    @pytest.mark.asyncio
    async def test_get_batch_single_item(self):
        """get_batch with single item returns list of one."""
        q = TelemetryQueue[int](maxsize=5, name="batch")
        await q.put(42)
        items = await q.get_batch(max_items=10, timeout=0.1)
        assert items == [42]

    @pytest.mark.asyncio
    async def test_get_batch_max_items_one(self):
        """get_batch with max_items=1 returns at most one item."""
        q = TelemetryQueue[int](maxsize=5, name="batch")
        await q.put(1)
        await q.put(2)
        items = await q.get_batch(max_items=1, timeout=0.1)
        assert len(items) == 1


class TestTelemetryQueueStatsAfterActivity:
    """Test stats reflect actual activity."""

    @pytest.mark.asyncio
    async def test_stats_after_drops_and_gets(self):
        """Stats correctly reflect drops and processed items."""
        q = TelemetryQueue[int](maxsize=2, name="stats")
        await q.put(1)
        await q.put(2)
        await q.put(3)  # Drops 1

        item = await q.get(timeout=0.1)
        assert item == 2

        stats = q.get_stats()
        assert stats.dropped == 1
        assert stats.processed == 1
        assert stats.size == 1
        assert stats.name == "stats"
        assert stats.max_size == 2


# =============================================================================
# PriorityQueue tests
# =============================================================================


class TestPriorityQueue:
    """Tests for PriorityQueue."""

    @pytest.fixture
    def pq(self):
        return PriorityQueue[str](maxsize=100, name="prio")

    @pytest.mark.asyncio
    async def test_critical_first(self, pq):
        """Critical items are retrieved before normal items."""
        await pq.put("normal-1", priority="normal")
        await pq.put("critical-1", priority="critical")
        await pq.put("high-1", priority="high")

        item = await pq.get(timeout=0.1)
        assert item == "critical-1"

    @pytest.mark.asyncio
    async def test_high_before_normal(self, pq):
        """High priority items come before normal."""
        await pq.put("normal-1", priority="normal")
        await pq.put("high-1", priority="high")

        item = await pq.get(timeout=0.1)
        assert item == "high-1"

    @pytest.mark.asyncio
    async def test_normal_when_no_critical_or_high(self, pq):
        """Normal items are retrieved when no higher priority items exist."""
        await pq.put("normal-1", priority="normal")
        item = await pq.get(timeout=0.1)
        assert item == "normal-1"

    @pytest.mark.asyncio
    async def test_size_counts_all_queues(self, pq):
        """Size reflects items across all priority queues."""
        await pq.put("c", priority="critical")
        await pq.put("h", priority="high")
        await pq.put("n", priority="normal")
        assert pq.size == 3

    @pytest.mark.asyncio
    async def test_put_returns_false_when_full(self):
        """put returns False when priority queue is full."""
        pq = PriorityQueue[str](maxsize=4, name="small")
        # maxsize//4 = 1 for critical
        result1 = await pq.put("c1", priority="critical")
        assert result1 is True
        result2 = await pq.put("c2", priority="critical")
        assert result2 is False
        assert pq._dropped == 1

    @pytest.mark.asyncio
    async def test_default_priority_is_normal(self, pq):
        """Default priority is normal."""
        await pq.put("item")
        # Should be in the normal queue
        assert pq._normal.qsize() == 1

    @pytest.mark.asyncio
    async def test_get_timeout_empty(self, pq):
        """get returns None on timeout when all queues empty."""
        item = await pq.get(timeout=0.05)
        assert item is None

    @pytest.mark.asyncio
    async def test_get_without_timeout(self):
        """get without timeout waits for normal queue item."""
        pq = PriorityQueue[str](maxsize=100, name="prio")

        async def delayed_put():
            await asyncio.sleep(0.05)
            await pq.put("late", priority="normal")

        asyncio.create_task(delayed_put())
        item = await asyncio.wait_for(pq.get(), timeout=2.0)
        assert item == "late"

    @pytest.mark.asyncio
    async def test_get_stats(self, pq):
        """get_stats returns correct stats."""
        await pq.put("a", priority="critical")
        await pq.put("b", priority="normal")
        item = await pq.get(timeout=0.1)

        stats = pq.get_stats()
        assert stats.name == "prio"
        assert stats.processed == 1
        assert stats.size == 1
        assert stats.max_size == 100

    @pytest.mark.asyncio
    async def test_priority_drain_order(self, pq):
        """Complete drain follows priority order."""
        await pq.put("n1", priority="normal")
        await pq.put("n2", priority="normal")
        await pq.put("h1", priority="high")
        await pq.put("c1", priority="critical")

        items = []
        for _ in range(4):
            item = await pq.get(timeout=0.1)
            if item is not None:
                items.append(item)

        # Critical first, then high, then normal
        assert items[0] == "c1"
        assert items[1] == "h1"
        assert items[2] in ("n1", "n2")

    @pytest.mark.asyncio
    async def test_dropped_counter(self):
        """Dropped counter tracks items that could not be queued."""
        pq = PriorityQueue[str](maxsize=4, name="small")
        # Critical queue has maxsize//4 = 1 slot
        await pq.put("c1", priority="critical")
        await pq.put("c2", priority="critical")  # Should be dropped
        assert pq._dropped == 1

        stats = pq.get_stats()
        assert stats.dropped == 1


# =============================================================================
# BatchProcessor tests
# =============================================================================


class TestBatchProcessor:
    """Tests for BatchProcessor."""

    @pytest.mark.asyncio
    async def test_batch_full(self):
        """Returns batch when batch_size items are collected."""
        source = TelemetryQueue[int](maxsize=100, name="src")
        for i in range(5):
            await source.put(i)

        bp = BatchProcessor(source, batch_size=5, flush_interval=10.0)
        batch = await asyncio.wait_for(bp.get_batch(), timeout=5.0)
        assert len(batch) == 5
        assert batch == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_batch_flush_interval(self):
        """Returns partial batch after flush interval."""
        source = TelemetryQueue[int](maxsize=100, name="src")
        await source.put(1)
        await source.put(2)

        bp = BatchProcessor(source, batch_size=100, flush_interval=0.1)
        batch = await asyncio.wait_for(bp.get_batch(), timeout=5.0)
        assert len(batch) >= 1  # At least 1 item returned after flush interval

    @pytest.mark.asyncio
    async def test_batch_empty_source_returns_after_timeout(self):
        """Returns empty batch items after source times out with partial data."""
        source = TelemetryQueue[int](maxsize=100, name="src")
        await source.put(1)

        bp = BatchProcessor(source, batch_size=100, flush_interval=0.1)
        batch = await asyncio.wait_for(bp.get_batch(), timeout=5.0)
        assert len(batch) >= 1
