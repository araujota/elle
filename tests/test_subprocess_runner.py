"""Tests for the safe subprocess runner."""

from unittest.mock import MagicMock, patch

import pytest

from elle.cli.subprocess_runner import (
    MAX_OUTPUT_SIZE,
    CommandDeniedError,
    DenyReason,
    RunMode,
    SubprocessResult,
    _check_denylist_legacy,
    _map_rules_to_deny_reason,
    _run_capture,
    _run_streaming,
    check_denylist,
    run,
    run_safe,
)


class TestDenylist:
    """Tests for command denylist checking."""

    # -------------------------------------------------------------------------
    # Destructive rm commands
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf /",
            "rm -fr /",
            "rm -rf / --no-preserve-root",
            "rm -rf /*",
            "rm -f -r /",
            "rm -rf /etc",
            "rm -rf /usr",
            "rm -rf /var",
            "rm -rf /home",
            "rm -rf /boot",
            "rm -rf ~",
        ],
    )
    def test_blocks_destructive_rm(self, command: str) -> None:
        """Should block destructive rm commands."""
        denied, reason, _ = check_denylist(command)
        assert denied is True
        assert reason == DenyReason.DESTRUCTIVE_RM

    @pytest.mark.parametrize(
        "command",
        [
            "rm file.txt",
            "rm -rf ./build",
            "rm -rf /tmp/test",
            "rm -r mydir",
            "rm -f test.log",
        ],
    )
    def test_allows_safe_rm(self, command: str) -> None:
        """Should allow safe rm commands."""
        denied, reason, _ = check_denylist(command)
        assert denied is False
        assert reason is None

    # -------------------------------------------------------------------------
    # Fork bombs
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "command",
        [
            ":(){:|:&};:",
            ": (){ : | : & }; :",
        ],
    )
    def test_blocks_fork_bombs(self, command: str) -> None:
        """Should block fork bomb patterns."""
        denied, reason, _ = check_denylist(command)
        assert denied is True
        assert reason == DenyReason.FORK_BOMB

    # -------------------------------------------------------------------------
    # Filesystem formatting
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "command",
        [
            "mkfs.ext4 /dev/sda1",
            "mkfs -t ext4 /dev/sda",
            "mkswap /dev/sda2",
            "mke2fs /dev/sdb1",
        ],
    )
    def test_blocks_format_commands(self, command: str) -> None:
        """Should block filesystem formatting commands."""
        denied, reason, _ = check_denylist(command)
        assert denied is True
        assert reason == DenyReason.FILESYSTEM_FORMAT

    # -------------------------------------------------------------------------
    # Raw disk writes
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "command",
        [
            "dd if=/dev/zero of=/dev/sda",
            "dd if=image.iso of=/dev/sdb bs=4M",
            "dd if=/dev/urandom of=/dev/nvme0n1",
        ],
    )
    def test_blocks_dd_to_devices(self, command: str) -> None:
        """Should block dd to block devices."""
        denied, reason, _ = check_denylist(command)
        assert denied is True
        assert reason == DenyReason.RAW_DISK_WRITE

    def test_allows_safe_dd(self) -> None:
        """Should allow dd to regular files."""
        denied, _, _ = check_denylist("dd if=/dev/zero of=./testfile bs=1M count=10")
        assert denied is False

    # -------------------------------------------------------------------------
    # Recursive permission changes
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "command",
        [
            "chmod -R 777 /",
            "chmod -R 777 /etc",
            "chmod -R 777 /usr",
            "chown -R root:root /",
            "chown -R user:user /etc",
        ],
    )
    def test_blocks_recursive_perms(self, command: str) -> None:
        """Should block recursive permission changes on system dirs."""
        denied, reason, _ = check_denylist(command)
        assert denied is True
        assert reason == DenyReason.RECURSIVE_PERMISSION

    def test_allows_safe_chmod(self) -> None:
        """Should allow chmod on user directories."""
        denied, _, _ = check_denylist("chmod -R 755 ./myproject")
        assert denied is False

    # -------------------------------------------------------------------------
    # Pipe to shell
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "command",
        [
            "curl http://example.com/script.sh | bash",
            "wget http://example.com/install.sh | sh",
            "curl -s https://get.something.io | sudo bash",
            "wget -qO- http://example.com | bash -c",
        ],
    )
    def test_blocks_pipe_to_shell(self, command: str) -> None:
        """Should block piping remote content to shell."""
        denied, reason, _ = check_denylist(command)
        assert denied is True
        assert reason == DenyReason.PIPE_TO_SHELL

    def test_allows_safe_curl(self) -> None:
        """Should allow safe curl usage."""
        denied, _, _ = check_denylist("curl http://example.com/data.json")
        assert denied is False

    # -------------------------------------------------------------------------
    # Sudo
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "command",
        [
            "sudo ls",
            "sudo apt update",
            "sudo rm file.txt",
        ],
    )
    def test_blocks_sudo(self, command: str) -> None:
        """Should block any sudo commands."""
        denied, reason, _ = check_denylist(command)
        assert denied is True
        assert reason == DenyReason.SUDO_ATTEMPT

    # -------------------------------------------------------------------------
    # Shutdown commands
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "command",
        [
            "shutdown now",
            "shutdown -h now",
            "reboot",
            "poweroff",
            "halt",
            "init 0",
            "init 6",
            "systemctl reboot",
            "systemctl poweroff",
        ],
    )
    def test_blocks_shutdown(self, command: str) -> None:
        """Should block shutdown/reboot commands."""
        denied, reason, _ = check_denylist(command)
        assert denied is True
        assert reason == DenyReason.SYSTEM_SHUTDOWN

    # -------------------------------------------------------------------------
    # History clearing
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "command",
        [
            "history -c",
            "> ~/.bash_history",
            "rm ~/.bash_history",
            "rm -f ~/.zsh_history",
            "export HISTSIZE=0",
            "unset HISTFILE",
        ],
    )
    def test_blocks_history_clear(self, command: str) -> None:
        """Should block history clearing commands."""
        denied, reason, _ = check_denylist(command)
        assert denied is True
        assert reason == DenyReason.HISTORY_CLEAR


