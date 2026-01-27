"""Post-boot verification for reboot recovery.

Runs verification checks after reboot to ensure system health
and determine whether to confirm boot or trigger rollback.

Includes comprehensive diagnostic collection for LLM-assisted retry.
"""

import asyncio
import logging
import re
import socket
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from elle.daemon.reboot.models import (
    CRITICAL_MOUNT_POINTS,
    CRITICAL_SERVICES,
    DISK_SPACE_REQUIREMENTS,
    DMESG_LINES_TO_CAPTURE,
    JOURNAL_LINES_TO_CAPTURE,
    MAX_VERIFICATION_ATTEMPTS,
    NETWORK_CHECK_TIMEOUT_SEC,
    VERIFICATION_DELAYS,
    VERIFICATION_TIMEOUT_SEC,
    FailureDiagnostics,
    PendingVerification,
    RebootIntent,
)

logger = logging.getLogger(__name__)


class VerificationResult(BaseModel):
    """Result of running verification checks."""

    model_config = ConfigDict(frozen=True)

    passed: bool = Field(description="Whether all required checks passed")
    all_passed: bool = Field(description="Whether ALL checks passed (including optional)")
    critical_failure: bool = Field(
        default=False,
        description="Whether a critical service is down",
    )
    required_passed: int = Field(ge=0, description="Number of required checks that passed")
    required_failed: int = Field(ge=0, description="Number of required checks that failed")
    optional_passed: int = Field(ge=0, description="Number of optional checks that passed")
    optional_failed: int = Field(ge=0, description="Number of optional checks that failed")
    results: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Detailed results for each check",
    )
    error: str | None = Field(default=None, description="Error message if verification failed")


# =============================================================================
# Check Runners
# =============================================================================


async def run_command_check(
    command: str,
    expected_exit_code: int = 0,
    expected_output_contains: str | None = None,
    timeout: float = VERIFICATION_TIMEOUT_SEC,
) -> tuple[bool, int, str, str]:
    """Run a command verification check.

    Args:
        command: Shell command to execute.
        expected_exit_code: Expected exit code.
        expected_output_contains: String that stdout must contain.
        timeout: Command timeout in seconds.

    Returns:
        Tuple of (passed, exit_code, stdout, stderr).
    """
    try:
        # Run command with timeout
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return (False, -1, "", f"Command timed out after {timeout}s")

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = proc.returncode or 0

        # Check exit code
        passed = exit_code == expected_exit_code

        # Check output contains expected string
        if passed and expected_output_contains:
            passed = expected_output_contains in stdout

        return (passed, exit_code, stdout, stderr)

    except Exception as e:
        logger.error(f"Command check failed: {e}")
        return (False, -1, "", str(e))


async def run_service_check(
    service_name: str,
    timeout: float = VERIFICATION_TIMEOUT_SEC,
) -> tuple[bool, int, str, str]:
    """Check if a systemd service is active.

    Args:
        service_name: Name of the systemd service.
        timeout: Check timeout in seconds.

    Returns:
        Tuple of (passed, exit_code, stdout, stderr).
    """
    command = f"systemctl is-active {service_name}"
    passed, exit_code, stdout, stderr = await run_command_check(
        command,
        expected_exit_code=0,
        timeout=timeout,
    )

    # is-active returns "active" on stdout when service is running
    if passed:
        passed = stdout.strip() == "active"

    return (passed, exit_code, stdout, stderr)


async def run_file_check(
    file_path: str,
) -> tuple[bool, int, str, str]:
    """Check if a file exists.

    Args:
        file_path: Path to the file.

    Returns:
        Tuple of (passed, exit_code, stdout, stderr).
    """
    path = Path(file_path)
    exists = path.exists()
    return (
        exists,
        0 if exists else 1,
        f"File {'exists' if exists else 'does not exist'}: {file_path}",
        "",
    )


async def run_port_check(
    port_spec: str,
    timeout: float = 5.0,
) -> tuple[bool, int, str, str]:
    """Check if a port is listening.

    Args:
        port_spec: Port specification (e.g., "8080" or "127.0.0.1:8080").
        timeout: Connection timeout in seconds.

    Returns:
        Tuple of (passed, exit_code, stdout, stderr).
    """
    # Parse port spec
    if ":" in port_spec:
        host, port_str = port_spec.rsplit(":", 1)
    else:
        host = "127.0.0.1"
        port_str = port_spec

    try:
        port = int(port_str)
    except ValueError:
        return (False, -1, "", f"Invalid port: {port_str}")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()

        passed = result == 0
        return (
            passed,
            0 if passed else 1,
            f"Port {host}:{port} is {'open' if passed else 'closed'}",
            "",
        )
    except Exception as e:
        return (False, -1, "", str(e))


async def run_filesystem_writable_check(
    path: str,
) -> tuple[bool, int, str, str]:
    """Check if a filesystem path is writable.

    Creates and removes a temp file to verify write capability.

    Args:
        path: Directory path to check.

    Returns:
        Tuple of (passed, exit_code, stdout, stderr).
    """
    test_file = Path(path) / ".elle-write-test"
    try:
        # Try to create a test file
        test_file.write_text("write-test")
        test_file.unlink()
        return (True, 0, f"Path {path} is writable", "")
    except PermissionError:
        return (False, 1, "", f"Permission denied: {path}")
    except OSError as e:
        if "Read-only file system" in str(e):
            return (False, 1, "", f"Read-only file system: {path}")
        return (False, 1, "", f"Write failed: {e}")
    except Exception as e:
        return (False, 1, "", str(e))


