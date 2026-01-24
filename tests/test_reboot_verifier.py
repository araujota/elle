"""Tests for reboot module verifier."""

import asyncio
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest

from elle.daemon.reboot.models import PendingVerification
from elle.daemon.reboot.verifier import (
    VerificationResult,
    create_default_verifications,
    create_kernel_update_verifications,
    create_service_restart_verifications,
    run_command_check,
    run_file_check,
    run_port_check,
    run_service_check,
    run_verification,
    run_all_verifications,
)


class TestRunCommandCheck:
    """Tests for run_command_check function."""

    @pytest.mark.asyncio
    async def test_successful_command(self):
        """Test running a successful command."""
        passed, exit_code, stdout, stderr = await run_command_check("echo hello")

        assert passed is True
        assert exit_code == 0
        assert "hello" in stdout
        assert stderr == ""

    @pytest.mark.asyncio
    async def test_failed_command(self):
        """Test running a failed command."""
        passed, exit_code, stdout, stderr = await run_command_check(
            "exit 1",
            expected_exit_code=0,
        )

        assert passed is False
        assert exit_code == 1

    @pytest.mark.asyncio
    async def test_expected_nonzero_exit(self):
        """Test command with expected non-zero exit."""
        passed, exit_code, stdout, stderr = await run_command_check(
            "exit 2",
            expected_exit_code=2,
        )

        assert passed is True
        assert exit_code == 2

    @pytest.mark.asyncio
    async def test_output_contains_check(self):
        """Test checking output contains string."""
        # Passing case
        passed, _, stdout, _ = await run_command_check(
            "echo 'hello world'",
            expected_output_contains="world",
        )
        assert passed is True

        # Failing case
        passed, _, _, _ = await run_command_check(
            "echo 'hello world'",
            expected_output_contains="missing",
        )
        assert passed is False

    @pytest.mark.asyncio
    async def test_command_timeout(self):
        """Test command timeout."""
        passed, exit_code, stdout, stderr = await run_command_check(
            "sleep 10",
            timeout=0.1,
        )

        assert passed is False
        assert exit_code == -1
        assert "timed out" in stderr.lower()


class TestRunServiceCheck:
    """Tests for run_service_check function."""

    @pytest.mark.asyncio
    async def test_service_check_format(self):
        """Test that service check uses correct command."""
        # We can't reliably test actual service status in tests,
        # but we can verify the check runs without error
        passed, exit_code, stdout, stderr = await run_service_check(
            "nonexistent-service-for-testing"
        )

        # Should fail gracefully for non-existent service
        assert passed is False


class TestRunFileCheck:
    """Tests for run_file_check function."""

    @pytest.mark.asyncio
    async def test_existing_file(self):
        """Test checking existing file."""
        with NamedTemporaryFile(delete=False) as f:
            temp_path = f.name

        try:
            passed, exit_code, stdout, stderr = await run_file_check(temp_path)

            assert passed is True
            assert exit_code == 0
            assert "exists" in stdout
        finally:
            Path(temp_path).unlink()

    @pytest.mark.asyncio
    async def test_nonexistent_file(self):
        """Test checking non-existent file."""
        passed, exit_code, stdout, stderr = await run_file_check(
            "/nonexistent/path/file.txt"
        )

        assert passed is False
        assert exit_code == 1
        assert "does not exist" in stdout


class TestRunPortCheck:
    """Tests for run_port_check function."""

    @pytest.mark.asyncio
    async def test_closed_port(self):
        """Test checking a closed port."""
        # Use a high port that's unlikely to be in use
        passed, exit_code, stdout, stderr = await run_port_check(
            "127.0.0.1:59999",
            timeout=1.0,
        )

        assert passed is False

    @pytest.mark.asyncio
    async def test_port_spec_parsing(self):
        """Test port specification parsing."""
        # Port only
        passed, _, _, _ = await run_port_check("59999", timeout=0.5)
        assert passed is False  # Will fail but should parse correctly

        # Invalid port
        passed, exit_code, _, stderr = await run_port_check("invalid", timeout=0.5)
        assert passed is False
        assert "Invalid port" in stderr


class TestRunVerification:
    """Tests for run_verification function."""

    @pytest.mark.asyncio
    async def test_run_command_verification(self):
        """Test running a command verification."""
        v = PendingVerification(
            step_index=0,
            check_type="command",
            check_command="echo test",
            expected_exit_code=0,
        )

        result = await run_verification(v)

        assert result["passed"] is True
        assert result["exit_code"] == 0
        assert result["check_type"] == "command"
        assert "executed_at" in result

    @pytest.mark.asyncio
    async def test_run_file_verification(self):
        """Test running a file verification."""
        with NamedTemporaryFile(delete=False) as f:
            temp_path = f.name

        try:
            v = PendingVerification(
                step_index=0,
                check_type="file_exists",
                check_command=temp_path,
            )

            result = await run_verification(v)

            assert result["passed"] is True
        finally:
            Path(temp_path).unlink()

    @pytest.mark.asyncio
    async def test_unknown_check_type(self):
        """Test handling unknown check type."""
        # Create verification with invalid type by bypassing validation
        v = PendingVerification(
            step_index=0,
            check_type="command",  # Start with valid
            check_command="true",
        )
        # Monkey-patch for test
        v = v.model_copy(update={"check_type": "invalid_type"})

        result = await run_verification(v)

        assert result["passed"] is False
        assert "Unknown check type" in result["stderr"]


