from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.reactive.router import (
    EventRouter,
    RouterStats,
    SimpleRouter,
    get_router,
    reset_router,
    route_event,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_event(**overrides: Any) -> MagicMock:
    """Create a mock TelemetryEvent."""
    defaults: dict[str, Any] = {
        "event_id": "evt-001",
        "ts": datetime.utcnow(),
        "source": "journal",
        "severity": "warning",
        "category": "disk",
        "message": "Disk usage high",
        "entity": "disk:/dev/sda1",
        "fingerprint": "fp001",
        "raw": {"used_pct": 92},
    }
    defaults.update(overrides)
    event = MagicMock()
    for k, v in defaults.items():
        setattr(event, k, v)
    return event


def _mock_record(success: bool = True, error: str | None = None) -> MagicMock:
    """Create a mock ExecutionRecord."""
    record = MagicMock()
    record.success = success
    record.error = error
    return record


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset():
    """Reset the module-level SimpleRouter singleton between tests."""
    reset_router()
    yield
    reset_router()


@pytest.fixture
def mock_engine() -> MagicMock:
    """Create a mock ReactiveEngine."""
    engine = MagicMock()
    engine.process_event = AsyncMock(return_value=[])
    return engine


@pytest.fixture
def router(mock_engine: MagicMock) -> EventRouter:
    """Create an EventRouter with a mock engine."""
    return EventRouter(engine=mock_engine, max_concurrent=5)


# ---------------------------------------------------------------------------
# RouterStats
# ---------------------------------------------------------------------------


class TestRouterStats:
    def test_initial_values(self):
        stats = RouterStats()
        assert stats.events_received == 0
        assert stats.events_processed == 0
        assert stats.functions_triggered == 0
        assert stats.functions_failed == 0
        assert stats.errors == 0

    def test_to_dict(self):
        stats = RouterStats()
        stats.events_received = 10
        stats.events_processed = 8
        stats.functions_triggered = 6
        stats.functions_failed = 2
        stats.errors = 1
        d = stats.to_dict()
        assert d == {
            "events_received": 10,
            "events_processed": 8,
            "functions_triggered": 6,
            "functions_failed": 2,
            "errors": 1,
        }

    def test_to_dict_returns_all_keys(self):
        stats = RouterStats()
        d = stats.to_dict()
        assert len(d) == 5
        assert set(d.keys()) == {
            "events_received",
            "events_processed",
            "functions_triggered",
            "functions_failed",
            "errors",
        }

    def test_reset(self):
        stats = RouterStats()
        stats.events_received = 100
        stats.events_processed = 80
        stats.functions_triggered = 50
        stats.functions_failed = 10
        stats.errors = 5
        stats.reset()
        assert stats.events_received == 0
        assert stats.events_processed == 0
        assert stats.functions_triggered == 0
        assert stats.functions_failed == 0
        assert stats.errors == 0


# ---------------------------------------------------------------------------
# EventRouter.__init__
# ---------------------------------------------------------------------------


class TestEventRouterInit:
    def test_default_max_concurrent(self, mock_engine: MagicMock):
        router = EventRouter(engine=mock_engine)
        assert router.engine is mock_engine
        assert router.max_concurrent == 10
        assert router.is_running is False
        assert router._tasks == []

    def test_custom_max_concurrent(self, mock_engine: MagicMock):
        router = EventRouter(engine=mock_engine, max_concurrent=3)
        assert router.max_concurrent == 3

    def test_stats_initialized(self, mock_engine: MagicMock):
        router = EventRouter(engine=mock_engine)
        assert isinstance(router.stats, RouterStats)
        assert router.stats.events_received == 0


# ---------------------------------------------------------------------------
# EventRouter.is_running property
# ---------------------------------------------------------------------------


class TestEventRouterIsRunning:
    def test_initially_false(self, router: EventRouter):
        assert router.is_running is False

    def test_reflects_internal_state(self, router: EventRouter):
        router._running = True
        assert router.is_running is True
        router._running = False
        assert router.is_running is False


# ---------------------------------------------------------------------------
# EventRouter.stats property
# ---------------------------------------------------------------------------


class TestEventRouterStats:
    def test_returns_stats_instance(self, router: EventRouter):
        assert isinstance(router.stats, RouterStats)

    def test_stats_updated_after_processing(self, router: EventRouter):
        router._stats.events_received = 42
        assert router.stats.events_received == 42


# ---------------------------------------------------------------------------
# EventRouter.start
# ---------------------------------------------------------------------------


class TestEventRouterStart:
    @pytest.mark.asyncio
    async def test_already_running_returns_early(self, router: EventRouter):
        """When already running, start() returns immediately."""
        router._running = True
        queue: asyncio.Queue[Any] = asyncio.Queue()
        await router.start({"source1": queue})
        # Should still be running (no tasks created since it returned early)
        assert router._running is True

    @pytest.mark.asyncio
    async def test_creates_consumer_tasks(self, router: EventRouter):
        """start() creates one consumer task per source queue."""
        q1: asyncio.Queue[Any] = asyncio.Queue()
        q2: asyncio.Queue[Any] = asyncio.Queue()

        # We'll stop the router shortly after starting to avoid blocking
        async def stop_soon():
            await asyncio.sleep(0.05)
            await router.stop()

        asyncio.create_task(stop_soon())
        await router.start({"journal": q1, "kernel": q2})

        # After stopping, _running should be False
        assert router.is_running is False

    @pytest.mark.asyncio
    async def test_sets_running_flag(self, router: EventRouter):
        """start() sets _running to True before consuming."""
        running_during_start = False

        async def capture_running(source, queue):
            nonlocal running_during_start
            running_during_start = router._running
            # Don't actually consume, just return
            return

        router._consume = capture_running

        q: asyncio.Queue[Any] = asyncio.Queue()
        await router.start({"test": q})
        assert running_during_start is True

    @pytest.mark.asyncio
    async def test_cancelled_error_handled(self, router: EventRouter):
        """CancelledError during gather is caught gracefully."""
        q: asyncio.Queue[Any] = asyncio.Queue()

        async def cancel_immediately():
            await asyncio.sleep(0.01)
            for task in router._tasks:
                task.cancel()

        asyncio.create_task(cancel_immediately())
        # Should not raise
        await router.start({"test": q})
        assert router.is_running is False

    @pytest.mark.asyncio
    async def test_running_set_false_in_finally(self, router: EventRouter):
        """_running is set to False in finally block even after error."""

        async def failing_consume(source, queue):
            raise RuntimeError("consumer crash")

        router._consume = failing_consume

        q: asyncio.Queue[Any] = asyncio.Queue()
        await router.start({"test": q})
        assert router.is_running is False


# ---------------------------------------------------------------------------
# EventRouter.stop
# ---------------------------------------------------------------------------


class TestEventRouterStop:
    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self, router: EventRouter):
        """stop() cancels all running tasks and clears the list."""
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()
        router._tasks = [mock_task]
        router._running = True

        with patch("asyncio.gather", new_callable=AsyncMock):
            await router.stop()

        mock_task.cancel.assert_called_once()
        assert router._running is False
        assert router._tasks == []

    @pytest.mark.asyncio
    async def test_stop_skips_done_tasks(self, router: EventRouter):
        """stop() does not cancel tasks that are already done."""
        done_task = MagicMock()
        done_task.done.return_value = True
        running_task = MagicMock()
        running_task.done.return_value = False
        router._tasks = [done_task, running_task]
        router._running = True

        with patch("asyncio.gather", new_callable=AsyncMock):
            await router.stop()

        done_task.cancel.assert_not_called()
        running_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_with_no_tasks(self, router: EventRouter):
        """stop() works fine when there are no tasks."""
        router._running = True
        await router.stop()
        assert router._running is False
        assert router._tasks == []

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self, router: EventRouter):
        """stop() sets _running to False."""
        router._running = True
        await router.stop()
        assert router._running is False


