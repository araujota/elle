"""6-Tier Deterministic Editing Stack.

A hierarchical file editing system that ensures the safest, most structured
editing method is always used for any given file type.

Design Principles:
    - Deterministic Selection: Given (file_path, operation), always select same tier
    - Highest-First Ordering: Must try Tier 0 before Tier 1 before Tier 2, etc.
    - Mandatory Audit Trail: Every tier skip recorded with justification
    - Graceful Degradation: Fall back to lower tiers when tools unavailable

Tier Hierarchy:
    Tier 0 - Native Override: systemd drop-ins, *.d/ directories
    Tier 1 - Augeas: Grammar-based structured editing
    Tier 2 - Structured Data: jq, yq, Python TOML for JSON/YAML/TOML
    Tier 3 - Format Editors: crudini, xmlstarlet for INI/XML
    Tier 4 - Document-aware: Markdown/RST parsers
    Tier 5 - Text Patching: Anchor-based last resort (requires justification)

Public API:
    Core:
        - TierSelector: Deterministic tier selection
        - select_tier(): Convenience function for tier selection

    Models:
        - EditOperation: Universal edit operation specification
        - FileEditRequest: Request to edit a file
        - FileEditPreview: Preview of proposed changes
        - TierEditResult: Result of an edit operation
        - TierSelection: Result of tier selection with audit trail
        - TierEscalation: Record of why a tier was skipped
        - TierApplicability: Whether a tier can handle an edit
        - TierInfo: Information about a tier
        - EditingStackStatus: Status of all tiers

    Registry:
        - TIER_REGISTRY: Mapping of tier numbers to TierInfo
        - detect_tier_from_path(): Quick heuristic tier detection
        - is_dropin_candidate(): Check if file supports drop-ins
        - get_tier_info(): Get info about a specific tier
        - list_all_tiers(): List all tier information

    Exceptions:
        - EditingStackError: Base exception
        - TierUnavailableError: Required tier not available
        - NoApplicableTierError: No tier can handle the edit
        - Tier5RequiresJustificationError: Tier 5 needs justification

Usage:
    from elle.ops.editing import TierSelector, EditOperation

    selector = TierSelector()

    # Select tier for an edit
    selection = selector.select_tier(
        "/etc/nginx/nginx.conf",
        EditOperation(kind="set", path="/http/server/listen", value="8080"),
    )
    print(f"Selected Tier {selection.tier}: {selection.tier_name}")
    print(f"Tool: {selection.tool_used}")
    print(f"Reason: {selection.reason}")

    # See why other tiers were skipped
    for escalation in selection.escalation_trail:
        print(f"Skipped Tier {escalation.tier}: {escalation.reason}")

    # Get applicability of all tiers
    all_tiers = selector.get_all_tier_applicability(
        "/etc/docker/daemon.json",
        EditOperation(kind="set", path=".storage-driver", value="overlay2"),
    )
    for tier, applicability in all_tiers.items():
        status = "YES" if applicability.applicable else "NO"
        print(f"Tier {tier}: {status} - {applicability.reason}")

Integration with Capabilities:
    The editing stack is used by the `file.edit` capability:

        from elle.capabilities import execute_capability

        result = await execute_capability(
            "file.edit",
            FileEditInput(
                file_path="/etc/docker/daemon.json",
                operation=EditOperation(kind="set", path=".log-driver", value="json-file"),
                description="Set Docker log driver",
            ),
        )
"""

# Core selector
from elle.ops.editing.tier_selector import TierSelector

# Models
from elle.ops.editing.models import (
    TIER_NAMES,
    EditingStackError,
    EditingStackStatus,
    EditOperation,
    FileEditPreview,
    FileEditRequest,
    NoApplicableTierError,
    Tier5RequiresJustificationError,
    TierApplicability,
    TierEditResult,
    TierEscalation,
    TierInfo,
    TierNumber,
    TierSelection,
    TierUnavailableError,
    ValidationResult,
)

# Registry
from elle.ops.editing.tier_registry import (
    TIER_REGISTRY,
    detect_tier_from_path,
    get_tier_info,
    is_dropin_candidate,
    list_all_tiers,
    matches_pattern,
)


def select_tier(
    file_path: str,
    operation: EditOperation,
    *,
    max_tier: TierNumber = 5,
    preferred_tier: TierNumber | None = None,
) -> TierSelection:
    """Convenience function for tier selection.

    Args:
        file_path: Path to the file to edit.
        operation: The edit operation to perform.
        max_tier: Maximum tier to consider (default 5).
        preferred_tier: If set, validates and uses this tier if applicable.

    Returns:
        TierSelection with the selected tier and audit trail.

    Raises:
        NoApplicableTierError: If no tier can handle the edit.
    """
    selector = TierSelector()
    return selector.select_tier(
        file_path,
        operation,
        max_tier=max_tier,
        preferred_tier=preferred_tier,
    )


__all__ = [
    # Core
    "TierSelector",
    "select_tier",
    # Models
    "EditOperation",
    "FileEditRequest",
    "FileEditPreview",
    "TierEditResult",
    "TierSelection",
    "TierEscalation",
    "TierApplicability",
    "TierInfo",
    "TierNumber",
    "TIER_NAMES",
    "ValidationResult",
    "EditingStackStatus",
    # Registry
    "TIER_REGISTRY",
    "detect_tier_from_path",
    "is_dropin_candidate",
    "get_tier_info",
    "list_all_tiers",
    "matches_pattern",
    # Exceptions
    "EditingStackError",
    "TierUnavailableError",
    "NoApplicableTierError",
    "Tier5RequiresJustificationError",
]
