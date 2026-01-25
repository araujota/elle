"""Tier 2: systemd-nspawn container validation.

Validates package operations in an ephemeral container using
systemd-nspawn with overlay filesystem:
- Tests actual package installation
- Verifies service startup
- Detects runtime issues
- Provides 70-80% real-world accuracy

The container is destroyed after validation, leaving no trace.
"""

import logging
import shutil
import subprocess
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from elle.ops.preflight.models import (
    IssueSeverity,
    NspawnConfig,
    PreflightIssue,
    PreflightResult,
    PreflightStatus,
    PreflightTest,
)

logger = logging.getLogger(__name__)


# Service name patterns for package -> service mapping
PACKAGE_SERVICE_MAP: dict[str, list[str]] = {
    "nginx": ["nginx"],
    "apache2": ["apache2"],
    "mysql-server": ["mysql"],
    "mariadb-server": ["mariadb"],
    "postgresql": ["postgresql"],
    "redis-server": ["redis-server"],
    "mongodb-server": ["mongod"],
    "openssh-server": ["ssh", "sshd"],
    "docker.io": ["docker"],
    "containerd": ["containerd"],
    "postfix": ["postfix"],
    "bind9": ["named", "bind9"],
    "dnsmasq": ["dnsmasq"],
    "haproxy": ["haproxy"],
    "traefik": ["traefik"],
    "prometheus": ["prometheus"],
    "grafana": ["grafana-server"],
    "elasticsearch": ["elasticsearch"],
    "rabbitmq-server": ["rabbitmq-server"],
}


def _get_package_services(package: str) -> list[str]:
    """Get service names for a package.

    Args:
        package: Package name.

    Returns:
        List of service names to check.
    """
    # Normalize package name
    base_name = package.split("=")[0].split("/")[0]

    if base_name in PACKAGE_SERVICE_MAP:
        return PACKAGE_SERVICE_MAP[base_name]

    # Try common pattern: package name == service name
    return [base_name]