async def run_mount_check(
    mount_point: str,
) -> tuple[bool, int, str, str]:
    """Check if a mount point is mounted.

    Args:
        mount_point: Mount point path to check.

    Returns:
        Tuple of (passed, exit_code, stdout, stderr).
    """
    try:
        result = subprocess.run(
            ["mountpoint", "-q", mount_point],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return (True, 0, f"{mount_point} is mounted", "")
        else:
            return (False, 1, "", f"{mount_point} is not a mount point")
    except Exception as e:
        return (False, -1, "", str(e))


async def run_kernel_module_check(
    module_name: str,
) -> tuple[bool, int, str, str]:
    """Check if a kernel module is loaded.

    Args:
        module_name: Name of the kernel module.

    Returns:
        Tuple of (passed, exit_code, stdout, stderr).
    """
    try:
        result = subprocess.run(
            ["lsmod"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Check if module is in output
            for line in result.stdout.splitlines():
                if line.split()[0] == module_name:
                    return (True, 0, f"Module {module_name} is loaded", "")
            return (False, 1, "", f"Module {module_name} not loaded")
        return (False, result.returncode, "", result.stderr)
    except Exception as e:
        return (False, -1, "", str(e))


async def run_dmesg_error_check(
    error_pattern: str | None = None,
) -> tuple[bool, int, str, str]:
    """Check dmesg for error patterns.

    Args:
        error_pattern: Optional specific pattern to search for.
                      If None, checks for common error patterns.

    Returns:
        Tuple of (passed, exit_code, stdout, stderr).
    """
    try:
        result = subprocess.run(
            ["dmesg", "--level=err,crit,alert,emerg", "-T"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        errors = result.stdout.strip()
        if not errors:
            return (True, 0, "No critical errors in dmesg", "")

        if error_pattern:
            # Check for specific pattern
            if re.search(error_pattern, errors, re.IGNORECASE):
                return (False, 1, "", f"Found error pattern '{error_pattern}' in dmesg")
            return (True, 0, f"Pattern '{error_pattern}' not found in dmesg", "")

        # Return the errors found
        error_lines = errors.splitlines()[:20]
        return (False, 1, "\n".join(error_lines), f"Found {len(error_lines)} error(s) in dmesg")
    except Exception as e:
        return (False, -1, "", str(e))


async def run_journal_error_check(
    error_pattern: str | None = None,
    since_boot: bool = True,
) -> tuple[bool, int, str, str]:
    """Check systemd journal for error patterns.

    Args:
        error_pattern: Optional specific pattern to search for.
        since_boot: If True, only check since last boot.

    Returns:
        Tuple of (passed, exit_code, stdout, stderr).
    """
    try:
        cmd = ["journalctl", "-p", "err", "--no-pager", "-n", "50"]
        if since_boot:
            cmd.append("-b")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )

        output = result.stdout.strip()

        # Skip the header line if present
        lines = [l for l in output.splitlines() if l and not l.startswith("-- ")]

        if not lines:
            return (True, 0, "No errors in journal since boot", "")

        if error_pattern:
            matching = [l for l in lines if re.search(error_pattern, l, re.IGNORECASE)]
            if matching:
                return (False, 1, "\n".join(matching[:10]), f"Found {len(matching)} matching errors")
            return (True, 0, f"Pattern '{error_pattern}' not found", "")

        return (False, 1, "\n".join(lines[:20]), f"Found {len(lines)} error(s) in journal")
    except Exception as e:
        return (False, -1, "", str(e))


async def run_disk_space_check(
    path_and_min_mb: str,
) -> tuple[bool, int, str, str]:
    """Check if path has minimum free disk space.

    Args:
        path_and_min_mb: Format "path:min_mb" e.g. "/:500" or just path for 100MB default.

    Returns:
        Tuple of (passed, exit_code, stdout, stderr).
    """
    try:
        if ":" in path_and_min_mb:
            path, min_mb_str = path_and_min_mb.rsplit(":", 1)
            min_mb = int(min_mb_str)
        else:
            path = path_and_min_mb
            min_mb = 100

        import shutil

        stat = shutil.disk_usage(path)
        free_mb = stat.free // (1024 * 1024)

        if free_mb >= min_mb:
            return (True, 0, f"{path} has {free_mb}MB free (>= {min_mb}MB required)", "")
        else:
            return (False, 1, "", f"{path} only has {free_mb}MB free (< {min_mb}MB required)")
    except Exception as e:
        return (False, -1, "", str(e))


async def run_network_interface_check(
    interface: str,
) -> tuple[bool, int, str, str]:
    """Check if a network interface is up.

    Args:
        interface: Interface name (e.g., "eth0", "enp0s3").

    Returns:
        Tuple of (passed, exit_code, stdout, stderr).
    """
    try:
        # Check interface state via sysfs
        state_path = Path(f"/sys/class/net/{interface}/operstate")
        if not state_path.exists():
            return (False, 1, "", f"Interface {interface} not found")

        state = state_path.read_text().strip()
        if state == "up":
            return (True, 0, f"Interface {interface} is up", "")
        else:
            return (False, 1, "", f"Interface {interface} is {state}")
    except Exception as e:
        return (False, -1, "", str(e))


# =============================================================================
# Main Verification Runner
# =============================================================================


async def run_verification(
    verification: PendingVerification,
) -> dict[str, Any]:
    """Run a single verification check.

    Args:
        verification: The verification to run.

    Returns:
        Dict with check results.
    """
    start = datetime.utcnow()

    # Route to appropriate checker
    if verification.check_type == "command":
        passed, exit_code, stdout, stderr = await run_command_check(
            verification.check_command,
            verification.expected_exit_code,
            verification.expected_output_contains,
        )
    elif verification.check_type == "service_active":
        passed, exit_code, stdout, stderr = await run_service_check(
            verification.check_command,
        )
    elif verification.check_type == "file_exists":
        passed, exit_code, stdout, stderr = await run_file_check(
            verification.check_command,
        )
    elif verification.check_type == "port_listening":
        passed, exit_code, stdout, stderr = await run_port_check(
            verification.check_command,
        )
    elif verification.check_type == "filesystem_writable":
        passed, exit_code, stdout, stderr = await run_filesystem_writable_check(
            verification.check_command,
        )
    elif verification.check_type == "mount_exists":
        passed, exit_code, stdout, stderr = await run_mount_check(
            verification.check_command,
        )
    elif verification.check_type == "kernel_module":
        passed, exit_code, stdout, stderr = await run_kernel_module_check(
            verification.check_command,
        )
    elif verification.check_type == "dmesg_no_errors":
        passed, exit_code, stdout, stderr = await run_dmesg_error_check(
            verification.check_command if verification.check_command else None,
        )
    elif verification.check_type == "journal_no_errors":
        passed, exit_code, stdout, stderr = await run_journal_error_check(
            verification.check_command if verification.check_command else None,
        )
    elif verification.check_type == "disk_space":
        passed, exit_code, stdout, stderr = await run_disk_space_check(
            verification.check_command,
        )
    elif verification.check_type == "network_interface":
        passed, exit_code, stdout, stderr = await run_network_interface_check(
            verification.check_command,
        )
    else:
        passed = False
        exit_code = -1
        stdout = ""
        stderr = f"Unknown check type: {verification.check_type}"

    end = datetime.utcnow()
    duration_ms = int((end - start).total_seconds() * 1000)

    return {
        "verification_id": verification.id,
        "step_index": verification.step_index,
        "check_type": verification.check_type,
        "check_command": verification.check_command,
        "required": verification.required,
        "passed": passed,
        "exit_code": exit_code,
        "stdout": stdout[:4096],  # Truncate
        "stderr": stderr[:4096],  # Truncate
        "duration_ms": duration_ms,
        "executed_at": end,
    }


async def run_all_verifications(
    verifications: list[PendingVerification] | tuple[PendingVerification, ...],
) -> VerificationResult:
    """Run all verification checks for a reboot intent.

    Args:
        verifications: List of verifications to run.

    Returns:
        VerificationResult with outcomes.
    """
    results: list[dict[str, Any]] = []
    required_passed = 0
    required_failed = 0
    optional_passed = 0
    optional_failed = 0

    for verification in sorted(verifications, key=lambda v: v.step_index):
        result = await run_verification(verification)
        results.append(result)

        if verification.required:
            if result["passed"]:
                required_passed += 1
            else:
                required_failed += 1
                logger.warning(
                    f"Required verification failed: {verification.check_type} '{verification.check_command}'"
                )
        else:
            if result["passed"]:
                optional_passed += 1
            else:
                optional_failed += 1
                logger.info(f"Optional verification failed: {verification.check_type} '{verification.check_command}'")

    # All required checks must pass
    passed = required_failed == 0
    all_passed = required_failed == 0 and optional_failed == 0

    return VerificationResult(
        passed=passed,
        all_passed=all_passed,
        critical_failure=False,  # Set by critical services check
        required_passed=required_passed,
        required_failed=required_failed,
        optional_passed=optional_passed,
        optional_failed=optional_failed,
        results=tuple(results),
    )


# =============================================================================
# Critical Service Checks
# =============================================================================


async def check_critical_services() -> VerificationResult:
    """Check that critical system services are running.

    Critical services include NetworkManager, systemd-resolved, ssh, etc.
    Failure of these services may trigger automatic rollback.

    Returns:
        VerificationResult indicating critical services status.
    """
    results: list[dict[str, Any]] = []
    failed_services: list[str] = []

    for service in CRITICAL_SERVICES:
        # Try both service name and .service suffix
        for name in (service, f"{service}.service"):
            passed, exit_code, stdout, stderr = await run_service_check(name)
            if passed:
                break
        else:
            # Neither worked
            passed = False

        results.append(
            {
                "verification_id": None,
                "step_index": CRITICAL_SERVICES.index(service),
                "check_type": "service_active",
                "check_command": service,
                "required": True,
                "passed": passed,
                "exit_code": exit_code,
                "stdout": stdout[:256],
                "stderr": stderr[:256],
                "duration_ms": 0,
                "executed_at": datetime.utcnow(),
            }
        )

        if not passed:
            failed_services.append(service)
            logger.error(f"Critical service not running: {service}")

    critical_failure = len(failed_services) > 0

    return VerificationResult(
        passed=not critical_failure,
        all_passed=not critical_failure,
        critical_failure=critical_failure,
        required_passed=len(CRITICAL_SERVICES) - len(failed_services),
        required_failed=len(failed_services),
        optional_passed=0,
        optional_failed=0,
        results=tuple(results),
        error=f"Critical services down: {', '.join(failed_services)}" if failed_services else None,
    )


async def check_network_connectivity(
    timeout: float = NETWORK_CHECK_TIMEOUT_SEC,
) -> bool:
    """Check basic network connectivity.

    Attempts to reach the default gateway.

    Args:
        timeout: Maximum time to wait for network.

    Returns:
        True if network is available.
    """
    # Get default gateway
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            logger.warning("Could not get default route")
            return False

        # Parse gateway from output: "default via 192.168.1.1 dev eth0"
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "default" and parts[1] == "via":
                gateway = parts[2]
                break
        else:
            logger.warning("No default gateway found")
            return False

    except Exception as e:
        logger.warning(f"Failed to get gateway: {e}")
        return False

    # Ping gateway
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping",
            "-c",
            "1",
            "-W",
            "5",
            gateway,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
            return proc.returncode == 0
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return False
    except Exception as e:
        logger.warning(f"Network check failed: {e}")
        return False


# =============================================================================
# Retry Logic
# =============================================================================


async def run_verification_with_retry(
    intent: RebootIntent,
    on_attempt: Any | None = None,
) -> tuple[bool, VerificationResult | None]:
    """Run verification checks with retry logic.

    Attempts verification up to MAX_VERIFICATION_ATTEMPTS times
    with delays between attempts.

    Args:
        intent: The reboot intent with verifications.
        on_attempt: Optional callback(attempt_number, total_attempts) called before each attempt.

    Returns:
        Tuple of (success, final_result).
    """
    final_result: VerificationResult | None = None

    for attempt in range(MAX_VERIFICATION_ATTEMPTS):
        # Wait before this attempt (first attempt has no delay)
        if attempt < len(VERIFICATION_DELAYS):
            delay = VERIFICATION_DELAYS[attempt]
        else:
            delay = VERIFICATION_DELAYS[-1]

        if delay > 0:
            logger.info(f"Waiting {delay}s before verification attempt {attempt + 1}")
            await asyncio.sleep(delay)

        if on_attempt:
            on_attempt(attempt + 1, MAX_VERIFICATION_ATTEMPTS)

        logger.info(f"Running verification attempt {attempt + 1}/{MAX_VERIFICATION_ATTEMPTS}")

        # First check critical services
        critical_result = await check_critical_services()
        if critical_result.critical_failure:
            logger.error("Critical services check failed!")
            return (False, critical_result)

        # Check network
        network_ok = await check_network_connectivity()
        if not network_ok:
            logger.warning("Network connectivity check failed, will retry")
            continue

        # Run custom verifications
        if intent.verifications:
            result = await run_all_verifications(intent.verifications)
            final_result = result

            if result.passed:
                logger.info(f"Verification passed: {result.required_passed} required checks OK")
                return (True, result)
            else:
                logger.warning(
                    f"Verification attempt {attempt + 1} failed: {result.required_failed} required checks failed"
                )
        else:
            # No custom verifications, just critical services + network
            logger.info("No custom verifications, critical services OK")
            return (True, critical_result)

    # All attempts failed
    logger.error(f"All {MAX_VERIFICATION_ATTEMPTS} verification attempts failed")
    return (False, final_result)


# =============================================================================
# Default Verifications (Comprehensive)
# =============================================================================


def create_default_verifications() -> list[PendingVerification]:
    """Create comprehensive default verification checks for any reboot.

    Includes:
    - Critical service checks
    - Filesystem writability
    - Critical mount points
    - Disk space checks
    - Basic system health

    Returns:
        List of default PendingVerifications.
    """
    verifications = []
    idx = 0

    # === Critical Services ===
    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="service_active",
            check_command="NetworkManager",
            required=True,
        )
    )
    idx += 1

    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="service_active",
            check_command="systemd-resolved",
            required=True,
        )
    )
    idx += 1

    # === Filesystem Writability (Critical for read-only root detection) ===
    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="filesystem_writable",
            check_command="/",  # Root filesystem
            required=True,
        )
    )
    idx += 1

    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="filesystem_writable",
            check_command="/var",
            required=True,
        )
    )
    idx += 1

    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="filesystem_writable",
            check_command="/tmp",
            required=True,
        )
    )
    idx += 1

    # === Critical Mount Points ===
    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="mount_exists",
            check_command="/",
            required=True,
        )
    )
    idx += 1

    # === Disk Space ===
    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="disk_space",
            check_command="/:500",  # At least 500MB on root
            required=True,
        )
    )
    idx += 1

    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="disk_space",
            check_command="/var:200",  # At least 200MB on /var
            required=False,  # Warning only
        )
    )
    idx += 1

    # === System Health (optional - may be degraded but usable) ===
    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="command",
            check_command="systemctl is-system-running --quiet || true",
            expected_exit_code=0,
            required=False,
        )
    )
    idx += 1

    return verifications


