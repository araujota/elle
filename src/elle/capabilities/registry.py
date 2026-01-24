"""Capability Registry for discovery and resolution.

Provides a central registry for capabilities, enabling:
- Registration of capability implementations
- Lookup by name
- Discovery by domain
- Search by keyword
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from elle.capabilities.exceptions import (
    CapabilityNotFoundError,
    CapabilityRegistrationError,
)
from elle.capabilities.models import CapabilityDomain, CapabilitySpec

if TYPE_CHECKING:
    from elle.capabilities.protocol import Capability

logger = logging.getLogger(__name__)


class CapabilityRegistry:
    """Central registry for capability discovery and resolution.

    Maintains a mapping of capability names to implementations,
    enabling runtime discovery and instantiation.

    Usage:
        registry = CapabilityRegistry()

        # Register a capability
        registry.register(ServiceRestartCapability)

        # Get capability by name
        cap = registry.get("service.restart")
        if cap:
            result = cap.run(input)

        # List capabilities in a domain
        service_caps = registry.list_by_domain("service")
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._capabilities: dict[str, type[Capability]] = {}
        self._by_domain: dict[CapabilityDomain, list[str]] = {}
        self._instances: dict[str, Capability] = {}  # Cached instances

    def register(self, capability_class: type[Capability]) -> None:
        """Register a capability implementation.

        Args:
            capability_class: The capability class to register.

        Raises:
            CapabilityRegistrationError: If registration fails.
        """
        try:
            # Create temporary instance to get spec
            instance = capability_class()
            spec = instance.spec
        except Exception as e:
            raise CapabilityRegistrationError(
                f"Failed to instantiate capability for registration: {e}"
            ) from e

        name = spec.name

        if name in self._capabilities:
            logger.warning(f"Overwriting existing capability: {name}")

        self._capabilities[name] = capability_class
        self._by_domain.setdefault(spec.domain, []).append(name)

        logger.debug(f"Registered capability: {name} (domain={spec.domain})")

    def unregister(self, name: str) -> bool:
        """Unregister a capability by name.

        Args:
            name: The capability name to unregister.

        Returns:
            True if capability was unregistered, False if not found.
        """
        if name not in self._capabilities:
            return False

        # Get spec to find domain
        cap_class = self._capabilities[name]
        try:
            spec = cap_class().spec
            if spec.domain in self._by_domain:
                if name in self._by_domain[spec.domain]:
                    self._by_domain[spec.domain].remove(name)
        except Exception:
            pass

        del self._capabilities[name]

        # Clear cached instance
        if name in self._instances:
            del self._instances[name]

        logger.debug(f"Unregistered capability: {name}")
        return True

    def get(self, name: str) -> Capability | None:
        """Get capability by name.

        Returns a cached instance if available, otherwise creates one.

        Args:
            name: The capability name (e.g., 'service.restart').

        Returns:
            Capability instance or None if not found.
        """
        if name not in self._capabilities:
            return None

        # Return cached instance
        if name in self._instances:
            return self._instances[name]

        # Create and cache instance
        cap_class = self._capabilities[name]
        try:
            instance = cap_class()
            self._instances[name] = instance
            return instance
        except Exception as e:
            logger.error(f"Failed to instantiate capability {name}: {e}")
            return None

    def get_or_raise(self, name: str) -> Capability:
        """Get capability by name, raising if not found.

        Args:
            name: The capability name.

        Returns:
            Capability instance.

        Raises:
            CapabilityNotFoundError: If capability not found.
        """
        cap = self.get(name)
        if cap is None:
            raise CapabilityNotFoundError(name)
        return cap

    def get_spec(self, name: str) -> CapabilitySpec | None:
        """Get capability spec without instantiating.

        Args:
            name: The capability name.

        Returns:
            CapabilitySpec or None if not found.
        """
        cap = self.get(name)
        if cap:
            return cap.spec
        return None

    def list_all(self) -> list[CapabilitySpec]:
        """List all registered capabilities.

        Returns:
            List of CapabilitySpec for all registered capabilities.
        """
        specs = []
        for name in self._capabilities:
            cap = self.get(name)
            if cap:
                specs.append(cap.spec)
        return specs

    def list_by_domain(self, domain: CapabilityDomain) -> list[CapabilitySpec]:
        """List all capabilities in a domain.

        Args:
            domain: The domain to filter by.

        Returns:
            List of CapabilitySpec in the domain.
        """
        names = self._by_domain.get(domain, [])
        specs = []
        for name in names:
            cap = self.get(name)
            if cap:
                specs.append(cap.spec)
        return specs

    def list_names(self) -> list[str]:
        """List all registered capability names.

        Returns:
            List of capability names.
        """
        return list(self._capabilities.keys())

    def list_domains(self) -> list[CapabilityDomain]:
        """List all domains that have capabilities.

        Returns:
            List of domains with at least one capability.
        """
        return [d for d, names in self._by_domain.items() if names]

    def search(self, query: str) -> list[CapabilitySpec]:
        """Search capabilities by name/summary.

        Args:
            query: Search query (case-insensitive).

        Returns:
            List of matching CapabilitySpec.
        """
        query_lower = query.lower()
        matches = []

        for name in self._capabilities:
            cap = self.get(name)
            if cap:
                spec = cap.spec
                if (
                    query_lower in spec.name.lower()
                    or query_lower in spec.summary.lower()
                ):
                    matches.append(spec)

        return matches

    def is_registered(self, name: str) -> bool:
        """Check if a capability is registered.

        Args:
            name: The capability name.

        Returns:
            True if registered.
        """
        return name in self._capabilities

    def count(self) -> int:
        """Get total number of registered capabilities.

        Returns:
            Number of registered capabilities.
        """
        return len(self._capabilities)

    def clear(self) -> None:
        """Clear all registered capabilities.

        Useful for testing.
        """
        self._capabilities.clear()
        self._by_domain.clear()
        self._instances.clear()


# =============================================================================
# Singleton Instance
# =============================================================================

_registry: CapabilityRegistry | None = None


def get_registry() -> CapabilityRegistry:
    """Get the shared registry instance.

    Returns:
        The global CapabilityRegistry singleton.
    """
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
        _load_core_capabilities(_registry)
    return _registry


def reset_registry() -> None:
    """Reset the global registry instance.

    Useful for testing.
    """
    global _registry
    _registry = None


def _load_core_capabilities(registry: CapabilityRegistry) -> None:
    """Load core capabilities into the registry.

    Args:
        registry: The registry to populate.
    """
    try:
        from elle.capabilities.core import get_core_capabilities

        for cap_class in get_core_capabilities():
            try:
                registry.register(cap_class)
            except Exception as e:
                logger.warning(f"Failed to register core capability: {e}")

    except ImportError:
        logger.debug("Core capabilities not available")
    except Exception as e:
        logger.warning(f"Failed to load core capabilities: {e}")
