from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.reactive.models import (
    ActionSpec,
    ExecutionRecord,
    ReactiveFunction,
    ScheduleTrigger,
    Trigger,
)
from elle.reactive.scheduler import (
    ReactiveScheduler,
    SchedulerStats,
    get_scheduler,
    reset_scheduler,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sched_func(name: str = "sched-func", cron: str = "*/5 * * * *") -> ReactiveFunction:
    return ReactiveFunction(
        name=name,
        description="Scheduled function",
        trigger=Trigger(type="schedule", schedule=ScheduleTrigger(cron=cron)),
        actions=(ActionSpec(capability="docker.prune", input={"all": True}),),
    )


def _event_func(name: str = "event-func") -> ReactiveFunction:
    return ReactiveFunction(
        name=name,
        trigger=Trigger(type="event"),
        actions=(ActionSpec(capability="notify.send"),),
    )


def _mock_record(success: bool = True, error: str | None = None) -> ExecutionRecord:
    return ExecutionRecord(
        function_id="fid",
        function_name="test",
        triggered_at=datetime.utcnow(),
        condition_result=True,
        success=success,
        error=error,
    )


# A fake croniter to avoid requiring the real package.
class _FakeCroniter:
    def __init__(self, expr: str, start: datetime | None = None):
        self._expr = expr
        self._start = start or datetime.now(timezone.utc)

    def get_prev(self, ret_type: type = datetime) -> datetime:
        return self._start

    def get_next(self, ret_type: type = datetime) -> datetime:
        return self._start


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset():
    reset_scheduler()
    yield
    reset_scheduler()


@pytest.fixture
def engine():
    eng = MagicMock()
    eng.process_scheduled = AsyncMock(return_value=_mock_record(success=True))
    return eng


@pytest.fixture
def scheduler(engine):
    return ReactiveScheduler(engine=engine, check_interval=0.1)


# ---------------------------------------------------------------------------
# SchedulerStats
# ---------------------------------------------------------------------------

class TestSchedulerStats:
    def test_initial_values(self):
        s = SchedulerStats()
        assert s.executions_started == 0
        assert s.executions_succeeded == 0
        assert s.executions_failed == 0
        assert s.executions_skipped == 0

    def test_to_dict(self):
        s = SchedulerStats()
        s.executions_started = 5
        d = s.to_dict()
        assert d["executions_started"] == 5
        assert len(d) == 4

    def test_reset(self):
        s = SchedulerStats()
        s.executions_started = 10
        s.executions_failed = 3
        s.reset()
        assert s.executions_started == 0
        assert s.executions_failed == 0


# ---------------------------------------------------------------------------
# Scheduler init / properties
# ---------------------------------------------------------------------------

class TestSchedulerInit:
    def test_defaults(self):
        s = ReactiveScheduler()
        assert s.check_interval == 60.0
        assert s.is_running is False
        assert s.scheduled_count == 0

    def test_custom_interval(self):
        s = ReactiveScheduler(check_interval=5.0)
        assert s.check_interval == 5.0

    def test_stats_property(self):
        s = ReactiveScheduler()
        assert isinstance(s.stats, SchedulerStats)


# ---------------------------------------------------------------------------
# Engine property
# ---------------------------------------------------------------------------

class TestEngineProperty:
    def test_provided(self, engine):
        s = ReactiveScheduler(engine=engine)
        assert s.engine is engine

    def test_lazy_init(self):
        sentinel = MagicMock()
        with patch("elle.reactive.engine.get_engine", return_value=sentinel):
            s = ReactiveScheduler(engine=None)
            assert s.engine is sentinel


# ---------------------------------------------------------------------------
# schedule / unschedule (using fake croniter)
# ---------------------------------------------------------------------------

class TestScheduleUnschedule:
    @patch("elle.reactive.scheduler.croniter", _FakeCroniter)
    @patch("elle.reactive.scheduler.HAS_CRONITER", True)
    def test_schedule_function(self, scheduler):
        func = _sched_func()
        scheduler.schedule_function(func)
        assert scheduler.scheduled_count == 1

    def test_schedule_without_schedule_trigger(self, scheduler):
        func = _event_func()
        scheduler.schedule_function(func)
        assert scheduler.scheduled_count == 0

    @patch("elle.reactive.scheduler.croniter", _FakeCroniter)
    @patch("elle.reactive.scheduler.HAS_CRONITER", True)
    def test_unschedule_function(self, scheduler):
        func = _sched_func()
        scheduler.schedule_function(func)
        scheduler.unschedule_function(func.id)
        assert scheduler.scheduled_count == 0

    def test_unschedule_nonexistent(self, scheduler):
        scheduler.unschedule_function("nonexistent")
        assert scheduler.scheduled_count == 0

    @patch("elle.reactive.scheduler.croniter", _FakeCroniter)
    @patch("elle.reactive.scheduler.HAS_CRONITER", True)
    def test_unschedule_cancels_task(self, scheduler):
        func = _sched_func()
        scheduler.schedule_function(func)
        mock_task = MagicMock()
        mock_task.done.return_value = False
        scheduler._tasks[func.id] = mock_task
        scheduler.unschedule_function(func.id)
        mock_task.cancel.assert_called_once()

    def test_invalid_cron_not_added(self, scheduler):
        """Invalid cron should be handled gracefully."""
        # When croniter is None (not installed), _add_schedule catches the error
        func = _sched_func(cron="BOGUS")
        scheduler.schedule_function(func)
        assert scheduler.scheduled_count == 0


# ---------------------------------------------------------------------------
# _should_run
# ---------------------------------------------------------------------------

class TestShouldRun:
    def test_not_scheduled(self, scheduler):
        func = _sched_func()
        assert scheduler._should_run(func, datetime.now(timezone.utc)) is False

    @patch("elle.reactive.scheduler.croniter", _FakeCroniter)
    @patch("elle.reactive.scheduler.HAS_CRONITER", True)
    def test_already_running(self, scheduler):
        func = _sched_func()
        scheduler.schedule_function(func)
        mock_task = MagicMock()
        mock_task.done.return_value = False
        scheduler._tasks[func.id] = mock_task
        assert scheduler._should_run(func, datetime.now(timezone.utc)) is False


# ---------------------------------------------------------------------------
# _run_function
# ---------------------------------------------------------------------------

class TestRunFunction:
    async def test_success(self, scheduler, engine):
        func = _sched_func()
        await scheduler._run_function(func)
        engine.process_scheduled.assert_awaited_once_with(func)
        assert scheduler.stats.executions_started == 1
        assert scheduler.stats.executions_succeeded == 1

    async def test_failure(self, scheduler, engine):
        engine.process_scheduled = AsyncMock(return_value=_mock_record(success=False, error="boom"))
        func = _sched_func()
        await scheduler._run_function(func)
        assert scheduler.stats.executions_failed == 1

    async def test_skipped(self, scheduler, engine):
        engine.process_scheduled = AsyncMock(return_value=None)
        func = _sched_func()
        await scheduler._run_function(func)
        assert scheduler.stats.executions_skipped == 1

    async def test_exception(self, scheduler, engine):
        engine.process_scheduled = AsyncMock(side_effect=RuntimeError("oops"))
        func = _sched_func()
        await scheduler._run_function(func)
        assert scheduler.stats.executions_failed == 1


# ---------------------------------------------------------------------------
# _check_and_schedule
# ---------------------------------------------------------------------------

class TestCheckAndSchedule:
    @patch("elle.reactive.scheduler.croniter", _FakeCroniter)
    @patch("elle.reactive.scheduler.HAS_CRONITER", True)
    @patch("elle.reactive.scheduler.list_enabled_with_schedule_trigger")
    async def test_adds_new_functions(self, mock_list, scheduler):
        func = _sched_func()
        mock_list.return_value = [func]
        await scheduler._check_and_schedule()
        assert scheduler.scheduled_count == 1

    @patch("elle.reactive.scheduler.croniter", _FakeCroniter)
    @patch("elle.reactive.scheduler.HAS_CRONITER", True)
    @patch("elle.reactive.scheduler.list_enabled_with_schedule_trigger")
    async def test_removes_disabled_functions(self, mock_list, scheduler):
        func = _sched_func()
        mock_list.return_value = [func]
        await scheduler._check_and_schedule()
        assert scheduler.scheduled_count == 1
        mock_list.return_value = []
        await scheduler._check_and_schedule()
        assert scheduler.scheduled_count == 0


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

class TestStartStop:
    async def test_stop(self, scheduler):
        scheduler._running = True
        await scheduler.stop()
        assert scheduler.is_running is False

    async def test_start_without_croniter(self, scheduler):
        with patch("elle.reactive.scheduler.HAS_CRONITER", False):
            await scheduler.start()
            assert scheduler.is_running is False

    async def test_start_already_running_returns_early(self, scheduler):
        scheduler._running = True
        with patch("elle.reactive.scheduler.HAS_CRONITER", True):
            await scheduler.start()
        # It should remain running (early return, no-op)
        assert scheduler._running is True

    async def test_cleanup_tasks(self, scheduler):
        task1 = MagicMock()
        task1.done.return_value = False
        task1.cancel = MagicMock()
        scheduler._tasks["t1"] = task1
        scheduler._schedules["t1"] = MagicMock()
        with patch("asyncio.gather", new_callable=AsyncMock):
            await scheduler._cleanup_tasks()
        assert len(scheduler._tasks) == 0
        assert len(scheduler._schedules) == 0
        task1.cancel.assert_called_once()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_returns_same(self):
        assert get_scheduler() is get_scheduler()

    def test_reset_clears(self):
        s1 = get_scheduler()
        reset_scheduler()
        assert get_scheduler() is not s1