def create_kernel_update_verifications(
    kernel_version: str,
    expected_modules: list[str] | None = None,
) -> list[PendingVerification]:
    """Create verifications specific to kernel updates.

    Args:
        kernel_version: Expected kernel version after reboot (or substring).
        expected_modules: Optional list of kernel modules that must be loaded.

    Returns:
        List of PendingVerifications for kernel update.
    """
    verifications = create_default_verifications()
    idx = len(verifications)

    # Verify kernel version
    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="command",
            check_command="uname -r",
            expected_exit_code=0,
            expected_output_contains=kernel_version,
            required=True,
        )
    )
    idx += 1

    # Check for kernel errors in dmesg
    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="dmesg_no_errors",
            check_command="panic|oops|BUG:",  # Critical kernel errors
            required=True,
        )
    )
    idx += 1

    # Check expected modules are loaded
    if expected_modules:
        for module in expected_modules:
            verifications.append(
                PendingVerification(
                    step_index=idx,
                    check_type="kernel_module",
                    check_command=module,
                    required=True,
                )
            )
            idx += 1

    return verifications


def create_driver_update_verifications(
    driver_module: str,
    device_path: str | None = None,
    service_name: str | None = None,
) -> list[PendingVerification]:
    """Create verifications for driver updates (GPU, network, etc.).

    Args:
        driver_module: Kernel module name for the driver.
        device_path: Optional device path that should exist (e.g., /dev/nvidia0).
        service_name: Optional service that depends on the driver.

    Returns:
        List of PendingVerifications for driver update.
    """
    verifications = create_default_verifications()
    idx = len(verifications)

    # Verify driver module is loaded
    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="kernel_module",
            check_command=driver_module,
            required=True,
        )
    )
    idx += 1

    # Check for driver errors in dmesg
    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="dmesg_no_errors",
            check_command=f"{driver_module}.*error|{driver_module}.*failed",
            required=True,
        )
    )
    idx += 1

    # Check device path exists
    if device_path:
        verifications.append(
            PendingVerification(
                step_index=idx,
                check_type="file_exists",
                check_command=device_path,
                required=True,
            )
        )
        idx += 1

    # Check dependent service
    if service_name:
        verifications.append(
            PendingVerification(
                step_index=idx,
                check_type="service_active",
                check_command=service_name,
                required=True,
            )
        )
        idx += 1

    return verifications