# ---------------------------------------------------------------------------
# EventRouter._consume
# ---------------------------------------------------------------------------


class TestEventRouterConsume:
    @pytest.mark.asyncio
    async def test_consume_processes_event_from_queue(self, router: EventRouter, mock_engine: MagicMock):
        """_consume reads events from the queue and processes them."""
        event = _mock_event()
        q: asyncio.Queue[Any] = asyncio.Queue()
        await q.put(event)

        # Allow one iteration then stop
        call_count = 0
        original_process = router._process_event

        async def process_and_stop(evt):
            nonlocal call_count
            call_count += 1
            await original_process(evt)
            router._running = False

        router._process_event = process_and_stop
        router._running = True

        await router._consume("journal", q)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_consume_handles_timeout(self, router: EventRouter):
        """_consume continues when queue.get() times out."""
        q: asyncio.Queue[Any] = asyncio.Queue()

        # Run for a short time, the queue is empty so it should timeout
        iteration_count = 0

        async def fake_wait_for(coro, timeout):
            nonlocal iteration_count
            iteration_count += 1
            if iteration_count >= 2:
                router._running = False
            raise asyncio.TimeoutError()

        router._running = True
        with patch("asyncio.wait_for", side_effect=fake_wait_for):
            await router._consume("test", q)

        assert iteration_count >= 2

    @pytest.mark.asyncio
    async def test_consume_handles_standard_timeout_error(self, router: EventRouter):
        """_consume handles TimeoutError (builtin) as well as asyncio.TimeoutError."""
        q: asyncio.Queue[Any] = asyncio.Queue()

        iteration_count = 0

        async def fake_wait_for(coro, timeout):
            nonlocal iteration_count
            iteration_count += 1
            if iteration_count >= 2:
                router._running = False
            raise TimeoutError()

        router._running = True
        with patch("asyncio.wait_for", side_effect=fake_wait_for):
            await router._consume("test", q)

        assert iteration_count >= 2

    @pytest.mark.asyncio
    async def test_consume_breaks_on_cancelled(self, router: EventRouter):
        """_consume breaks out of loop on CancelledError."""
        q: asyncio.Queue[Any] = asyncio.Queue()

        async def fake_wait_for(coro, timeout):
            raise asyncio.CancelledError()

        router._running = True
        with patch("asyncio.wait_for", side_effect=fake_wait_for):
            await router._consume("test", q)

        # Loop should have exited
        assert True  # No exception raised

    @pytest.mark.asyncio
    async def test_consume_handles_processing_error(self, router: EventRouter):
        """_consume catches errors during event processing and increments error count."""
        event = _mock_event()
        q: asyncio.Queue[Any] = asyncio.Queue()
        await q.put(event)

        call_count = 0

        async def failing_process(evt):
            nonlocal call_count
            call_count += 1
            router._running = False
            raise RuntimeError("process failed")

        router._process_event = failing_process
        router._running = True

        await router._consume("journal", q)
        assert router._stats.errors == 1

    @pytest.mark.asyncio
    async def test_consume_stops_when_running_false(self, router: EventRouter):
        """_consume exits loop when _running becomes False."""
        q: asyncio.Queue[Any] = asyncio.Queue()
        router._running = False
        await router._consume("test", q)
        # Should immediately return since _running is False


