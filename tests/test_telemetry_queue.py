"""Tests for telemetry queue with backpressure handling."""

import asyncio

import pytest

from elle.daemon.telemetry.queue import TelemetryQueue, create_queues


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
        result = await queue.get(timeout=0.1)
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
        items = await queue.get_batch(max_items=10, timeout=0.1)
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
            while not stop.is_set() or queue.size > 0:
                item = await queue.get(timeout=0.1)
                if item is not None:
                    consumed.append(item)

        await asyncio.gather(producer(), consumer())

        assert len(produced) == 50
        assert sorted(consumed) == list(range(50))
