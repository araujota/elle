"""Deterministic tier selection algorithm for the 6-Tier Editing Stack.

Implements the highest-first ordering principle: always try Tier 0
before Tier 1 before Tier 2, etc., with mandatory audit trail for
every tier skip.
"""

from __future__ import annotations

import logging
from pathlib import Path

from elle.ops.editing.models import (
    EditOperation,
    NoApplicableTierError,
    TierApplicability,
    TierEscalation,
    TierNumber,
    TierSelection,
)
from elle.ops.editing.tier_registry import (
    TIER1_AUGEAS_PATTERNS,
    TIER2_JSON_PATTERNS,
    TIER2_TOML_PATTERNS,
    TIER2_YAML_PATTERNS,
    TIER3_INI_PATTERNS,
    TIER3_XML_PATTERNS,
    TIER4_MARKDOWN_PATTERNS,
    TIER4_RST_PATTERNS,
    TIER_REGISTRY,
    is_dropin_candidate,
    matches_pattern,
)

logger = logging.getLogger(__name__)


class TierSelector:
    """Deterministic tier selection for file editing.

    Implements the core principle: given (file_path, operation),
    always select the same tier. Tries Tier 0 first, then 1, 2, etc.

    Usage:
        selector = TierSelector()
        selection = selector.select_tier("/etc/nginx/nginx.conf", EditOperation(kind="set", ...))
        print(f"Selected Tier {selection.tier}: {selection.tier_name}")
        print(f"Reason: {selection.reason}")
        for skip in selection.escalation_trail:
            print(f"Skipped Tier {skip.tier}: {skip.reason}")
    """

    def __init__(self) -> None:
        """Initialize the tier selector with tool availability cache."""
        self._tool_availability: dict[str, bool] = {}

    def _check_tool_available(self, tool: str) -> bool:
        """Check if a tool is available (cached).

        Args:
            tool: Tool name to check.

        Returns:
            True if the tool is available.
        """
        if tool not in self._tool_availability:
            if tool == "augeas":
                try:
                    from elle.ops.augeas import is_available

                    self._tool_availability[tool] = is_available()
                except ImportError:
                    self._tool_availability[tool] = False
            elif tool == "jq":
                try:
                    from elle.ops.jq import is_available

                    self._tool_availability[tool] = is_available()
                except ImportError:
                    self._tool_availability[tool] = False
            elif tool == "yq":
                try:
                    from elle.ops.yq import is_available

                    self._tool_availability[tool] = is_available()
                except ImportError:
                    self._tool_availability[tool] = False
            elif tool == "crudini":
                try:
                    from elle.ops.crudini import is_available

                    self._tool_availability[tool] = is_available()
                except ImportError:
                    self._tool_availability[tool] = False
            elif tool == "xmlstarlet":
                try:
                    from elle.ops.xmlstarlet import is_available

                    self._tool_availability[tool] = is_available()
                except ImportError:
                    self._tool_availability[tool] = False
            elif tool == "ruamel.yaml":
                try:
                    import ruamel.yaml  # noqa: F401

                    self._tool_availability[tool] = True
                except ImportError:
                    self._tool_availability[tool] = False
            elif tool == "toml":
                try:
                    import tomllib  # noqa: F401

                    self._tool_availability[tool] = True
                except ImportError:
                    try:
                        import toml  # noqa: F401

                        self._tool_availability[tool] = True
                    except ImportError:
                        self._tool_availability[tool] = False
            elif tool == "markdown-it":
                try:
                    import markdown_it  # noqa: F401

                    self._tool_availability[tool] = True
                except ImportError:
                    self._tool_availability[tool] = False
            elif tool == "docutils":
                try:
                    import docutils  # noqa: F401

                    self._tool_availability[tool] = True
                except ImportError:
                    self._tool_availability[tool] = False
            else:
                # Unknown tool - assume not available
                self._tool_availability[tool] = False

        return self._tool_availability[tool]

    def select_tier(
        self,
        file_path: str | Path,
        operation: EditOperation,
        *,
        max_tier: TierNumber = 5,
        preferred_tier: TierNumber | None = None,
    ) -> TierSelection:
        """Select the appropriate tier for an edit operation.

        Implements deterministic selection: given the same inputs,
        always returns the same tier. Tries tiers in order 0->5,
        recording why each tier is skipped.

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
        # Use the original path string for pattern matching
        # Don't resolve symlinks as /etc -> /private/etc on macOS
        path_str = str(Path(file_path))
        escalation_trail: list[TierEscalation] = []

        # If a preferred tier is specified, validate it first
        if preferred_tier is not None:
            applicability = self._check_tier_applicability(
                preferred_tier, path_str, operation
            )
            if applicability.applicable and applicability.tool_available:
                return TierSelection(
                    tier=preferred_tier,
                    tier_name=TIER_REGISTRY[preferred_tier].name,
                    reason=f"Preferred tier {preferred_tier}: {applicability.reason}",
                    escalation_trail=tuple(escalation_trail),
                    tool_used=self._get_tier_tool(preferred_tier, path_str),
                )
            else:
                # Record why preferred tier couldn't be used
                escalation_trail.append(
                    TierEscalation(
                        tier=preferred_tier,
                        tier_name=TIER_REGISTRY[preferred_tier].name,
                        reason=f"Preferred tier not applicable: {applicability.reason}",
                        tool_unavailable=not applicability.tool_available,
                    )
                )

        # Try each tier in order
        for tier in range(0, min(max_tier + 1, 6)):
            tier_num: TierNumber = tier  # type: ignore

            applicability = self._check_tier_applicability(tier_num, path_str, operation)

            if applicability.applicable and applicability.tool_available:
                return TierSelection(
                    tier=tier_num,
                    tier_name=TIER_REGISTRY[tier_num].name,
                    reason=applicability.reason,
                    escalation_trail=tuple(escalation_trail),
                    tool_used=self._get_tier_tool(tier_num, path_str),
                )

            # Record why this tier was skipped
            escalation_trail.append(
                TierEscalation(
                    tier=tier_num,
                    tier_name=TIER_REGISTRY[tier_num].name,
                    reason=applicability.reason,
                    tool_unavailable=not applicability.tool_available,
                )
            )

        # No tier could handle the edit
        raise NoApplicableTierError(path_str, escalation_trail)

    def _check_tier_applicability(
        self,
        tier: TierNumber,
        file_path: str,
        operation: EditOperation,
    ) -> TierApplicability:
        """Check if a specific tier can handle the edit.

        Args:
            tier: Tier number to check.
            file_path: Path to the file.
            operation: Edit operation.

        Returns:
            TierApplicability with result and reason.
        """
        if tier == 0:
            return self._check_tier0(file_path, operation)
        elif tier == 1:
            return self._check_tier1(file_path, operation)
        elif tier == 2:
            return self._check_tier2(file_path, operation)
        elif tier == 3:
            return self._check_tier3(file_path, operation)
        elif tier == 4:
            return self._check_tier4(file_path, operation)
        elif tier == 5:
            return self._check_tier5(file_path, operation)
        else:
            return TierApplicability(
                tier=tier,
                applicable=False,
                reason=f"Unknown tier: {tier}",
            )

    def _check_tier0(
        self,
        file_path: str,
        operation: EditOperation,
    ) -> TierApplicability:
        """Check Tier 0 (Native Override / Drop-in) applicability."""
        supports_dropin, dropin_dir = is_dropin_candidate(file_path)

        if not supports_dropin:
            return TierApplicability(
                tier=0,
                applicable=False,
                reason="File does not support drop-in overrides",
            )

        # Drop-ins work best for additive operations
        if operation.kind in ("add", "append", "set"):
            return TierApplicability(
                tier=0,
                applicable=True,
                reason=f"Drop-in supported via {dropin_dir or 'existing .d directory'}",
                confidence=0.95,
            )
        else:
            return TierApplicability(
                tier=0,
                applicable=False,
                reason=f"Operation '{operation.kind}' not suitable for drop-in (use for additive changes)",
            )

    def _check_tier1(
        self,
        file_path: str,
        operation: EditOperation,
    ) -> TierApplicability:
        """Check Tier 1 (Augeas) applicability."""
        # Check if Augeas is available
        if not self._check_tool_available("augeas"):
            return TierApplicability(
                tier=1,
                applicable=True,  # Would be applicable if tool was available
                reason="Augeas not installed",
                tool_available=False,
                missing_tool="python-augeas",
            )

        # Check if file matches Augeas patterns
        if matches_pattern(file_path, TIER1_AUGEAS_PATTERNS):
            # Also check if Augeas has a lens for this file
            try:
                from elle.ops.augeas import detect_lens

                lens = detect_lens(file_path)
                if lens:
                    return TierApplicability(
                        tier=1,
                        applicable=True,
                        reason=f"Augeas lens '{lens}' available for structured editing",
                        confidence=0.9,
                    )
                else:
                    return TierApplicability(
                        tier=1,
                        applicable=False,
                        reason="No Augeas lens found for this file type",
                    )
            except Exception as e:
                return TierApplicability(
                    tier=1,
                    applicable=False,
                    reason=f"Augeas lens detection failed: {e}",
                )

        return TierApplicability(
            tier=1,
            applicable=False,
            reason="File does not match Augeas-supported patterns",
        )

    def _check_tier2(
        self,
        file_path: str,
        operation: EditOperation,
    ) -> TierApplicability:
        """Check Tier 2 (Structured Data: JSON/YAML/TOML) applicability."""
        # JSON
        if matches_pattern(file_path, TIER2_JSON_PATTERNS):
            if self._check_tool_available("jq"):
                return TierApplicability(
                    tier=2,
                    applicable=True,
                    reason="JSON file - using jq for transformation",
                    confidence=0.95,
                )
            else:
                # Fall back to Python json module
                return TierApplicability(
                    tier=2,
                    applicable=True,
                    reason="JSON file - using Python json module (jq not available)",
                    confidence=0.85,
                )

        # YAML
        if matches_pattern(file_path, TIER2_YAML_PATTERNS):
            if self._check_tool_available("yq"):
                return TierApplicability(
                    tier=2,
                    applicable=True,
                    reason="YAML file - using yq for transformation",
                    confidence=0.95,
                )
            elif self._check_tool_available("ruamel.yaml"):
                return TierApplicability(
                    tier=2,
                    applicable=True,
                    reason="YAML file - using ruamel.yaml (yq not available)",
                    confidence=0.85,
                )
            else:
                return TierApplicability(
                    tier=2,
                    applicable=True,
                    reason="YAML file - using PyYAML (no comment preservation)",
                    confidence=0.7,
                )

        # TOML
        if matches_pattern(file_path, TIER2_TOML_PATTERNS):
            if self._check_tool_available("toml"):
                return TierApplicability(
                    tier=2,
                    applicable=True,
                    reason="TOML file - using Python toml library",
                    confidence=0.9,
                )
            else:
                return TierApplicability(
                    tier=2,
                    applicable=False,
                    reason="TOML file but no TOML library available",
                    tool_available=False,
                    missing_tool="toml",
                )

        return TierApplicability(
            tier=2,
            applicable=False,
            reason="File is not JSON, YAML, or TOML",
        )

    def _check_tier3(
        self,
        file_path: str,
        operation: EditOperation,
    ) -> TierApplicability:
        """Check Tier 3 (Format Editors: INI/XML) applicability."""
        # INI
        if matches_pattern(file_path, TIER3_INI_PATTERNS):
            if self._check_tool_available("crudini"):
                return TierApplicability(
                    tier=3,
                    applicable=True,
                    reason="INI/CFG file - using crudini for editing",
                    confidence=0.9,
                )
            else:
                # Python configparser is always available
                return TierApplicability(
                    tier=3,
                    applicable=True,
                    reason="INI/CFG file - using Python configparser (crudini not available)",
                    confidence=0.8,
                )

        # XML
        if matches_pattern(file_path, TIER3_XML_PATTERNS):
            if self._check_tool_available("xmlstarlet"):
                return TierApplicability(
                    tier=3,
                    applicable=True,
                    reason="XML file - using xmlstarlet for editing",
                    confidence=0.9,
                )
            else:
                # Python xml.etree is always available
                return TierApplicability(
                    tier=3,
                    applicable=True,
                    reason="XML file - using Python xml.etree (xmlstarlet not available)",
                    confidence=0.75,
                )

        return TierApplicability(
            tier=3,
            applicable=False,
            reason="File is not INI/CFG or XML",
        )

    def _check_tier4(
        self,
        file_path: str,
        operation: EditOperation,
    ) -> TierApplicability:
        """Check Tier 4 (Document-aware: Markdown/RST) applicability."""
        # Markdown
        if matches_pattern(file_path, TIER4_MARKDOWN_PATTERNS):
            if self._check_tool_available("markdown-it"):
                return TierApplicability(
                    tier=4,
                    applicable=True,
                    reason="Markdown file - using markdown-it for structure preservation",
                    confidence=0.85,
                )
            else:
                return TierApplicability(
                    tier=4,
                    applicable=True,
                    reason="Markdown file - using regex-based editing (markdown-it not available)",
                    confidence=0.7,
                )

        # RST
        if matches_pattern(file_path, TIER4_RST_PATTERNS):
            if self._check_tool_available("docutils"):
                return TierApplicability(
                    tier=4,
                    applicable=True,
                    reason="RST file - using docutils for structure preservation",
                    confidence=0.85,
                )
            else:
                return TierApplicability(
                    tier=4,
                    applicable=True,
                    reason="RST file - using regex-based editing (docutils not available)",
                    confidence=0.7,
                )

        return TierApplicability(
            tier=4,
            applicable=False,
            reason="File is not Markdown or RST",
        )

    def _check_tier5(
        self,
        file_path: str,
        operation: EditOperation,
    ) -> TierApplicability:
        """Check Tier 5 (Text Patching) applicability.

        Tier 5 is always applicable as a last resort, but requires
        anchor patterns for reliability.
        """
        # Check if anchor pattern is provided for better reliability
        if operation.anchor_pattern:
            return TierApplicability(
                tier=5,
                applicable=True,
                reason="Text file - using anchor-based patching with context verification",
                confidence=0.8,
            )
        else:
            return TierApplicability(
                tier=5,
                applicable=True,
                reason="Text file - using best-effort text patching (no anchor pattern provided)",
                confidence=0.5,
            )

    def _get_tier_tool(self, tier: TierNumber, file_path: str) -> str | None:
        """Get the primary tool that will be used for a tier.

        Args:
            tier: Tier number.
            file_path: Path to the file.

        Returns:
            Tool name or None.
        """
        if tier == 0:
            return "drop-in"
        elif tier == 1:
            return "augeas"
        elif tier == 2:
            if matches_pattern(file_path, TIER2_JSON_PATTERNS):
                return "jq" if self._check_tool_available("jq") else "python-json"
            elif matches_pattern(file_path, TIER2_YAML_PATTERNS):
                if self._check_tool_available("yq"):
                    return "yq"
                elif self._check_tool_available("ruamel.yaml"):
                    return "ruamel.yaml"
                return "pyyaml"
            elif matches_pattern(file_path, TIER2_TOML_PATTERNS):
                return "python-toml"
        elif tier == 3:
            if matches_pattern(file_path, TIER3_INI_PATTERNS):
                return "crudini" if self._check_tool_available("crudini") else "configparser"
            elif matches_pattern(file_path, TIER3_XML_PATTERNS):
                return "xmlstarlet" if self._check_tool_available("xmlstarlet") else "xml.etree"
        elif tier == 4:
            if matches_pattern(file_path, TIER4_MARKDOWN_PATTERNS):
                return "markdown-it" if self._check_tool_available("markdown-it") else "regex"
            elif matches_pattern(file_path, TIER4_RST_PATTERNS):
                return "docutils" if self._check_tool_available("docutils") else "regex"
        elif tier == 5:
            return "text-patch"

        return None

    def get_all_tier_applicability(
        self,
        file_path: str | Path,
        operation: EditOperation,
    ) -> dict[TierNumber, TierApplicability]:
        """Get applicability status for all tiers.

        Useful for debugging or showing the user all options.

        Args:
            file_path: Path to the file.
            operation: Edit operation.

        Returns:
            Dict mapping tier number to applicability.
        """
        # Use the original path string for pattern matching
        path_str = str(Path(file_path))
        result: dict[TierNumber, TierApplicability] = {}

        for tier in range(6):
            tier_num: TierNumber = tier  # type: ignore
            result[tier_num] = self._check_tier_applicability(tier_num, path_str, operation)

        return result
