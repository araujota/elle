"""Dependency Checker - checks availability of dependencies.

Provides cached checking of dependencies via import, command, and file methods.
"""

from __future__ import annotations

import importlib
import logging
import shutil
import time
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from elle.capabilities.dependencies.models import DependencyCheckResult

if TYPE_CHECKING:
    from elle.capabilities.dependencies.models import DependencySpec
    from elle.capabilities.dependencies.registry import DependencyRegistry

logger = logging.getLogger(__name__)


class DependencyChecker:
    """Checks availability of dependencies.

    Provides cached checking via multiple methods:
    - Python import checking
    - Shell command existence (via shutil.which)
    - File/directory existence

    Example:
        checker = DependencyChecker(registry)

        result = checker.check("atspi")
        if not result.available:
            print(f"Missing: {result.install_command}")
    """

    def __init__(
        self,
        registry: "DependencyRegistry",
        cache_ttl: float = 300.0,
    ) -> None:
        """Initialize the checker.

        Args:
            registry: The dependency registry.
            cache_ttl: Cache time-to-live in seconds (default: 5 minutes).
        """
        self._registry = registry
        self._cache_ttl = cache_ttl
        # Cache: name -> (available, timestamp)
        self._cache: dict[str, tuple[bool, float]] = {}

    def check(self, name: str, *, use_cache: bool = True) -> DependencyCheckResult:
        """Check if a dependency is available.

        Args:
            name: The dependency name.
            use_cache: Whether to use cached results.

        Returns:
            DependencyCheckResult with availability status.
        """
        # Check cache first
        if use_cache and name in self._cache:
            available, timestamp = self._cache[name]
            if time.time() - timestamp < self._cache_ttl:
                logger.debug(f"Cache hit for dependency: {name}")
                return self._build_result(name, available)

        # Get dependency spec
        dep = self._registry.get(name)
        if dep is None:
            logger.warning(f"Unknown dependency: {name}")
            return DependencyCheckResult(
                dependency=name,
                available=False,
                check_method="none",
                error_message=f"Unknown dependency: {name}",
            )

        # Run checks
        available, check_method, error_message = self._run_checks(dep)

        # Update cache
        self._cache[name] = (available, time.time())

        # Build result
        return self._build_result(
            name,
            available,
            check_method=check_method,
            error_message=error_message,
        )

    def check_all(
        self,
        names: Iterable[str],
        *,
        use_cache: bool = True,
    ) -> list[DependencyCheckResult]:
        """Check multiple dependencies.

        Args:
            names: Dependency names to check.
            use_cache: Whether to use cached results.

        Returns:
            List of DependencyCheckResult for each dependency.
        """
        return [self.check(name, use_cache=use_cache) for name in names]

    def check_for_capability(
        self,
        capability: str,
        *,
        use_cache: bool = True,
    ) -> list[DependencyCheckResult]:
        """Check all dependencies for a capability.

        Args:
            capability: The capability name (e.g., 'gui.learn').
            use_cache: Whether to use cached results.

        Returns:
            List of DependencyCheckResult for missing dependencies.
        """
        deps = self._registry.get_for_capability(capability)
        results = []
        for dep in deps:
            result = self.check(dep.name, use_cache=use_cache)
            if not result.available:
                results.append(result)
        return results

    def invalidate_cache(self, name: str | None = None) -> None:
        """Clear cached check results.

        Args:
            name: Specific dependency to invalidate, or None for all.
        """
        if name is not None:
            self._cache.pop(name, None)
            logger.debug(f"Invalidated cache for: {name}")
        else:
            self._cache.clear()
            logger.debug("Invalidated all dependency cache")

    def _run_checks(
        self,
        dep: "DependencySpec",
    ) -> tuple[bool, str, str | None]:
        """Run availability checks for a dependency.

        Args:
            dep: The dependency to check.

        Returns:
            Tuple of (available, check_method, error_message).
        """
        # Try import check first (most reliable for Python deps)
        if dep.check_import:
            available, error = self._check_import(dep.check_import)
            if available:
                return True, "import", None
            # Continue to other checks - import might fail for non-Python deps

        # Try command check
        if dep.check_command:
            if self._check_command(dep.check_command):
                return True, "command", None

        # Try file check
        if dep.check_file:
            if self._check_file(dep.check_file):
                return True, "file", None

        # All checks failed
        error_parts = []
        if dep.check_import:
            error_parts.append(f"import '{dep.check_import}' failed")
        if dep.check_command:
            error_parts.append(f"command '{dep.check_command}' not found")
        if dep.check_file:
            error_parts.append(f"file '{dep.check_file}' not found")

        return False, "failed", "; ".join(error_parts) if error_parts else "No checks defined"

    def _check_import(self, module: str) -> tuple[bool, str | None]:
        """Check if a Python module can be imported.

        Args:
            module: Module path (e.g., 'gi.repository.Atspi').

        Returns:
            Tuple of (success, error_message).
        """
        try:
            # Handle nested modules like gi.repository.Atspi
            parts = module.split(".")
            if len(parts) == 1:
                importlib.import_module(module)
            else:
                # Import the top-level and then get nested attributes
                top = importlib.import_module(parts[0])
                obj = top
                for part in parts[1:]:
                    if hasattr(obj, part):
                        obj = getattr(obj, part)
                    else:
                        # Try importing as submodule
                        obj = importlib.import_module(".".join(parts[: parts.index(part) + 1]))
            return True, None
        except ImportError as e:
            return False, str(e)
        except Exception as e:
            return False, str(e)

    def _check_command(self, command: str) -> bool:
        """Check if a shell command exists.

        Args:
            command: Command name (e.g., 'augtool').

        Returns:
            True if command exists in PATH.
        """
        return shutil.which(command) is not None

    def _check_file(self, path: str) -> bool:
        """Check if a file or directory exists.

        Args:
            path: Absolute path to check.

        Returns:
            True if path exists.
        """
        return Path(path).exists()

    def _build_result(
        self,
        name: str,
        available: bool,
        check_method: str = "cached",
        error_message: str | None = None,
    ) -> DependencyCheckResult:
        """Build a DependencyCheckResult.

        Args:
            name: Dependency name.
            available: Whether available.
            check_method: How availability was determined.
            error_message: Error if unavailable.

        Returns:
            DependencyCheckResult instance.
        """
        install_command = None
        if not available:
            dep = self._registry.get(name)
            if dep and dep.apt_packages:
                packages = " ".join(dep.apt_packages)
                install_command = f"sudo apt install {packages}"

        return DependencyCheckResult(
            dependency=name,
            available=available,
            check_method=check_method,
            error_message=error_message,
            install_command=install_command,
        )


# =============================================================================
# Module-level convenience functions
# =============================================================================

_checker: DependencyChecker | None = None


def get_checker() -> DependencyChecker:
    """Get the global DependencyChecker singleton.

    Returns:
        The global DependencyChecker instance.
    """
    global _checker
    if _checker is None:
        from elle.capabilities.dependencies.registry import get_registry

        _checker = DependencyChecker(get_registry())
    return _checker


def reset_checker() -> None:
    """Reset the global checker.

    Useful for testing.
    """
    global _checker
    _checker = None


def check_dependency(name: str) -> DependencyCheckResult:
    """Check if a dependency is available.

    Convenience function using the global checker.

    Args:
        name: The dependency name.

    Returns:
        DependencyCheckResult.
    """
    return get_checker().check(name)


def check_dependencies_for_capability(capability: str) -> list[DependencyCheckResult]:
    """Check dependencies for a capability.

    Convenience function using the global checker.

    Args:
        capability: The capability name.

    Returns:
        List of missing DependencyCheckResult.
    """
    return get_checker().check_for_capability(capability)
