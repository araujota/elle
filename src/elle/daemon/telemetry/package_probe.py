"""Package version monitoring probe.

Monitors /var/lib/dpkg/status for package version changes
and emits events when watched packages are upgraded or new packages are installed.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elle.daemon.config import ThresholdConfig
from elle.daemon.telemetry.models import ProbeResult, TelemetryEvent
from elle.daemon.telemetry.probes import BaseProbe

logger = logging.getLogger(__name__)


# Callback type for new package detection
NewPackageCallback = Callable[[str, str], None]  # (package_name, version) -> None


class PackageProbe(BaseProbe):
    """Detects package version changes via dpkg status monitoring.

    Monitors:
    1. Packages with generated capabilities for version upgrades
    2. All installed packages for new package installations (optional)
    """

    name = "package"
    interval = 300  # 5 min polling interval

    DPKG_STATUS_PATH = Path("/var/lib/dpkg/status")

    def __init__(
        self,
        thresholds: ThresholdConfig | None = None,
        detect_new_packages: bool = False,
    ) -> None:
        """Initialize the probe.

        Args:
            thresholds: Threshold configuration (unused but required by base).
            detect_new_packages: If True, track all packages and emit events
                for newly installed packages.
        """
        super().__init__(thresholds)
        self._package_versions: dict[str, str] = {}  # package -> version
        self._watched_packages: set[str] = set()  # only packages with capabilities
        self._all_packages: dict[str, str] = {}  # all installed packages (for new detection)
        self._detect_new_packages = detect_new_packages
        self._new_package_callback: NewPackageCallback | None = None
        self._initialized = False

    def set_watched_packages(self, packages: set[str]) -> None:
        """Set which packages to monitor (those with capabilities).

        Args:
            packages: Set of package names to watch.
        """
        self._watched_packages = packages
        logger.info(f"PackageProbe watching {len(packages)} packages")

    def add_watched_package(self, package: str) -> None:
        """Add a package to the watch list.

        Args:
            package: Package name to add.
        """
        self._watched_packages.add(package)

    def remove_watched_package(self, package: str) -> None:
        """Remove a package from the watch list.

        Args:
            package: Package name to remove.
        """
        self._watched_packages.discard(package)
        self._package_versions.pop(package, None)

    def enable_new_package_detection(self, enabled: bool = True) -> None:
        """Enable or disable detection of newly installed packages.

        Args:
            enabled: Whether to detect new packages.
        """
        self._detect_new_packages = enabled

    def set_new_package_callback(self, callback: NewPackageCallback | None) -> None:
        """Set callback for newly installed packages.

        Args:
            callback: Function to call when new package detected.
                Receives (package_name, version).
        """
        self._new_package_callback = callback

    async def run(self) -> ProbeResult:
        """Parse dpkg status, diff against known versions, emit events.

        Returns:
            ProbeResult with any upgrade events detected.
        """
        self._last_run = datetime.now(UTC)
        self._run_count += 1

        # If not watching anything and not detecting new packages, nothing to do
        if not self._watched_packages and not self._detect_new_packages:
            return ProbeResult(
                probe_name=self.name,
                ts=self._last_run,
                success=True,
                data={"watched_packages": 0, "events": []},
                events=(),
            )

        try:
            current = await self._parse_dpkg_status()
            events: list[TelemetryEvent] = []
            new_packages_detected: list[tuple[str, str]] = []

            if not self._initialized:
                # First run - record baseline
                self._package_versions = {pkg: ver for pkg, ver in current.items() if pkg in self._watched_packages}
                if self._detect_new_packages:
                    self._all_packages = dict(current)
                self._initialized = True
                logger.debug(
                    f"PackageProbe initialized with {len(self._package_versions)} "
                    f"watched packages, {len(self._all_packages)} total packages"
                )
            else:
                # Check for version changes in watched packages
                for pkg in self._watched_packages:
                    old_ver = self._package_versions.get(pkg)
                    new_ver = current.get(pkg)

                    if old_ver and new_ver and old_ver != new_ver:
                        event = self._create_upgrade_event(pkg, old_ver, new_ver)
                        events.append(event)
                        logger.info(f"Package upgrade detected: {pkg} {old_ver} -> {new_ver}")
                    elif not old_ver and new_ver:
                        # Newly installed package we're watching
                        logger.debug(f"Package {pkg} now installed: {new_ver}")

                # Check for NEW package installations (not upgrades)
                if self._detect_new_packages:
                    for pkg, ver in current.items():
                        if pkg not in self._all_packages:
                            # This is a newly installed package
                            new_packages_detected.append((pkg, ver))
                            event = self._create_new_package_event(pkg, ver)
                            events.append(event)
                            logger.info(f"New package installed: {pkg} {ver}")

                            # Trigger callback if set
                            if self._new_package_callback:
                                try:
                                    self._new_package_callback(pkg, ver)
                                except Exception as cb_err:
                                    logger.warning(f"New package callback error for {pkg}: {cb_err}")

                # Update tracked versions
                self._package_versions = {pkg: ver for pkg, ver in current.items() if pkg in self._watched_packages}
                if self._detect_new_packages:
                    self._all_packages = dict(current)

            return ProbeResult(
                probe_name=self.name,
                ts=self._last_run,
                success=True,
                data={
                    "watched_packages": len(self._watched_packages),
                    "tracked_packages": len(self._package_versions),
                    "all_packages": len(self._all_packages),
                    "upgrades_detected": len([e for e in events if e.raw.get("event_type") == "upgrade"]),
                    "new_packages_detected": len(new_packages_detected),
                },
                events=tuple(events),
            )

        except Exception as e:
            self._error_count += 1
            logger.error(f"PackageProbe error: {e}")
            return ProbeResult(
                probe_name=self.name,
                ts=self._last_run,
                success=False,
                data={},
                error=str(e),
            )

    async def _parse_dpkg_status(self) -> dict[str, str]:
        """Parse dpkg status file for package versions.

        Returns:
            Dict mapping package name to version string.
        """
        packages: dict[str, str] = {}

        if not self.DPKG_STATUS_PATH.exists():
            logger.warning(f"dpkg status file not found: {self.DPKG_STATUS_PATH}")
            return packages

        try:
            content = await asyncio.to_thread(self.DPKG_STATUS_PATH.read_text)
            current_package: str | None = None
            current_version: str | None = None
            current_status: str | None = None

            for line in content.splitlines():
                if line.startswith("Package: "):
                    # Save previous package if valid
                    if current_package and current_version and self._is_installed(current_status):
                        packages[current_package] = current_version

                    current_package = line[9:].strip()
                    current_version = None
                    current_status = None

                elif line.startswith("Version: "):
                    current_version = line[9:].strip()

                elif line.startswith("Status: "):
                    current_status = line[8:].strip()

            # Don't forget last package
            if current_package and current_version and self._is_installed(current_status):
                packages[current_package] = current_version

        except Exception as e:
            logger.error(f"Failed to parse dpkg status: {e}")

        return packages

    def _is_installed(self, status: str | None) -> bool:
        """Check if package status indicates installed.

        Args:
            status: dpkg status string.

        Returns:
            True if package is installed.
        """
        if not status:
            return False
        # Status format: want flag, error flag, status
        # e.g., "install ok installed"
        return "installed" in status.lower()

    def _create_upgrade_event(
        self,
        package_name: str,
        old_version: str,
        new_version: str,
    ) -> TelemetryEvent:
        """Create a TelemetryEvent for package upgrade.

        Args:
            package_name: Name of upgraded package.
            old_version: Previous version.
            new_version: New version.

        Returns:
            TelemetryEvent for the upgrade.
        """
        upgrade_type = self._classify_upgrade(old_version, new_version)

        raw_data: dict[str, Any] = {
            "event_type": "upgrade",
            "package_name": package_name,
            "old_version": old_version,
            "new_version": new_version,
            "upgrade_type": upgrade_type,
        }

        return self._create_event(
            message=f"Package {package_name} upgraded: {old_version} -> {new_version}",
            severity="info",
            category="pkg",
            entity=f"package:{package_name}",
            data=raw_data,
        )

    def _create_new_package_event(
        self,
        package_name: str,
        version: str,
    ) -> TelemetryEvent:
        """Create a TelemetryEvent for new package installation.

        Args:
            package_name: Name of newly installed package.
            version: Installed version.

        Returns:
            TelemetryEvent for the installation.
        """
        raw_data: dict[str, Any] = {
            "event_type": "install",
            "package_name": package_name,
            "version": version,
        }

        return self._create_event(
            message=f"Package {package_name} installed: {version}",
            severity="info",
            category="pkg",
            entity=f"package:{package_name}",
            data=raw_data,
        )

    def _classify_upgrade(self, old_version: str, new_version: str) -> str:
        """Classify the type of version change.

        Args:
            old_version: Previous version string.
            new_version: New version string.

        Returns:
            One of: "major", "minor", "patch", "rebuild", "unknown"
        """
        # Extract major.minor.patch from version strings
        # Debian versions can be complex: epoch:upstream-debian
        old_parts = self._extract_version_parts(old_version)
        new_parts = self._extract_version_parts(new_version)

        if not old_parts or not new_parts:
            return "unknown"

        old_major, old_minor, old_patch = old_parts
        new_major, new_minor, new_patch = new_parts

        if new_major > old_major:
            return "major"
        elif new_minor > old_minor:
            return "minor"
        elif new_patch > old_patch:
            return "patch"
        else:
            return "rebuild"

    def _extract_version_parts(self, version: str) -> tuple[int, int, int] | None:
        """Extract major.minor.patch from a version string.

        Args:
            version: Debian version string.

        Returns:
            Tuple of (major, minor, patch) or None.
        """
        # Remove epoch if present (e.g., "1:2.3.4")
        if ":" in version:
            version = version.split(":", 1)[1]

        # Remove debian revision if present (e.g., "2.3.4-1ubuntu1")
        if "-" in version:
            version = version.split("-", 1)[0]

        # Match version numbers
        match = re.match(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", version)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2)) if match.group(2) else 0
            patch = int(match.group(3)) if match.group(3) else 0
            return (major, minor, patch)

        return None

    def get_current_version(self, package_name: str) -> str | None:
        """Get the currently tracked version of a package.

        Args:
            package_name: Package name.

        Returns:
            Version string or None.
        """
        return self._package_versions.get(package_name)

    def get_stats(self) -> dict[str, Any]:
        """Get probe statistics.

        Returns:
            Dict with probe stats.
        """
        return {
            "name": self.name,
            "interval": self.interval,
            "watched_packages": len(self._watched_packages),
            "tracked_packages": len(self._package_versions),
            "all_packages": len(self._all_packages),
            "detect_new_packages": self._detect_new_packages,
            "run_count": self._run_count,
            "error_count": self._error_count,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "initialized": self._initialized,
        }