class TestSubprocessResult:
    """Tests for SubprocessResult model."""

    def test_success_property(self) -> None:
        """success should be True for exit code 0."""
        result = SubprocessResult(
            command="ls",
            exit_code=0,
            stdout="file.txt",
            stderr="",
        )
        assert result.success is True
        assert result.failed is False

    def test_failed_property(self) -> None:
        """failed should be True for non-zero exit."""
        result = SubprocessResult(
            command="ls /nonexistent",
            exit_code=1,
            stdout="",
            stderr="No such file",
        )
        assert result.success is False
        assert result.failed is True

    def test_timeout_is_failure(self) -> None:
        """Timeout should count as failure."""
        result = SubprocessResult(
            command="sleep 100",
            exit_code=-1,
            stdout="",
            stderr="Timed out",
            timed_out=True,
        )
        assert result.success is False
        assert result.failed is True

    def test_denied_is_failure(self) -> None:
        """Denied command should count as failure."""
        result = SubprocessResult(
            command="sudo ls",
            exit_code=-1,
            stdout="",
            stderr="Command denied",
            denied=True,
            deny_reason=DenyReason.SUDO_ATTEMPT,
            deny_explanation="No sudo allowed",
        )
        assert result.success is False
        assert result.failed is True


class TestRun:
    """Tests for the run() function."""

    def test_simple_command(self) -> None:
        """Should execute simple commands."""
        result = run("echo hello")
        assert result.success is True
        assert result.exit_code == 0
        assert "hello" in result.stdout

    def test_captures_stdout(self) -> None:
        """Should capture stdout."""
        result = run("echo 'line1' && echo 'line2'")
        assert "line1" in result.stdout
        assert "line2" in result.stdout

    def test_captures_stderr(self) -> None:
        """Should capture stderr."""
        result = run("echo 'error' >&2")
        assert "error" in result.stderr

    def test_captures_exit_code(self) -> None:
        """Should capture exit code."""
        result = run("exit 42")
        assert result.exit_code == 42
        assert result.failed is True

    def test_timeout(self) -> None:
        """Should timeout long-running commands."""
        result = run("sleep 10", timeout=0.1)
        assert result.timed_out is True
        assert result.failed is True
        assert "timed out" in result.stderr.lower()

    def test_denylist_raises(self) -> None:
        """Should raise CommandDeniedError for blocked commands."""
        with pytest.raises(CommandDeniedError) as exc_info:
            run("sudo ls")

        assert exc_info.value.reason == DenyReason.SUDO_ATTEMPT

    def test_denylist_can_be_disabled(self) -> None:
        """Should allow disabling denylist check."""
        # This won't actually run sudo (no sudo available in test),
        # but it shouldn't raise CommandDeniedError
        result = run("sudo --version", check_denylist_flag=False)
        # Will fail because sudo isn't available, but shouldn't be denied
        assert result.denied is False

    def test_working_directory(self, tmp_path) -> None:
        """Should respect working directory."""
        result = run("pwd", cwd=tmp_path)
        assert str(tmp_path) in result.stdout

    def test_environment_variables(self) -> None:
        """Should pass environment variables."""
        result = run("echo $TEST_VAR", env={"TEST_VAR": "hello123"})
        assert "hello123" in result.stdout


