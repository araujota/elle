"""On-demand syscall trace lifecycle management.

Manages the lifecycle of syscall tracing for command explanation:
- Starting/stopping traces for specific PIDs
- Collecting and summarizing events
- Generating explanations

This is designed to be used by the CLI for /trace commands
and for explaining what commands actually did.
"""

import logging
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from elle.daemon.telemetry.ebpf.syscall_explainer import (
    create_trace_with_explanation,
)
from elle.daemon.telemetry.ebpf.syscall_models import (
    SyscallEvent,
    SyscallSummary,
    SyscallTrace,
)
from elle.daemon.telemetry.ebpf.syscall_summarizer import (
    SyscallSummarizer,
    create_summarizer,
)

logger = logging.getLogger(__name__)


class SyscallManager:
    """Manages on-demand syscall tracing.

    Provides a high-level interface for tracing commands and
    generating explanations of what they did.
    """

    def __init__(self) -> None:
        """Initialize the syscall manager."""
        self._program = None
        self._summarizer: SyscallSummarizer | None = None
        self._lock = threading.Lock()
        self._polling = False
        self._poll_thread: threading.Thread | None = None
        self._active_trace: bool = False
        self._initialized = False
        self._init_error: str | None = None

    def _ensure_initialized(self) -> bool:
        """Ensure the eBPF program is loaded.

        Returns:
            True if initialized successfully.
        """
        if self._initialized:
            return True

        if self._init_error:
            return False

        try:
            from elle.daemon.telemetry.ebpf.bcc.syscall import create_syscall_program

            self._program = create_syscall_program()

            if not self._program.load():
                self._init_error = "Failed to load eBPF program"
                return False

            if not self._program.attach():
                self._init_error = "Failed to attach eBPF program"
                return False

            if not self._program.setup_ring_buffer():
                self._init_error = "Failed to setup ring buffer"
                return False

            self._initialized = True
            logger.info("Syscall manager initialized")
            return True

        except ImportError as e:
            self._init_error = f"BCC not available: {e}"
            logger.warning(f"Syscall tracing unavailable: {self._init_error}")
            return False
        except Exception as e:
            self._init_error = f"Initialization failed: {e}"
            logger.error(f"Syscall manager initialization failed: {e}")
            return False

    @property
    def is_available(self) -> bool:
        """Check if syscall tracing is available."""
        return self._ensure_initialized()

    @property
    def is_tracing(self) -> bool:
        """Check if a trace is currently active."""
        return self._active_trace

    def start_trace(
        self,
        command: str,
        pid: int,
        cwd: Path | None = None,
    ) -> bool:
        """Start tracing syscalls for a PID.

        Args:
            command: Command being traced (for summary).
            pid: Process ID to trace.
            cwd: Current working directory.

        Returns:
            True if tracing started successfully.
        """
        with self._lock:
            if not self._ensure_initialized():
                return False

            if self._active_trace:
                logger.warning("Trace already active, stopping previous trace")
                self._stop_trace_internal()

            # Create summarizer
            self._summarizer = create_summarizer(
                command=command,
                pid=pid,
                cwd=cwd,
            )

            # Set up event callback
            self._program.set_syscall_callback(self._on_syscall_event)

            # Add PID to trace
            if not self._program.add_traced_pid(pid):
                logger.error(f"Failed to add PID {pid} to trace")
                return False

            # Start polling thread
            self._polling = True
            self._poll_thread = threading.Thread(
                target=self._poll_loop,
                daemon=True,
                name="syscall-poll",
            )
            self._poll_thread.start()

            self._active_trace = True
            logger.debug(f"Started syscall trace for PID {pid}")
            return True

    def stop_trace(self) -> SyscallTrace:
        """Stop the current trace and get results.

        Returns:
            SyscallTrace with summary and explanation.
        """
        with self._lock:
            return self._stop_trace_internal()

    def _stop_trace_internal(self) -> SyscallTrace:
        """Internal stop trace (must hold lock).

        Returns:
            SyscallTrace with results.
        """
        if not self._active_trace:
            return SyscallTrace(
                enabled=False,
                error="No active trace",
            )

        # Stop polling
        self._polling = False
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=1.0)
        self._poll_thread = None

        # Clear traced PIDs
        if self._program:
            self._program.clear_traced_pids()
            self._program.set_syscall_callback(None)

        # Get summary
        if self._summarizer:
            summary = self._summarizer.get_summary()
            trace = create_trace_with_explanation(summary)
        else:
            trace = SyscallTrace(
                enabled=True,
                error="No summarizer available",
            )

        self._summarizer = None
        self._active_trace = False

        logger.debug("Stopped syscall trace")
        return trace

    def _on_syscall_event(self, event: SyscallEvent) -> None:
        """Handle incoming syscall event.

        Args:
            event: Syscall event from eBPF.
        """
        if self._summarizer:
            self._summarizer.add_event(event)

    def _poll_loop(self) -> None:
        """Background polling loop for events."""
        while self._polling:
            try:
                if self._program:
                    self._program.poll(timeout_ms=100)
            except Exception as e:
                logger.debug(f"Poll error: {e}")
            time.sleep(0.01)  # Small sleep to prevent busy loop

    @contextmanager
    def trace_command(
        self,
        command: str,
        pid: int,
        cwd: Path | None = None,
    ) -> Generator[None, None, SyscallTrace]:
        """Context manager for tracing a command.

        Usage:
            with manager.trace_command("ls -la", pid) as trace:
                subprocess.run(...)
            result = trace  # SyscallTrace with results

        Args:
            command: Command being traced.
            pid: Process ID to trace.
            cwd: Current working directory.

        Yields:
            None (trace is returned after context exits).
        """
        started = self.start_trace(command, pid, cwd)

        if not started:
            # Yield and return error trace
            try:
                yield
            finally:
                pass
            return SyscallTrace(
                enabled=False,
                error=self._init_error or "Failed to start trace",
            )

        try:
            yield
        finally:
            pass

        return self.stop_trace()

    def get_current_summary(self) -> SyscallSummary | None:
        """Get the current summary without stopping the trace.

        Returns:
            Current summary or None if not tracing.
        """
        with self._lock:
            if self._summarizer:
                return self._summarizer.get_summary()
            return None

    def cleanup(self) -> None:
        """Clean up resources."""
        with self._lock:
            if self._active_trace:
                self._stop_trace_internal()

            if self._program:
                self._program.unload()
                self._program = None

            self._initialized = False


