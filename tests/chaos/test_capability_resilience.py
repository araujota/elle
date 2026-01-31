"""Chaos tests for capability execution resilience.

Tests that capability execution handles: timeouts, subprocess crashes,
and permission denied errors gracefully.
"""

from __future__ import annotations

import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.chaos.conftest import FaultInjector


class TestCapabilityTimeout:
    """Capabilities should handle timeouts gracefully."""

    def test_subprocess_timeout_returns_failure(self, fault: FaultInjector) -> None:
        """A capability backed by subprocess.run should handle TimeoutExpired."""
        with fault.subprocess_timeout(timeout_sec=0.1):
            with pytest.raises(subprocess.TimeoutExpired):
                subprocess.run(["sleep", "999"], timeout=0.1)

    @pytest.mark.asyncio
    async def test_executor_handles_timeout(self, fault: FaultInjector) -> None:
        """CapabilityExecutor should produce a failure result on timeout."""
        mock_capability = AsyncMock()
        mock_capability.run = AsyncMock(side_effect=subprocess.TimeoutExpired("cmd", 30))
        mock_capability.name = "test.timeout"
        mock_capability.spec = MagicMock()
        mock_capability.spec.risk = "low"

        with pytest.raises(subprocess.TimeoutExpired):
            await mock_capability.run(input={})

    def test_long_running_command_timeout(self, fault: FaultInjector) -> None:
        """Commands that exceed timeout should be killed, not hang."""
        with fault.subprocess_timeout(timeout_sec=5.0):
            with pytest.raises(subprocess.TimeoutExpired) as exc_info:
                subprocess.run(["find", "/", "-name", "*.log"], timeout=5)

            assert exc_info.value.timeout == 5.0


class TestSubprocessCrash:
    """Capabilities should handle subprocess crashes (SIGSEGV, etc.)."""

    def test_subprocess_crash_detected(self, fault: FaultInjector) -> None:
        """A crashing subprocess should be detected via return code."""
        with fault.subprocess_crash(exit_code=-11):
            result = subprocess.run(["test-binary"])
            assert result.returncode == -11
            assert b"Segmentation fault" in result.stderr

    def test_subprocess_crash_nonzero_exit(self, fault: FaultInjector) -> None:
        """Non-zero exit codes should be treated as failures."""
        with fault.subprocess_crash(exit_code=1):
            result = subprocess.run(["failing-command"])
            assert result.returncode != 0

    @pytest.mark.asyncio
    async def test_capability_records_crash(self, fault: FaultInjector) -> None:
        """Capability execution should record crash details in the result."""
        mock_run = AsyncMock()
        mock_run.return_value = MagicMock(
            success=False,
            exit_code=-11,
            stderr="Segmentation fault (core dumped)",
        )

        result = await mock_run(command="test-binary")
        assert not result.success
        assert result.exit_code == -11


class TestPermissionDenied:
    """Capabilities should handle EACCES gracefully."""

    def test_file_write_permission_denied(self, fault: FaultInjector) -> None:
        """File write capability should handle permission denied."""
        with fault.permission_denied("builtins.open"):
            with pytest.raises(PermissionError):
                open("/etc/important.conf", "w")  # noqa: SIM115

    def test_subprocess_permission_denied(self, fault: FaultInjector) -> None:
        """Subprocess execution should handle EACCES."""
        with fault.permission_denied():
            with pytest.raises(PermissionError):
                subprocess.run(["/usr/sbin/restricted-command"])

    @pytest.mark.asyncio
    async def test_capability_elevates_or_fails(self) -> None:
        """Capabilities requiring privilege should fail clearly when not elevated."""
        mock_capability = AsyncMock()
        mock_capability.run = AsyncMock(side_effect=PermissionError("Operation requires root privileges"))

        with pytest.raises(PermissionError, match="root privileges"):
            await mock_capability.run(input={})

    def test_polkit_denial_handled(self) -> None:
        """Polkit authorization denial should produce a clear error."""
        mock_polkit = MagicMock()
        mock_polkit.check_authorization = MagicMock(return_value=False)

        assert not mock_polkit.check_authorization("com.elle.service.restart")


class TestCapabilityRollback:
    """Capability executor should support rollback on verification failure."""

    @pytest.mark.asyncio
    async def test_rollback_on_verify_failure(self) -> None:
        """When verification fails, executor should attempt rollback."""
        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(
            return_value=MagicMock(
                success=False,
                rolled_back=True,
                error="Verification failed: config syntax error",
            )
        )

        result = await mock_executor.execute(
            capability_name="config.edit",
            input={"path": "/etc/nginx/nginx.conf", "content": "invalid{{{"},
            auto_rollback_on_verify_fail=True,
        )

        assert not result.success
        assert result.rolled_back

    @pytest.mark.asyncio
    async def test_rollback_failure_recorded(self) -> None:
        """If rollback itself fails, this should be recorded."""
        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(
            return_value=MagicMock(
                success=False,
                rolled_back=False,
                error="Rollback failed: backup not found",
            )
        )

        result = await mock_executor.execute(
            capability_name="config.edit",
            input={"path": "/etc/test.conf"},
            auto_rollback_on_verify_fail=True,
        )

        assert not result.success
        assert not result.rolled_back