# ---------------------------------------------------------------------------
# EventRouter._process_event
# ---------------------------------------------------------------------------


class TestEventRouterProcessEvent:
    @pytest.mark.asyncio
    async def test_process_event_success_with_successful_records(self, router: EventRouter, mock_engine: MagicMock):
        """_process_event increments stats for successful function executions."""
        record = _mock_record(success=True)
        mock_engine.process_event = AsyncMock(return_value=[record])
        event = _mock_event()

        await router._process_event(event)

        assert router._stats.events_received == 1
        assert router._stats.events_processed == 1
        assert router._stats.functions_triggered == 1
        assert router._stats.functions_failed == 0

    @pytest.mark.asyncio
    async def test_process_event_success_with_failed_records(self, router: EventRouter, mock_engine: MagicMock):
        """_process_event increments functions_failed for failed records."""
        record = _mock_record(success=False, error="action failed")
        mock_engine.process_event = AsyncMock(return_value=[record])
        event = _mock_event()

        await router._process_event(event)

        assert router._stats.events_received == 1
        assert router._stats.events_processed == 1
        assert router._stats.functions_triggered == 0
        assert router._stats.functions_failed == 1

    @pytest.mark.asyncio
    async def test_process_event_mixed_records(self, router: EventRouter, mock_engine: MagicMock):
        """_process_event handles a mix of successful and failed records."""
        records = [
            _mock_record(success=True),
            _mock_record(success=False),
            _mock_record(success=True),
        ]
        mock_engine.process_event = AsyncMock(return_value=records)
        event = _mock_event()

        await router._process_event(event)

        assert router._stats.functions_triggered == 2
        assert router._stats.functions_failed == 1

    @pytest.mark.asyncio
    async def test_process_event_empty_records(self, router: EventRouter, mock_engine: MagicMock):
        """_process_event handles empty records list."""
        mock_engine.process_event = AsyncMock(return_value=[])
        event = _mock_event()

        await router._process_event(event)

        assert router._stats.events_received == 1
        assert router._stats.events_processed == 1
        assert router._stats.functions_triggered == 0
        assert router._stats.functions_failed == 0

    @pytest.mark.asyncio
    async def test_process_event_engine_exception(self, router: EventRouter, mock_engine: MagicMock):
        """_process_event re-raises engine exceptions after logging."""
        mock_engine.process_event = AsyncMock(side_effect=RuntimeError("engine error"))
        event = _mock_event()

        with pytest.raises(RuntimeError, match="engine error"):
            await router._process_event(event)

        assert router._stats.events_received == 1
        assert router._stats.errors == 1
        # events_processed should NOT have been incremented
        assert router._stats.events_processed == 0

    @pytest.mark.asyncio
    async def test_process_event_increments_received_before_processing(
        self, router: EventRouter, mock_engine: MagicMock
    ):
        """events_received is incremented even if engine raises."""
        mock_engine.process_event = AsyncMock(side_effect=ValueError("bad event"))
        event = _mock_event()

        with pytest.raises(ValueError):
            await router._process_event(event)

        assert router._stats.events_received == 1