def create_fstab_change_verifications(
    expected_mounts: list[str],
    check_writable: bool = True,
) -> list[PendingVerification]:
    """Create verifications for FSTAB configuration changes.

    Args:
        expected_mounts: List of mount points that should be mounted.
        check_writable: If True, also verify mounts are writable.

    Returns:
        List of PendingVerifications for FSTAB changes.
    """
    verifications = create_default_verifications()
    idx = len(verifications)

    for mount_point in expected_mounts:
        # Check mount exists
        verifications.append(
            PendingVerification(
                step_index=idx,
                check_type="mount_exists",
                check_command=mount_point,
                required=True,
            )
        )
        idx += 1

        # Check mount is writable (if applicable)
        if check_writable and mount_point not in ("/proc", "/sys", "/dev"):
            verifications.append(
                PendingVerification(
                    step_index=idx,
                    check_type="filesystem_writable",
                    check_command=mount_point,
                    required=True,
                )
            )
            idx += 1

    # Check for mount errors in journal
    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="journal_no_errors",
            check_command="Failed to mount|mount.*failed",
            required=True,
        )
    )
    idx += 1

    return verifications


def create_service_restart_verifications(
    service_name: str,
    port: int | None = None,
    health_endpoint: str | None = None,
) -> list[PendingVerification]:
    """Create verifications for service restart.

    Args:
        service_name: Name of service that should be running.
        port: Optional port that service should be listening on.
        health_endpoint: Optional health check command.

    Returns:
        List of PendingVerifications for service restart.
    """
    verifications = create_default_verifications()
    idx = len(verifications)

    # Service must be active
    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="service_active",
            check_command=service_name,
            required=True,
        )
    )
    idx += 1

    # Port must be listening
    if port:
        verifications.append(
            PendingVerification(
                step_index=idx,
                check_type="port_listening",
                check_command=f"127.0.0.1:{port}",
                required=True,
            )
        )
        idx += 1

    # Health check
    if health_endpoint:
        verifications.append(
            PendingVerification(
                step_index=idx,
                check_type="command",
                check_command=health_endpoint,
                expected_exit_code=0,
                required=True,
            )
        )
        idx += 1

    return verifications