# Module-level manager instance
_manager: SyscallManager | None = None
_manager_lock = threading.Lock()


def get_syscall_manager() -> SyscallManager:
    """Get the shared syscall manager instance.

    Returns:
        The SyscallManager singleton.
    """
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = SyscallManager()
        return _manager


def is_syscall_tracing_available() -> bool:
    """Check if syscall tracing is available on this system.

    Returns:
        True if tracing is available.
    """
    return get_syscall_manager().is_available


def trace_command_sync(
    command: str,
    pid: int,
    cwd: Path | None = None,
    wait_for_exit: Callable[[], None] | None = None,
) -> SyscallTrace:
    """Trace a command synchronously.

    Convenience function for tracing a command and waiting
    for it to complete.

    Args:
        command: Command being traced.
        pid: Process ID to trace.
        cwd: Current working directory.
        wait_for_exit: Function to call to wait for command exit.

    Returns:
        SyscallTrace with results.
    """
    manager = get_syscall_manager()

    if not manager.start_trace(command, pid, cwd):
        return SyscallTrace(
            enabled=False,
            error="Failed to start trace",
        )

    # Wait for command to exit
    if wait_for_exit:
        try:
            wait_for_exit()
        except Exception as e:
            logger.warning(f"Error waiting for command: {e}")

    return manager.stop_trace()
