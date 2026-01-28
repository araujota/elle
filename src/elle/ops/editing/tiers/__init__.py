"""Tier implementations for the 6-Tier Deterministic Editing Stack.

Each tier provides a specific editing strategy:
    - Tier 0: Native override via drop-in files
    - Tier 1: Augeas grammar-based editing
    - Tier 2: Structured data (JSON/YAML/TOML)
    - Tier 3: Format editors (INI/XML)
    - Tier 4: Document-aware (Markdown/RST)
    - Tier 5: Text patching (last resort)
"""

from elle.ops.editing.tiers.base import BaseTier
from elle.ops.editing.tiers.tier0_dropin import Tier0DropIn
from elle.ops.editing.tiers.tier1_augeas import Tier1Augeas
from elle.ops.editing.tiers.tier2_structured import Tier2Structured
from elle.ops.editing.tiers.tier3_format import Tier3Format
from elle.ops.editing.tiers.tier4_document import Tier4Document
from elle.ops.editing.tiers.tier5_patch import Tier5Patch

# Tier class registry
TIER_CLASSES: dict[int, type[BaseTier]] = {
    0: Tier0DropIn,
    1: Tier1Augeas,
    2: Tier2Structured,
    3: Tier3Format,
    4: Tier4Document,
    5: Tier5Patch,
}


def get_tier_class(tier: int) -> type[BaseTier]:
    """Get the tier class for a tier number.

    Args:
        tier: Tier number (0-5).

    Returns:
        The tier class.

    Raises:
        KeyError: If tier number is invalid.
    """
    return TIER_CLASSES[tier]


def get_tier_instance(tier: int) -> BaseTier:
    """Get an instance of a tier.

    Args:
        tier: Tier number (0-5).

    Returns:
        An instance of the tier class.
    """
    return TIER_CLASSES[tier]()


__all__ = [
    "BaseTier",
    "Tier0DropIn",
    "Tier1Augeas",
    "Tier2Structured",
    "Tier3Format",
    "Tier4Document",
    "Tier5Patch",
    "TIER_CLASSES",
    "get_tier_class",
    "get_tier_instance",
]