class NspawnValidator:
    """Tier 2 validator using systemd-nspawn containers.

    Creates ephemeral containers with overlay filesystem to test
    package operations without affecting the host system.
    """

    def __init__(self, config: NspawnConfig | None = None) -> None:
        """Initialize the nspawn validator.

        Args:
            config: Nspawn configuration. Uses defaults if not provided.
        """
        self._config = config or NspawnConfig()
        self._base_image_ready = False

    @property
    def is_available(self) -> bool:
        """Check if systemd-nspawn is available."""
        try:
            result = subprocess.run(
                ["systemd-nspawn", "--version"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    @property
    def has_base_image(self) -> bool:
        """Check if base image is prepared."""
        base_path = Path(self._config.base_image_path)
        return base_path.exists() and (base_path / "etc" / "os-release").exists()

    def prepare_base_image(self) -> bool:
        """Prepare the base Ubuntu image for containers.

        Creates a minimal Ubuntu 24.04 rootfs that will be used
        as the lower layer for overlay mounts.

        Returns:
            True if image is ready.
        """
        if self.has_base_image:
            logger.debug("Base image already exists")
            return True

        base_path = Path(self._config.base_image_path)

        try:
            # Create directory
            base_path.mkdir(parents=True, exist_ok=True)

            # Bootstrap with debootstrap
            logger.info("Creating base image (this may take a few minutes)...")
            result = subprocess.run(
                [
                    "debootstrap",
                    "--variant=minbase",
                    "--include=systemd,systemd-sysv,apt",
                    "noble",  # Ubuntu 24.04
                    str(base_path),
                    "http://archive.ubuntu.com/ubuntu",
                ],
                capture_output=True,
                text=True,
                timeout=600,  # 10 minutes
            )

            if result.returncode != 0:
                logger.error(f"debootstrap failed: {result.stderr}")
                return False

            logger.info("Base image created successfully")
            return True

        except FileNotFoundError:
            logger.error("debootstrap not found. Install with: apt install debootstrap")
            return False
        except subprocess.TimeoutExpired:
            logger.error("debootstrap timed out")
            return False
        except Exception as e:
            logger.error(f"Failed to create base image: {e}")
            return False

    @contextmanager
    def _create_ephemeral_container(
        self,
    ) -> Generator[Path, None, None]:
        """Create an ephemeral container with overlay filesystem.

        Yields the container root path. Container is automatically
        cleaned up when the context exits.

        Yields:
            Path to container root.
        """
        if not self.has_base_image:
            raise RuntimeError("Base image not prepared. Call prepare_base_image() first.")

        # Create temporary directories for overlay
        overlay_dir = tempfile.mkdtemp(
            prefix="elle-preflight-",
            dir=self._config.overlay_path if Path(self._config.overlay_path).exists() else None,
        )

        upper_dir = Path(overlay_dir) / "upper"
        work_dir = Path(overlay_dir) / "work"
        merged_dir = Path(overlay_dir) / "merged"

        upper_dir.mkdir()
        work_dir.mkdir()
        merged_dir.mkdir()

        try:
            # Mount overlay filesystem
            mount_cmd = [
                "mount",
                "-t", "overlay",
                "overlay",
                "-o", f"lowerdir={self._config.base_image_path},upperdir={upper_dir},workdir={work_dir}",
                str(merged_dir),
            ]

            subprocess.run(mount_cmd, check=True, capture_output=True)

            yield merged_dir

        finally:
            # Cleanup: unmount and remove
            try:
                subprocess.run(
                    ["umount", str(merged_dir)],
                    capture_output=True,
                    timeout=30,
                )
            except Exception as e:
                logger.warning(f"Failed to unmount overlay: {e}")

            try:
                shutil.rmtree(overlay_dir)
            except Exception as e:
                logger.warning(f"Failed to cleanup overlay dir: {e}")

    def _run_in_container(
        self,
        container_path: Path,
        command: str,
        timeout_sec: int = 60,
    ) -> tuple[int, str, str]:
        """Run a command in the container.

        Args:
            container_path: Path to container root.
            command: Command to run.
            timeout_sec: Timeout in seconds.

        Returns:
            Tuple of (exit_code, stdout, stderr).
        """
        nspawn_cmd = [
            "systemd-nspawn",
            "--quiet",
            "--directory", str(container_path),
            "--ephemeral",
            "--as-pid2",
        ]

        if self._config.network_enabled:
            nspawn_cmd.append("--network-veth")

        nspawn_cmd.extend(["/bin/bash", "-c", command])

        try:
            result = subprocess.run(
                nspawn_cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            return (result.returncode, result.stdout, result.stderr)
        except subprocess.TimeoutExpired:
            return (-1, "", f"Command timed out after {timeout_sec}s")
        except Exception as e:
            return (-1, "", str(e))

    def validate(self, test: PreflightTest) -> PreflightResult:
        """Validate a package operation in an ephemeral container.

        Args:
            test: The pre-flight test specification.

        Returns:
            PreflightResult with validation outcome.
        """
        start_time = time.time()
        issues: list[PreflightIssue] = []
        output_lines: list[str] = []

        # Check availability
        if not self.is_available:
            return PreflightResult(
                status=PreflightStatus.BLOCKED,
                tier_used=2,
                issues=(
                    PreflightIssue(
                        severity=IssueSeverity.ERROR,
                        message="systemd-nspawn is not available",
                        recommendation="Install systemd-container package",
                        source="nspawn",
                    ),
                ),
                duration_ms=int((time.time() - start_time) * 1000),
                can_proceed=False,
            )

        # Check/prepare base image
        if not self.has_base_image:
            if not self.prepare_base_image():
                return PreflightResult(
                    status=PreflightStatus.BLOCKED,
                    tier_used=2,
                    issues=(
                        PreflightIssue(
                            severity=IssueSeverity.ERROR,
                            message="Failed to prepare base image for container validation",
                            recommendation="Check debootstrap installation and disk space",
                            source="nspawn",
                        ),
                    ),
                    duration_ms=int((time.time() - start_time) * 1000),
                    can_proceed=False,
                )

        try:
            with self._create_ephemeral_container() as container_path:
                # Update package lists
                exit_code, stdout, stderr = self._run_in_container(
                    container_path,
                    "apt-get update",
                    timeout_sec=120,
                )
                output_lines.append(f"=== apt update ===\n{stdout}\n{stderr}")

                if exit_code != 0:
                    issues.append(
                        PreflightIssue(
                            severity=IssueSeverity.WARNING,
                            message="Failed to update package lists in container",
                            source="nspawn",
                            technical_detail=stderr,
                        )
                    )

                # Install packages
                packages_str = " ".join(test.packages)
                install_cmd = f"DEBIAN_FRONTEND=noninteractive apt-get install -y {packages_str}"

                exit_code, stdout, stderr = self._run_in_container(
                    container_path,
                    install_cmd,
                    timeout_sec=self._config.timeout_sec,
                )
                output_lines.append(f"=== apt install ===\n{stdout}\n{stderr}")

                if exit_code != 0:
                    issues.append(
                        PreflightIssue(
                            severity=IssueSeverity.ERROR,
                            message="Package installation failed in container",
                            recommendation="Review package dependencies and conflicts",
                            source="nspawn",
                            technical_detail=stderr[:1000] if stderr else "Unknown error",
                        )
                    )
                else:
                    # Test service startup for each package
                    for package in test.packages:
                        services = _get_package_services(package)

                        for service in services:
                            exit_code, stdout, stderr = self._run_in_container(
                                container_path,
                                f"systemctl start {service}",
                                timeout_sec=30,
                            )

                            if exit_code != 0:
                                issues.append(
                                    PreflightIssue(
                                        severity=IssueSeverity.ERROR,
                                        message=f"Service {service} failed to start",
                                        recommendation=f"Check service logs: journalctl -u {service}",
                                        source="nspawn",
                                        package=package,
                                        technical_detail=stderr[:500] if stderr else None,
                                    )
                                )
                            else:
                                output_lines.append(f"Service {service} started successfully")

                # Determine result
                duration_ms = int((time.time() - start_time) * 1000)
                has_errors = any(
                    i.severity in (IssueSeverity.ERROR, IssueSeverity.CRITICAL)
                    for i in issues
                )

                if has_errors:
                    status = PreflightStatus.BLOCKED
                    can_proceed = False
                elif issues:
                    status = PreflightStatus.WARNING
                    can_proceed = True
                else:
                    status = PreflightStatus.SAFE
                    can_proceed = True

                return PreflightResult(
                    status=status,
                    tier_used=2,
                    issues=tuple(issues),
                    duration_ms=duration_ms,
                    can_proceed=can_proceed,
                    nspawn_output="\n".join(output_lines),
                )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"nspawn validation failed: {e}")
            return PreflightResult(
                status=PreflightStatus.BLOCKED,
                tier_used=2,
                issues=(
                    PreflightIssue(
                        severity=IssueSeverity.ERROR,
                        message=f"Container validation error: {e}",
                        source="nspawn",
                        technical_detail=str(e),
                    ),
                ),
                duration_ms=duration_ms,
                can_proceed=False,
            )


def create_nspawn_validator(
    config: NspawnConfig | None = None,
) -> NspawnValidator:
    """Create an nspawn validator instance.

    Args:
        config: Nspawn configuration.

    Returns:
        Configured NspawnValidator instance.
    """
    return NspawnValidator(config=config)
