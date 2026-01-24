"""Tests for the safe subprocess runner."""

import pytest

from elle.cli.subprocess_runner import (
    CommandDeniedError,
    DenyReason,
    RunMode,
    SubprocessResult,
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
        assert len(captured) >= 2
        assert any("line1" in line for line in captured)
        assert any("line2" in line for line in captured)

    def test_streaming_timeout(self) -> None:
        """Streaming mode should respect timeout."""
        result = run(
            "sleep 10",
            mode=RunMode.STREAM,
            timeout=0.1,
        )
        assert result.timed_out is True