class TestRunSafe:
    """Tests for run_safe() convenience function."""

    def test_returns_result_on_success(self) -> None:
        """Should return normal result for allowed commands."""
        result = run_safe("echo test")
        assert result.success is True
        assert result.denied is False

    def test_returns_denied_result(self) -> None:
        """Should return denied result instead of raising."""
        result = run_safe("sudo ls")
        assert result.denied is True
        assert result.deny_reason == DenyReason.SUDO_ATTEMPT
        assert result.deny_explanation is not None
        assert result.failed is True


class TestRunStreaming:
    """Tests for streaming mode."""

    def test_streaming_captures_output(self) -> None:
        """Streaming mode should still capture output."""
        captured: list[str] = []

        def callback(line: str) -> None:
            captured.append(line)

        result = run(
            "echo 'line1' && echo 'line2'",
            mode=RunMode.STREAM,
            stream_callback=callback,
        )

        assert result.success is True
        all_output = "".join(captured)
        assert "line1" in all_output
        assert "line2" in all_output

    def test_streaming_timeout(self) -> None:
        """Streaming mode should respect timeout."""
        result = run(
            "sleep 10",
            mode=RunMode.STREAM,
            timeout=0.1,
        )
        assert result.timed_out is True


# =============================================================================
# New tests for uncovered branches
# =============================================================================


