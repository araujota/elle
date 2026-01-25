"""Dependency Installer - secure installation of dependencies.

Provides safe, auditable package installation with strict allowlist enforcement.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from typing import TYPE_CHECKING

from elle.capabilities.dependencies.core import ALLOWED_PACKAGES
from elle.capabilities.dependencies.models import (
    InstallationRequest,
    InstallationResult,
)

if TYPE_CHECKING:
    from elle.capabilities.dependencies.checker import DependencyChecker
    from elle.capabilities.dependencies.registry import DependencyRegistry
    from elle.capabilities.dependencies.store import DependencyPreferenceStore

logger = logging.getLogger(__name__)


class DependencySecurityError(Exception):
    """Raised when an installation request violates security constraints."""

    def __init__(self, message: str, packages: tuple[str, ...] = ()) -> None:
        self.packages = packages
        super().__init__(message)


class DependencyInstaller:
    """Securely installs dependencies.

    Key security features:
    - Only packages in ALLOWED_PACKAGES can be installed
    - Package names are passed as explicit arguments (no shell injection)
    - All installations are logged for audit

    Example:
        installer = DependencyInstaller(registry, checker, store)

        request = InstallationRequest(
            dependency="atspi",
            apt_packages=("python3-pyatspi", "at-spi2-core"),
            reason="Required for GUI automation",
        )

        result = await installer.install(request)
        if result.success:
            print("Installed!")
    """

    def __init__(
        self,
        registry: "DependencyRegistry",
        checker: "DependencyChecker",
        store: "DependencyPreferenceStore",
    ) -> None:
        """Initialize the installer.

        Args:
            registry: The dependency registry.
            checker: The dependency checker.
            store: The preference store for recording history.
        """
        self._registry = registry
        self._checker = checker
        self._store = store

    async def install(self, request: InstallationRequest) -> InstallationResult:
        """Install a dependency.

        Security:
        - Only packages in ALLOWED_PACKAGES can be installed
        - Uses subprocess with explicit package names
        - Records installation to audit log

        Args:
            request: The installation request.

        Returns:
            InstallationResult with outcome.

        Raises:
            DependencySecurityError: If any package is not in the allowlist.
        """
        start_time = time.time()

        # Security check: Validate all packages against allowlist
        self._validate_packages(request.apt_packages)
        if request.pip_packages:
            # For now, we don't support pip packages
            logger.warning("Pip packages not yet supported, ignoring")

        logger.info(
            f"Installing dependency '{request.dependency}': {request.apt_packages}"
        )

        # Run apt install
        success, error_message = await self._run_apt_install(request.apt_packages)

        duration = time.time() - start_time

        # Build result
        result = InstallationResult(
            success=success,
            dependency=request.dependency,
            installed_packages=request.apt_packages if success else (),
            error_message=error_message,
            duration_sec=duration,
        )

        # Record to history
        self._store.record_installation(result)

        # Invalidate checker cache
        self._checker.invalidate_cache(request.dependency)

        return result

    def _validate_packages(self, packages: tuple[str, ...]) -> None:
        """Validate that all packages are in the allowlist.

        Args:
            packages: Packages to validate.

        Raises:
            DependencySecurityError: If any package is not allowed.
        """
        disallowed = [p for p in packages if p not in ALLOWED_PACKAGES]
        if disallowed:
            raise DependencySecurityError(
                f"Package(s) not in allowlist: {', '.join(disallowed)}. "
                f"Only packages in ALLOWED_PACKAGES can be installed.",
                packages=tuple(disallowed),
            )

    async def _run_apt_install(
        self,
        packages: tuple[str, ...],
    ) -> tuple[bool, str | None]:
        """Run apt install with the specified packages.

        Uses pkexec for privilege escalation if needed.

        Args:
            packages: Packages to install.

        Returns:
            Tuple of (success, error_message).
        """
        # Build command - packages are passed as explicit arguments
        # We use apt-get for non-interactive installs
        cmd = [
            "pkexec",
            "apt-get",
            "install",
            "-y",
            "--no-install-recommends",
            *packages,
        ]

        logger.debug(f"Running: {' '.join(cmd)}")

        try:
            # Run in executor to not block event loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5 minute timeout
                ),
            )

            if result.returncode == 0:
                logger.info(f"Successfully installed: {', '.join(packages)}")
                return True, None
            else:
                error = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                logger.error(f"Installation failed (code {result.returncode}): {error}")
                return False, error

        except subprocess.TimeoutExpired:
            error = "Installation timed out after 5 minutes"
            logger.error(error)
            return False, error

        except Exception as e:
            error = str(e)
            logger.exception(f"Installation error: {error}")
            return False, error

    def can_install(self, packages: tuple[str, ...]) -> bool:
        """Check if packages can be installed (are in allowlist).

        Args:
            packages: Packages to check.

        Returns:
            True if all packages are allowed.
        """
        return all(p in ALLOWED_PACKAGES for p in packages)

    def get_disallowed_packages(self, packages: tuple[str, ...]) -> tuple[str, ...]:
        """Get packages that are not in the allowlist.

        Args:
            packages: Packages to check.

        Returns:
            Tuple of disallowed package names.
        """
        return tuple(p for p in packages if p not in ALLOWED_PACKAGES)


# =============================================================================
# Module-level convenience functions
# =============================================================================

_installer: DependencyInstaller | None = None


def get_installer() -> DependencyInstaller:
    """Get the global DependencyInstaller singleton.

    Returns:
        The global DependencyInstaller instance.
    """
    global _installer
    if _installer is None:
        from elle.capabilities.dependencies.checker import get_checker
        from elle.capabilities.dependencies.registry import get_registry
        from elle.capabilities.dependencies.store import get_store

        _installer = DependencyInstaller(
            get_registry(),
            get_checker(),
            get_store(),
        )
    return _installer


def reset_installer() -> None:
    """Reset the global installer.

    Useful for testing.
    """
    global _installer
    _installer = None


async def install_dependency(request: InstallationRequest) -> InstallationResult:
    """Install a dependency.

    Convenience function using the global installer.

    Args:
        request: The installation request.

    Returns:
        InstallationResult.
    """
    return await get_installer().install(request)
