"""Capability Registry for discovery and resolution.

Provides a central registry for capabilities, enabling:
- Registration of capability implementations
- Lookup by name
- Discovery by domain
- Search by keyword
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

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

    Example::

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
        self._capabilities: dict[str, type[Capability[Any, Any]]] = {}
        self._by_domain: dict[CapabilityDomain, list[str]] = {}
        self._instances: dict[str, Capability[Any, Any]] = {}  # Cached instances

    def register(self, capability_class: type[Capability[Any, Any]]) -> None:
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
            raise CapabilityRegistrationError(f"Failed to instantiate capability for registration: {e}") from e

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
            if spec.domain in self._by_domain and name in self._by_domain[spec.domain]:
                self._by_domain[spec.domain].remove(name)
        except Exception:
            pass

        del self._capabilities[name]

        # Clear cached instance
        if name in self._instances:
            del self._instances[name]

        logger.debug(f"Unregistered capability: {name}")
        return True

    def get(self, name: str) -> Capability[Any, Any] | None:
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

    def get_or_raise(self, name: str) -> Capability[Any, Any]:
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
                if query_lower in spec.name.lower() or query_lower in spec.summary.lower():
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
_registry_lock = threading.Lock()


def get_registry() -> CapabilityRegistry:
    """Get the shared registry instance.

    Returns:
        The global CapabilityRegistry singleton.
    """
    global _registry
    if _registry is not None:
        return _registry
    with _registry_lock:
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

    Strategy:
    1. Sync core capabilities if source files changed (auto-versioning)
    2. Load from autogen.db (primary source)
    3. Fallback to Python files for any not yet migrated

    Args:
        registry: The registry to populate.
    """
    # First, sync any changed core modules to autogen.db
    # This ensures stored capabilities match current Python source
    _sync_core_if_needed()

    # Load from stored capabilities (preferred)
    stored_count = _load_stored_core_capabilities(registry)

    # Fallback to Python files for any not yet migrated
    python_count = _load_python_core_capabilities(registry)

    if stored_count > 0 or python_count > 0:
        logger.info(f"Loaded core capabilities: {stored_count} from DB, {python_count} from Python")


def _sync_core_if_needed() -> None:
    """Sync core capabilities if source files have changed.

    This enables automatic versioning - when ELLE is upgraded and
    capability source files change, the stored versions are updated.
    """
    try:
        from elle.capabilities.autogen.core_migrator import sync_core_capabilities_if_needed

        result = sync_core_capabilities_if_needed()
        if result.get("updated_count", 0) > 0:
            logger.info(f"Updated {result['updated_count']} core capabilities from changed source files")
        if result.get("migrated_count", 0) > 0:
            logger.info(f"Migrated {result['migrated_count']} new core capabilities")
    except Exception as e:
        logger.debug(f"Could not sync core capabilities: {e}")


def _load_stored_core_capabilities(registry: CapabilityRegistry) -> int:
    """Load core capabilities from autogen.db.

    Args:
        registry: The registry to populate.

    Returns:
        Number of capabilities loaded.
    """
    try:
        from elle.capabilities.autogen.loader import load_capability_from_stored
        from elle.capabilities.autogen.store import get_store

        store = get_store()
        core_caps = store.list_core()

        count = 0
        for stored in core_caps:
            if not stored.approved or not stored.enabled:
                continue

            # Skip if already registered (e.g., from Python files in tests)
            if registry.is_registered(stored.capability_name):
                continue

            try:
                cap_class = load_capability_from_stored(stored)
                if cap_class:
                    registry.register(cap_class)
                    count += 1
            except Exception as e:
                logger.warning(f"Failed to load stored core capability {stored.capability_name}: {e}")

        return count

    except Exception as e:
        logger.debug(f"No stored core capabilities available: {e}")
        return 0


def _load_python_core_capabilities(registry: CapabilityRegistry) -> int:
    """Load core capabilities from Python files.

    Only loads capabilities not already in the registry (i.e., not migrated).

    Args:
        registry: The registry to populate.

    Returns:
        Number of capabilities loaded.
    """
    try:
        from elle.capabilities.core import get_core_capabilities

        count = 0
        for cap_class in get_core_capabilities():
            try:
                instance = cap_class()
                name = instance.spec.name

                # Skip if already loaded from DB
                if registry.is_registered(name):
                    continue

                registry.register(cap_class)
                count += 1
            except Exception as e:
                logger.warning(f"Failed to register core capability: {e}")

        return count

    except ImportError:
        logger.debug("Core capabilities Python module not available")
        return 0
    except Exception as e:
        logger.warning(f"Failed to load core capabilities from Python: {e}")
        return 0