class TestCheckDenylistFallback:
    """Tests for check_denylist fallback paths (lines 220-227)."""

    def test_import_error_falls_back_to_legacy(self) -> None:
        """When policy module import fails, should fall back to legacy check."""
        with patch(
            "elle.cli.subprocess_runner.check_denylist.__module__",
            new="elle.cli.subprocess_runner",
        ):
            # Simulate ImportError by patching the policy import inside check_denylist
            original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

            def mock_import(name, *args, **kwargs):
                if name == "elle.policy":
                    raise ImportError("No module named 'elle.policy'")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                denied, reason, explanation = check_denylist("sudo ls")
                assert denied is True
                assert reason == DenyReason.SUDO_ATTEMPT

    def test_policy_evaluation_error_falls_back_to_legacy(self) -> None:
        """When policy evaluation raises an error, should fall back to legacy."""
        with patch(
            "elle.cli.subprocess_runner.check_denylist.__module__",
            new="elle.cli.subprocess_runner",
        ):
            original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

            def mock_import(name, *args, **kwargs):
                if name == "elle.policy":
                    raise RuntimeError("Policy engine broken")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                denied, reason, explanation = check_denylist("rm -rf /")
                assert denied is True
                assert reason == DenyReason.DESTRUCTIVE_RM

    def test_safe_command_with_import_error_fallback(self) -> None:
        """Safe command through legacy fallback should be allowed."""
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def mock_import(name, *args, **kwargs):
            if name == "elle.policy":
                raise ImportError("No module named 'elle.policy'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            denied, reason, explanation = check_denylist("echo hello")
            assert denied is False
            assert reason is None


class TestMapRulesToDenyReason:
    """Tests for _map_rules_to_deny_reason (lines 240, 262)."""

    def test_empty_matched_rules_returns_default(self) -> None:
        """Empty matched rules tuple should return DESTRUCTIVE_RM default."""
        result = _map_rules_to_deny_reason(())
        assert result == DenyReason.DESTRUCTIVE_RM

    def test_unknown_rule_returns_default_fallback(self) -> None:
        """Unknown rule ID should return DESTRUCTIVE_RM default fallback."""
        result = _map_rules_to_deny_reason(("unknown-rule-xyz",))
        assert result == DenyReason.DESTRUCTIVE_RM

    def test_deny_sudo_rule(self) -> None:
        """deny-sudo rule should map to SUDO_ATTEMPT."""
        result = _map_rules_to_deny_reason(("deny-sudo",))
        assert result == DenyReason.SUDO_ATTEMPT

    def test_deny_fork_bomb_rule(self) -> None:
        """deny-fork-bomb rule should map to FORK_BOMB."""
        result = _map_rules_to_deny_reason(("deny-fork-bomb",))
        assert result == DenyReason.FORK_BOMB

    def test_deny_filesystem_format_rule(self) -> None:
        """deny-filesystem-format rule should map to FILESYSTEM_FORMAT."""
        result = _map_rules_to_deny_reason(("deny-filesystem-format",))
        assert result == DenyReason.FILESYSTEM_FORMAT

    def test_deny_raw_disk_write_rule(self) -> None:
        """deny-raw-disk-write rule should map to RAW_DISK_WRITE."""
        result = _map_rules_to_deny_reason(("deny-raw-disk-write",))
        assert result == DenyReason.RAW_DISK_WRITE

    def test_deny_recursive_perm_rule(self) -> None:
        """deny-recursive-perm rule should map to RECURSIVE_PERMISSION."""
        result = _map_rules_to_deny_reason(("deny-recursive-perm",))
        assert result == DenyReason.RECURSIVE_PERMISSION

    def test_deny_pipe_to_shell_rule(self) -> None:
        """deny-pipe-to-shell rule should map to PIPE_TO_SHELL."""
        result = _map_rules_to_deny_reason(("deny-pipe-to-shell",))
        assert result == DenyReason.PIPE_TO_SHELL

    def test_deny_shutdown_rule(self) -> None:
        """deny-shutdown rule should map to SYSTEM_SHUTDOWN."""
        result = _map_rules_to_deny_reason(("deny-shutdown",))
        assert result == DenyReason.SYSTEM_SHUTDOWN

    def test_deny_history_clear_rule(self) -> None:
        """deny-history-clear rule should map to HISTORY_CLEAR."""
        result = _map_rules_to_deny_reason(("deny-history-clear",))
        assert result == DenyReason.HISTORY_CLEAR

    def test_rule_prefix_matching(self) -> None:
        """Rule with suffix should still match by prefix."""
        result = _map_rules_to_deny_reason(("deny-sudo-escalation",))
        assert result == DenyReason.SUDO_ATTEMPT

    def test_first_rule_takes_priority(self) -> None:
        """First matched rule in tuple should determine the reason."""
        result = _map_rules_to_deny_reason(("deny-shutdown", "deny-sudo"))
        assert result == DenyReason.SYSTEM_SHUTDOWN


class TestCheckDenylistLegacy:
    """Tests for _check_denylist_legacy (lines 277-336)."""

    def test_sudo_blocked(self) -> None:
        """Should block sudo commands."""
        denied, reason, explanation = _check_denylist_legacy("sudo apt update")
        assert denied is True
        assert reason == DenyReason.SUDO_ATTEMPT
        assert "Polkit" in explanation

    def test_sudo_with_leading_whitespace(self) -> None:
        """Should strip and detect sudo with leading whitespace."""
        denied, reason, _ = _check_denylist_legacy("  sudo ls")
        assert denied is True
        assert reason == DenyReason.SUDO_ATTEMPT

    def test_destructive_rm_blocked(self) -> None:
        """Should block destructive rm."""
        denied, reason, explanation = _check_denylist_legacy("rm -rf /")
        assert denied is True
        assert reason == DenyReason.DESTRUCTIVE_RM
        assert "destroy" in explanation.lower() or "critical" in explanation.lower()

    def test_fork_bomb_blocked(self) -> None:
        """Should block fork bombs."""
        denied, reason, explanation = _check_denylist_legacy(":(){:|:&};:")
        assert denied is True
        assert reason == DenyReason.FORK_BOMB
        assert "fork bomb" in explanation.lower()

    def test_format_command_blocked(self) -> None:
        """Should block filesystem format commands."""
        denied, reason, explanation = _check_denylist_legacy("mkfs.ext4 /dev/sda1")
        assert denied is True
        assert reason == DenyReason.FILESYSTEM_FORMAT

    def test_dd_to_device_blocked(self) -> None:
        """Should block dd to block devices."""
        denied, reason, explanation = _check_denylist_legacy(
            "dd if=/dev/zero of=/dev/sda"
        )
        assert denied is True
        assert reason == DenyReason.RAW_DISK_WRITE

    def test_recursive_perm_blocked(self) -> None:
        """Should block recursive permission changes on system dirs."""
        denied, reason, explanation = _check_denylist_legacy("chmod -R 777 /etc")
        assert denied is True
        assert reason == DenyReason.RECURSIVE_PERMISSION

    def test_pipe_to_shell_blocked(self) -> None:
        """Should block piping remote content to shell."""
        denied, reason, explanation = _check_denylist_legacy(
            "curl http://example.com/s.sh | bash"
        )
        assert denied is True
        assert reason == DenyReason.PIPE_TO_SHELL

    def test_shutdown_blocked(self) -> None:
        """Should block shutdown commands."""
        denied, reason, explanation = _check_denylist_legacy("shutdown now")
        assert denied is True
        assert reason == DenyReason.SYSTEM_SHUTDOWN

    def test_history_clear_blocked(self) -> None:
        """Should block history clearing."""
        denied, reason, explanation = _check_denylist_legacy("history -c")
        assert denied is True
        assert reason == DenyReason.HISTORY_CLEAR

    def test_safe_command_allowed(self) -> None:
        """Safe commands should not be blocked."""
        denied, reason, explanation = _check_denylist_legacy("echo hello")
        assert denied is False
        assert reason is None
        assert explanation is None

    def test_safe_command_ls(self) -> None:
        """ls commands should be allowed."""
        denied, reason, explanation = _check_denylist_legacy("ls -la /tmp")
        assert denied is False
        assert reason is None


class TestOutputTruncation:
    """Tests for output truncation in _run_capture (lines 437, 439)."""

    def test_stdout_truncation(self) -> None:
        """Stdout exceeding MAX_OUTPUT_SIZE should be truncated."""
        large_stdout = "x" * (MAX_OUTPUT_SIZE + 100)
        mock_result = MagicMock()
        mock_result.stdout = large_stdout
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            from pathlib import Path

            result = _run_capture("echo big", Path("/tmp"), 30.0, None)
            assert result.stdout.endswith("... (output truncated)")
            assert len(result.stdout) < len(large_stdout)

    def test_stderr_truncation(self) -> None:
        """Stderr exceeding MAX_OUTPUT_SIZE should be truncated."""
        large_stderr = "e" * (MAX_OUTPUT_SIZE + 100)
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = large_stderr
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            from pathlib import Path

            result = _run_capture("bad cmd", Path("/tmp"), 30.0, None)
            assert result.stderr.endswith("... (output truncated)")
            assert len(result.stderr) < len(large_stderr)

    def test_both_truncated(self) -> None:
        """Both stdout and stderr should be truncated independently."""
        large_out = "o" * (MAX_OUTPUT_SIZE + 50)
        large_err = "e" * (MAX_OUTPUT_SIZE + 50)
        mock_result = MagicMock()
        mock_result.stdout = large_out
        mock_result.stderr = large_err
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            from pathlib import Path

            result = _run_capture("cmd", Path("/tmp"), 30.0, None)
            assert "truncated" in result.stdout
            assert "truncated" in result.stderr


class TestStreamingEdgeCases:
    """Tests for streaming mode edge cases (lines 463, 510, 517-519, 524-533)."""

    def test_streaming_default_callback(self, capsys) -> None:
        """Streaming with no callback should print to stdout (line 463)."""
        result = run(
            "echo 'default_callback_test'",
            mode=RunMode.STREAM,
            stream_callback=None,
        )
        assert result.success is True
        captured = capsys.readouterr()
        assert "default_callback_test" in captured.out

    def test_streaming_captures_stderr(self) -> None:
        """Streaming mode should capture stderr lines (lines 517-519)."""
        captured_lines: list[str] = []

        def cb(line: str) -> None:
            captured_lines.append(line)

        result = run(
            "echo 'stderr_output' >&2",
            mode=RunMode.STREAM,
            stream_callback=cb,
            timeout=5.0,
        )
        # stderr should be in the result or captured via callback
        full_output = result.stderr + "".join(captured_lines)
        assert "stderr_output" in full_output

    def test_streaming_remaining_output_after_poll(self) -> None:
        """process.communicate should capture remaining output (lines 524-528)."""
        captured_lines: list[str] = []

        def cb(line: str) -> None:
            captured_lines.append(line)

        # A fast command where output may be caught by communicate() rather than
        # the polling loop
        result = run(
            "echo 'remaining_test'",
            mode=RunMode.STREAM,
            stream_callback=cb,
            timeout=5.0,
        )
        assert result.success is True
        full = result.stdout + "".join(captured_lines)
        assert "remaining_test" in full

    def test_streaming_communicate_timeout(self) -> None:
        """TimeoutExpired during communicate should be handled (lines 530-538)."""
        import subprocess as sp
        from pathlib import Path

        mock_process = MagicMock()
        mock_process.poll = MagicMock(return_value=0)
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.communicate = MagicMock(
            side_effect=sp.TimeoutExpired("cmd", 1.0)
        )
        mock_process.returncode = None
        mock_process.wait = MagicMock()
        mock_process.kill = MagicMock()

        with patch("subprocess.Popen", return_value=mock_process):
            result = _run_streaming("slow cmd", Path("/tmp"), 30.0, lambda l: None, None)
            assert result.timed_out is True
            assert "timed out" in result.stderr.lower()
            mock_process.kill.assert_called_once()

    def test_streaming_none_stream_skip(self) -> None:
        """None stream in readable list should be skipped (line 510)."""
        import io
        from pathlib import Path

        # Create a mock process that returns None stream in readable set
        mock_stdout = io.StringIO("line1\n")
        mock_stderr = io.StringIO("")

        poll_count = [0]

        def mock_poll():
            poll_count[0] += 1
            if poll_count[0] >= 3:
                return 0
            return None

        mock_process = MagicMock()
        mock_process.poll = mock_poll
        mock_process.stdout = mock_stdout
        mock_process.stderr = mock_stderr
        mock_process.communicate = MagicMock(return_value=("", ""))
        mock_process.returncode = 0
        mock_process.wait = MagicMock()

        captured: list[str] = []

        with patch("subprocess.Popen", return_value=mock_process):
            with patch("select.select", return_value=([None, mock_stdout], [], [])):
                result = _run_streaming(
                    "test", Path("/tmp"), 30.0, lambda l: captured.append(l), None
                )
                # Should complete without error, None stream is skipped
                assert result.exit_code == 0

    def test_streaming_with_remaining_stderr(self) -> None:
        """Remaining stderr from communicate should be captured (lines 527-528)."""
        from pathlib import Path

        mock_process = MagicMock()
        mock_process.poll = MagicMock(return_value=0)
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.communicate = MagicMock(
            return_value=("remaining_stdout\n", "remaining_stderr\n")
        )
        mock_process.returncode = 0

        captured: list[str] = []

        with patch("subprocess.Popen", return_value=mock_process):
            result = _run_streaming(
                "cmd", Path("/tmp"), 30.0, lambda l: captured.append(l), None
            )
            assert "remaining_stdout" in result.stdout
            assert "remaining_stderr" in result.stderr
            # Both remaining outputs should have been passed to callback
            assert any("remaining_stdout" in c for c in captured)
            assert any("remaining_stderr" in c for c in captured)
