"""Tests for agentic loop health monitoring and watchdog."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from elle.cli.agentic.health import (
    AbortReason,
    DEFAULT_CONFIG,
    IterationTracker,
    LLMCallTracker,
    LoopAbortedError,
    LoopHealthConfig,
    LoopHealthError,
    LoopHealthMonitor,
    LoopState,
    LoopStats,
    LoopTimeoutError,
    LoopTracker,
    StreamCompletionVerifier,
    ToolCallTracker,
    get_health_monitor,
    reset_health_monitor,
)


# =============================================================================
# LoopHealthConfig Tests
# =============================================================================


class TestLoopHealthConfig:
    """Tests for LoopHealthConfig defaults and construction."""

    def test_default_values(self):
        """Test that default configuration values are sensible."""
        config = LoopHealthConfig()
        assert config.max_duration_seconds == 300.0
        assert config.max_iterations == 10
        assert config.llm_timeout_seconds == 120.0
        assert config.tool_timeout_seconds == 60.0
        assert config.retrieval_timeout_seconds == 30.0
        assert config.stream_chunk_timeout_seconds == 30.0
        assert config.min_response_length == 10
        assert config.warn_at_iteration == 7

    def test_custom_values(self):
        """Test constructing config with custom values."""
        config = LoopHealthConfig(
            max_duration_seconds=600.0,
            max_iterations=20,
            llm_timeout_seconds=180.0,
            tool_timeout_seconds=90.0,
            warn_at_iteration=15,
        )
        assert config.max_duration_seconds == 600.0
        assert config.max_iterations == 20
        assert config.llm_timeout_seconds == 180.0
        assert config.tool_timeout_seconds == 90.0
        assert config.warn_at_iteration == 15

    def test_config_is_frozen(self):
        """Test that config is immutable."""
        config = LoopHealthConfig()
        with pytest.raises(Exception):
            config.max_iterations = 999

    def test_default_config_singleton(self):
        """Test that DEFAULT_CONFIG is a valid LoopHealthConfig."""
        assert isinstance(DEFAULT_CONFIG, LoopHealthConfig)
        assert DEFAULT_CONFIG.max_duration_seconds == 300.0


# =============================================================================
# LoopState and AbortReason Enum Tests
# =============================================================================


class TestEnums:
    """Tests for LoopState and AbortReason enums."""

    def test_loop_state_values(self):
        """Test all LoopState enum values."""
        assert LoopState.IDLE.value == "idle"
        assert LoopState.RUNNING.value == "running"
        assert LoopState.COMPLETED.value == "completed"
        assert LoopState.ABORTED.value == "aborted"
        assert LoopState.TIMED_OUT.value == "timed_out"

    def test_abort_reason_values(self):
        """Test all AbortReason enum values."""
        assert AbortReason.MAX_DURATION.value == "max_duration"
        assert AbortReason.MAX_ITERATIONS.value == "max_iterations"
        assert AbortReason.LLM_TIMEOUT.value == "llm_timeout"
        assert AbortReason.TOOL_TIMEOUT.value == "tool_timeout"
        assert AbortReason.STREAM_STALLED.value == "stream_stalled"
        assert AbortReason.USER_ABORT.value == "user_abort"
        assert AbortReason.ERROR.value == "error"


# =============================================================================
# LoopStats Tests
# =============================================================================


class TestLoopStats:
    """Tests for LoopStats dataclass."""

    def test_default_stats(self):
        """Test default LoopStats values."""
        stats = LoopStats()
        assert stats.iterations == 0
        assert stats.llm_calls == 0
        assert stats.tool_calls == 0
        assert stats.total_llm_time_ms == 0.0
        assert stats.total_tool_time_ms == 0.0
        assert stats.aborted is False
        assert stats.abort_reason is None
        assert stats.abort_message == ""
        assert stats.end_time is None
        assert isinstance(stats.start_time, datetime)

    def test_duration_ms_with_end_time(self):
        """Test duration calculation when end_time is set."""
        stats = LoopStats()
        stats.start_time = datetime(2025, 1, 15, 12, 0, 0)
        stats.end_time = datetime(2025, 1, 15, 12, 0, 5)
        assert stats.duration_ms == 5000.0

    def test_duration_ms_without_end_time(self):
        """Test duration calculation when still running (no end_time)."""
        stats = LoopStats()
        stats.start_time = datetime(2025, 1, 15, 12, 0, 0)
        # duration_ms should use datetime.utcnow() which will be > start_time
        assert stats.duration_ms > 0

    def test_duration_seconds(self):
        """Test duration_seconds property."""
        stats = LoopStats()
        stats.start_time = datetime(2025, 1, 15, 12, 0, 0)
        stats.end_time = datetime(2025, 1, 15, 12, 0, 10)
        assert stats.duration_seconds == 10.0

    def test_mutable_fields(self):
        """Test that stats fields can be updated."""
        stats = LoopStats()
        stats.iterations = 5
        stats.llm_calls = 3
        stats.tool_calls = 7
        stats.total_llm_time_ms = 1500.0
        stats.total_tool_time_ms = 800.0
        stats.aborted = True
        stats.abort_reason = AbortReason.MAX_ITERATIONS
        stats.abort_message = "Limit reached"

        assert stats.iterations == 5
        assert stats.llm_calls == 3
        assert stats.tool_calls == 7
        assert stats.aborted is True
        assert stats.abort_reason == AbortReason.MAX_ITERATIONS


# =============================================================================
# StreamCompletionVerifier Tests
# =============================================================================


class TestStreamCompletionVerifier:
    """Tests for StreamCompletionVerifier."""

    @pytest.fixture()
    def verifier(self):
        """Create a fresh verifier."""
        return StreamCompletionVerifier()

    def test_initial_state(self, verifier):
        """Test verifier starts in clean state."""
        assert verifier.content == ""
        assert verifier._done is False

    def test_on_chunk_accumulates_content(self, verifier):
        """Test that chunks are accumulated."""
        verifier.on_chunk("Hello ")
        verifier.on_chunk("World!")
        assert verifier.content == "Hello World!"

    def test_on_done_marks_complete(self, verifier):
        """Test that on_done sets the done flag."""
        verifier.on_done()
        assert verifier._done is True

    def test_verify_complete_stream(self, verifier):
        """Test verification of a properly completed stream."""
        verifier.on_chunk("This is a complete response with enough content.")
        verifier.on_done()

        is_complete, error = verifier.verify_completion()
        assert is_complete is True
        assert error is None

    def test_verify_incomplete_no_done_signal(self, verifier):
        """Test verification fails when done signal not received."""
        verifier.on_chunk("This is a response that was never completed.")
        # on_done() NOT called

        is_complete, error = verifier.verify_completion()
        assert is_complete is False
        assert "completion signal" in error

    def test_verify_too_short_response(self, verifier):
        """Test verification fails for too-short responses."""
        verifier.on_chunk("Hi")
        verifier.on_done()

        is_complete, error = verifier.verify_completion()
        assert is_complete is False
        assert "too short" in error.lower()

    def test_verify_empty_response(self, verifier):
        """Test verification fails for empty responses."""
        verifier.on_done()

        is_complete, error = verifier.verify_completion()
        assert is_complete is False

    def test_verify_whitespace_only_response(self, verifier):
        """Test verification fails for whitespace-only responses."""
        verifier.on_chunk("    \n\t   ")
        verifier.on_done()

        is_complete, error = verifier.verify_completion()
        assert is_complete is False
        assert "too short" in error.lower()

    def test_verify_unclosed_json(self, verifier):
        """Test detection of unclosed JSON objects."""
        verifier.on_chunk('Here is data: {"key": "value", {"nested": "more"')
        verifier.on_done()

        is_complete, error = verifier.verify_completion()
        assert is_complete is False
        assert "truncation" in error.lower()

    def test_verify_unclosed_bracket(self, verifier):
        """Test detection of unclosed array brackets."""
        verifier.on_chunk("Here is data: [item1, item2, [nested, more")
        verifier.on_done()

        is_complete, error = verifier.verify_completion()
        assert is_complete is False
        assert "truncation" in error.lower()

    def test_verify_balanced_code_blocks(self, verifier):
        """Test that balanced code blocks pass verification."""
        verifier.on_chunk("Here is code:\n```python\nprint('hello')\n```\nDone.")
        verifier.on_done()

        is_complete, error = verifier.verify_completion()
        assert is_complete is True
        assert error is None

    def test_reset_clears_state(self, verifier):
        """Test that reset clears all accumulated state."""
        verifier.on_chunk("Some content here for the verifier.")
        verifier.on_done()

        verifier.reset()

        assert verifier.content == ""
        assert verifier._done is False
        assert verifier._last_chunk_time == 0.0

    def test_on_chunk_updates_timestamp(self, verifier):
        """Test that on_chunk updates the last chunk time."""
        before = time.time()
        verifier.on_chunk("chunk")
        after = time.time()

        assert before <= verifier._last_chunk_time <= after

    def test_custom_config_min_response_length(self):
        """Test verifier respects custom min_response_length."""
        config = LoopHealthConfig(min_response_length=50)
        verifier = StreamCompletionVerifier(config)
        verifier.on_chunk("Short response content.")  # 23 chars
        verifier.on_done()

        is_complete, error = verifier.verify_completion()
        assert is_complete is False
        assert "too short" in error.lower()


# =============================================================================
# LLMCallTracker Tests
# =============================================================================


class TestLLMCallTracker:
    """Tests for LLMCallTracker with timeout protection."""

    @pytest.fixture()
    def config(self):
        """Create a config with short timeouts for testing."""
        return LoopHealthConfig(llm_timeout_seconds=0.5)

    @pytest.fixture()
    def parent_tracker(self, config):
        """Create a parent IterationTracker for the LLM tracker."""
        loop_tracker = LoopTracker(config)
        loop_tracker.stats = LoopStats()
        return IterationTracker(1, config, loop_tracker)

    @pytest.mark.asyncio
    async def test_successful_llm_call(self, config, parent_tracker):
        """Test a successful LLM call tracked correctly."""
        tracker = LLMCallTracker(config, parent_tracker)

        async with tracker:
            result = await tracker.with_timeout(self._fast_coro("response"))

        assert result == "response"
        assert tracker.duration_ms > 0

    @pytest.mark.asyncio
    async def test_llm_call_timeout_raises(self, config, parent_tracker):
        """Test that LLM call timeout raises LoopTimeoutError."""
        tracker = LLMCallTracker(config, parent_tracker)

        with pytest.raises(LoopTimeoutError) as exc_info:
            async with tracker:
                await tracker.with_timeout(self._slow_coro(5.0))

        assert exc_info.value.reason == AbortReason.LLM_TIMEOUT
        assert tracker._timed_out is True

    @pytest.mark.asyncio
    async def test_llm_call_records_time_on_parent(self, config, parent_tracker):
        """Test that LLM call duration is recorded on the parent tracker."""
        tracker = LLMCallTracker(config, parent_tracker)

        async with tracker:
            await tracker.with_timeout(self._fast_coro("ok"))

        assert parent_tracker._llm_time_ms > 0

    @pytest.mark.asyncio
    async def test_has_stream_verifier(self, config, parent_tracker):
        """Test that LLMCallTracker has a StreamCompletionVerifier."""
        tracker = LLMCallTracker(config, parent_tracker)
        assert isinstance(tracker.verifier, StreamCompletionVerifier)

    @staticmethod
    async def _fast_coro(value: str) -> str:
        """A fast coroutine that returns immediately."""
        await asyncio.sleep(0.01)
        return value

    @staticmethod
    async def _slow_coro(duration: float) -> str:
        """A slow coroutine that exceeds timeout."""
        await asyncio.sleep(duration)
        return "too late"


# =============================================================================
# ToolCallTracker Tests
# =============================================================================


class TestToolCallTracker:
    """Tests for ToolCallTracker with timeout protection."""

    @pytest.fixture()
    def config(self):
        """Create a config with short tool timeouts."""
        return LoopHealthConfig(tool_timeout_seconds=0.5)

    @pytest.fixture()
    def parent_tracker(self, config):
        """Create a parent IterationTracker."""
        loop_tracker = LoopTracker(config)
        loop_tracker.stats = LoopStats()
        return IterationTracker(1, config, loop_tracker)

    @pytest.mark.asyncio
    async def test_successful_tool_call(self, config, parent_tracker):
        """Test a successful tool call."""
        tracker = ToolCallTracker("search_man_vault", config, parent_tracker)

        async with tracker:
            result = await tracker.with_timeout(self._fast_tool())

        assert result == "tool_result"
        assert tracker.duration_ms > 0
        assert tracker.tool_name == "search_man_vault"

    @pytest.mark.asyncio
    async def test_tool_call_timeout_raises(self, config, parent_tracker):
        """Test that tool call timeout raises LoopTimeoutError."""
        tracker = ToolCallTracker("execute_capability", config, parent_tracker)

        with pytest.raises(LoopTimeoutError) as exc_info:
            async with tracker:
                await tracker.with_timeout(self._slow_tool(5.0))

        assert exc_info.value.reason == AbortReason.TOOL_TIMEOUT
        assert tracker._timed_out is True
        assert "execute_capability" in tracker._error

    @pytest.mark.asyncio
    async def test_tool_call_records_time_on_parent(self, config, parent_tracker):
        """Test that tool call duration is recorded on parent."""
        tracker = ToolCallTracker("get_system_info", config, parent_tracker)

        async with tracker:
            await tracker.with_timeout(self._fast_tool())

        assert parent_tracker._tool_time_ms > 0

    @staticmethod
    async def _fast_tool() -> str:
        await asyncio.sleep(0.01)
        return "tool_result"

    @staticmethod
    async def _slow_tool(duration: float) -> str:
        await asyncio.sleep(duration)
        return "too late"


# =============================================================================
# IterationTracker Tests
# =============================================================================


class TestIterationTracker:
    """Tests for IterationTracker."""

    @pytest.fixture()
    def config(self):
        return LoopHealthConfig(
            llm_timeout_seconds=1.0,
            tool_timeout_seconds=1.0,
        )

    @pytest.fixture()
    def loop_tracker(self, config):
        return LoopTracker(config)

    @pytest.mark.asyncio
    async def test_iteration_tracks_duration(self, config, loop_tracker):
        """Test that iteration tracks its own duration."""
        tracker = IterationTracker(1, config, loop_tracker)
        async with tracker:
            await asyncio.sleep(0.05)

        assert tracker.duration_ms >= 40  # allow some tolerance

    @pytest.mark.asyncio
    async def test_iteration_updates_parent_stats(self, config, loop_tracker):
        """Test that exiting iteration updates parent stats."""
        tracker = IterationTracker(3, config, loop_tracker)
        async with tracker:
            pass

        assert loop_tracker.stats.iterations == 3

    @pytest.mark.asyncio
    async def test_track_llm_call_increments_count(self, config, loop_tracker):
        """Test that tracking an LLM call increments the count."""
        tracker = IterationTracker(1, config, loop_tracker)
        async with tracker:
            async with tracker.track_llm_call() as llm_tracker:
                assert isinstance(llm_tracker, LLMCallTracker)

        assert loop_tracker.stats.llm_calls == 1

    @pytest.mark.asyncio
    async def test_track_tool_call_increments_count(self, config, loop_tracker):
        """Test that tracking a tool call increments the count."""
        tracker = IterationTracker(1, config, loop_tracker)
        async with tracker:
            async with tracker.track_tool_call("shell_command") as tool_tracker:
                assert isinstance(tool_tracker, ToolCallTracker)
                assert tool_tracker.tool_name == "shell_command"

        assert loop_tracker.stats.tool_calls == 1
        assert tracker._tool_count == 1

    @pytest.mark.asyncio
    async def test_add_llm_time_propagates(self, config, loop_tracker):
        """Test that add_llm_time propagates to parent stats."""
        tracker = IterationTracker(1, config, loop_tracker)
        tracker.add_llm_time(500.0)

        assert tracker._llm_time_ms == 500.0
        assert loop_tracker.stats.total_llm_time_ms == 500.0

    @pytest.mark.asyncio
    async def test_add_tool_time_propagates(self, config, loop_tracker):
        """Test that add_tool_time propagates to parent stats."""
        tracker = IterationTracker(1, config, loop_tracker)
        tracker.add_tool_time(200.0)

        assert tracker._tool_time_ms == 200.0
        assert loop_tracker.stats.total_tool_time_ms == 200.0


# =============================================================================
# LoopTracker Tests
# =============================================================================


class TestLoopTracker:
    """Tests for LoopTracker."""

    @pytest.fixture()
    def config(self):
        return LoopHealthConfig(
            max_duration_seconds=1.0,
            max_iterations=5,
            warn_at_iteration=3,
        )

    @pytest.mark.asyncio
    async def test_loop_lifecycle(self, config):
        """Test loop tracker enters running state and completes."""
        tracker = LoopTracker(config)
        assert tracker.state == LoopState.IDLE

        async with tracker:
            assert tracker.state == LoopState.RUNNING

        assert tracker.state == LoopState.COMPLETED
        assert tracker.stats.end_time is not None

    @pytest.mark.asyncio
    async def test_track_iteration(self, config):
        """Test tracking an iteration within the loop."""
        tracker = LoopTracker(config)
        async with tracker:
            async with tracker.track_iteration(1) as iter_tracker:
                assert isinstance(iter_tracker, IterationTracker)
                assert iter_tracker.iteration == 1

    def test_check_iteration_limit_not_exceeded(self, config):
        """Test iteration limit check when under the limit."""
        tracker = LoopTracker(config)
        tracker._current_iteration = 2

        exceeded, message = tracker.check_iteration_limit()
        assert exceeded is False
        assert message is None

    def test_check_iteration_limit_at_warning(self, config):
        """Test iteration limit check at warning threshold."""
        tracker = LoopTracker(config)
        tracker._current_iteration = 3  # warn_at_iteration = 3

        exceeded, message = tracker.check_iteration_limit()
        assert exceeded is False
        assert message is not None
        assert "remaining" in message

    def test_check_iteration_limit_exceeded(self, config):
        """Test iteration limit check when exceeded."""
        tracker = LoopTracker(config)
        tracker._current_iteration = 5  # max_iterations = 5

        exceeded, message = tracker.check_iteration_limit()
        assert exceeded is True
        assert "maximum iterations" in message.lower()

    def test_check_max_duration_not_exceeded(self, config):
        """Test max duration check when under the limit."""
        tracker = LoopTracker(config)
        tracker.stats.start_time = datetime.utcnow()

        exceeded, message = tracker.check_max_duration()
        assert exceeded is False
        assert message is None

    def test_check_max_duration_exceeded(self, config):
        """Test max duration check when exceeded."""
        tracker = LoopTracker(config)
        # Set start_time far in the past to exceed 1-second limit
        from datetime import timedelta
        tracker.stats.start_time = datetime.utcnow() - timedelta(seconds=10)

        exceeded, message = tracker.check_max_duration()
        assert exceeded is True
        assert "maximum duration" in message.lower()

    def test_abort_sets_state(self, config):
        """Test that abort changes state and records reason."""
        tracker = LoopTracker(config)
        tracker.abort(AbortReason.MAX_ITERATIONS, "Too many iterations")

        assert tracker.state == LoopState.ABORTED
        assert tracker.is_aborted is True
        assert tracker.stats.aborted is True
        assert tracker.stats.abort_reason == AbortReason.MAX_ITERATIONS
        assert tracker.stats.abort_message == "Too many iterations"

    @pytest.mark.asyncio
    async def test_track_iteration_aborts_on_duration_exceeded(self, config):
        """Test that starting a new iteration aborts if duration is exceeded."""
        from datetime import timedelta

        tracker = LoopTracker(config)

        with pytest.raises(LoopAbortedError):
            async with tracker:
                # Override start_time AFTER __aenter__ sets it, so
                # the duration check sees an elapsed time exceeding the limit.
                tracker.stats.start_time = datetime.utcnow() - timedelta(seconds=10)
                async with tracker.track_iteration(1):
                    pass

    @pytest.mark.asyncio
    async def test_aborted_loop_stays_aborted(self, config):
        """Test that an aborted loop stays in aborted state after exit."""
        tracker = LoopTracker(config)
        async with tracker:
            tracker.abort(AbortReason.USER_ABORT, "User cancelled")

        # State should still be ABORTED, not overwritten to COMPLETED
        assert tracker.state == LoopState.ABORTED


# =============================================================================
# LoopHealthMonitor Tests
# =============================================================================


class TestLoopHealthMonitor:
    """Tests for the main LoopHealthMonitor class."""

    def test_default_construction(self):
        """Test constructing monitor with default config."""
        monitor = LoopHealthMonitor()
        assert monitor.config == DEFAULT_CONFIG
        assert monitor.is_active is False
        assert monitor.current_stats is None

    def test_custom_config(self):
        """Test constructing monitor with custom config."""
        config = LoopHealthConfig(max_iterations=20)
        monitor = LoopHealthMonitor(config)
        assert monitor.config.max_iterations == 20

    @pytest.mark.asyncio
    async def test_track_loop_lifecycle(self):
        """Test that track_loop creates and manages a LoopTracker."""
        monitor = LoopHealthMonitor()

        assert monitor.is_active is False

        async with monitor.track_loop() as tracker:
            assert monitor.is_active is True
            assert isinstance(tracker, LoopTracker)
            assert monitor.current_stats is not None

        assert monitor.is_active is False
        assert monitor.current_stats is None

    @pytest.mark.asyncio
    async def test_track_loop_cleanup_on_exception(self):
        """Test that track_loop cleans up even on exception."""
        monitor = LoopHealthMonitor()

        with pytest.raises(ValueError):
            async with monitor.track_loop():
                raise ValueError("test error")

        assert monitor.is_active is False

    def test_abort_without_active_tracker(self):
        """Test that abort is a no-op when no loop is active."""
        monitor = LoopHealthMonitor()
        # Should not raise
        monitor.abort("no active loop")
        assert monitor.is_active is False

    @pytest.mark.asyncio
    async def test_abort_active_loop(self):
        """Test aborting an active loop from outside."""
        monitor = LoopHealthMonitor()

        async with monitor.track_loop() as tracker:
            monitor.abort("User pressed Ctrl-C")
            assert tracker.is_aborted is True
            assert tracker.stats.abort_reason == AbortReason.USER_ABORT


# =============================================================================
# Module-Level Singleton Tests
# =============================================================================


class TestSingleton:
    """Tests for the module-level singleton management."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_health_monitor()

    def teardown_method(self):
        """Reset singleton after each test."""
        reset_health_monitor()

    def test_get_health_monitor_creates_singleton(self):
        """Test that get_health_monitor creates a monitor on first call."""
        monitor = get_health_monitor()
        assert isinstance(monitor, LoopHealthMonitor)

    def test_get_health_monitor_returns_same_instance(self):
        """Test that get_health_monitor returns the same instance."""
        m1 = get_health_monitor()
        m2 = get_health_monitor()
        assert m1 is m2

    def test_get_health_monitor_with_config(self):
        """Test that config is used on first call."""
        config = LoopHealthConfig(max_iterations=50)
        monitor = get_health_monitor(config)
        assert monitor.config.max_iterations == 50

    def test_reset_health_monitor(self):
        """Test that reset creates a fresh instance."""
        m1 = get_health_monitor()
        reset_health_monitor()
        m2 = get_health_monitor()
        assert m1 is not m2