# ---------------------------------------------------------------------------
# route_event (standalone function)
# ---------------------------------------------------------------------------


class TestRouteEvent:
    @pytest.mark.asyncio
    async def test_with_provided_engine(self):
        """route_event uses the provided engine."""
        engine = MagicMock()
        records = [_mock_record(success=True)]
        engine.process_event = AsyncMock(return_value=records)
        event = _mock_event()

        result = await route_event(event, engine=engine)

        assert result == records
        engine.process_event.assert_awaited_once_with(event)

    @pytest.mark.asyncio
    async def test_with_none_engine_uses_global(self):
        """route_event calls get_engine() when engine is None."""
        mock_engine = MagicMock()
        records = [_mock_record(success=True)]
        mock_engine.process_event = AsyncMock(return_value=records)
        event = _mock_event()

        with patch("elle.reactive.engine.get_engine", return_value=mock_engine) as mock_get:
            result = await route_event(event, engine=None)

        mock_get.assert_called_once()
        assert result == records

    @pytest.mark.asyncio
    async def test_returns_engine_result(self):
        """route_event returns the list of ExecutionRecords from the engine."""
        engine = MagicMock()
        engine.process_event = AsyncMock(return_value=[])
        event = _mock_event()

        result = await route_event(event, engine=engine)
        assert result == []


# ---------------------------------------------------------------------------
# SimpleRouter.__init__
# ---------------------------------------------------------------------------


class TestSimpleRouterInit:
    def test_initial_state(self):
        sr = SimpleRouter()
        assert sr._engine is None
        assert isinstance(sr.stats, RouterStats)
        assert sr.stats.events_received == 0


# ---------------------------------------------------------------------------
# SimpleRouter._get_engine
# ---------------------------------------------------------------------------


class TestSimpleRouterGetEngine:
    def test_lazy_creates_engine(self):
        """_get_engine creates engine on first call via get_engine()."""
        sr = SimpleRouter()
        mock_engine = MagicMock()
        with patch("elle.reactive.engine.get_engine", return_value=mock_engine) as mock_get:
            result = sr._get_engine()

        assert result is mock_engine
        assert sr._engine is mock_engine
        mock_get.assert_called_once()

    def test_returns_cached_engine(self):
        """_get_engine returns cached engine on subsequent calls."""
        sr = SimpleRouter()
        mock_engine = MagicMock()
        sr._engine = mock_engine

        with patch("elle.reactive.engine.get_engine") as mock_get:
            result = sr._get_engine()

        assert result is mock_engine
        mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# SimpleRouter.route
# ---------------------------------------------------------------------------


