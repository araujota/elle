"""LoopHealthMonitor - watchdog for agent loop execution.

Provides timeout protection, stall detection, and stream completion verification
for the agentic loop execution.

Key components:
- LoopHealthConfig: Configuration for timeouts and limits
- LoopHealthMonitor: Main watchdog class
- LoopTracker: Tracks overall loop execution
- IterationTracker: Tracks individual iterations
- LLMCallTracker: Wraps LLM calls with timeouts
- StreamCompletionVerifier: Detects truncated streams

Usage:
    monitor = LoopHealthMonitor()

    async with monitor.track_loop() as tracker:
        async with tracker.track_iteration(1) as iter_tracker:
            async with iter_tracker.track_llm_call() as llm_tracker:
                response = await llm_tracker.with_timeout(session.chat(...))

        while response.has_tool_calls:
            exceeded, message = tracker.check_iteration_limit()
            if exceeded:
                stream_callback(f"\\n\\n[Notice: {message}]\\n")
                break

            for tool in response.tool_calls:
                async with iter_tracker.track_tool_call(tool.name) as tool_tracker:
                    result = await tool_tracker.with_timeout(
                        self.tools.execute(tool.name, tool.arguments)
                    )
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# Configuration
# =============================================================================


class LoopHealthConfig(BaseModel):
    """Configuration for loop health monitoring."""

    model_config = ConfigDict(frozen=True)

    # Overall loop limits
    max_duration_seconds: float = Field(
        default=300.0,
        description="Maximum total duration for the entire loop (5 minutes)",
    )
    max_iterations: int = Field(
        default=10,
        description="Maximum number of tool call iterations",
    )

    # Per-operation timeouts
    llm_timeout_seconds: float = Field(
        default=120.0,
        description="Timeout for individual LLM calls (2 minutes)",
    )
    tool_timeout_seconds: float = Field(
        default=60.0,
        description="Timeout for individual tool executions (1 minute)",
    )
    retrieval_timeout_seconds: float = Field(
        default=30.0,
        description="Timeout for retrieval pipeline operations",
    )

    # Streaming settings
    stream_chunk_timeout_seconds: float = Field(
        default=30.0,
        description="Max time between stream chunks before considering stalled",
    )
    min_response_length: int = Field(
        default=10,
        description="Minimum expected response length (chars)",
    )

    # User notification thresholds
    warn_at_iteration: int = Field(
        default=7,
        description="Warn user when reaching this iteration count",
    )


# Default configuration
DEFAULT_CONFIG = LoopHealthConfig()


# =============================================================================
# Enums and Status Types
# =============================================================================


class LoopState(Enum):
    """Current state of the loop execution."""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"
    TIMED_OUT = "timed_out"


class AbortReason(Enum):
    """Reason for loop abortion."""

    MAX_DURATION = "max_duration"
    MAX_ITERATIONS = "max_iterations"
    LLM_TIMEOUT = "llm_timeout"
    TOOL_TIMEOUT = "tool_timeout"
    STREAM_STALLED = "stream_stalled"
    USER_ABORT = "user_abort"
    ERROR = "error"


@dataclass
class LoopStats:
    """Statistics for a loop execution."""

    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: datetime | None = None
    iterations: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    total_llm_time_ms: float = 0.0
    total_tool_time_ms: float = 0.0
    aborted: bool = False
    abort_reason: AbortReason | None = None
    abort_message: str = ""

    @property
    def duration_ms(self) -> float:
        """Total duration in milliseconds."""
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds() * 1000
        return (datetime.utcnow() - self.start_time).total_seconds() * 1000

    @property
    def duration_seconds(self) -> float:
        """Total duration in seconds."""
        return self.duration_ms / 1000


# =============================================================================
# Stream Completion Verifier
# =============================================================================


class StreamCompletionVerifier:
    """Verifies that streaming LLM responses are complete.

    Detects:
    - Truncated responses (incomplete JSON, unclosed tags)
    - Missing end markers
    - Suspiciously short responses
    """

    def __init__(self, config: LoopHealthConfig | None = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self._chunks: list[str] = []
        self._last_chunk_time: float = 0.0
        self._done = False

    def on_chunk(self, content: str) -> None:
        """Record a received chunk.

        Args:
            content: The chunk content.
        """
        self._chunks.append(content)
        self._last_chunk_time = time.time()

    def on_done(self) -> None:
        """Mark stream as complete."""
        self._done = True

    def verify_completion(self) -> tuple[bool, str | None]:
        """Verify the stream completed properly.

        Returns:
            Tuple of (is_complete, error_message).
        """
        full_content = "".join(self._chunks)

        # Check if marked as done
        if not self._done:
            return False, "Stream did not receive completion signal"

        # Check minimum length
        if len(full_content.strip()) < self.config.min_response_length:
            return False, f"Response too short ({len(full_content)} chars)"

        # Check for truncation indicators
        truncation_indicators = [
            ('{"', "}"),  # Unclosed JSON
            ("[", "]"),  # Unclosed array
            ("<", ">"),  # Unclosed tag
            ("```", "```"),  # Unclosed code block
        ]

        for opener, closer in truncation_indicators:
            open_count = full_content.count(opener)
            close_count = full_content.count(closer)
            if open_count > close_count:
                return False, f"Possible truncation: {open_count} '{opener}' but {close_count} '{closer}'"

        return True, None

    @property
    def content(self) -> str:
        """Get the full collected content."""
        return "".join(self._chunks)

    def reset(self) -> None:
        """Reset for a new stream."""
        self._chunks.clear()
        self._last_chunk_time = 0.0
        self._done = False


# =============================================================================
# LLM Call Tracker
# =============================================================================


class LLMCallTracker:
    """Tracks and wraps LLM calls with timeout protection."""

    def __init__(
        self,
        config: LoopHealthConfig,
        parent: IterationTracker,
    ) -> None:
        self.config = config
        self.parent = parent
        self._start_time: float = 0.0
        self._end_time: float = 0.0
        self._timed_out = False
        self._error: str | None = None
        self.verifier = StreamCompletionVerifier(config)

    async def __aenter__(self) -> LLMCallTracker:
        self._start_time = time.time()
        return self

    async def __aexit__(self, *args: Any) -> None:
        self._end_time = time.time()
        duration_ms = (self._end_time - self._start_time) * 1000
        self.parent.add_llm_time(duration_ms)

    async def with_timeout(self, coro: Coroutine[Any, Any, T]) -> T:
        """Execute a coroutine with timeout protection.

        Args:
            coro: The coroutine to execute.

        Returns:
            Result from the coroutine.

        Raises:
            asyncio.TimeoutError: If timeout exceeded.
            LoopTimeoutError: With additional context.
        """
        try:
            result = await asyncio.wait_for(
                coro,
                timeout=self.config.llm_timeout_seconds,
            )
            return result
        except TimeoutError as e:
            self._timed_out = True
            self._error = f"LLM call timed out after {self.config.llm_timeout_seconds}s"
            raise LoopTimeoutError(
                self._error,
                AbortReason.LLM_TIMEOUT,
            ) from e

    @property
    def duration_ms(self) -> float:
        """Duration of the LLM call in milliseconds."""
        if self._end_time:
            return (self._end_time - self._start_time) * 1000
        return (time.time() - self._start_time) * 1000


# =============================================================================
# Tool Call Tracker
# =============================================================================


class ToolCallTracker:
    """Tracks and wraps tool calls with timeout protection."""

    def __init__(
        self,
        tool_name: str,
        config: LoopHealthConfig,
        parent: IterationTracker,
    ) -> None:
        self.tool_name = tool_name
        self.config = config
        self.parent = parent
        self._start_time: float = 0.0
        self._end_time: float = 0.0
        self._timed_out = False
        self._error: str | None = None

    async def __aenter__(self) -> ToolCallTracker:
        self._start_time = time.time()
        return self

    async def __aexit__(self, *args: Any) -> None:
        self._end_time = time.time()
        duration_ms = (self._end_time - self._start_time) * 1000
        self.parent.add_tool_time(duration_ms)

    async def with_timeout(self, coro: Coroutine[Any, Any, T]) -> T:
        """Execute a coroutine with timeout protection.

        Args:
            coro: The coroutine to execute.

        Returns:
            Result from the coroutine.

        Raises:
            asyncio.TimeoutError: If timeout exceeded.
            LoopTimeoutError: With additional context.
        """
        try:
            result = await asyncio.wait_for(
                coro,
                timeout=self.config.tool_timeout_seconds,
            )
            return result
        except TimeoutError as e:
            self._timed_out = True
            self._error = f"Tool '{self.tool_name}' timed out after {self.config.tool_timeout_seconds}s"
            raise LoopTimeoutError(
                self._error,
                AbortReason.TOOL_TIMEOUT,
            ) from e

    @property
    def duration_ms(self) -> float:
        """Duration of the tool call in milliseconds."""
        if self._end_time:
            return (self._end_time - self._start_time) * 1000
        return (time.time() - self._start_time) * 1000


# =============================================================================
# Iteration Tracker
# =============================================================================


class IterationTracker:
    """Tracks a single iteration of the agent loop."""

    def __init__(
        self,
        iteration: int,
        config: LoopHealthConfig,
        parent: LoopTracker,
    ) -> None:
        self.iteration = iteration
        self.config = config
        self.parent = parent
        self._start_time: float = 0.0
        self._end_time: float = 0.0
        self._llm_time_ms: float = 0.0
        self._tool_time_ms: float = 0.0
        self._tool_count: int = 0

    async def __aenter__(self) -> IterationTracker:
        self._start_time = time.time()
        return self

    async def __aexit__(self, *args: Any) -> None:
        self._end_time = time.time()
        self.parent.stats.iterations = self.iteration

    @asynccontextmanager
    async def track_llm_call(self) -> AsyncIterator[LLMCallTracker]:
        """Track an LLM call within this iteration.

        Yields:
            LLMCallTracker for the call.
        """
        tracker = LLMCallTracker(self.config, self)
        self.parent.stats.llm_calls += 1

        async with tracker:
            yield tracker

    @asynccontextmanager
    async def track_tool_call(self, tool_name: str) -> AsyncIterator[ToolCallTracker]:
        """Track a tool call within this iteration.

        Args:
            tool_name: Name of the tool being called.

        Yields:
            ToolCallTracker for the call.
        """
        tracker = ToolCallTracker(tool_name, self.config, self)
        self._tool_count += 1
        self.parent.stats.tool_calls += 1

        async with tracker:
            yield tracker

    def add_llm_time(self, ms: float) -> None:
        """Add LLM call time.

        Args:
            ms: Time in milliseconds.
        """
        self._llm_time_ms += ms
        self.parent.stats.total_llm_time_ms += ms

    def add_tool_time(self, ms: float) -> None:
        """Add tool call time.

        Args:
            ms: Time in milliseconds.
        """
        self._tool_time_ms += ms
        self.parent.stats.total_tool_time_ms += ms

    @property
    def duration_ms(self) -> float:
        """Duration of this iteration in milliseconds."""
        if self._end_time:
            return (self._end_time - self._start_time) * 1000
        return (time.time() - self._start_time) * 1000


# =============================================================================
# Loop Tracker
# =============================================================================


class LoopTracker:
    """Tracks the overall agent loop execution."""

    def __init__(self, config: LoopHealthConfig) -> None:
        self.config = config
        self.stats = LoopStats()
        self._state = LoopState.IDLE
        self._current_iteration: int = 0

    async def __aenter__(self) -> LoopTracker:
        self._state = LoopState.RUNNING
        self.stats.start_time = datetime.utcnow()
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.stats.end_time = datetime.utcnow()
        if self._state == LoopState.RUNNING:
            self._state = LoopState.COMPLETED

    @asynccontextmanager
    async def track_iteration(self, iteration: int) -> AsyncIterator[IterationTracker]:
        """Track a loop iteration.

        Args:
            iteration: Iteration number (1-based).

        Yields:
            IterationTracker for the iteration.
        """
        # Check max duration before starting new iteration
        exceeded, message = self.check_max_duration()
        if exceeded:
            self.abort(AbortReason.MAX_DURATION, message or "Max duration exceeded")
            raise LoopAbortedError(message or "Max duration exceeded", AbortReason.MAX_DURATION)

        self._current_iteration = iteration
        tracker = IterationTracker(iteration, self.config, self)

        async with tracker:
            yield tracker

    def check_iteration_limit(self) -> tuple[bool, str | None]:
        """Check if iteration limit is exceeded.

        Returns:
            Tuple of (exceeded, user_message).
        """
        if self._current_iteration >= self.config.max_iterations:
            message = (
                f"Reached maximum iterations ({self.config.max_iterations}). "
                "Consider breaking down your request into smaller steps."
            )
            return True, message

        # Warning message near limit
        if self._current_iteration >= self.config.warn_at_iteration:
            remaining = self.config.max_iterations - self._current_iteration
            message = f"Note: {remaining} iteration(s) remaining before automatic stop."
            return False, message

        return False, None

    def check_max_duration(self) -> tuple[bool, str | None]:
        """Check if max duration is exceeded.

        Returns:
            Tuple of (exceeded, user_message).
        """
        if self.stats.duration_seconds >= self.config.max_duration_seconds:
            message = (
                f"Execution exceeded maximum duration ({self.config.max_duration_seconds}s). "
                "The operation was stopped to prevent runaway execution."
            )
            return True, message

        return False, None

    def abort(self, reason: AbortReason, message: str = "") -> None:
        """Abort the loop execution.

        Args:
            reason: Why the loop is being aborted.
            message: User-facing message.
        """
        self._state = LoopState.ABORTED
        self.stats.aborted = True
        self.stats.abort_reason = reason
        self.stats.abort_message = message
        logger.warning(f"Loop aborted: {reason.value} - {message}")

    @property
    def state(self) -> LoopState:
        """Current loop state."""
        return self._state

    @property
    def is_aborted(self) -> bool:
        """Check if loop was aborted."""
        return self._state == LoopState.ABORTED


# =============================================================================
# Loop Health Monitor
# =============================================================================


class LoopHealthMonitor:
    """Main watchdog for agent loop execution.

    Provides:
    - Overall loop duration tracking
    - Per-iteration timeout protection
    - LLM call timeouts
    - Tool call timeouts
    - Stream completion verification
    - User notification for limits

    Usage:
        monitor = LoopHealthMonitor()

        async with monitor.track_loop() as tracker:
            # ... loop execution
            exceeded, msg = tracker.check_iteration_limit()
            if exceeded:
                break
    """

    def __init__(self, config: LoopHealthConfig | None = None) -> None:
        """Initialize the health monitor.

        Args:
            config: Configuration options. Uses defaults if not provided.
        """
        self.config = config or DEFAULT_CONFIG
        self._active_tracker: LoopTracker | None = None

    @asynccontextmanager
    async def track_loop(self) -> AsyncIterator[LoopTracker]:
        """Start tracking a loop execution.

        Yields:
            LoopTracker for the execution.
        """
        tracker = LoopTracker(self.config)
        self._active_tracker = tracker

        try:
            async with tracker:
                yield tracker
        finally:
            self._active_tracker = None

    def abort(self, reason: str) -> None:
        """Signal abort from outside the loop.

        Args:
            reason: Reason for abort.
        """
        if self._active_tracker:
            self._active_tracker.abort(AbortReason.USER_ABORT, reason)

    @property
    def is_active(self) -> bool:
        """Check if a loop is currently being tracked."""
        return self._active_tracker is not None

    @property
    def current_stats(self) -> LoopStats | None:
        """Get stats for the current loop (if active)."""
        if self._active_tracker:
            return self._active_tracker.stats
        return None


# =============================================================================
# Exceptions
# =============================================================================


class LoopHealthError(Exception):
    """Base exception for loop health issues."""

    def __init__(self, message: str, reason: AbortReason | None = None) -> None:
        self.message = message
        self.reason = reason
        super().__init__(message)


class LoopTimeoutError(LoopHealthError):
    """Raised when an operation times out."""

    pass


class LoopAbortedError(LoopHealthError):
    """Raised when loop is aborted."""

    pass


# =============================================================================
# Module-level Singleton
# =============================================================================


_monitor: LoopHealthMonitor | None = None


def get_health_monitor(config: LoopHealthConfig | None = None) -> LoopHealthMonitor:
    """Get the shared health monitor instance.

    Args:
        config: Optional configuration (only used on first call).

    Returns:
        The LoopHealthMonitor singleton.
    """
    global _monitor
    if _monitor is None:
        _monitor = LoopHealthMonitor(config)
    return _monitor


def reset_health_monitor() -> None:
    """Reset the shared monitor (for testing)."""
    global _monitor
    _monitor = None