# =============================================================================
# Exception Tests
# =============================================================================


class TestExceptions:
    """Tests for health monitoring exceptions."""

    def test_loop_health_error(self):
        """Test LoopHealthError construction."""
        err = LoopHealthError("something went wrong")
        assert err.message == "something went wrong"
        assert err.reason is None
        assert str(err) == "something went wrong"

    def test_loop_health_error_with_reason(self):
        """Test LoopHealthError with an abort reason."""
        err = LoopHealthError("timed out", AbortReason.LLM_TIMEOUT)
        assert err.reason == AbortReason.LLM_TIMEOUT

    def test_loop_timeout_error_is_health_error(self):
        """Test that LoopTimeoutError inherits from LoopHealthError."""
        err = LoopTimeoutError("LLM timed out", AbortReason.LLM_TIMEOUT)
        assert isinstance(err, LoopHealthError)
        assert err.reason == AbortReason.LLM_TIMEOUT

    def test_loop_aborted_error_is_health_error(self):
        """Test that LoopAbortedError inherits from LoopHealthError."""
        err = LoopAbortedError("max duration exceeded", AbortReason.MAX_DURATION)
        assert isinstance(err, LoopHealthError)
        assert err.reason == AbortReason.MAX_DURATION


# =============================================================================
# Integration-style Tests (full pipeline)
# =============================================================================