class TestRunAllVerifications:
    """Tests for run_all_verifications function."""

    @pytest.mark.asyncio
    async def test_all_pass(self):
        """Test when all verifications pass."""
        verifications = [
            PendingVerification(
                step_index=0,
                check_type="command",
                check_command="true",
                required=True,
            ),
            PendingVerification(
                step_index=1,
                check_type="command",
                check_command="echo hello",
                required=True,
            ),
        ]

        result = await run_all_verifications(verifications)

        assert result.passed is True
        assert result.all_passed is True
        assert result.required_passed == 2
        assert result.required_failed == 0

    @pytest.mark.asyncio
    async def test_required_fails(self):
        """Test when required verification fails."""
        verifications = [
            PendingVerification(
                step_index=0,
                check_type="command",
                check_command="true",
                required=True,
            ),
            PendingVerification(
                step_index=1,
                check_type="command",
                check_command="false",
                required=True,
            ),
        ]

        result = await run_all_verifications(verifications)

        assert result.passed is False
        assert result.all_passed is False
        assert result.required_passed == 1
        assert result.required_failed == 1

    @pytest.mark.asyncio
    async def test_optional_fails_but_passes(self):
        """Test when only optional verification fails."""
        verifications = [
            PendingVerification(
                step_index=0,
                check_type="command",
                check_command="true",
                required=True,
            ),
            PendingVerification(
                step_index=1,
                check_type="command",
                check_command="false",
                required=False,
            ),
        ]

        result = await run_all_verifications(verifications)

        assert result.passed is True  # Required passed
        assert result.all_passed is False  # Not all passed
        assert result.required_passed == 1
        assert result.optional_failed == 1

    @pytest.mark.asyncio
    async def test_empty_verifications(self):
        """Test with empty verifications list."""
        result = await run_all_verifications([])

        assert result.passed is True  # No requirements to fail
        assert result.all_passed is True


class TestVerificationResult:
    """Tests for VerificationResult model."""

    def test_create_result(self):
        """Test creating a verification result."""
        result = VerificationResult(
            passed=True,
            all_passed=True,
            required_passed=2,
            required_failed=0,
            optional_passed=1,
            optional_failed=0,
        )

        assert result.passed is True
        assert result.critical_failure is False

    def test_critical_failure(self):
        """Test result with critical failure."""
        result = VerificationResult(
            passed=False,
            all_passed=False,
            critical_failure=True,
            required_passed=0,
            required_failed=1,
            optional_passed=0,
            optional_failed=0,
            error="Critical service down",
        )

        assert result.critical_failure is True
        assert result.error is not None


class TestCreateDefaultVerifications:
    """Tests for create_default_verifications function."""

    def test_creates_verifications(self):
        """Test that defaults are created."""
        verifications = create_default_verifications()

        assert len(verifications) >= 1

        # Should include NetworkManager check
        service_checks = [v for v in verifications if v.check_type == "service_active"]
        assert len(service_checks) >= 1

    def test_verifications_are_valid(self):
        """Test that created verifications are valid."""
        verifications = create_default_verifications()

        valid_check_types = (
            "command", "service_active", "file_exists", "port_listening",
            "filesystem_writable", "mount_exists", "kernel_module",
            "dmesg_no_errors", "journal_no_errors", "disk_space", "network_interface"
        )

        for v in verifications:
            assert v.step_index >= 0
            assert v.check_type in valid_check_types
            assert v.check_command


class TestCreateKernelUpdateVerifications:
    """Tests for create_kernel_update_verifications function."""

    def test_includes_kernel_check(self):
        """Test that kernel version check is included."""
        verifications = create_kernel_update_verifications("6.8.0")

        # Should have at least defaults plus kernel check
        assert len(verifications) >= 2

        # Find the kernel check
        kernel_checks = [
            v for v in verifications
            if "uname" in v.check_command
        ]
        assert len(kernel_checks) == 1
        assert kernel_checks[0].expected_output_contains == "6.8.0"
        assert kernel_checks[0].required is True


class TestCreateServiceRestartVerifications:
    """Tests for create_service_restart_verifications function."""

    def test_includes_service_check(self):
        """Test that service check is included."""
        verifications = create_service_restart_verifications("nginx")

        # Should have at least defaults plus service check
        assert len(verifications) >= 2

        # Find the nginx check
        nginx_checks = [
            v for v in verifications
            if v.check_command == "nginx"
        ]
        assert len(nginx_checks) == 1
        assert nginx_checks[0].check_type == "service_active"
        assert nginx_checks[0].required is True
