from __future__ import annotations

"""Tests for syscall_manager.py -- on-demand syscall trace lifecycle management."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.telemetry.ebpf.syscall_models import (
    SyscallEvent,
    SyscallSummary,
    SyscallTrace,
)

# ---------------------------------------------------------------------------
# Helpers -- reusable factory functions for test data
# ---------------------------------------------------------------------------


def _make_summary(**overrides) -> SyscallSummary:
    """Build a SyscallSummary with sane defaults, optionally overridden."""
    defaults = {
        "command": "ls -la",
        "pid": 1234,
        "duration_ms": 42,
        "total_syscalls": 10,
    }
    defaults.update(overrides)
    return SyscallSummary(**defaults)


def _make_event(**overrides) -> SyscallEvent:
    """Build a SyscallEvent with sane defaults, optionally overridden."""
    defaults = {
        "ts_ns": 1000000,
        "syscall": "openat",
        "category": "file_read",
        "pid": 1234,
        "comm": "ls",
    }
    defaults.update(overrides)
    return SyscallEvent(**defaults)


def _make_trace(**overrides) -> SyscallTrace:
    """Build a SyscallTrace with sane defaults, optionally overridden."""
    defaults = {"enabled": True, "summary": _make_summary(), "explanation": "test explanation"}
    defaults.update(overrides)
    return SyscallTrace(**defaults)


# ---------------------------------------------------------------------------
# We need to mock the three elle-internal imports used by syscall_manager
# at module level so the import itself succeeds regardless of environment.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_singleton():
    """Reset the module-level _manager singleton between tests."""
    import elle.daemon.telemetry.ebpf.syscall_manager as mod

    mod._manager = None
    yield
    mod._manager = None


# ===================================================================
# SyscallManager -- __init__ & _ensure_initialized
# ===================================================================


class TestSyscallManagerInit:
    """Tests for SyscallManager construction and lazy initialisation."""

    def test_initial_state(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        assert mgr._program is None
        assert mgr._summarizer is None
        assert mgr._polling is False
        assert mgr._poll_thread is None
        assert mgr._active_trace is False
        assert mgr._initialized is False
        assert mgr._init_error is None

    def test_ensure_initialized_returns_true_when_already_initialized(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        mgr._initialized = True
        assert mgr._ensure_initialized() is True

    def test_ensure_initialized_returns_false_when_init_error_cached(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        mgr._init_error = "already failed"
        assert mgr._ensure_initialized() is False

    def test_ensure_initialized_sets_init_error_on_first_call(self):
        """BCC was removed; _ensure_initialized should set _init_error and return False."""
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        result = mgr._ensure_initialized()
        assert result is False
        assert mgr._init_error is not None
        assert "BCC" in mgr._init_error or "unavailable" in mgr._init_error.lower()


# ===================================================================
# SyscallManager -- is_available / is_tracing properties
# ===================================================================


class TestSyscallManagerProperties:
    """Tests for read-only properties."""

    def test_is_available_false_by_default(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        assert mgr.is_available is False

    def test_is_available_true_when_initialized(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        mgr._initialized = True
        assert mgr.is_available is True

    def test_is_tracing_false_by_default(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        assert mgr.is_tracing is False

    def test_is_tracing_true_when_active(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        mgr._active_trace = True
        assert mgr.is_tracing is True


# ===================================================================
# SyscallManager.start_trace
# ===================================================================


class TestStartTrace:
    """Tests for SyscallManager.start_trace."""

    def test_start_trace_returns_false_when_not_initialized(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        result = mgr.start_trace("ls", 123)
        assert result is False

    @patch("elle.daemon.telemetry.ebpf.syscall_manager.create_summarizer")
    def test_start_trace_success(self, mock_create_summarizer):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mock_summarizer = MagicMock()
        mock_create_summarizer.return_value = mock_summarizer

        mock_program = MagicMock()
        mock_program.add_traced_pid.return_value = True

        mgr = SyscallManager()
        mgr._initialized = True
        mgr._program = mock_program

        result = mgr.start_trace("ls -la", 1234, cwd=Path("/tmp"))

        assert result is True
        assert mgr._active_trace is True
        assert mgr._summarizer is mock_summarizer
        assert mgr._polling is True
        assert mgr._poll_thread is not None
        mock_program.set_syscall_callback.assert_called_once()
        mock_program.add_traced_pid.assert_called_once_with(1234)
        mock_create_summarizer.assert_called_once_with(
            command="ls -la",
            pid=1234,
            cwd=Path("/tmp"),
        )

        # Clean up the polling thread
        mgr._polling = False
        mgr._poll_thread.join(timeout=2)

    @patch("elle.daemon.telemetry.ebpf.syscall_manager.create_summarizer")
    def test_start_trace_fails_when_add_pid_fails(self, mock_create_summarizer):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mock_create_summarizer.return_value = MagicMock()
        mock_program = MagicMock()
        mock_program.add_traced_pid.return_value = False

        mgr = SyscallManager()
        mgr._initialized = True
        mgr._program = mock_program

        result = mgr.start_trace("ls", 1234)

        assert result is False
        assert mgr._active_trace is False

    @patch("elle.daemon.telemetry.ebpf.syscall_manager.create_trace_with_explanation")
    @patch("elle.daemon.telemetry.ebpf.syscall_manager.create_summarizer")
    def test_start_trace_stops_existing_trace_first(self, mock_create_summarizer, mock_create_trace):
        """If a trace is already active, start_trace should stop it first."""
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mock_create_summarizer.return_value = MagicMock()
        mock_create_trace.return_value = _make_trace()
        mock_program = MagicMock()
        mock_program.add_traced_pid.return_value = True

        old_summarizer = MagicMock()
        old_summarizer.get_summary.return_value = _make_summary()

        mgr = SyscallManager()
        mgr._initialized = True
        mgr._program = mock_program
        mgr._active_trace = True
        mgr._summarizer = old_summarizer

        result = mgr.start_trace("cat /etc/hosts", 5678)

        assert result is True
        # The old trace should have been stopped: clear_traced_pids called
        mock_program.clear_traced_pids.assert_called()

        # Cleanup
        mgr._polling = False
        if mgr._poll_thread:
            mgr._poll_thread.join(timeout=2)

    def test_start_trace_with_no_cwd(self):
        """start_trace should accept cwd=None."""
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        # Not initialized, so it will return False but should not raise
        result = mgr.start_trace("echo hi", 999, cwd=None)
        assert result is False


# ===================================================================
# SyscallManager.stop_trace / _stop_trace_internal
# ===================================================================


class TestStopTrace:
    """Tests for SyscallManager.stop_trace and _stop_trace_internal."""

    def test_stop_trace_when_no_active_trace(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        trace = mgr.stop_trace()

        assert isinstance(trace, SyscallTrace)
        assert trace.enabled is False
        assert trace.error == "No active trace"

    @patch("elle.daemon.telemetry.ebpf.syscall_manager.create_trace_with_explanation")
    def test_stop_trace_with_summarizer(self, mock_create_trace):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        summary = _make_summary()
        expected_trace = _make_trace(summary=summary)
        mock_create_trace.return_value = expected_trace

        mock_summarizer = MagicMock()
        mock_summarizer.get_summary.return_value = summary

        mock_program = MagicMock()

        mgr = SyscallManager()
        mgr._active_trace = True
        mgr._summarizer = mock_summarizer
        mgr._program = mock_program
        mgr._polling = False

        trace = mgr.stop_trace()

        assert trace is expected_trace
        assert mgr._active_trace is False
        assert mgr._summarizer is None
        mock_summarizer.get_summary.assert_called_once()
        mock_create_trace.assert_called_once_with(summary)
        mock_program.clear_traced_pids.assert_called_once()
        mock_program.set_syscall_callback.assert_called_once_with(None)

    def test_stop_trace_without_summarizer(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mock_program = MagicMock()

        mgr = SyscallManager()
        mgr._active_trace = True
        mgr._summarizer = None
        mgr._program = mock_program

        trace = mgr.stop_trace()

        assert trace.enabled is True
        assert trace.error == "No summarizer available"
        assert mgr._active_trace is False

    def test_stop_trace_joins_poll_thread(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True

        mock_program = MagicMock()

        mgr = SyscallManager()
        mgr._active_trace = True
        mgr._summarizer = None
        mgr._program = mock_program
        mgr._poll_thread = mock_thread
        mgr._polling = True

        mgr.stop_trace()

        assert mgr._polling is False
        mock_thread.join.assert_called_once_with(timeout=1.0)
        assert mgr._poll_thread is None

    def test_stop_trace_skips_join_when_thread_not_alive(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False

        mock_program = MagicMock()

        mgr = SyscallManager()
        mgr._active_trace = True
        mgr._summarizer = None
        mgr._program = mock_program
        mgr._poll_thread = mock_thread

        mgr.stop_trace()

        mock_thread.join.assert_not_called()
        assert mgr._poll_thread is None


# ===================================================================
# SyscallManager._on_syscall_event
# ===================================================================


class TestOnSyscallEvent:
    """Tests for the internal event callback."""

    def test_event_forwarded_to_summarizer(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mock_summarizer = MagicMock()
        event = _make_event()

        mgr = SyscallManager()
        mgr._summarizer = mock_summarizer

        mgr._on_syscall_event(event)

        mock_summarizer.add_event.assert_called_once_with(event)

    def test_event_ignored_when_no_summarizer(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        mgr._summarizer = None

        # Should not raise
        mgr._on_syscall_event(_make_event())


# ===================================================================
# SyscallManager._poll_loop
# ===================================================================


class TestPollLoop:
    """Tests for the background polling loop."""

    def test_poll_loop_calls_program_poll(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mock_program = MagicMock()
        call_count = 0

        def stop_after_a_few(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                mgr._polling = False

        mock_program.poll.side_effect = stop_after_a_few

        mgr = SyscallManager()
        mgr._program = mock_program
        mgr._polling = True

        mgr._poll_loop()

        assert mock_program.poll.call_count >= 3

    def test_poll_loop_handles_exception(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mock_program = MagicMock()
        call_count = 0

        def raise_then_stop(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("poll failed")
            mgr._polling = False

        mock_program.poll.side_effect = raise_then_stop

        mgr = SyscallManager()
        mgr._program = mock_program
        mgr._polling = True

        # Should not raise -- exceptions are caught internally
        mgr._poll_loop()

        assert call_count >= 2

    def test_poll_loop_exits_when_polling_false(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        mgr._program = MagicMock()
        mgr._polling = False

        # Should return immediately
        mgr._poll_loop()

        mgr._program.poll.assert_not_called()

    def test_poll_loop_skips_poll_when_no_program(self):
        """If _program is None the loop should still iterate and exit cleanly."""
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        mgr._program = None
        mgr._polling = True

        # Use a thread so we can stop the loop after a short pause
        def stop_soon():
            time.sleep(0.05)
            mgr._polling = False

        stopper = threading.Thread(target=stop_soon, daemon=True)
        stopper.start()

        mgr._poll_loop()  # blocks until _polling becomes False

        stopper.join(timeout=2)


# ===================================================================
# SyscallManager.trace_command (context manager)
# ===================================================================


class TestTraceCommandContextManager:
    """Tests for the trace_command context manager."""

    def test_trace_command_when_start_fails(self):
        """When tracing is unavailable the context manager should not raise."""
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        # Not initialized, start_trace will fail
        with mgr.trace_command("ls", 123):
            pass  # body executes even though tracing failed

        # Manager should still be in a clean state
        assert mgr.is_tracing is False

    @patch("elle.daemon.telemetry.ebpf.syscall_manager.create_trace_with_explanation")
    @patch("elle.daemon.telemetry.ebpf.syscall_manager.create_summarizer")
    def test_trace_command_full_lifecycle(self, mock_create_summarizer, mock_create_trace):
        """Full context-manager lifecycle: enter, body, exit."""
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        summary = _make_summary()
        expected_trace = _make_trace(summary=summary)

        mock_summarizer = MagicMock()
        mock_summarizer.get_summary.return_value = summary
        mock_create_summarizer.return_value = mock_summarizer
        mock_create_trace.return_value = expected_trace

        mock_program = MagicMock()
        mock_program.add_traced_pid.return_value = True

        mgr = SyscallManager()
        mgr._initialized = True
        mgr._program = mock_program

        with mgr.trace_command("ls -la", 1234, cwd=Path("/tmp")):
            # Inside the body, trace should have been started
            pass

        # After exit, stop_trace path should have been invoked
        mock_summarizer.get_summary.assert_called_once()
        mock_create_trace.assert_called_once_with(summary)

        # Cleanup
        mgr._polling = False
        if mgr._poll_thread and mgr._poll_thread.is_alive():
            mgr._poll_thread.join(timeout=2)

    def test_trace_command_with_init_error_does_not_raise(self):
        """Context manager with an init error should not raise."""
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        mgr._init_error = "BCC not available"

        with mgr.trace_command("echo test", 999):
            pass

        assert mgr.is_tracing is False

    def test_trace_command_yields_none(self):
        """The context manager yields None."""
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        with mgr.trace_command("ls", 1) as val:
            assert val is None


# ===================================================================
# SyscallManager.get_current_summary
# ===================================================================


class TestGetCurrentSummary:
    """Tests for SyscallManager.get_current_summary."""

    def test_returns_none_when_no_summarizer(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        assert mgr.get_current_summary() is None

    def test_returns_summary_from_summarizer(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        summary = _make_summary()
        mock_summarizer = MagicMock()
        mock_summarizer.get_summary.return_value = summary

        mgr = SyscallManager()
        mgr._summarizer = mock_summarizer

        result = mgr.get_current_summary()

        assert result is summary
        mock_summarizer.get_summary.assert_called_once()


# ===================================================================
# SyscallManager.cleanup
# ===================================================================


class TestCleanup:
    """Tests for SyscallManager.cleanup."""

    def test_cleanup_when_nothing_active(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        # Should not raise
        mgr.cleanup()
        assert mgr._initialized is False

    @patch("elle.daemon.telemetry.ebpf.syscall_manager.create_trace_with_explanation")
    def test_cleanup_stops_active_trace(self, mock_create_trace):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mock_create_trace.return_value = _make_trace()

        mock_summarizer = MagicMock()
        mock_summarizer.get_summary.return_value = _make_summary()

        mock_program = MagicMock()
        mgr = SyscallManager()
        mgr._active_trace = True
        mgr._program = mock_program
        mgr._summarizer = mock_summarizer

        mgr.cleanup()

        assert mgr._active_trace is False
        assert mgr._program is None
        assert mgr._initialized is False
        mock_program.unload.assert_called_once()

    def test_cleanup_unloads_program_without_active_trace(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mock_program = MagicMock()
        mgr = SyscallManager()
        mgr._program = mock_program

        mgr.cleanup()

        mock_program.unload.assert_called_once()
        assert mgr._program is None

    def test_cleanup_tolerates_no_program(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        mgr._program = None
        mgr.cleanup()
        assert mgr._initialized is False


# ===================================================================
# Module-level functions: get_syscall_manager
# ===================================================================


class TestGetSyscallManager:
    """Tests for the module-level singleton accessor."""

    def test_returns_syscall_manager_instance(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import (
            SyscallManager,
            get_syscall_manager,
        )

        mgr = get_syscall_manager()
        assert isinstance(mgr, SyscallManager)

    def test_returns_same_instance_on_repeated_calls(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import get_syscall_manager

        mgr1 = get_syscall_manager()
        mgr2 = get_syscall_manager()
        assert mgr1 is mgr2

    def test_creates_new_instance_after_reset(self):
        import elle.daemon.telemetry.ebpf.syscall_manager as mod
        from elle.daemon.telemetry.ebpf.syscall_manager import get_syscall_manager

        mgr1 = get_syscall_manager()
        mod._manager = None  # simulate reset
        mgr2 = get_syscall_manager()
        assert mgr1 is not mgr2


# ===================================================================
# Module-level function: is_syscall_tracing_available
# ===================================================================


class TestIsSyscallTracingAvailable:
    """Tests for is_syscall_tracing_available."""

    def test_returns_false_by_default(self):
        from elle.daemon.telemetry.ebpf.syscall_manager import (
            is_syscall_tracing_available,
        )

        # BCC removed, so always False currently
        assert is_syscall_tracing_available() is False

    def test_returns_true_when_initialized(self):
        import elle.daemon.telemetry.ebpf.syscall_manager as mod
        from elle.daemon.telemetry.ebpf.syscall_manager import (
            is_syscall_tracing_available,
        )

        mgr = mod.get_syscall_manager()
        mgr._initialized = True
        assert is_syscall_tracing_available() is True


# ===================================================================
# Module-level function: trace_command_sync
# ===================================================================


class TestTraceCommandSync:
    """Tests for the trace_command_sync convenience function."""

    @patch("elle.daemon.telemetry.ebpf.syscall_manager.get_syscall_manager")
    def test_returns_error_trace_when_start_fails(self, mock_get_mgr):
        from elle.daemon.telemetry.ebpf.syscall_manager import trace_command_sync

        mock_mgr = MagicMock()
        mock_mgr.start_trace.return_value = False
        mock_get_mgr.return_value = mock_mgr

        trace = trace_command_sync("ls", 123)

        assert isinstance(trace, SyscallTrace)
        assert trace.enabled is False
        assert trace.error == "Failed to start trace"
        mock_mgr.stop_trace.assert_not_called()

    @patch("elle.daemon.telemetry.ebpf.syscall_manager.get_syscall_manager")
    def test_calls_wait_for_exit(self, mock_get_mgr):
        from elle.daemon.telemetry.ebpf.syscall_manager import trace_command_sync

        mock_mgr = MagicMock()
        mock_mgr.start_trace.return_value = True
        expected_trace = _make_trace()
        mock_mgr.stop_trace.return_value = expected_trace
        mock_get_mgr.return_value = mock_mgr

        wait_fn = MagicMock()
        trace = trace_command_sync("cat /etc/hosts", 5678, wait_for_exit=wait_fn)

        wait_fn.assert_called_once()
        mock_mgr.stop_trace.assert_called_once()
        assert trace is expected_trace

    @patch("elle.daemon.telemetry.ebpf.syscall_manager.get_syscall_manager")
    def test_handles_wait_for_exit_exception(self, mock_get_mgr):
        from elle.daemon.telemetry.ebpf.syscall_manager import trace_command_sync

        mock_mgr = MagicMock()
        mock_mgr.start_trace.return_value = True
        expected_trace = _make_trace()
        mock_mgr.stop_trace.return_value = expected_trace
        mock_get_mgr.return_value = mock_mgr

        wait_fn = MagicMock(side_effect=RuntimeError("process died"))
        trace = trace_command_sync("bad_cmd", 9999, wait_for_exit=wait_fn)

        # Should still return a trace, not raise
        assert trace is expected_trace
        mock_mgr.stop_trace.assert_called_once()

    @patch("elle.daemon.telemetry.ebpf.syscall_manager.get_syscall_manager")
    def test_no_wait_for_exit(self, mock_get_mgr):
        from elle.daemon.telemetry.ebpf.syscall_manager import trace_command_sync

        mock_mgr = MagicMock()
        mock_mgr.start_trace.return_value = True
        expected_trace = _make_trace()
        mock_mgr.stop_trace.return_value = expected_trace
        mock_get_mgr.return_value = mock_mgr

        trace = trace_command_sync("echo hi", 111, wait_for_exit=None)

        mock_mgr.stop_trace.assert_called_once()
        assert trace is expected_trace

    @patch("elle.daemon.telemetry.ebpf.syscall_manager.get_syscall_manager")
    def test_passes_cwd_to_start_trace(self, mock_get_mgr):
        from elle.daemon.telemetry.ebpf.syscall_manager import trace_command_sync

        mock_mgr = MagicMock()
        mock_mgr.start_trace.return_value = False
        mock_get_mgr.return_value = mock_mgr

        trace_command_sync("ls", 100, cwd=Path("/home/user"))

        mock_mgr.start_trace.assert_called_once_with("ls", 100, Path("/home/user"))


# ===================================================================
# Thread-safety sanity checks
# ===================================================================


class TestThreadSafety:
    """Basic thread-safety smoke tests."""

    def test_concurrent_get_syscall_manager(self):
        """Multiple threads calling get_syscall_manager should get the same instance."""
        from elle.daemon.telemetry.ebpf.syscall_manager import get_syscall_manager

        results = []
        errors = []

        def worker():
            try:
                results.append(get_syscall_manager())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        assert len(results) == 10
        assert all(r is results[0] for r in results)

    def test_concurrent_stop_trace_does_not_raise(self):
        """Calling stop_trace from multiple threads should not crash."""
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        errors = []

        def worker():
            try:
                mgr.stop_trace()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors


# ===================================================================
# Edge cases and integration-style tests
# ===================================================================


class TestEdgeCases:
    """Edge-case and regression tests."""

    @patch("elle.daemon.telemetry.ebpf.syscall_manager.create_summarizer")
    @patch("elle.daemon.telemetry.ebpf.syscall_manager.create_trace_with_explanation")
    def test_start_stop_roundtrip(self, mock_create_trace, mock_create_summarizer):
        """Full start -> stop roundtrip."""
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        summary = _make_summary()
        expected_trace = _make_trace(summary=summary)

        mock_summarizer = MagicMock()
        mock_summarizer.get_summary.return_value = summary
        mock_create_summarizer.return_value = mock_summarizer
        mock_create_trace.return_value = expected_trace

        mock_program = MagicMock()
        mock_program.add_traced_pid.return_value = True

        mgr = SyscallManager()
        mgr._initialized = True
        mgr._program = mock_program

        assert mgr.start_trace("whoami", 42) is True
        assert mgr.is_tracing is True

        trace = mgr.stop_trace()

        assert trace is expected_trace
        assert mgr.is_tracing is False

        # Cleanup
        mgr._polling = False
        if mgr._poll_thread and mgr._poll_thread.is_alive():
            mgr._poll_thread.join(timeout=2)

    def test_double_stop_trace_is_safe(self):
        """Calling stop_trace twice should not raise."""
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        t1 = mgr.stop_trace()
        t2 = mgr.stop_trace()
        assert t1.enabled is False
        assert t2.enabled is False

    def test_cleanup_then_stop_trace(self):
        """Cleanup followed by stop_trace should not raise."""
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        mgr.cleanup()
        trace = mgr.stop_trace()
        assert trace.enabled is False

    def test_stop_internal_clears_program_callbacks(self):
        """_stop_trace_internal should clear callbacks on the program."""
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mock_program = MagicMock()
        mgr = SyscallManager()
        mgr._active_trace = True
        mgr._program = mock_program
        mgr._summarizer = None

        mgr._stop_trace_internal()

        mock_program.clear_traced_pids.assert_called_once()
        mock_program.set_syscall_callback.assert_called_once_with(None)

    def test_stop_internal_without_program(self):
        """_stop_trace_internal with _program=None should not raise."""
        from elle.daemon.telemetry.ebpf.syscall_manager import SyscallManager

        mgr = SyscallManager()
        mgr._active_trace = True
        mgr._program = None
        mgr._summarizer = None

        trace = mgr._stop_trace_internal()

        assert trace.enabled is True
        assert trace.error == "No summarizer available"
        assert mgr._active_trace is False