class TestHealthMonitorIntegration:
    """Integration tests using the full monitoring pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline_successful(self):
        """Test a complete successful loop execution with monitoring."""
        config = LoopHealthConfig(
            max_duration_seconds=10.0,
            max_iterations=5,
            llm_timeout_seconds=2.0,
            tool_timeout_seconds=2.0,
        )
        monitor = LoopHealthMonitor(config)

        async with monitor.track_loop() as tracker:
            async with tracker.track_iteration(1) as iter_tracker:
                async with iter_tracker.track_llm_call() as llm_tracker:
                    result = await llm_tracker.with_timeout(
                        self._mock_llm_call("Hello from LLM")
                    )
                    assert result == "Hello from LLM"

                async with iter_tracker.track_tool_call("search_man_vault") as tool_tracker:
                    result = await tool_tracker.with_timeout(
                        self._mock_tool_call("search results")
                    )
                    assert result == "search results"

        assert tracker.state == LoopState.COMPLETED
        assert tracker.stats.llm_calls == 1
        assert tracker.stats.tool_calls == 1
        assert tracker.stats.iterations == 1
        assert tracker.stats.total_llm_time_ms > 0
        assert tracker.stats.total_tool_time_ms > 0

    @pytest.mark.asyncio
    async def test_multiple_iterations(self):
        """Test tracking multiple iterations."""
        config = LoopHealthConfig(
            max_duration_seconds=10.0,
            max_iterations=5,
        )
        monitor = LoopHealthMonitor(config)

        async with monitor.track_loop() as tracker:
            for i in range(1, 4):
                async with tracker.track_iteration(i) as iter_tracker:
                    async with iter_tracker.track_llm_call() as llm_tracker:
                        await llm_tracker.with_timeout(
                            self._mock_llm_call(f"response {i}")
                        )

        assert tracker.stats.iterations == 3
        assert tracker.stats.llm_calls == 3

    @pytest.mark.asyncio
    async def test_iteration_limit_check_in_loop(self):
        """Test checking iteration limits during loop execution."""
        config = LoopHealthConfig(
            max_duration_seconds=10.0,
            max_iterations=3,
            warn_at_iteration=2,
        )
        tracker = LoopTracker(config)

        async with tracker:
            for i in range(1, 5):
                tracker._current_iteration = i
                exceeded, message = tracker.check_iteration_limit()
                if exceeded:
                    break

        # Should have stopped at iteration 3 (the max)
        assert tracker._current_iteration == 3

    @staticmethod
    async def _mock_llm_call(response: str) -> str:
        await asyncio.sleep(0.01)
        return response

    @staticmethod
    async def _mock_tool_call(result: str) -> str:
        await asyncio.sleep(0.01)
        return result