def create_grub_update_verifications(
    expected_default: str | None = None,
) -> list[PendingVerification]:
    """Create verifications for GRUB configuration updates.

    Args:
        expected_default: Expected GRUB default entry after update.

    Returns:
        List of PendingVerifications for GRUB update.
    """
    verifications = create_default_verifications()
    idx = len(verifications)

    # GRUB config must exist
    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="file_exists",
            check_command="/boot/grub/grub.cfg",
            required=True,
        )
    )
    idx += 1

    # Check for boot errors
    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="dmesg_no_errors",
            check_command="ACPI.*error|EFI.*error",
            required=False,  # May have benign errors
        )
    )
    idx += 1

    return verifications


def create_network_config_verifications(
    interfaces: list[str] | None = None,
    expected_gateway: bool = True,
    expected_dns: bool = True,
) -> list[PendingVerification]:
    """Create verifications for network configuration changes.

    Args:
        interfaces: List of network interfaces that should be up.
        expected_gateway: Whether to check for default gateway.
        expected_dns: Whether to check DNS resolution.

    Returns:
        List of PendingVerifications for network config.
    """
    verifications = create_default_verifications()
    idx = len(verifications)

    # Check specific interfaces
    if interfaces:
        for iface in interfaces:
            verifications.append(
                PendingVerification(
                    step_index=idx,
                    check_type="network_interface",
                    check_command=iface,
                    required=True,
                )
            )
            idx += 1

    # Check for default gateway
    if expected_gateway:
        verifications.append(
            PendingVerification(
                step_index=idx,
                check_type="command",
                check_command="ip route show default | grep -q via",
                expected_exit_code=0,
                required=True,
            )
        )
        idx += 1

    # Check DNS resolution
    if expected_dns:
        verifications.append(
            PendingVerification(
                step_index=idx,
                check_type="command",
                check_command="host -W 5 google.com > /dev/null 2>&1",
                expected_exit_code=0,
                required=False,  # May fail without internet
            )
        )
        idx += 1

    # Check for network errors in journal
    verifications.append(
        PendingVerification(
            step_index=idx,
            check_type="journal_no_errors",
            check_command="NetworkManager.*failed|network.*unreachable",
            required=True,
        )
    )
    idx += 1

    return verifications


