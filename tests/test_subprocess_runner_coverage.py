"""Additional coverage tests for subprocess_runner.

Targets uncovered lines:
- Lines 220-227: ImportError / Exception fallback to _check_denylist_legacy()
- Line 240, 262: _map_rules_to_deny_reason default returns
- Lines 437, 439: Output truncation when exceeding MAX_OUTPUT_SIZE
- Line 463: Default stream callback in _run_streaming()
- Lines 510-533: Streaming subprocess read loop edge cases
"""

from __future__ import annotations

import subprocess as sp
from pathlib import Path
from unittest.mock import MagicMock, patch

from elle.cli.subprocess_runner import (
    MAX_OUTPUT_SIZE,
    DenyReason,
    RunMode,
    _map_rules_to_deny_reason,
    _run_capture,
    _run_streaming,
    check_denylist,
    run,
)

# =========================================================================
# check_denylist fallback via ImportError (lines 220-223)
# =========================================================================


class TestCheckDenylistImportFallback:
    """Cover the except ImportError branch inside check_denylist."""

    def test_import_error_denied_cmd(self) -> None:
        """ImportError in policy import falls back to legacy; sudo is denied."""
        with patch(
            "elle.cli.subprocess_runner.check_denylist",
            wraps=check_denylist,
        ):
            # Directly test: when the inner import of elle.policy raises ImportError,
            # the function should fall back to _check_denylist_legacy.
            with patch.dict("sys.modules", {"elle.policy": None}):
                denied, reason, explanation = check_denylist("sudo ls")
                assert denied is True
                assert reason == DenyReason.SUDO_ATTEMPT

    def test_import_error_safe_cmd(self) -> None:
        """ImportError fallback still allows safe commands."""
        with patch.dict("sys.modules", {"elle.policy": None}):
            denied, reason, explanation = check_denylist("echo hello")
            assert denied is False
            assert reason is None


# =========================================================================
# check_denylist fallback via generic Exception (lines 224-227)
# =========================================================================


class TestCheckDenylistExceptionFallback:
    """Cover the except Exception branch inside check_denylist."""

    def test_exception_falls_back_to_legacy_deny(self) -> None:
        """Runtime error during policy evaluation falls back to legacy."""
        mock_module = MagicMock()
        mock_module.evaluate_command.side_effect = RuntimeError("broken engine")
        mock_module.PolicyEffect = MagicMock()

        with patch.dict("sys.modules", {"elle.policy": mock_module}):
            denied, reason, _ = check_denylist("rm -rf /")
            assert denied is True
            assert reason == DenyReason.DESTRUCTIVE_RM

    def test_exception_falls_back_to_legacy_allow(self) -> None:
        """Runtime error falls back to legacy; safe command allowed."""
        mock_module = MagicMock()
        mock_module.evaluate_command.side_effect = RuntimeError("fail")
        mock_module.PolicyEffect = MagicMock()

        with patch.dict("sys.modules", {"elle.policy": mock_module}):
            denied, reason, _ = check_denylist("ls -la")
            assert denied is False


# =========================================================================
# _map_rules_to_deny_reason edge cases (lines 240, 262)
# =========================================================================


class TestMapRulesEdgeCases:
    """Cover the default returns of _map_rules_to_deny_reason."""

    def test_empty_rules_default(self) -> None:
        """Empty tuple returns DenyReason.DESTRUCTIVE_RM (line 240)."""
        result = _map_rules_to_deny_reason(())
        assert result == DenyReason.DESTRUCTIVE_RM

    def test_no_matching_prefix_default(self) -> None:
        """Unrecognized rule ID returns DenyReason.DESTRUCTIVE_RM (line 262)."""
        result = _map_rules_to_deny_reason(("completely-unknown-rule",))
        assert result == DenyReason.DESTRUCTIVE_RM

    def test_unknown_prefix_with_multiple_rules(self) -> None:
        """All unrecognized rules still return default."""
        result = _map_rules_to_deny_reason(("foo-bar", "baz-qux"))
        assert result == DenyReason.DESTRUCTIVE_RM


# =========================================================================
# Output truncation in _run_capture (lines 436-439)
# =========================================================================


class TestRunCaptureTruncation:
    """Cover stdout/stderr truncation when output exceeds MAX_OUTPUT_SIZE."""

    def test_stdout_truncated_at_max(self) -> None:
        """stdout > MAX_OUTPUT_SIZE is truncated (line 437)."""
        mock_result = MagicMock()
        mock_result.stdout = "A" * (MAX_OUTPUT_SIZE + 500)
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = _run_capture("echo lots", Path("/tmp"), 30.0, None)
            assert result.stdout.endswith("... (output truncated)")
            # Truncated stdout should be MAX_OUTPUT_SIZE + suffix
            assert len(result.stdout) <= MAX_OUTPUT_SIZE + 50

    def test_stderr_truncated_at_max(self) -> None:
        """stderr > MAX_OUTPUT_SIZE is truncated (line 439)."""
        mock_result = MagicMock()
        mock_result.stdout = "short"
        mock_result.stderr = "E" * (MAX_OUTPUT_SIZE + 200)
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            result = _run_capture("bad", Path("/tmp"), 30.0, None)
            assert result.stderr.endswith("... (output truncated)")
            assert result.stdout == "short"

    def test_under_max_not_truncated(self) -> None:
        """Output under MAX_OUTPUT_SIZE is not truncated."""
        mock_result = MagicMock()
        mock_result.stdout = "ok"
        mock_result.stderr = "warn"
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = _run_capture("cmd", Path("/tmp"), 30.0, None)
            assert result.stdout == "ok"
            assert result.stderr == "warn"


