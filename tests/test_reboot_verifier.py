from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.reboot.models import (
    PendingVerification,
    RebootIntent,
)
from elle.daemon.reboot.verifier import (
    VerificationResult,
    _analyze_likely_causes,
    _check_disk_space_issues,
    _check_filesystem_state,
    _check_module_state,
    _check_network_state,
    _collect_dmesg_errors,
    _collect_failed_services,
    _collect_journal_errors,
    _get_kernel_version,
    _get_uptime_seconds,
    _suggest_investigation,
    check_critical_services,
    check_network_connectivity,
    collect_failure_diagnostics,
    create_default_verifications,
    create_driver_update_verifications,
    create_fstab_change_verifications,
    create_grub_update_verifications,
    create_kernel_update_verifications,
    create_network_config_verifications,
    create_service_restart_verifications,
    run_all_verifications,
    run_command_check,
    run_disk_space_check,
    run_dmesg_error_check,
    run_file_check,
    run_filesystem_writable_check,
    run_journal_error_check,
    run_kernel_module_check,
    run_mount_check,
    run_network_interface_check,
    run_port_check,
    run_service_check,
    run_verification,
    run_verification_with_retry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_intent(**overrides: Any) -> RebootIntent:
    defaults = {
        "id": "intent-001",
        "goal": "Test reboot",
        "task_description": "Testing reboot flow",
        "reason": "user_requested",
        "status": "rebooting",
        "boot_id": "boot-1",
    }
    defaults.update(overrides)
    return RebootIntent(**defaults)


# ===========================================================================
# run_command_check
# ===========================================================================


class TestRunCommandCheck:
    @pytest.mark.asyncio
    async def test_successful_command(self):
        passed, exit_code, stdout, stderr = await run_command_check("echo hello")
        assert passed is True
        assert exit_code == 0
        assert "hello" in stdout

    @pytest.mark.asyncio
    async def test_failed_command(self):
        passed, exit_code, stdout, stderr = await run_command_check("exit 1", expected_exit_code=0)
        assert passed is False
        assert exit_code == 1

    @pytest.mark.asyncio
    async def test_expected_nonzero_exit(self):
        passed, exit_code, _, _ = await run_command_check("exit 2", expected_exit_code=2)
        assert passed is True

    @pytest.mark.asyncio
    async def test_output_contains_check_pass(self):
        passed, _, stdout, _ = await run_command_check("echo 'hello world'", expected_output_contains="world")
        assert passed is True

    @pytest.mark.asyncio
    async def test_output_contains_check_fail(self):
        passed, _, _, _ = await run_command_check("echo 'hello world'", expected_output_contains="missing")
        assert passed is False

    @pytest.mark.asyncio
    async def test_command_timeout(self):
        passed, exit_code, _, stderr = await run_command_check("sleep 10", timeout=0.1)
        assert passed is False
        assert exit_code == -1
        assert "timed out" in stderr.lower()

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        with patch("asyncio.create_subprocess_shell", side_effect=OSError("fail")):
            passed, exit_code, _, stderr = await run_command_check("echo test")
            assert passed is False
            assert exit_code == -1


# ===========================================================================
# run_service_check
# ===========================================================================


class TestRunServiceCheck:
    @pytest.mark.asyncio
    async def test_nonexistent_service(self):
        passed, _, _, _ = await run_service_check("nonexistent-service-for-testing")
        assert passed is False

    @pytest.mark.asyncio
    async def test_active_service_check(self):
        """Test when the command returns 'active'."""
        with patch("elle.daemon.reboot.verifier.run_command_check", new_callable=AsyncMock) as mock:
            mock.return_value = (True, 0, "active\n", "")
            passed, _, stdout, _ = await run_service_check("some-service")
            assert passed is True

    @pytest.mark.asyncio
    async def test_inactive_service(self):
        """Test when the command returns 'inactive'."""
        with patch("elle.daemon.reboot.verifier.run_command_check", new_callable=AsyncMock) as mock:
            mock.return_value = (True, 0, "inactive\n", "")
            passed, _, _, _ = await run_service_check("some-service")
            assert passed is False


# ===========================================================================
# run_file_check
# ===========================================================================


class TestRunFileCheck:
    @pytest.mark.asyncio
    async def test_existing_file(self):
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
        passed, exit_code, stdout, _ = await run_file_check("/nonexistent/path/file.txt")
        assert passed is False
        assert exit_code == 1
        assert "does not exist" in stdout


# ===========================================================================
# run_port_check
# ===========================================================================


class TestRunPortCheck:
    @pytest.mark.asyncio
    async def test_closed_port(self):
        passed, _, _, _ = await run_port_check("127.0.0.1:59999", timeout=0.5)
        assert passed is False

    @pytest.mark.asyncio
    async def test_port_only(self):
        passed, _, _, _ = await run_port_check("59999", timeout=0.5)
        assert passed is False

    @pytest.mark.asyncio
    async def test_invalid_port(self):
        passed, _, _, stderr = await run_port_check("invalid", timeout=0.5)
        assert passed is False
        assert "Invalid port" in stderr

    @pytest.mark.asyncio
    async def test_socket_exception(self):
        with patch("socket.socket") as mock_sock:
            mock_sock.side_effect = OSError("connection error")
            passed, _, _, stderr = await run_port_check("127.0.0.1:8080")
            assert passed is False


# ===========================================================================
# run_filesystem_writable_check
# ===========================================================================


class TestRunFilesystemWritableCheck:
    @pytest.mark.asyncio
    async def test_writable_path(self):
        with TemporaryDirectory() as tmpdir:
            passed, _, stdout, _ = await run_filesystem_writable_check(tmpdir)
            assert passed is True
            assert "writable" in stdout

    @pytest.mark.asyncio
    async def test_permission_denied(self):
        with patch.object(Path, "write_text", side_effect=PermissionError("denied")):
            passed, _, _, stderr = await run_filesystem_writable_check("/some/path")
            assert passed is False
            assert "Permission denied" in stderr

    @pytest.mark.asyncio
    async def test_read_only_filesystem(self):
        with patch.object(Path, "write_text", side_effect=OSError("Read-only file system")):
            passed, _, _, stderr = await run_filesystem_writable_check("/some/path")
            assert passed is False
            assert "Read-only" in stderr

    @pytest.mark.asyncio
    async def test_other_os_error(self):
        with patch.object(Path, "write_text", side_effect=OSError("something else")):
            passed, _, _, stderr = await run_filesystem_writable_check("/some/path")
            assert passed is False
            assert "Write failed" in stderr

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        with patch.object(Path, "write_text", side_effect=RuntimeError("unexpected")):
            passed, _, _, stderr = await run_filesystem_writable_check("/some/path")
            assert passed is False


# ===========================================================================
# run_disk_space_check
# ===========================================================================


class TestRunDiskSpaceCheck:
    @pytest.mark.asyncio
    async def test_sufficient_space(self):
        with patch("shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(free=1_000_000_000)  # 1GB
            passed, _, stdout, _ = await run_disk_space_check("/:100")
            assert passed is True
            assert "free" in stdout

    @pytest.mark.asyncio
    async def test_insufficient_space(self):
        with patch("shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(free=10_000_000)  # 10MB
            passed, _, _, stderr = await run_disk_space_check("/:500")
            assert passed is False
            assert "only has" in stderr

    @pytest.mark.asyncio
    async def test_default_min_mb(self):
        with patch("shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(free=200_000_000)  # 200MB
            passed, _, _, _ = await run_disk_space_check("/var")
            assert passed is True  # default is 100MB

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        with patch("shutil.disk_usage", side_effect=OSError("fail")):
            passed, _, _, _ = await run_disk_space_check("/")
            assert passed is False


# ===========================================================================
# run_verification (dispatch)
# ===========================================================================


class TestRunVerification:
    @pytest.mark.asyncio
    async def test_command_check_dispatch(self):
        v = PendingVerification(step_index=0, check_type="command", check_command="echo test")
        result = await run_verification(v)
        assert result["passed"] is True
        assert result["check_type"] == "command"
        assert "executed_at" in result

    @pytest.mark.asyncio
    async def test_file_exists_dispatch(self):
        with NamedTemporaryFile(delete=False) as f:
            temp_path = f.name
        try:
            v = PendingVerification(step_index=0, check_type="file_exists", check_command=temp_path)
            result = await run_verification(v)
            assert result["passed"] is True
        finally:
            Path(temp_path).unlink()

    @pytest.mark.asyncio
    async def test_service_active_dispatch(self):
        with patch("elle.daemon.reboot.verifier.run_service_check", new_callable=AsyncMock) as mock:
            mock.return_value = (True, 0, "active", "")
            v = PendingVerification(step_index=0, check_type="service_active", check_command="sshd")
            result = await run_verification(v)
            assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_port_listening_dispatch(self):
        v = PendingVerification(step_index=0, check_type="port_listening", check_command="127.0.0.1:59999")
        result = await run_verification(v)
        assert result["check_type"] == "port_listening"

    @pytest.mark.asyncio
    async def test_filesystem_writable_dispatch(self):
        with TemporaryDirectory() as tmpdir:
            v = PendingVerification(step_index=0, check_type="filesystem_writable", check_command=tmpdir)
            result = await run_verification(v)
            assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_disk_space_dispatch(self):
        with patch("shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(free=2_000_000_000)
            v = PendingVerification(step_index=0, check_type="disk_space", check_command="/:100")
            result = await run_verification(v)
            assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_unknown_check_type(self):
        v = PendingVerification(step_index=0, check_type="command", check_command="true")
        v = v.model_copy(update={"check_type": "invalid_type"})
        result = await run_verification(v)
        assert result["passed"] is False
        assert "Unknown check type" in result["stderr"]

    @pytest.mark.asyncio
    async def test_mount_exists_dispatch(self):
        with patch("elle.daemon.reboot.verifier.run_mount_check", new_callable=AsyncMock) as mock:
            mock.return_value = (True, 0, "/ is mounted", "")
            v = PendingVerification(step_index=0, check_type="mount_exists", check_command="/")
            result = await run_verification(v)
            assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_kernel_module_dispatch(self):
        with patch("elle.daemon.reboot.verifier.run_kernel_module_check", new_callable=AsyncMock) as mock:
            mock.return_value = (True, 0, "Module ext4 is loaded", "")
            v = PendingVerification(step_index=0, check_type="kernel_module", check_command="ext4")
            result = await run_verification(v)
            assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_dmesg_no_errors_dispatch(self):
        with patch("elle.daemon.reboot.verifier.run_dmesg_error_check", new_callable=AsyncMock) as mock:
            mock.return_value = (True, 0, "No errors", "")
            v = PendingVerification(step_index=0, check_type="dmesg_no_errors", check_command="")
            result = await run_verification(v)
            assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_journal_no_errors_dispatch(self):
        with patch("elle.daemon.reboot.verifier.run_journal_error_check", new_callable=AsyncMock) as mock:
            mock.return_value = (True, 0, "No errors", "")
            v = PendingVerification(step_index=0, check_type="journal_no_errors", check_command="")
            result = await run_verification(v)
            assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_network_interface_dispatch(self):
        with patch("elle.daemon.reboot.verifier.run_network_interface_check", new_callable=AsyncMock) as mock:
            mock.return_value = (True, 0, "Interface eth0 is up", "")
            v = PendingVerification(step_index=0, check_type="network_interface", check_command="eth0")
            result = await run_verification(v)
            assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_output_truncated_to_4096(self):
        """Verify stdout and stderr are truncated to 4096 characters."""
        long_output = "x" * 8192
        with patch("elle.daemon.reboot.verifier.run_command_check", new_callable=AsyncMock) as mock:
            mock.return_value = (True, 0, long_output, long_output)
            v = PendingVerification(step_index=0, check_type="command", check_command="echo test")
            result = await run_verification(v)
            assert len(result["stdout"]) == 4096
            assert len(result["stderr"]) == 4096


# ===========================================================================
# run_all_verifications
# ===========================================================================


class TestRunAllVerifications:
    @pytest.mark.asyncio
    async def test_all_pass(self):
        verifications = [
            PendingVerification(step_index=0, check_type="command", check_command="true", required=True),
            PendingVerification(step_index=1, check_type="command", check_command="echo hello", required=True),
        ]
        result = await run_all_verifications(verifications)
        assert result.passed is True
        assert result.all_passed is True
        assert result.required_passed == 2
        assert result.required_failed == 0

    @pytest.mark.asyncio
    async def test_required_fails(self):
        verifications = [
            PendingVerification(step_index=0, check_type="command", check_command="true", required=True),
            PendingVerification(step_index=1, check_type="command", check_command="false", required=True),
        ]
        result = await run_all_verifications(verifications)
        assert result.passed is False
        assert result.required_failed == 1

    @pytest.mark.asyncio
    async def test_optional_fails_overall_passes(self):
        verifications = [
            PendingVerification(step_index=0, check_type="command", check_command="true", required=True),
            PendingVerification(step_index=1, check_type="command", check_command="false", required=False),
        ]
        result = await run_all_verifications(verifications)
        assert result.passed is True
        assert result.all_passed is False
        assert result.optional_failed == 1

    @pytest.mark.asyncio
    async def test_empty_verifications(self):
        result = await run_all_verifications([])
        assert result.passed is True
        assert result.all_passed is True

    @pytest.mark.asyncio
    async def test_sorted_by_step_index(self):
        verifications = [
            PendingVerification(step_index=2, check_type="command", check_command="true", required=True),
            PendingVerification(step_index=0, check_type="command", check_command="true", required=True),
            PendingVerification(step_index=1, check_type="command", check_command="true", required=True),
        ]
        result = await run_all_verifications(verifications)
        assert result.passed is True
        assert result.required_passed == 3

    @pytest.mark.asyncio
    async def test_optional_pass_counted(self):
        """Verify optional_passed counter increments correctly."""
        verifications = [
            PendingVerification(step_index=0, check_type="command", check_command="true", required=False),
            PendingVerification(step_index=1, check_type="command", check_command="true", required=False),
        ]
        result = await run_all_verifications(verifications)
        assert result.passed is True
        assert result.all_passed is True
        assert result.optional_passed == 2
        assert result.required_passed == 0


# ===========================================================================
# VerificationResult model
# ===========================================================================


class TestVerificationResult:
    def test_create_result(self):
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


# ===========================================================================
# create_*_verifications factory functions
# ===========================================================================


class TestCreateDefaultVerifications:
    def test_creates_verifications(self):
        vs = create_default_verifications()
        assert len(vs) >= 1
        service_checks = [v for v in vs if v.check_type == "service_active"]
        assert len(service_checks) >= 1

    def test_step_indices_are_sequential(self):
        vs = create_default_verifications()
        indices = [v.step_index for v in vs]
        assert indices == list(range(len(vs)))


class TestCreateKernelUpdateVerifications:
    def test_includes_kernel_check(self):
        vs = create_kernel_update_verifications("6.8.0")
        kernel_checks = [v for v in vs if "uname" in v.check_command]
        assert len(kernel_checks) == 1
        assert kernel_checks[0].expected_output_contains == "6.8.0"

    def test_includes_dmesg_check(self):
        vs = create_kernel_update_verifications("6.8.0")
        dmesg_checks = [v for v in vs if v.check_type == "dmesg_no_errors"]
        assert len(dmesg_checks) >= 1

    def test_with_expected_modules(self):
        vs = create_kernel_update_verifications("6.8.0", expected_modules=["ext4", "btrfs"])
        module_checks = [v for v in vs if v.check_type == "kernel_module"]
        assert len(module_checks) == 2


class TestCreateDriverUpdateVerifications:
    def test_basic_driver(self):
        vs = create_driver_update_verifications("nvidia")
        module_checks = [v for v in vs if v.check_type == "kernel_module" and v.check_command == "nvidia"]
        assert len(module_checks) == 1

    def test_with_device_path(self):
        vs = create_driver_update_verifications("nvidia", device_path="/dev/nvidia0")
        file_checks = [v for v in vs if v.check_type == "file_exists" and v.check_command == "/dev/nvidia0"]
        assert len(file_checks) == 1

    def test_with_service_name(self):
        vs = create_driver_update_verifications("nvidia", service_name="nvidia-persistenced")
        svc_checks = [v for v in vs if v.check_type == "service_active" and v.check_command == "nvidia-persistenced"]
        assert len(svc_checks) == 1

    def test_includes_dmesg_driver_check(self):
        vs = create_driver_update_verifications("nvidia")
        dmesg_checks = [v for v in vs if v.check_type == "dmesg_no_errors" and "nvidia" in v.check_command]
        assert len(dmesg_checks) == 1


class TestCreateFstabChangeVerifications:
    def test_includes_mount_checks(self):
        vs = create_fstab_change_verifications(["/data", "/backup"])
        mount_checks = [v for v in vs if v.check_type == "mount_exists"]
        assert len(mount_checks) >= 2

    def test_includes_writable_checks(self):
        vs = create_fstab_change_verifications(["/data"], check_writable=True)
        write_checks = [v for v in vs if v.check_type == "filesystem_writable" and v.check_command == "/data"]
        assert len(write_checks) == 1

    def test_skip_writable_for_special_mounts(self):
        vs = create_fstab_change_verifications(["/proc", "/sys", "/dev"], check_writable=True)
        write_checks = [v for v in vs if v.check_type == "filesystem_writable"]
        # /proc, /sys, /dev should be excluded from writable checks
        excluded = [v for v in write_checks if v.check_command in ("/proc", "/sys", "/dev")]
        assert len(excluded) == 0

    def test_includes_journal_mount_errors_check(self):
        vs = create_fstab_change_verifications(["/data"])
        journal_checks = [v for v in vs if v.check_type == "journal_no_errors" and "mount" in v.check_command.lower()]
        assert len(journal_checks) >= 1

    def test_check_writable_false_skips_writable_checks(self):
        """When check_writable=False, no filesystem_writable checks for custom mounts."""
        vs = create_fstab_change_verifications(["/data", "/backup"], check_writable=False)
        # Only the default verifications should have filesystem_writable, not /data or /backup
        write_checks = [
            v for v in vs if v.check_type == "filesystem_writable" and v.check_command in ("/data", "/backup")
        ]
        assert len(write_checks) == 0


class TestCreateServiceRestartVerifications:
    def test_includes_service_check(self):
        vs = create_service_restart_verifications("nginx")
        svc_checks = [v for v in vs if v.check_command == "nginx" and v.check_type == "service_active"]
        assert len(svc_checks) == 1

    def test_with_port(self):
        vs = create_service_restart_verifications("nginx", port=80)
        port_checks = [v for v in vs if v.check_type == "port_listening"]
        assert len(port_checks) == 1
        assert "80" in port_checks[0].check_command

    def test_with_health_endpoint(self):
        vs = create_service_restart_verifications("nginx", health_endpoint="curl http://localhost/health")
        cmd_checks = [v for v in vs if v.check_type == "command" and "curl" in v.check_command]
        assert len(cmd_checks) == 1


class TestCreateGrubUpdateVerifications:
    def test_includes_grub_file_check(self):
        vs = create_grub_update_verifications()
        file_checks = [v for v in vs if v.check_type == "file_exists" and "grub.cfg" in v.check_command]
        assert len(file_checks) == 1

    def test_includes_boot_error_check(self):
        vs = create_grub_update_verifications()
        dmesg_checks = [v for v in vs if v.check_type == "dmesg_no_errors"]
        assert len(dmesg_checks) >= 1


class TestCreateNetworkConfigVerifications:
    def test_with_interfaces(self):
        vs = create_network_config_verifications(interfaces=["eth0", "wlan0"])
        iface_checks = [v for v in vs if v.check_type == "network_interface"]
        assert len(iface_checks) == 2

    def test_with_gateway_check(self):
        vs = create_network_config_verifications(expected_gateway=True)
        gw_checks = [v for v in vs if "ip route" in v.check_command]
        assert len(gw_checks) == 1

    def test_with_dns_check(self):
        vs = create_network_config_verifications(expected_dns=True)
        dns_checks = [v for v in vs if "host" in v.check_command]
        assert len(dns_checks) == 1

    def test_without_gateway_or_dns(self):
        vs = create_network_config_verifications(expected_gateway=False, expected_dns=False)
        gw_checks = [v for v in vs if "ip route" in v.check_command]
        dns_checks = [v for v in vs if "host" in v.check_command]
        assert len(gw_checks) == 0
        assert len(dns_checks) == 0


# ===========================================================================
# _analyze_likely_causes
# ===========================================================================


class TestAnalyzeLikelyCauses:
    def test_read_only_mounts(self):
        causes = _analyze_likely_causes([], [], [], [], ["/"], [], [], [], [])
        assert any("read-only" in c.lower() for c in causes)

    def test_missing_mounts(self):
        causes = _analyze_likely_causes([], [], [], [], [], ["/var"], [], [], [])
        assert any("mount" in c.lower() for c in causes)

    def test_disk_space_issues(self):
        causes = _analyze_likely_causes([], [], [], [], [], [], ["/: 10MB"], [], [])
        assert any("disk space" in c.lower() for c in causes)

    def test_interfaces_down(self):
        causes = _analyze_likely_causes([], [], [], [], [], [], [], ["eth0: down"], [])
        assert any("network" in c.lower() for c in causes)

    def test_missing_modules(self):
        causes = _analyze_likely_causes([], [], [], [], [], [], [], [], ["nvidia"])
        assert any("module" in c.lower() for c in causes)

    def test_failed_services(self):
        causes = _analyze_likely_causes([], [], [], ["nginx.service"], [], [], [], [], [])
        assert any("service" in c.lower() for c in causes)

    def test_dmesg_kernel_errors(self):
        causes = _analyze_likely_causes([], ["[123.456] kernel panic - not syncing"], [], [], [], [], [], [], [])
        assert any("panic" in c.lower() for c in causes)

    def test_journal_errors(self):
        causes = _analyze_likely_causes([], [], ["Failed to start nginx.service"], [], [], [], [], [], [])
        assert any("Service startup failure" in c for c in causes)

    def test_deduplication(self):
        causes = _analyze_likely_causes(
            [],
            ["kernel panic detected", "kernel panic again"],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
        )
        panic_causes = [c for c in causes if "panic" in c.lower()]
        assert len(panic_causes) <= 2

    def test_empty_inputs(self):
        causes = _analyze_likely_causes([], [], [], [], [], [], [], [], [])
        assert causes == []

    def test_max_10_causes(self):
        causes = _analyze_likely_causes(
            [],
            [],
            [],
            ["svc" + str(i) for i in range(20)],
            ["/ro"],
            ["/missing"],
            ["/ low"],
            ["eth0: down"],
            ["nvidia"],
        )
        assert len(causes) <= 10

    def test_dmesg_io_error_pattern(self):
        """Verify I/O error pattern detection in dmesg."""
        causes = _analyze_likely_causes([], ["[789] I/O error, dev sda, sector 12345"], [], [], [], [], [], [], [])
        assert any("I/O" in c or "hardware" in c.lower() for c in causes)

    def test_journal_dependency_failed_pattern(self):
        """Verify Dependency failed pattern detection in journal."""
        causes = _analyze_likely_causes([], [], ["Dependency failed for Some Service"], [], [], [], [], [], [])
        assert any("dependency" in c.lower() for c in causes)

    def test_journal_failed_to_mount_pattern(self):
        """Verify Failed to mount pattern detection in journal."""
        causes = _analyze_likely_causes([], [], ["Failed to mount /data"], [], [], [], [], [], [])
        assert any("mount" in c.lower() for c in causes)

    def test_dmesg_oops_pattern(self):
        """Verify kernel oops detection."""
        causes = _analyze_likely_causes([], ["[100] oops: 0000 [#1]"], [], [], [], [], [], [], [])
        assert any("oops" in c.lower() for c in causes)

    def test_dmesg_bug_pattern(self):
        """Verify BUG: detection."""
        causes = _analyze_likely_causes([], ["[200] BUG: unable to handle page fault"], [], [], [], [], [], [], [])
        assert any("bug" in c.lower() for c in causes)

    def test_dmesg_ext4_error_pattern(self):
        """Verify EXT4 error detection."""
        causes = _analyze_likely_causes(
            [], ["[300] EXT4-fs error (device sda1): ext4_mb_generate_buddy"], [], [], [], [], [], [], []
        )
        assert any("EXT4" in c for c in causes)


# ===========================================================================
# _suggest_investigation
# ===========================================================================


class TestSuggestInvestigation:
    def test_general_diagnostics_always_present(self):
        intent = _make_intent()
        suggestions = _suggest_investigation(intent, [], [], [])
        assert any("journalctl" in s for s in suggestions)
        assert any("dmesg" in s for s in suggestions)

    def test_kernel_update_suggestions(self):
        intent = _make_intent(reason="kernel_update")
        suggestions = _suggest_investigation(intent, [], [], [])
        assert any("uname" in s for s in suggestions)

    def test_driver_update_suggestions(self):
        intent = _make_intent(reason="driver_update")
        suggestions = _suggest_investigation(intent, [], [], [])
        assert any("lsmod" in s for s in suggestions)

    def test_config_change_suggestions(self):
        intent = _make_intent(reason="config_change")
        suggestions = _suggest_investigation(intent, [], [], [])
        assert any("fstab" in s for s in suggestions)

    def test_read_only_cause_suggestions(self):
        intent = _make_intent()
        suggestions = _suggest_investigation(intent, ["Filesystem read-only"], [], [])
        assert any("mount" in s.lower() for s in suggestions)

    def test_failed_services_suggestions(self):
        intent = _make_intent()
        suggestions = _suggest_investigation(intent, [], ["nginx.service"], [])
        assert any("nginx" in s for s in suggestions)

    def test_missing_modules_suggestions(self):
        intent = _make_intent()
        suggestions = _suggest_investigation(intent, [], [], ["nvidia"])
        assert any("modinfo" in s for s in suggestions)

    def test_max_15_suggestions(self):
        intent = _make_intent(reason="kernel_update")
        suggestions = _suggest_investigation(
            intent,
            ["read-only mount"],
            ["svc" + str(i) for i in range(10)],
            ["mod" + str(i) for i in range(10)],
        )
        assert len(suggestions) <= 15


# ===========================================================================
# run_verification_with_retry
# ===========================================================================


class TestRunVerificationWithRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        intent = _make_intent(
            verifications=(
                PendingVerification(step_index=0, check_type="command", check_command="true", required=True),
            ),
        )
        with (
            patch("elle.daemon.reboot.verifier.check_critical_services", new_callable=AsyncMock) as mock_crit,
            patch("elle.daemon.reboot.verifier.check_network_connectivity", new_callable=AsyncMock) as mock_net,
        ):
            mock_crit.return_value = VerificationResult(
                passed=True,
                all_passed=True,
                critical_failure=False,
                required_passed=4,
                required_failed=0,
                optional_passed=0,
                optional_failed=0,
            )
            mock_net.return_value = True
            success, result = await run_verification_with_retry(intent)
            assert success is True

    @pytest.mark.asyncio
    async def test_critical_failure_returns_immediately(self):
        intent = _make_intent()
        with patch("elle.daemon.reboot.verifier.check_critical_services", new_callable=AsyncMock) as mock_crit:
            mock_crit.return_value = VerificationResult(
                passed=False,
                all_passed=False,
                critical_failure=True,
                required_passed=0,
                required_failed=1,
                optional_passed=0,
                optional_failed=0,
                error="Critical services down: sshd",
            )
            success, result = await run_verification_with_retry(intent)
            assert success is False
            assert result.critical_failure is True

    @pytest.mark.asyncio
    async def test_no_verifications_succeeds(self):
        intent = _make_intent(verifications=())
        with (
            patch("elle.daemon.reboot.verifier.check_critical_services", new_callable=AsyncMock) as mock_crit,
            patch("elle.daemon.reboot.verifier.check_network_connectivity", new_callable=AsyncMock) as mock_net,
        ):
            mock_crit.return_value = VerificationResult(
                passed=True,
                all_passed=True,
                critical_failure=False,
                required_passed=4,
                required_failed=0,
                optional_passed=0,
                optional_failed=0,
            )
            mock_net.return_value = True
            success, result = await run_verification_with_retry(intent)
            assert success is True

    @pytest.mark.asyncio
    async def test_on_attempt_callback(self):
        intent = _make_intent(
            verifications=(
                PendingVerification(step_index=0, check_type="command", check_command="true", required=True),
            ),
        )
        callback = MagicMock()
        with (
            patch("elle.daemon.reboot.verifier.check_critical_services", new_callable=AsyncMock) as mock_crit,
            patch("elle.daemon.reboot.verifier.check_network_connectivity", new_callable=AsyncMock) as mock_net,
            patch("elle.daemon.reboot.verifier.VERIFICATION_DELAYS", (0, 0, 0)),
        ):
            mock_crit.return_value = VerificationResult(
                passed=True,
                all_passed=True,
                critical_failure=False,
                required_passed=4,
                required_failed=0,
                optional_passed=0,
                optional_failed=0,
            )
            mock_net.return_value = True
            await run_verification_with_retry(intent, on_attempt=callback)
            callback.assert_called()

    @pytest.mark.asyncio
    async def test_network_failure_retries_then_fails(self):
        """When network is never available, all attempts fail and return (False, None)."""
        intent = _make_intent(
            verifications=(
                PendingVerification(step_index=0, check_type="command", check_command="true", required=True),
            ),
        )
        with (
            patch("elle.daemon.reboot.verifier.check_critical_services", new_callable=AsyncMock) as mock_crit,
            patch("elle.daemon.reboot.verifier.check_network_connectivity", new_callable=AsyncMock) as mock_net,
            patch("elle.daemon.reboot.verifier.VERIFICATION_DELAYS", (0, 0, 0)),
            patch("elle.daemon.reboot.verifier.MAX_VERIFICATION_ATTEMPTS", 3),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_crit.return_value = VerificationResult(
                passed=True,
                all_passed=True,
                critical_failure=False,
                required_passed=4,
                required_failed=0,
                optional_passed=0,
                optional_failed=0,
            )
            mock_net.return_value = False  # network always fails
            success, result = await run_verification_with_retry(intent)
            assert success is False
            # Network failures cause continue, so final_result stays None
            assert result is None

    @pytest.mark.asyncio
    async def test_verification_fails_all_attempts(self):
        """Custom verifications fail every attempt; returns (False, last_result)."""
        intent = _make_intent(
            verifications=(
                PendingVerification(step_index=0, check_type="command", check_command="false", required=True),
            ),
        )
        with (
            patch("elle.daemon.reboot.verifier.check_critical_services", new_callable=AsyncMock) as mock_crit,
            patch("elle.daemon.reboot.verifier.check_network_connectivity", new_callable=AsyncMock) as mock_net,
            patch("elle.daemon.reboot.verifier.VERIFICATION_DELAYS", (0, 0, 0)),
            patch("elle.daemon.reboot.verifier.MAX_VERIFICATION_ATTEMPTS", 3),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_crit.return_value = VerificationResult(
                passed=True,
                all_passed=True,
                critical_failure=False,
                required_passed=4,
                required_failed=0,
                optional_passed=0,
                optional_failed=0,
            )
            mock_net.return_value = True
            success, result = await run_verification_with_retry(intent)
            assert success is False
            assert result is not None
            assert result.required_failed >= 1

    @pytest.mark.asyncio
    async def test_delay_beyond_verification_delays_length(self):
        """When attempt index >= len(VERIFICATION_DELAYS), use the last delay value."""
        intent = _make_intent(
            verifications=(
                PendingVerification(step_index=0, check_type="command", check_command="false", required=True),
            ),
        )
        with (
            patch("elle.daemon.reboot.verifier.check_critical_services", new_callable=AsyncMock) as mock_crit,
            patch("elle.daemon.reboot.verifier.check_network_connectivity", new_callable=AsyncMock) as mock_net,
            # Only one delay value, but 3 attempts -- triggers else branch
            patch("elle.daemon.reboot.verifier.VERIFICATION_DELAYS", (0,)),
            patch("elle.daemon.reboot.verifier.MAX_VERIFICATION_ATTEMPTS", 3),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_crit.return_value = VerificationResult(
                passed=True,
                all_passed=True,
                critical_failure=False,
                required_passed=4,
                required_failed=0,
                optional_passed=0,
                optional_failed=0,
            )
            mock_net.return_value = True
            success, result = await run_verification_with_retry(intent)
            assert success is False
            # The function should not error out when index exceeds VERIFICATION_DELAYS


# ===========================================================================
# run_mount_check
# ===========================================================================


class TestRunMountCheck:
    @pytest.mark.asyncio
    async def test_mounted_path(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            passed, _, stdout, _ = await run_mount_check("/")
            assert passed is True
            assert "is mounted" in stdout

    @pytest.mark.asyncio
    async def test_not_mounted_path(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            passed, _, _, stderr = await run_mount_check("/nonexistent")
            assert passed is False
            assert "not a mount point" in stderr

    @pytest.mark.asyncio
    async def test_mount_check_exception(self):
        with patch("subprocess.run", side_effect=OSError("fail")):
            passed, exit_code, _, _ = await run_mount_check("/")
            assert passed is False
            assert exit_code == -1


# ===========================================================================
# run_kernel_module_check
# ===========================================================================


class TestRunKernelModuleCheck:
    @pytest.mark.asyncio
    async def test_module_loaded(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ext4 12345 1\nsnd 67890 2\n")
            passed, _, stdout, _ = await run_kernel_module_check("ext4")
            assert passed is True
            assert "loaded" in stdout

    @pytest.mark.asyncio
    async def test_module_not_loaded(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="snd 67890 2\n")
            passed, _, _, stderr = await run_kernel_module_check("nvidia")
            assert passed is False
            assert "not loaded" in stderr

    @pytest.mark.asyncio
    async def test_lsmod_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="permission denied")
            passed, _, _, _ = await run_kernel_module_check("ext4")
            assert passed is False

    @pytest.mark.asyncio
    async def test_lsmod_exception(self):
        with patch("subprocess.run", side_effect=OSError("fail")):
            passed, exit_code, _, _ = await run_kernel_module_check("ext4")
            assert passed is False
            assert exit_code == -1


# ===========================================================================
# run_dmesg_error_check
# ===========================================================================


class TestRunDmesgErrorCheck:
    @pytest.mark.asyncio
    async def test_no_errors(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            passed, _, stdout, _ = await run_dmesg_error_check()
            assert passed is True
            assert "No critical errors" in stdout

    @pytest.mark.asyncio
    async def test_errors_found(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="[123] BUG: kernel panic\n[456] ERROR: oops\n")
            passed, _, _, stderr = await run_dmesg_error_check()
            assert passed is False
            assert "error(s) in dmesg" in stderr

    @pytest.mark.asyncio
    async def test_specific_pattern_found(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="[123] EXT4-fs error on device sda1\n")
            passed, _, _, stderr = await run_dmesg_error_check("EXT4")
            assert passed is False
            assert "EXT4" in stderr

    @pytest.mark.asyncio
    async def test_specific_pattern_not_found(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="[123] other warning\n")
            passed, _, stdout, _ = await run_dmesg_error_check("EXT4")
            assert passed is True
            assert "not found" in stdout

    @pytest.mark.asyncio
    async def test_dmesg_exception(self):
        with patch("subprocess.run", side_effect=OSError("fail")):
            passed, exit_code, _, _ = await run_dmesg_error_check()
            assert passed is False
            assert exit_code == -1


# ===========================================================================
# run_journal_error_check
# ===========================================================================


class TestRunJournalErrorCheck:
    @pytest.mark.asyncio
    async def test_no_errors(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="-- No entries --")
            passed, _, stdout, _ = await run_journal_error_check()
            assert passed is True

    @pytest.mark.asyncio
    async def test_errors_found(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Jan 01 00:00:00 host systemd[1]: Failed to start nginx.service\nJan 01 00:00:01 host kernel: I/O error\n",
            )
            passed, _, _, stderr = await run_journal_error_check()
            assert passed is False
            assert "error(s) in journal" in stderr

    @pytest.mark.asyncio
    async def test_specific_pattern_found(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Jan 01 00:00:00 host systemd[1]: Failed to mount /data\n"
            )
            passed, _, _, stderr = await run_journal_error_check("mount")
            assert passed is False
            assert "matching errors" in stderr

    @pytest.mark.asyncio
    async def test_specific_pattern_not_found(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Jan 01 00:00:00 host systemd[1]: Something else\n")
            passed, _, stdout, _ = await run_journal_error_check("mount")
            assert passed is True
            assert "not found" in stdout

    @pytest.mark.asyncio
    async def test_journal_exception(self):
        with patch("subprocess.run", side_effect=OSError("fail")):
            passed, exit_code, _, _ = await run_journal_error_check()
            assert passed is False
            assert exit_code == -1

    @pytest.mark.asyncio
    async def test_since_boot_false_omits_b_flag(self):
        """When since_boot=False, the -b flag should not be passed to journalctl."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="-- No entries --")
            await run_journal_error_check(since_boot=False)
            call_args = mock_run.call_args[0][0]
            assert "-b" not in call_args

    @pytest.mark.asyncio
    async def test_since_boot_true_includes_b_flag(self):
        """When since_boot=True (default), the -b flag should be in the journalctl args."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="-- No entries --")
            await run_journal_error_check(since_boot=True)
            call_args = mock_run.call_args[0][0]
            assert "-b" in call_args


# ===========================================================================
# run_network_interface_check
# ===========================================================================


class TestRunNetworkInterfaceCheck:
    @pytest.mark.asyncio
    async def test_interface_up(self):
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "read_text", return_value="up\n"):
            passed, _, stdout, _ = await run_network_interface_check("eth0")
            assert passed is True
            assert "is up" in stdout

    @pytest.mark.asyncio
    async def test_interface_down(self):
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "read_text", return_value="down\n"):
            passed, _, _, stderr = await run_network_interface_check("eth0")
            assert passed is False
            assert "down" in stderr

    @pytest.mark.asyncio
    async def test_interface_not_found(self):
        with patch.object(Path, "exists", return_value=False):
            passed, _, _, stderr = await run_network_interface_check("eth99")
            assert passed is False
            assert "not found" in stderr

    @pytest.mark.asyncio
    async def test_interface_exception(self):
        with patch.object(Path, "exists", side_effect=OSError("fail")):
            passed, exit_code, _, _ = await run_network_interface_check("eth0")
            assert passed is False
            assert exit_code == -1


# ===========================================================================
# check_critical_services
# ===========================================================================


class TestCheckCriticalServices:
    @pytest.mark.asyncio
    async def test_all_services_active(self):
        with patch("elle.daemon.reboot.verifier.run_service_check", new_callable=AsyncMock) as mock:
            mock.return_value = (True, 0, "active", "")
            result = await check_critical_services()
            assert result.passed is True
            assert result.critical_failure is False

    @pytest.mark.asyncio
    async def test_some_services_down(self):
        call_count = [0]

        async def side_effect(name, **kwargs):
            call_count[0] += 1
            if "NetworkManager" in name:
                return (False, 3, "", "not active")
            return (True, 0, "active", "")

        with patch("elle.daemon.reboot.verifier.run_service_check", side_effect=side_effect):
            result = await check_critical_services()
            assert result.critical_failure is True
            assert result.required_failed >= 1

    @pytest.mark.asyncio
    async def test_error_message_lists_failed_services(self):
        """When services fail, error message should list them."""

        async def side_effect(name, **kwargs):
            if "ssh" in name.lower():
                return (False, 3, "", "not active")
            return (True, 0, "active", "")

        with patch("elle.daemon.reboot.verifier.run_service_check", side_effect=side_effect):
            result = await check_critical_services()
            assert result.critical_failure is True
            assert result.error is not None
            assert "ssh" in result.error.lower()

    @pytest.mark.asyncio
    async def test_service_suffix_fallback(self):
        """Tests the for/else loop: service fails without .service suffix but passes with it."""
        call_count = [0]

        async def side_effect(name, **kwargs):
            call_count[0] += 1
            # First call without .service suffix fails, second with .service passes
            if name.endswith(".service"):
                return (True, 0, "active", "")
            return (False, 3, "", "not active")

        with patch("elle.daemon.reboot.verifier.run_service_check", side_effect=side_effect):
            result = await check_critical_services()
            assert result.passed is True
            assert result.critical_failure is False


# ===========================================================================
# check_network_connectivity
# ===========================================================================


class TestCheckNetworkConnectivity:
    @pytest.mark.asyncio
    async def test_gateway_reachable(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        with patch("subprocess.run") as mock_run, patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            mock_run.return_value = MagicMock(returncode=0, stdout="default via 192.168.1.1 dev eth0\n")
            result = await check_network_connectivity()
            assert result is True

    @pytest.mark.asyncio
    async def test_no_default_route(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = await check_network_connectivity()
            assert result is False

    @pytest.mark.asyncio
    async def test_no_gateway_in_output(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="no default route\n")
            result = await check_network_connectivity()
            assert result is False

    @pytest.mark.asyncio
    async def test_ip_command_exception(self):
        with patch("subprocess.run", side_effect=OSError("fail")):
            result = await check_network_connectivity()
            assert result is False

    @pytest.mark.asyncio
    async def test_ping_exception(self):
        with patch("subprocess.run") as mock_run, patch("asyncio.create_subprocess_exec", side_effect=OSError("fail")):
            mock_run.return_value = MagicMock(returncode=0, stdout="default via 192.168.1.1 dev eth0\n")
            result = await check_network_connectivity()
            assert result is False

    @pytest.mark.asyncio
    async def test_ping_timeout(self):
        """When ping times out, check_network_connectivity returns False."""
        import asyncio as _asyncio

        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock(side_effect=_asyncio.TimeoutError)
        mock_proc.kill = MagicMock()

        with (
            patch("subprocess.run") as mock_run,
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="default via 192.168.1.1 dev eth0\n")
            result = await check_network_connectivity(timeout=0.01)
            assert result is False

    @pytest.mark.asyncio
    async def test_ping_nonzero_exit(self):
        """When ping exits with non-zero, network is considered down."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.wait = AsyncMock(return_value=1)

        with (
            patch("subprocess.run") as mock_run,
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="default via 192.168.1.1 dev eth0\n")
            result = await check_network_connectivity()
            assert result is False


# ===========================================================================
# Diagnostic collection helpers (private functions)
# ===========================================================================


class TestCollectDmesgErrors:
    @pytest.mark.asyncio
    async def test_returns_error_lines(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="[100] BUG: kernel panic\n[200] ERROR: oops\n",
            )
            errors = await _collect_dmesg_errors()
            assert len(errors) == 2
            assert "BUG" in errors[0]

    @pytest.mark.asyncio
    async def test_returns_empty_on_no_errors(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            errors = await _collect_dmesg_errors()
            assert errors == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        with patch("subprocess.run", side_effect=OSError("fail")):
            errors = await _collect_dmesg_errors()
            assert errors == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_nonzero_returncode(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="some output")
            errors = await _collect_dmesg_errors()
            assert errors == []


class TestCollectJournalErrors:
    @pytest.mark.asyncio
    async def test_returns_error_lines(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Jan 01 error line 1\nJan 01 error line 2\n",
            )
            errors = await _collect_journal_errors()
            assert len(errors) == 2

    @pytest.mark.asyncio
    async def test_filters_header_lines(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="-- Journal begins at ...\nJan 01 real error line\n",
            )
            errors = await _collect_journal_errors()
            assert len(errors) == 1
            assert "real error" in errors[0]

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        with patch("subprocess.run", side_effect=OSError("fail")):
            errors = await _collect_journal_errors()
            assert errors == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_empty_output(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            errors = await _collect_journal_errors()
            assert errors == []


class TestCollectFailedServices:
    @pytest.mark.asyncio
    async def test_returns_service_names(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="nginx.service loaded failed failed\nredis.service loaded failed failed\n",
            )
            services = await _collect_failed_services()
            assert len(services) == 2
            assert services[0] == "nginx.service"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_failures(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            services = await _collect_failed_services()
            assert services == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        with patch("subprocess.run", side_effect=OSError("fail")):
            services = await _collect_failed_services()
            assert services == []


class TestCheckFilesystemState:
    @pytest.mark.asyncio
    async def test_detects_read_only_mounts(self):
        mount_output = "/dev/sda1 on / type ext4 (ro,relatime)\n/dev/sda2 on /home type ext4 (rw,relatime)\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mount_output)
            read_only, missing = await _check_filesystem_state()
            assert "/" in read_only

    @pytest.mark.asyncio
    async def test_detects_missing_critical_mounts(self):
        # Only root is mounted; /var, /tmp, /home are missing
        mount_output = "/dev/sda1 on / type ext4 (rw,relatime)\n"
        with (
            patch("subprocess.run") as mock_run,
            patch.object(Path, "exists", return_value=False),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout=mount_output)
            read_only, missing = await _check_filesystem_state()
            # /var, /tmp, /home should be reported as missing
            assert len(missing) > 0

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        with patch("subprocess.run", side_effect=OSError("fail")):
            read_only, missing = await _check_filesystem_state()
            assert read_only == []
            assert missing == []


class TestCheckDiskSpaceIssues:
    @pytest.mark.asyncio
    async def test_reports_low_disk_space(self):
        with (
            patch("shutil.disk_usage") as mock_du,
            patch.object(Path, "exists", return_value=True),
        ):
            # 10MB free, below any requirement
            mock_du.return_value = MagicMock(free=10 * 1024 * 1024)
            issues = await _check_disk_space_issues()
            assert len(issues) > 0
            assert "free" in issues[0]

    @pytest.mark.asyncio
    async def test_no_issues_when_space_sufficient(self):
        with (
            patch("shutil.disk_usage") as mock_du,
            patch.object(Path, "exists", return_value=True),
        ):
            # 100GB free
            mock_du.return_value = MagicMock(free=100 * 1024 * 1024 * 1024)
            issues = await _check_disk_space_issues()
            assert issues == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        with patch("shutil.disk_usage", side_effect=OSError("fail")):
            issues = await _check_disk_space_issues()
            assert issues == []


class TestCheckNetworkState:
    @pytest.mark.asyncio
    async def test_detects_interfaces_down(self):
        mock_iface = MagicMock()
        mock_iface.name = "eth0"
        mock_operstate = MagicMock()
        mock_operstate.exists.return_value = True
        mock_operstate.read_text.return_value = "down\n"
        mock_iface.__truediv__ = MagicMock(return_value=mock_operstate)

        mock_net_dir = MagicMock()
        mock_net_dir.exists.return_value = True
        mock_net_dir.iterdir.return_value = [mock_iface]

        with (
            patch("elle.daemon.reboot.verifier.Path") as mock_path_cls,
            patch("subprocess.run") as mock_run,
        ):
            mock_path_cls.return_value = mock_net_dir
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            interfaces_down, network_errors = await _check_network_state()
            assert len(interfaces_down) == 1
            assert "eth0" in interfaces_down[0]

    @pytest.mark.asyncio
    async def test_skips_loopback(self):
        """Loopback interface should be skipped."""
        mock_lo = MagicMock()
        mock_lo.name = "lo"

        mock_net_dir = MagicMock()
        mock_net_dir.exists.return_value = True
        mock_net_dir.iterdir.return_value = [mock_lo]

        with (
            patch("elle.daemon.reboot.verifier.Path") as mock_path_cls,
            patch("subprocess.run") as mock_run,
        ):
            mock_path_cls.return_value = mock_net_dir
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            interfaces_down, network_errors = await _check_network_state()
            assert len(interfaces_down) == 0


class TestCheckModuleState:
    @pytest.mark.asyncio
    async def test_detects_missing_modules_for_driver_update(self):
        intent = _make_intent(
            reason="driver_update",
            plan_json={"expected_modules": ["nvidia"]},
        )
        with patch("subprocess.run") as mock_run:
            # First call for dmesg, second for lsmod
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=""),  # dmesg - no module errors
                MagicMock(returncode=0, stdout="Module Size Used\next4 12345 1\n"),  # lsmod - nvidia missing
            ]
            missing, errors = await _check_module_state(intent)
            assert "nvidia" in missing

    @pytest.mark.asyncio
    async def test_no_expected_modules_for_non_driver(self):
        intent = _make_intent(reason="user_requested")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            missing, errors = await _check_module_state(intent)
            assert missing == []

    @pytest.mark.asyncio
    async def test_detects_module_errors_in_dmesg(self):
        intent = _make_intent(reason="driver_update", plan_json={})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="[100] modprobe: failed to load module nvidia\n",
            )
            missing, errors = await _check_module_state(intent)
            assert len(errors) == 1
            assert "modprobe" in errors[0]


class TestGetKernelVersion:
    @pytest.mark.asyncio
    async def test_returns_kernel_version(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="6.8.0-40-generic\n")
            version = await _get_kernel_version()
            assert version == "6.8.0-40-generic"

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            version = await _get_kernel_version()
            assert version is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        with patch("subprocess.run", side_effect=OSError("fail")):
            version = await _get_kernel_version()
            assert version is None


class TestGetUptimeSeconds:
    @pytest.mark.asyncio
    async def test_returns_uptime(self):
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", return_value="123.45 678.90\n"),
        ):
            uptime = await _get_uptime_seconds()
            assert uptime == 123

    @pytest.mark.asyncio
    async def test_returns_none_when_file_missing(self):
        with patch.object(Path, "exists", return_value=False):
            uptime = await _get_uptime_seconds()
            assert uptime is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", side_effect=OSError("fail")),
        ):
            uptime = await _get_uptime_seconds()
            assert uptime is None


# ===========================================================================
# collect_failure_diagnostics (integration-level with mocked externals)
# ===========================================================================


class TestCollectFailureDiagnostics:
    @pytest.mark.asyncio
    async def test_collects_all_diagnostic_fields(self):
        """Verify collect_failure_diagnostics assembles all diagnostic data."""
        intent = _make_intent(
            reason="kernel_update",
            reason_detail="Upgrading to 6.8.0",
            session_history=("apt upgrade", "reboot"),
            plan_json={"config_files": ["/etc/default/grub"]},
            pre_snapshot_json={"kernel": "6.5.0-generic"},
            grub_default_saved="0",
        )

        verification_results = [
            {"passed": True, "check_type": "service_active", "check_command": "sshd"},
            {"passed": False, "check_type": "command", "check_command": "uname -r", "stderr": "wrong kernel"},
        ]

        mock_grub_state = MagicMock()
        mock_grub_state.entries = ("Ubuntu 6.8.0", "Ubuntu 6.5.0")

        with (
            patch(
                "elle.daemon.reboot.verifier._collect_dmesg_errors",
                new_callable=AsyncMock,
                return_value=["[100] EXT4 error"],
            ),
            patch(
                "elle.daemon.reboot.verifier._collect_journal_errors",
                new_callable=AsyncMock,
                return_value=["Failed to start nginx"],
            ),
            patch(
                "elle.daemon.reboot.verifier._collect_failed_services",
                new_callable=AsyncMock,
                return_value=["nginx.service"],
            ),
            patch("elle.daemon.reboot.verifier._check_filesystem_state", new_callable=AsyncMock, return_value=([], [])),
            patch("elle.daemon.reboot.verifier._check_disk_space_issues", new_callable=AsyncMock, return_value=[]),
            patch("elle.daemon.reboot.verifier._check_network_state", new_callable=AsyncMock, return_value=([], [])),
            patch("elle.daemon.reboot.verifier._check_module_state", new_callable=AsyncMock, return_value=([], [])),
            patch(
                "elle.daemon.reboot.verifier._get_kernel_version",
                new_callable=AsyncMock,
                return_value="6.8.0-40-generic",
            ),
            patch("elle.daemon.reboot.verifier._get_uptime_seconds", new_callable=AsyncMock, return_value=120),
            patch.object(Path, "exists", return_value=False),  # BIOS mode (no /sys/firmware/efi)
            patch("elle.daemon.reboot.grub.get_grub_state", return_value=mock_grub_state),
            patch("elle.daemon.reboot.grub.get_saved_default", return_value="0"),
        ):
            diagnostics = await collect_failure_diagnostics(intent, verification_results)

            assert diagnostics.intent_goal == "Test reboot"
            assert diagnostics.intent_reason == "kernel_update"
            assert diagnostics.kernel_version == "6.8.0-40-generic"
            assert diagnostics.previous_kernel == "6.5.0-generic"
            assert diagnostics.boot_mode == "BIOS"
            assert diagnostics.uptime_seconds == 120
            assert len(diagnostics.failed_checks) == 1
            assert len(diagnostics.passed_checks) == 1
            assert len(diagnostics.dmesg_errors) == 1
            assert len(diagnostics.journal_errors) == 1
            assert len(diagnostics.failed_services) == 1
            assert diagnostics.config_changes == ("/etc/default/grub",)
            assert diagnostics.current_grub_entry == "0"
            assert diagnostics.previous_grub_entry == "0"
            assert len(diagnostics.grub_entries_available) == 2
            assert len(diagnostics.likely_causes) >= 1
            assert len(diagnostics.suggested_investigation) >= 1

    @pytest.mark.asyncio
    async def test_handles_none_verification_results(self):
        """collect_failure_diagnostics should handle None verification_results."""
        intent = _make_intent()

        mock_grub_state = MagicMock()
        mock_grub_state.entries = ()

        with (
            patch("elle.daemon.reboot.verifier._collect_dmesg_errors", new_callable=AsyncMock, return_value=[]),
            patch("elle.daemon.reboot.verifier._collect_journal_errors", new_callable=AsyncMock, return_value=[]),
            patch("elle.daemon.reboot.verifier._collect_failed_services", new_callable=AsyncMock, return_value=[]),
            patch("elle.daemon.reboot.verifier._check_filesystem_state", new_callable=AsyncMock, return_value=([], [])),
            patch("elle.daemon.reboot.verifier._check_disk_space_issues", new_callable=AsyncMock, return_value=[]),
            patch("elle.daemon.reboot.verifier._check_network_state", new_callable=AsyncMock, return_value=([], [])),
            patch("elle.daemon.reboot.verifier._check_module_state", new_callable=AsyncMock, return_value=([], [])),
            patch("elle.daemon.reboot.verifier._get_kernel_version", new_callable=AsyncMock, return_value=None),
            patch("elle.daemon.reboot.verifier._get_uptime_seconds", new_callable=AsyncMock, return_value=None),
            patch.object(Path, "exists", return_value=True),  # UEFI mode
            patch("elle.daemon.reboot.grub.get_grub_state", return_value=mock_grub_state),
            patch("elle.daemon.reboot.grub.get_saved_default", return_value=None),
        ):
            diagnostics = await collect_failure_diagnostics(intent, verification_results=None)

            assert diagnostics.boot_mode == "UEFI"
            assert diagnostics.failed_checks == ()
            assert diagnostics.passed_checks == ()
            assert diagnostics.kernel_version is None
            assert diagnostics.uptime_seconds is None

    @pytest.mark.asyncio
    async def test_handles_no_plan_json(self):
        """When intent has no plan_json, config_changes should be empty tuple."""
        intent = _make_intent(plan_json={})

        mock_grub_state = MagicMock()
        mock_grub_state.entries = ()

        with (
            patch("elle.daemon.reboot.verifier._collect_dmesg_errors", new_callable=AsyncMock, return_value=[]),
            patch("elle.daemon.reboot.verifier._collect_journal_errors", new_callable=AsyncMock, return_value=[]),
            patch("elle.daemon.reboot.verifier._collect_failed_services", new_callable=AsyncMock, return_value=[]),
            patch("elle.daemon.reboot.verifier._check_filesystem_state", new_callable=AsyncMock, return_value=([], [])),
            patch("elle.daemon.reboot.verifier._check_disk_space_issues", new_callable=AsyncMock, return_value=[]),
            patch("elle.daemon.reboot.verifier._check_network_state", new_callable=AsyncMock, return_value=([], [])),
            patch("elle.daemon.reboot.verifier._check_module_state", new_callable=AsyncMock, return_value=([], [])),
            patch("elle.daemon.reboot.verifier._get_kernel_version", new_callable=AsyncMock, return_value=None),
            patch("elle.daemon.reboot.verifier._get_uptime_seconds", new_callable=AsyncMock, return_value=None),
            patch.object(Path, "exists", return_value=False),
            patch("elle.daemon.reboot.grub.get_grub_state", return_value=mock_grub_state),
            patch("elle.daemon.reboot.grub.get_saved_default", return_value=None),
        ):
            diagnostics = await collect_failure_diagnostics(intent)

            assert diagnostics.config_changes == ()