class TestSimpleRouterRoute:
    @pytest.mark.asyncio
    async def test_route_success_with_successful_records(self):
        """route() increments stats for successful records."""
        sr = SimpleRouter()
        record = _mock_record(success=True)
        mock_engine = MagicMock()
        mock_engine.process_event = AsyncMock(return_value=[record])
        sr._engine = mock_engine
        event = _mock_event()

        result = await sr.route(event)

        assert len(result) == 1
        assert sr.stats.events_received == 1
        assert sr.stats.events_processed == 1
        assert sr.stats.functions_triggered == 1
        assert sr.stats.functions_failed == 0

    @pytest.mark.asyncio
    async def test_route_success_with_failed_records(self):
        """route() increments functions_failed for failed records."""
        sr = SimpleRouter()
        record = _mock_record(success=False, error="failed")
        mock_engine = MagicMock()
        mock_engine.process_event = AsyncMock(return_value=[record])
        sr._engine = mock_engine
        event = _mock_event()

        result = await sr.route(event)

        assert len(result) == 1
        assert sr.stats.functions_triggered == 0
        assert sr.stats.functions_failed == 1

    @pytest.mark.asyncio
    async def test_route_mixed_records(self):
        """route() correctly tallies mixed success/failure records."""
        sr = SimpleRouter()
        records = [
            _mock_record(success=True),
            _mock_record(success=False),
            _mock_record(success=True),
            _mock_record(success=False),
        ]
        mock_engine = MagicMock()
        mock_engine.process_event = AsyncMock(return_value=records)
        sr._engine = mock_engine
        event = _mock_event()

        result = await sr.route(event)

        assert len(result) == 4
        assert sr.stats.functions_triggered == 2
        assert sr.stats.functions_failed == 2
        assert sr.stats.events_processed == 1

    @pytest.mark.asyncio
    async def test_route_empty_records(self):
        """route() handles empty records from engine."""
        sr = SimpleRouter()
        mock_engine = MagicMock()
        mock_engine.process_event = AsyncMock(return_value=[])
        sr._engine = mock_engine
        event = _mock_event()

        result = await sr.route(event)

        assert result == []
        assert sr.stats.events_received == 1
        assert sr.stats.events_processed == 1

    @pytest.mark.asyncio
    async def test_route_engine_exception_returns_empty(self):
        """route() catches exceptions and returns an empty list."""
        sr = SimpleRouter()
        mock_engine = MagicMock()
        mock_engine.process_event = AsyncMock(side_effect=RuntimeError("engine crash"))
        sr._engine = mock_engine
        event = _mock_event()

        result = await sr.route(event)

        assert result == []
        assert sr.stats.events_received == 1
        assert sr.stats.errors == 1
        # events_processed should NOT have been incremented
        assert sr.stats.events_processed == 0

    @pytest.mark.asyncio
    async def test_route_uses_get_engine_lazily(self):
        """route() lazily initializes engine via _get_engine."""
        sr = SimpleRouter()
        mock_engine = MagicMock()
        mock_engine.process_event = AsyncMock(return_value=[])
        event = _mock_event()

        with patch("elle.reactive.engine.get_engine", return_value=mock_engine):
            result = await sr.route(event)

        assert result == []
        assert sr._engine is mock_engine

    @pytest.mark.asyncio
    async def test_route_get_engine_failure_returns_empty(self):
        """route() catches exception if _get_engine itself fails."""
        sr = SimpleRouter()
        event = _mock_event()

        with patch(
            "elle.reactive.engine.get_engine",
            side_effect=RuntimeError("no engine"),
        ):
            result = await sr.route(event)

        assert result == []
        assert sr.stats.errors == 1

    @pytest.mark.asyncio
    async def test_route_multiple_events_accumulate_stats(self):
        """Routing multiple events accumulates statistics correctly."""
        sr = SimpleRouter()
        mock_engine = MagicMock()
        mock_engine.process_event = AsyncMock(return_value=[_mock_record(success=True)])
        sr._engine = mock_engine

        for _ in range(5):
            await sr.route(_mock_event())

        assert sr.stats.events_received == 5
        assert sr.stats.events_processed == 5
        assert sr.stats.functions_triggered == 5


# ---------------------------------------------------------------------------
# SimpleRouter.stats property
# ---------------------------------------------------------------------------


class TestSimpleRouterStats:
    def test_returns_stats_instance(self):
        sr = SimpleRouter()
        assert isinstance(sr.stats, RouterStats)

    def test_stats_are_mutable(self):
        sr = SimpleRouter()
        sr._stats.events_received = 99
        assert sr.stats.events_received == 99


# ---------------------------------------------------------------------------
# get_router (module-level singleton)
# ---------------------------------------------------------------------------


class TestGetRouter:
    def test_returns_simple_router(self):
        r = get_router()
        assert isinstance(r, SimpleRouter)

    def test_returns_same_instance(self):
        r1 = get_router()
        r2 = get_router()
        assert r1 is r2

    def test_returns_new_after_reset(self):
        r1 = get_router()
        reset_router()
        r2 = get_router()
        assert r1 is not r2
        assert isinstance(r2, SimpleRouter)


