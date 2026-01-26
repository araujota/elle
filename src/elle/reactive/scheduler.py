"""Reactive Scheduler - cron-based scheduling for reactive functions.

Schedules and executes reactive functions with schedule triggers
based on cron expressions.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

try:
    from croniter import croniter

    HAS_CRONITER = True
except ImportError:
    HAS_CRONITER = False
    croniter = None  # type: ignore

from elle.reactive.models import ReactiveFunction
from elle.reactive.store import list_enabled_with_schedule_trigger

if TYPE_CHECKING:
    from elle.reactive.engine import ReactiveEngine

logger = logging.getLogger(__name__)


class ReactiveScheduler:
    """Scheduler for cron-triggered reactive functions.

    Manages scheduling and execution of reactive functions that
    have cron schedule triggers.

    Usage:
        scheduler = ReactiveScheduler(engine)
        await scheduler.start()  # Runs until stop() is called
    """

    def __init__(
        self,
        engine: ReactiveEngine | None = None,
        check_interval: float = 60.0,
    ) -> None:
        """Initialize the scheduler.

        Args:
            engine: ReactiveEngine for executing functions.
            check_interval: How often to check for functions to run (seconds).
        """
        self._engine = engine
        self.check_interval = check_interval
        self._running = False
        self._tasks: dict[str, asyncio.Task] = {}
        self._schedules: dict[str, croniter] = {}
        self._stats = SchedulerStats()

    @property
    def engine(self) -> ReactiveEngine:
        """Get the reactive engine, initializing lazily if needed."""
        if self._engine is None:
            from elle.reactive.engine import get_engine

            self._engine = get_engine()
        return self._engine

    async def start(self) -> None:
        """Start the scheduler.

        Loads scheduled functions and begins executing them
        according to their cron expressions. Blocks until stop() is called.
        """
        if not HAS_CRONITER:
            logger.error("croniter package not installed, scheduler disabled")
            return

        if self._running:
            logger.warning("ReactiveScheduler already running")
            return

        self._running = True
        logger.info("Starting ReactiveScheduler")

        try:
            while self._running:
                await self._check_and_schedule()
                await asyncio.sleep(self.check_interval)
        except asyncio.CancelledError:
            logger.info("ReactiveScheduler cancelled")
        finally:
            self._running = False
            await self._cleanup_tasks()

    async def stop(self) -> None:
        """Stop the scheduler and cancel all scheduled tasks."""
        self._running = False
        await self._cleanup_tasks()
        logger.info("ReactiveScheduler stopped")

    async def _cleanup_tasks(self) -> None:
        """Cancel and clean up all running tasks."""
        for task in self._tasks.values():
            if not task.done():
                task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

        self._tasks.clear()
        self._schedules.clear()

    async def _check_and_schedule(self) -> None:
        """Check for functions to schedule and manage their execution."""
        # Get all enabled scheduled functions
        functions = list_enabled_with_schedule_trigger()
        current_function_ids = {f.id for f in functions}

        # Remove schedules for functions that no longer exist or are disabled
        removed_ids = set(self._schedules.keys()) - current_function_ids
        for func_id in removed_ids:
            if func_id in self._tasks:
                self._tasks[func_id].cancel()
                del self._tasks[func_id]
            if func_id in self._schedules:
                del self._schedules[func_id]
            logger.debug(f"Removed schedule for function {func_id}")

        # Add or update schedules for current functions
        for func in functions:
            if func.id not in self._schedules:
                self._add_schedule(func)

        # Check which functions should run now
        now = datetime.now(UTC)
        for func in functions:
            if self._should_run(func, now):
                await self._run_function(func)

    def _add_schedule(self, func: ReactiveFunction) -> None:
        """Add a new schedule for a function.

        Args:
            func: The reactive function to schedule.
        """
        if not func.trigger.schedule:
            return

        try:
            # Parse cron expression
            cron_expr = func.trigger.schedule.cron
            # Note: timezone support would require pytz integration
            # tz = func.trigger.schedule.timezone

            # Create croniter with current time
            now = datetime.now(UTC)
            cron = croniter(cron_expr, now)
            self._schedules[func.id] = cron

            logger.debug(f"Scheduled function {func.name} with cron: {cron_expr}")

        except Exception as e:
            logger.error(f"Failed to schedule function {func.name}: {e}")

    def _should_run(self, func: ReactiveFunction, now: datetime) -> bool:
        """Check if a function should run now.

        Args:
            func: The reactive function.
            now: Current time.

        Returns:
            True if the function should execute.
        """
        if func.id not in self._schedules:
            return False

        # Skip if already running
        if func.id in self._tasks and not self._tasks[func.id].done():
            return False

        try:
            cron = self._schedules[func.id]

            # Get the last scheduled time
            prev_time = cron.get_prev(datetime)
            cron.get_next(datetime)  # Reset iterator

            # Check if we're within the check interval of the scheduled time
            time_since_scheduled = (now - prev_time.replace(tzinfo=UTC)).total_seconds()

            # Run if we're within the check interval of the scheduled time
            if 0 <= time_since_scheduled < self.check_interval:
                return True

        except Exception as e:
            logger.error(f"Error checking schedule for {func.name}: {e}")

        return False

    async def _run_function(self, func: ReactiveFunction) -> None:
        """Execute a scheduled function.

        Args:
            func: The reactive function to execute.
        """
        logger.info(f"Executing scheduled function: {func.name}")
        self._stats.executions_started += 1

        try:
            record = await self.engine.process_scheduled(func)

            if record:
                if record.success:
                    self._stats.executions_succeeded += 1
                    logger.info(f"Scheduled function {func.name} completed successfully")
                else:
                    self._stats.executions_failed += 1
                    logger.warning(f"Scheduled function {func.name} failed: {record.error}")
            else:
                # Function was rate-limited or condition not met
                self._stats.executions_skipped += 1
                logger.debug(f"Scheduled function {func.name} skipped (rate-limited or condition not met)")

        except Exception as e:
            self._stats.executions_failed += 1
            logger.exception(f"Error executing scheduled function {func.name}: {e}")

    def schedule_function(self, func: ReactiveFunction) -> None:
        """Manually add a function to the schedule.

        Args:
            func: The reactive function to schedule.
        """
        self._add_schedule(func)

    def unschedule_function(self, function_id: str) -> None:
        """Remove a function from the schedule.

        Args:
            function_id: The function ID to remove.
        """
        if function_id in self._tasks:
            self._tasks[function_id].cancel()
            del self._tasks[function_id]
        if function_id in self._schedules:
            del self._schedules[function_id]

    @property
    def is_running(self) -> bool:
        """Check if the scheduler is running."""
        return self._running

    @property
    def stats(self) -> SchedulerStats:
        """Get current scheduler statistics."""
        return self._stats

    @property
    def scheduled_count(self) -> int:
        """Get the number of scheduled functions."""
        return len(self._schedules)


class SchedulerStats:
    """Statistics for the scheduler."""

    def __init__(self) -> None:
        """Initialize statistics counters."""
        self.executions_started = 0
        self.executions_succeeded = 0
        self.executions_failed = 0
        self.executions_skipped = 0

    def to_dict(self) -> dict[str, int]:
        """Convert stats to a dictionary."""
        return {
            "executions_started": self.executions_started,
            "executions_succeeded": self.executions_succeeded,
            "executions_failed": self.executions_failed,
            "executions_skipped": self.executions_skipped,
        }

    def reset(self) -> None:
        """Reset all statistics to zero."""
        self.executions_started = 0
        self.executions_succeeded = 0
        self.executions_failed = 0
        self.executions_skipped = 0


# =============================================================================
# Singleton
# =============================================================================

_scheduler: ReactiveScheduler | None = None


def get_scheduler() -> ReactiveScheduler:
    """Get the singleton ReactiveScheduler instance.

    Returns:
        The global ReactiveScheduler.
    """
    global _scheduler
    if _scheduler is None:
        _scheduler = ReactiveScheduler()
    return _scheduler


def reset_scheduler() -> None:
    """Reset the global scheduler instance (for testing)."""
    global _scheduler
    _scheduler = None