# =============================================================================
# Diagnostic Collection
# =============================================================================


async def collect_failure_diagnostics(
    intent: RebootIntent,
    verification_results: list[dict[str, Any]] | None = None,
) -> FailureDiagnostics:
    """Collect comprehensive diagnostics after a failed reboot.

    This function gathers all relevant information for the LLM to
    understand what went wrong and propose a fix.

    Args:
        intent: The failed reboot intent.
        verification_results: Results from verification checks.

    Returns:
        FailureDiagnostics with all collected information.
    """
    logger.info(f"Collecting failure diagnostics for intent: {intent.id}")

    # Separate passed and failed checks
    failed_checks = []
    passed_checks = []
    if verification_results:
        for r in verification_results:
            if r.get("passed"):
                passed_checks.append(r)
            else:
                failed_checks.append(r)

    # Collect dmesg errors
    dmesg_errors = await _collect_dmesg_errors()

    # Collect journal errors
    journal_errors = await _collect_journal_errors()

    # Collect failed services
    failed_services = await _collect_failed_services()

    # Check filesystem state
    read_only_mounts, missing_mounts = await _check_filesystem_state()

    # Check disk space
    disk_space_issues = await _check_disk_space_issues()

    # Check network state
    interfaces_down, network_errors = await _check_network_state()

    # Check kernel modules
    missing_modules, module_errors = await _check_module_state(intent)

    # Get kernel info
    kernel_version = await _get_kernel_version()
    previous_kernel = intent.pre_snapshot_json.get("kernel") if intent.pre_snapshot_json else None

    # Get boot mode
    boot_mode = "UEFI" if Path("/sys/firmware/efi").exists() else "BIOS"

    # Get uptime
    uptime_seconds = await _get_uptime_seconds()

    # Analyze likely causes
    likely_causes = _analyze_likely_causes(
        failed_checks=failed_checks,
        dmesg_errors=dmesg_errors,
        journal_errors=journal_errors,
        failed_services=failed_services,
        read_only_mounts=read_only_mounts,
        missing_mounts=missing_mounts,
        disk_space_issues=disk_space_issues,
        interfaces_down=interfaces_down,
        missing_modules=missing_modules,
    )

    # Suggest investigation commands
    suggested_investigation = _suggest_investigation(
        intent=intent,
        likely_causes=likely_causes,
        failed_services=failed_services,
        missing_modules=missing_modules,
    )

    # Get GRUB state
    from elle.daemon.reboot.grub import get_grub_state, get_saved_default

    grub_state = get_grub_state()

    return FailureDiagnostics(
        intent_goal=intent.goal,
        intent_reason=intent.reason,
        intent_reason_detail=intent.reason_detail,
        commands_before_reboot=intent.session_history,
        config_changes=tuple(intent.plan_json.get("config_files", [])) if intent.plan_json else (),
        failed_checks=tuple(failed_checks),
        passed_checks=tuple(passed_checks),
        kernel_version=kernel_version,
        previous_kernel=previous_kernel,
        boot_mode=boot_mode,
        uptime_seconds=uptime_seconds,
        dmesg_errors=tuple(dmesg_errors),
        journal_errors=tuple(journal_errors),
        failed_services=tuple(failed_services),
        read_only_mounts=tuple(read_only_mounts),
        missing_mounts=tuple(missing_mounts),
        disk_space_issues=tuple(disk_space_issues),
        interfaces_down=tuple(interfaces_down),
        network_errors=tuple(network_errors),
        missing_modules=tuple(missing_modules),
        module_errors=tuple(module_errors),
        current_grub_entry=get_saved_default(),
        previous_grub_entry=intent.grub_default_saved,
        grub_entries_available=grub_state.entries,
        likely_causes=tuple(likely_causes),
        suggested_investigation=tuple(suggested_investigation),
    )