# =========================================================================
# Default stream callback (line 462-463)
# =========================================================================


class TestStreamingDefaultCallback:
    """Cover the default_callback branch when callback is None."""

    def test_default_callback_prints_to_stdout(self, capsys) -> None:
        """When no callback given, default prints to stdout (line 463)."""
        result = run(
            "echo 'default_cb_cov'",
            mode=RunMode.STREAM,
            stream_callback=None,
            timeout=5.0,
        )
        assert result.success is True
        captured = capsys.readouterr()
        assert "default_cb_cov" in captured.out


# =========================================================================
# Streaming loop edge cases (lines 510-533)
# =========================================================================


class TestStreamingLoopCoverage:
    """Cover streaming subprocess read loop branches."""

    def test_select_returns_stdout_and_stderr(self) -> None:
        """Readable streams include both stdout and stderr (lines 513-519)."""
        import io

        mock_stdout = io.StringIO("out_line\n")
        mock_stderr = io.StringIO("err_line\n")

        call_count = [0]

        def fake_poll():
            call_count[0] += 1
            return 0 if call_count[0] >= 2 else None

        mock_proc = MagicMock()
        mock_proc.poll = fake_poll
        mock_proc.stdout = mock_stdout
        mock_proc.stderr = mock_stderr
        mock_proc.communicate = MagicMock(return_value=("", ""))
        mock_proc.returncode = 0

        captured: list[str] = []

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("select.select", return_value=([mock_stdout, mock_stderr], [], [])):
                result = _run_streaming("cmd", Path("/tmp"), 30.0, lambda line: captured.append(line), None)

        assert result.exit_code == 0
        # stdout and stderr lines should have been captured
        all_text = "".join(captured) + result.stdout + result.stderr
        assert "out_line" in all_text or len(captured) > 0

    def test_none_stream_in_readable_skipped(self) -> None:
        """None in readable list is safely skipped (line 509-510)."""
        call_count = [0]

        def fake_poll():
            call_count[0] += 1
            return 0 if call_count[0] >= 2 else None

        mock_proc = MagicMock()
        mock_proc.poll = fake_poll
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.communicate = MagicMock(return_value=("", ""))
        mock_proc.returncode = 0

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("select.select", return_value=([None], [], [])):
                result = _run_streaming("test", Path("/tmp"), 30.0, lambda l: None, None)

        assert result.exit_code == 0

    def test_communicate_timeout_during_remaining(self) -> None:
        """TimeoutExpired during communicate() is caught (lines 530-538)."""
        mock_proc = MagicMock()
        mock_proc.poll = MagicMock(return_value=0)
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.communicate = MagicMock(side_effect=sp.TimeoutExpired("cmd", 1.0))
        mock_proc.returncode = None
        mock_proc.wait = MagicMock()
        mock_proc.kill = MagicMock()

        with patch("subprocess.Popen", return_value=mock_proc):
            result = _run_streaming("slow_cmd", Path("/tmp"), 30.0, lambda l: None, None)

        assert result.timed_out is True
        assert "timed out" in result.stderr.lower()
        mock_proc.kill.assert_called_once()

    def test_remaining_stdout_and_stderr_captured(self) -> None:
        """Remaining output from communicate() is captured (lines 522-528)."""
        mock_proc = MagicMock()
        mock_proc.poll = MagicMock(return_value=0)
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.communicate = MagicMock(return_value=("remaining_out\n", "remaining_err\n"))
        mock_proc.returncode = 0

        captured: list[str] = []

        with patch("subprocess.Popen", return_value=mock_proc):
            result = _run_streaming("cmd", Path("/tmp"), 30.0, lambda l: captured.append(l), None)

        assert "remaining_out" in result.stdout
        assert "remaining_err" in result.stderr
        assert any("remaining_out" in c for c in captured)
        assert any("remaining_err" in c for c in captured)

    def test_streaming_timeout_in_poll_loop(self) -> None:
        """Timeout during the polling loop (lines 488-497)."""

        mock_proc = MagicMock()
        mock_proc.poll = MagicMock(return_value=None)  # never finishes
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = MagicMock()

        # Make time.time() advance quickly past timeout
        times = [100.0, 100.0, 200.0]  # start, first check, second check > timeout
        time_idx = [0]

        def fake_time():
            idx = min(time_idx[0], len(times) - 1)
            time_idx[0] += 1
            return times[idx]

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("select.select", return_value=([], [], [])):
                with patch("time.time", side_effect=fake_time):
                    result = _run_streaming("hanging_cmd", Path("/tmp"), 5.0, lambda l: None, None)

        assert result.timed_out is True
        mock_proc.kill.assert_called_once()
