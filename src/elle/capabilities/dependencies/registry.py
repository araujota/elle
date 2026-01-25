"""Dependency Registry - central registry of all known dependencies.

Provides lookup of dependencies by name and by capability.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from elle.capabilities.dependencies.models import DependencySpec

logger = logging.getLogger(__name__)


class DependencyRegistry:
    """Central registry of all known dependencies.

    Provides fast lookup of dependencies by name or by the capabilities
    that require them.

    Example:
        registry = DependencyRegistry()
        registry.register(ATSPI_DEPENDENCY)

        # Lookup by name
        dep = registry.get("atspi")

        # Lookup by capability
        deps = registry.get_for_capability("gui.learn")
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._deps: dict[str, DependencySpec] = {}
        self._by_capability: dict[str, list[str]] = defaultdict(list)

    def register(self, dep: DependencySpec) -> None:
        """Register a dependency specification.

        Args:
            dep: The dependency to register.

        Raises:
            ValueError: If a dependency with the same name is already registered.
        """
        if dep.name in self._deps:
            raise ValueError(f"Dependency '{dep.name}' is already registered")

        self._deps[dep.name] = dep

        # Index by capability
        for capability in dep.required_by:
            self._by_capability[capability].append(dep.name)

        logger.debug(f"Registered dependency: {dep.name}")

    def get(self, name: str) -> DependencySpec | None:
        """Get a dependency by name.

        Args:
            name: The dependency name (e.g., 'atspi').

        Returns:
            The DependencySpec if found, None otherwise.
        """
        return self._deps.get(name)

    def get_for_capability(self, capability: str) -> list[DependencySpec]:
        """Get all dependencies required by a capability.

        Args:
            capability: The capability name (e.g., 'gui.learn').

        Returns:
            List of dependencies required by the capability.
        """
        dep_names = self._by_capability.get(capability, [])
        return [self._deps[name] for name in dep_names if name in self._deps]

    def list_all(self) -> list[DependencySpec]:
        """List all registered dependencies.

        Returns:
            List of all DependencySpec objects.
        """
        return list(self._deps.values())

    def list_names(self) -> list[str]:
        """List all registered dependency names.

        Returns:
            List of dependency names.
        """
        return list(self._deps.keys())

    def has(self, name: str) -> bool:
        """Check if a dependency is registered.

        Args:
            name: The dependency name.

        Returns:
            True if registered, False otherwise.
        """
        return name in self._deps

    def __len__(self) -> int:
        """Return the number of registered dependencies."""
        return len(self._deps)


# =============================================================================
# Global Registry
# =============================================================================

_registry: DependencyRegistry | None = None


def _load_core_dependencies(registry: DependencyRegistry) -> None:
    """Load core dependencies into the registry.

    Args:
        registry: The registry to populate.
    """
    from elle.capabilities.dependencies.core import CORE_DEPENDENCIES

    for dep in CORE_DEPENDENCIES:
        try:
            registry.register(dep)
        except ValueError:
            # Already registered
            pass


def get_registry() -> DependencyRegistry:
    """Get the global dependency registry singleton.

    Initializes the registry with core dependencies on first access.

    Returns:
        The global DependencyRegistry instance.
    """
    global _registry
    if _registry is None:
        _registry = DependencyRegistry()
        _load_core_dependencies(_registry)
    return _registry


def reset_registry() -> None:
    """Reset the global registry.

    Useful for testing.
    """
    global _registry
    _registry = None
