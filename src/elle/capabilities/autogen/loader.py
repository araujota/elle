"""Lazy loader for auto-generated and core capabilities.

Loads approved capabilities from the store at daemon startup and
registers them with the capability registry.

Supports two types of stored capabilities:
1. Auto-generated: Simple shell-command-based, compiled via factory
2. Core: Full Python implementations, compiled directly from stored code
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from elle.capabilities.autogen.factory import compile_capability_class
from elle.capabilities.autogen.models import (
    GeneratedCapabilitySpec,
    StoredCapability,
    TrustLevel,
)
from elle.capabilities.autogen.store import get_store

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# Capability Loading
# =============================================================================


def load_capability_from_stored(stored: StoredCapability) -> type | None:
    """Load a capability class from a stored capability.

    Supports two types:
    1. Core capabilities: Full Python code with complex logic
    2. Auto-generated: Simple shell-command-based capabilities

    Args:
        stored: StoredCapability from database.

    Returns:
        Compiled capability class or None.
    """
    try:
        # Core capabilities have full Python implementations
        if stored.trust_level == TrustLevel.CORE:
            cap_class = _compile_core_capability(stored)
        else:
            # Auto-generated capabilities use the factory
            spec = GeneratedCapabilitySpec.model_validate_json(stored.spec_json)
            cap_class = compile_capability_class(
                spec,
                stored.input_model_code,
                stored.output_model_code,
                stored.capability_class_code,
            )

        if cap_class:
            # Add metadata to class (dynamic attributes)
            cap_class._autogen_id = stored.id  # type: ignore[attr-defined]
            cap_class._autogen_name = stored.capability_name  # type: ignore[attr-defined]
            cap_class._autogen_trust = stored.trust_level  # type: ignore[attr-defined]

        return cap_class

    except Exception as e:
        logger.error(f"Failed to load capability {stored.capability_name}: {e}")
        return None


def _compile_core_capability(stored: StoredCapability) -> type | None:
    """Compile a core capability from stored Python code.

    Core capabilities have full Python implementations including:
    - Helper functions
    - Custom input/output models
    - Complex run(), verify(), rollback() logic

    Args:
        stored: StoredCapability with trust_level='core'.

    Returns:
        Compiled capability class or None.
    """
    try:
        # Derive class name from capability name
        # e.g., "service.restart" -> "ServiceRestartCapability"
        parts = stored.capability_name.split(".")
        class_name = "".join(p.title() for p in parts) + "Capability"

        # Build execution namespace with necessary imports
        namespace: dict[str, Any] = {}

        # Execute the capability class code which includes:
        # - Imports
        # - Helper functions
        # - The capability class itself
        exec(stored.capability_class_code, namespace)

        cap_class = namespace.get(class_name)
        if not cap_class:
            # Try alternative naming patterns
            # Some classes might have different naming
            for name, obj in namespace.items():
                if name.endswith("Capability") and isinstance(obj, type):
                    cap_class = obj
                    break

        if not cap_class:
            logger.error(f"Could not find capability class in {stored.capability_name}")
            return None

        return cap_class

    except Exception as e:
        logger.error(f"Failed to compile core capability {stored.capability_name}: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return None


def load_all_approved(registry: Any = None) -> int:
    """Load all approved and enabled capabilities.

    Args:
        registry: Optional CapabilityRegistry to register with.

    Returns:
        Number of capabilities loaded.
    """
    store = get_store()
    stored_caps = store.list_approved_enabled()
    loaded = 0

    for stored in stored_caps:
        cap_class = load_capability_from_stored(stored)
        if cap_class:
            if registry:
                try:
                    registry.register(cap_class)
                    loaded += 1
                    logger.debug(f"Registered autogen capability: {stored.capability_name}")
                except Exception as e:
                    logger.warning(f"Failed to register {stored.capability_name}: {e}")
            else:
                loaded += 1

    logger.info(f"Loaded {loaded} auto-generated capabilities")
    return loaded


def load_capability_by_name(name: str) -> type | None:
    """Load a specific capability by name.

    Args:
        name: Capability name.

    Returns:
        Capability class or None.
    """
    store = get_store()
    stored = store.get(name)

    if not stored:
        return None

    if not stored.approved or not stored.enabled:
        logger.warning(f"Capability {name} is not approved/enabled")
        return None

    return load_capability_from_stored(stored)


# =============================================================================
# Registry Integration
# =============================================================================


def register_autogen_capabilities(registry: Any) -> int:
    """Register all auto-generated capabilities with the registry.

    This should be called during daemon startup after core capabilities
    are registered.

    Args:
        registry: CapabilityRegistry instance.

    Returns:
        Number of capabilities registered.
    """
    return load_all_approved(registry)


def get_loaded_autogen_capabilities() -> list[dict[str, Any]]:
    """Get information about loaded auto-generated capabilities.

    Returns:
        List of capability info dicts.
    """
    store = get_store()
    capabilities = []

    for stored in store.list_approved_enabled():
        try:
            spec = GeneratedCapabilitySpec.model_validate_json(stored.spec_json)
            capabilities.append(
                {
                    "id": stored.id,
                    "name": stored.capability_name,
                    "source_command": stored.source_command,
                    "domain": spec.domain,
                    "risk_level": spec.risk_level,
                    "trust_level": stored.trust_level.value,
                    "generated_at": stored.generated_at.isoformat(),
                }
            )
        except Exception:
            pass

    return capabilities


# =============================================================================
# Capability Instance Creation
# =============================================================================


def create_capability_instance(name: str) -> Any | None:
    """Create an instance of an auto-generated capability.

    Args:
        name: Capability name.

    Returns:
        Capability instance or None.
    """
    cap_class = load_capability_by_name(name)
    if cap_class:
        return cap_class()
    return None


def execute_autogen_capability(
    name: str,
    input_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Execute an auto-generated capability.

    Args:
        name: Capability name.
        input_data: Input data dict.

    Returns:
        Result dict or None.
    """
    instance = create_capability_instance(name)
    if not instance:
        return None

    try:
        result = instance.run(input_data)
        return dict(result) if result is not None else None
    except Exception as e:
        logger.error(f"Execution failed for {name}: {e}")
        return {"success": False, "output": str(e), "return_code": -1}