async def _collect_dmesg_errors() -> list[str]:
    """Collect error lines from dmesg."""
    try:
        result = subprocess.run(
            ["dmesg", "-T", "--level=err,crit,alert,emerg"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().splitlines()
            return lines[:DMESG_LINES_TO_CAPTURE]
    except Exception as e:
        logger.warning(f"Failed to collect dmesg errors: {e}")
    return []


async def _collect_journal_errors() -> list[str]:
    """Collect error lines from journald since boot."""
    try:
        result = subprocess.run(
            ["journalctl", "-b", "-p", "err", "--no-pager", "-n", str(JOURNAL_LINES_TO_CAPTURE)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = [l for l in result.stdout.strip().splitlines() if l and not l.startswith("-- ")]
            return lines
    except Exception as e:
        logger.warning(f"Failed to collect journal errors: {e}")
    return []


async def _collect_failed_services() -> list[str]:
    """Collect list of failed systemd services."""
    try:
        result = subprocess.run(
            ["systemctl", "--failed", "--no-legend", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            services = []
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if parts:
                    services.append(parts[0])
            return services
    except Exception as e:
        logger.warning(f"Failed to collect failed services: {e}")
    return []


async def _check_filesystem_state() -> tuple[list[str], list[str]]:
    """Check for read-only mounts and missing critical mounts."""
    read_only = []
    missing = []

    # Check mounts
    try:
        result = subprocess.run(
            ["mount"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            mount_output = result.stdout
            mounted_paths = set()

            for line in mount_output.splitlines():
                parts = line.split()
                if len(parts) >= 6:
                    mount_point = parts[2]
                    options = parts[5] if len(parts) > 5 else ""
                    mounted_paths.add(mount_point)

                    # Check if mounted read-only when it shouldn't be
                    if mount_point in CRITICAL_MOUNT_POINTS:
                        if "(ro," in options or "(ro)" in options:
                            read_only.append(mount_point)

            # Check for missing critical mounts
            for mp in CRITICAL_MOUNT_POINTS:
                if mp not in mounted_paths and mp != "/":  # / is always there
                    # Check if it's supposed to be a mount point
                    if Path(mp).exists():
                        # Path exists but isn't a separate mount - could be intentional
                        pass
                    else:
                        missing.append(mp)

    except Exception as e:
        logger.warning(f"Failed to check filesystem state: {e}")

    return read_only, missing


async def _check_disk_space_issues() -> list[str]:
    """Check for disk space issues on critical paths."""
    issues = []
    try:
        import shutil

        for path, min_mb in DISK_SPACE_REQUIREMENTS:
            if Path(path).exists():
                stat = shutil.disk_usage(path)
                free_mb = stat.free // (1024 * 1024)
                if free_mb < min_mb:
                    issues.append(f"{path}: {free_mb}MB free (< {min_mb}MB required)")
    except Exception as e:
        logger.warning(f"Failed to check disk space: {e}")
    return issues


async def _check_network_state() -> tuple[list[str], list[str]]:
    """Check network interfaces and errors."""
    interfaces_down = []
    network_errors = []

    # Check interface states
    try:
        net_dir = Path("/sys/class/net")
        if net_dir.exists():
            for iface in net_dir.iterdir():
                if iface.name == "lo":
                    continue
                state_path = iface / "operstate"
                if state_path.exists():
                    state = state_path.read_text().strip()
                    if state not in ("up", "unknown"):
                        interfaces_down.append(f"{iface.name}: {state}")
    except Exception as e:
        logger.warning(f"Failed to check interface states: {e}")

    # Check for network errors in journal
    try:
        result = subprocess.run(
            ["journalctl", "-b", "-u", "NetworkManager", "-p", "err", "--no-pager", "-n", "10"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                if line and not line.startswith("-- "):
                    network_errors.append(line)
    except Exception:
        pass

    return interfaces_down, network_errors


async def _check_module_state(intent: RebootIntent) -> tuple[list[str], list[str]]:
    """Check kernel module state based on intent."""
    missing_modules = []
    module_errors = []

    # Get list of expected modules from intent
    expected_modules = set()
    if intent.reason == "driver_update":
        # Try to extract driver module from plan
        if intent.plan_json:
            modules = intent.plan_json.get("expected_modules", [])
            expected_modules.update(modules)

    # Check for module errors in dmesg
    try:
        result = subprocess.run(
            ["dmesg", "-T"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if re.search(r"module.*error|failed.*load.*module|modprobe.*failed", line, re.IGNORECASE):
                    module_errors.append(line)
    except Exception:
        pass

    # Check expected modules are loaded
    if expected_modules:
        try:
            result = subprocess.run(
                ["lsmod"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                loaded = {line.split()[0] for line in result.stdout.splitlines()[1:]}
                for mod in expected_modules:
                    if mod not in loaded:
                        missing_modules.append(mod)
        except Exception:
            pass

    return missing_modules, module_errors


async def _get_kernel_version() -> str | None:
    """Get current kernel version."""
    try:
        result = subprocess.run(
            ["uname", "-r"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


async def _get_uptime_seconds() -> int | None:
    """Get system uptime in seconds."""
    try:
        uptime_path = Path("/proc/uptime")
        if uptime_path.exists():
            content = uptime_path.read_text()
            return int(float(content.split()[0]))
    except Exception:
        pass
    return None


def _analyze_likely_causes(
    failed_checks: list[dict[str, Any]],
    dmesg_errors: list[str],
    journal_errors: list[str],
    failed_services: list[str],
    read_only_mounts: list[str],
    missing_mounts: list[str],
    disk_space_issues: list[str],
    interfaces_down: list[str],
    missing_modules: list[str],
) -> list[str]:
    """Analyze diagnostics to determine likely root causes."""
    causes = []

    # Read-only filesystem is critical
    if read_only_mounts:
        causes.append(
            f"CRITICAL: Filesystem(s) mounted read-only: {', '.join(read_only_mounts)}. "
            "This usually indicates disk errors, failed fsck, or fstab misconfiguration."
        )

    # Missing mounts
    if missing_mounts:
        causes.append(
            f"Missing mount points: {', '.join(missing_mounts)}. Check /etc/fstab for errors or missing devices."
        )

    # Disk space
    if disk_space_issues:
        causes.append(f"Low disk space: {'; '.join(disk_space_issues)}. System may fail to write logs or temp files.")

    # Network issues
    if interfaces_down:
        causes.append(
            f"Network interfaces down: {', '.join(interfaces_down)}. Check network configuration and drivers."
        )

    # Missing modules
    if missing_modules:
        causes.append(
            f"Expected kernel modules not loaded: {', '.join(missing_modules)}. "
            "Driver may have failed to build for new kernel."
        )

    # Failed services
    if failed_services:
        causes.append(
            f"Failed systemd services: {', '.join(failed_services[:5])}. "
            "Check 'systemctl status <service>' for details."
        )

    # Kernel errors
    kernel_patterns = {
        "panic": "Kernel panic detected - critical system failure",
        "oops": "Kernel oops detected - possible driver bug",
        "BUG:": "Kernel bug detected - may indicate driver incompatibility",
        "I/O error": "Disk I/O errors - possible hardware failure or cable issue",
        "EXT4-fs error": "EXT4 filesystem errors - run fsck",
        "BTRFS error": "BTRFS filesystem errors - check btrfs device stats",
    }
    for error in dmesg_errors[:20]:
        for pattern, description in kernel_patterns.items():
            if pattern.lower() in error.lower():
                causes.append(description)
                break

    # Service-specific patterns
    service_patterns = {
        "Failed to start": "Service startup failure",
        "Dependency failed": "Service dependency issue - check dependent services",
        "Failed to mount": "Mount failure - check fstab and device availability",
    }
    for error in journal_errors[:20]:
        for pattern, description in service_patterns.items():
            if pattern in error:
                causes.append(f"{description}: {error[:100]}")
                break

    # Deduplicate while preserving order
    seen = set()
    unique_causes = []
    for cause in causes:
        if cause not in seen:
            seen.add(cause)
            unique_causes.append(cause)

    return unique_causes[:10]  # Top 10 causes


def _suggest_investigation(
    intent: RebootIntent,
    likely_causes: list[str],
    failed_services: list[str],
    missing_modules: list[str],
) -> list[str]:
    """Suggest commands to investigate further."""
    suggestions = []

    # General diagnostics
    suggestions.append("journalctl -b -p err --no-pager | head -50")
    suggestions.append("dmesg -T --level=err,crit | tail -30")

    # Based on intent reason
    if intent.reason == "kernel_update":
        suggestions.append("uname -r  # Verify kernel version")
        suggestions.append("dpkg -l | grep linux-image  # List installed kernels")
        suggestions.append("cat /boot/grub/grub.cfg | grep menuentry  # Check GRUB entries")

    elif intent.reason == "driver_update":
        suggestions.append("lsmod | grep -i <driver>  # Check if driver loaded")
        suggestions.append("dmesg | grep -i <driver>  # Check driver messages")
        suggestions.append("modprobe -v <driver>  # Try loading driver manually")

    elif intent.reason == "config_change":
        suggestions.append("cat /etc/fstab  # Check fstab for errors")
        suggestions.append("mount  # Check current mounts")
        suggestions.append("findmnt --verify  # Verify fstab syntax")

    # Based on issues found
    if any("read-only" in c.lower() for c in likely_causes):
        suggestions.append("mount | grep ' / '  # Check root mount options")
        suggestions.append("dmesg | grep -i 'remount'  # Check for remount messages")
        suggestions.append("fsck -n /dev/<root>  # Check filesystem (dry run)")

    if failed_services:
        for svc in failed_services[:3]:
            suggestions.append(f"systemctl status {svc}  # Check {svc} status")
            suggestions.append(f"journalctl -u {svc} -b --no-pager | tail -20")

    if missing_modules:
        for mod in missing_modules[:2]:
            suggestions.append(f"modinfo {mod}  # Check module info")
            suggestions.append("dkms status  # Check DKMS status")

    return suggestions[:15]  # Top 15 suggestions