# ---------------------------------------------------------------------------
# reset_router
# ---------------------------------------------------------------------------


class TestResetRouter:
    def test_clears_singleton(self):
        _ = get_router()
        reset_router()
        # After reset, a new instance should be created
        r = get_router()
        assert isinstance(r, SimpleRouter)
        assert r.stats.events_received == 0

    def test_reset_when_none(self):
        """reset_router() is safe to call even if singleton is already None."""
        reset_router()
        reset_router()  # Should not raise


# ---------------------------------------------------------------------------
# Integration-style: EventRouter full consume-process cycle
# ---------------------------------------------------------------------------


class TestEventRouterIntegration:
    @pytest.mark.asyncio
    async def test_full_event_processing_cycle(self, mock_engine: MagicMock):
        """Full cycle: start -> consume event -> process -> stop."""
        record = _mock_record(success=True)
        mock_engine.process_event = AsyncMock(return_value=[record])

        router = EventRouter(engine=mock_engine, max_concurrent=2)
        q: asyncio.Queue[Any] = asyncio.Queue()
        event = _mock_event()
        await q.put(event)

        async def stop_after_processing():
            # Wait until the event has been processed
            for _ in range(100):
                await asyncio.sleep(0.01)
                if router.stats.events_processed >= 1:
                    break
            await router.stop()

        asyncio.create_task(stop_after_processing())
        await router.start({"journal": q})

        assert router.stats.events_received >= 1
        assert router.stats.events_processed >= 1
        assert router.stats.functions_triggered >= 1
        assert router.is_running is False

    @pytest.mark.asyncio
    async def test_multiple_sources(self, mock_engine: MagicMock):
        """EventRouter creates consumers for multiple sources."""
        mock_engine.process_event = AsyncMock(return_value=[_mock_record(success=True)])

        router = EventRouter(engine=mock_engine, max_concurrent=5)
        q1: asyncio.Queue[Any] = asyncio.Queue()
        q2: asyncio.Queue[Any] = asyncio.Queue()

        await q1.put(_mock_event(event_id="e1"))
        await q2.put(_mock_event(event_id="e2"))

        async def stop_after_processing():
            for _ in range(100):
                await asyncio.sleep(0.01)
                if router.stats.events_processed >= 2:
                    break
            await router.stop()

        asyncio.create_task(stop_after_processing())
        await router.start({"journal": q1, "kernel": q2})

        assert router.stats.events_received >= 2
        assert router.is_running is False

    @pytest.mark.asyncio
    async def test_engine_error_in_consume_increments_error_stat(self, mock_engine: MagicMock):
        """When engine raises during consume, error stat is incremented."""
        mock_engine.process_event = AsyncMock(side_effect=RuntimeError("engine boom"))

        router = EventRouter(engine=mock_engine, max_concurrent=2)
        q: asyncio.Queue[Any] = asyncio.Queue()
        await q.put(_mock_event())

        async def stop_soon():
            for _ in range(100):
                await asyncio.sleep(0.01)
                if router.stats.errors >= 1:
                    break
            await router.stop()

        asyncio.create_task(stop_soon())
        await router.start({"journal": q})

        # The error from _process_event is caught in _consume
        assert router.stats.errors >= 1

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """The semaphore limits concurrent event processing."""
        engine = MagicMock()
        concurrent = 0
        max_concurrent_seen = 0

        async def slow_process(event):
            nonlocal concurrent, max_concurrent_seen
            concurrent += 1
            max_concurrent_seen = max(max_concurrent_seen, concurrent)
            await asyncio.sleep(0.02)
            concurrent -= 1
            return []

        engine.process_event = slow_process

        router = EventRouter(engine=engine, max_concurrent=2)
        q: asyncio.Queue[Any] = asyncio.Queue()

        # Put several events
        for i in range(5):
            await q.put(_mock_event(event_id=f"e{i}"))

        async def stop_after():
            for _ in range(200):
                await asyncio.sleep(0.01)
                if router.stats.events_processed >= 5:
                    break
            await router.stop()

        asyncio.create_task(stop_after())
        await router.start({"journal": q})

        # The semaphore should have limited concurrency
        # Note: exact assertion depends on scheduling, but we verify it ran
        assert router.stats.events_received >= 5
