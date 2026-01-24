"""Policy file discovery and composition.

Discovers policy files from standard locations and composes them
with inheritance support via 'extends'.
"""

from __future__ import annotations

import logging
from pathlib import Path

from elle.policy.defaults import get_default_policy
from elle.policy.exceptions import PolicyCircularExtendError, PolicyLoadError
from elle.policy.models import PolicyDocument, PolicyRule
from elle.policy.parser import parse_file

logger = logging.getLogger(__name__)


# =============================================================================
# Standard Policy Locations
# =============================================================================

# System-wide policy
SYSTEM_POLICY_PATH = Path("/etc/elle/policy.yaml")

# User-specific policy (overrides system)
USER_POLICY_PATH = Path.home() / ".config" / "elle" / "policy.yaml"

# Built-in policy reference
BUILTIN_POLICY_REF = "system:defaults"


# =============================================================================
# Policy Loader
# =============================================================================


class PolicyLoader:
    """Loads and composes policy files.

    Handles:
    - Discovery of policy files from standard locations
    - Parsing and validation
    - Policy inheritance (extends)
    - Merging rules with priority ordering
    """

    def __init__(
        self,
        system_path: Path | None = None,
        user_path: Path | None = None,
    ) -> None:
        """Initialize the loader.

        Args:
            system_path: Override for system policy path.
            user_path: Override for user policy path.
        """
        self._system_path = system_path or SYSTEM_POLICY_PATH
        self._user_path = user_path or USER_POLICY_PATH
        self._cache: dict[str, PolicyDocument] = {}

    def load(self) -> PolicyDocument:
        """Load and compose the effective policy.

        Loads policies in order of precedence:
        1. Built-in defaults
        2. System policy (/etc/elle/policy.yaml)
        3. User policy (~/.config/elle/policy.yaml)

        Each policy can extend others, and rules are merged with
        priority ordering (higher priority rules evaluated first).

        Returns:
            Composed PolicyDocument with all rules.
        """
        self._cache.clear()

        # Start with user policy if it exists, else system, else defaults
        if self._user_path.exists():
            logger.debug(f"Loading user policy from {self._user_path}")
            base = self._load_with_extends(str(self._user_path), set())
        elif self._system_path.exists():
            logger.debug(f"Loading system policy from {self._system_path}")
            base = self._load_with_extends(str(self._system_path), set())
        else:
            logger.debug("Using default policy (no custom policy found)")
            base = get_default_policy()

        return base

    def load_file(self, path: Path | str) -> PolicyDocument:
        """Load a specific policy file with extends resolution.

        Args:
            path: Path to policy file.

        Returns:
            Composed PolicyDocument.
        """
        self._cache.clear()
        return self._load_with_extends(str(path), set())

    def _load_with_extends(
        self,
        path_or_ref: str,
        seen: set[str],
    ) -> PolicyDocument:
        """Load a policy with recursive extends handling.

        Args:
            path_or_ref: File path or built-in reference (e.g., "system:defaults").
            seen: Set of already-loaded paths (for cycle detection).

        Returns:
            Composed PolicyDocument.

        Raises:
            PolicyCircularExtendError: If circular extends detected.
            PolicyLoadError: If policy cannot be loaded.
        """
        # Check for cycles
        if path_or_ref in seen:
            chain = tuple(seen) + (path_or_ref,)
            raise PolicyCircularExtendError(chain)

        seen = seen | {path_or_ref}

        # Check cache
        if path_or_ref in self._cache:
            return self._cache[path_or_ref]

        # Load the policy
        policy = self._load_single(path_or_ref)

        # Process extends
        if policy.extends:
            # Load all parent policies
            parents = []
            for extend_ref in policy.extends:
                parent = self._load_with_extends(extend_ref, seen)
                parents.append(parent)

            # Merge parents (in order) then apply current
            merged = self._merge_policies(parents)
            policy = self._apply_child_to_parent(merged, policy)

        # Cache and return
        self._cache[path_or_ref] = policy
        return policy

    def _load_single(self, path_or_ref: str) -> PolicyDocument:
        """Load a single policy without extends resolution.

        Args:
            path_or_ref: File path or built-in reference.

        Returns:
            PolicyDocument.
        """
        # Handle built-in references
        if path_or_ref == BUILTIN_POLICY_REF:
            return get_default_policy()

        if path_or_ref.startswith("system:"):
            # Other system references could be added here
            logger.warning(f"Unknown system reference: {path_or_ref}")
            return get_default_policy()

        # Load from file
        path = Path(path_or_ref)
        if not path.is_absolute():
            # Resolve relative to user config dir
            path = self._user_path.parent / path_or_ref

        try:
            return parse_file(path)
        except Exception as e:
            raise PolicyLoadError(str(path), str(e))

    def _merge_policies(
        self,
        policies: list[PolicyDocument],
    ) -> PolicyDocument:
        """Merge multiple policies into one.

        Rules are combined and sorted by priority.

        Args:
            policies: Policies to merge.

        Returns:
            Merged PolicyDocument.
        """
        if not policies:
            return get_default_policy()

        if len(policies) == 1:
            return policies[0]

        # Combine all rules
        all_rules: list[PolicyRule] = []
        for policy in policies:
            all_rules.extend(policy.rules)

        # Use last policy's settings for metadata
        last = policies[-1]

        return PolicyDocument(
            version=last.version,
            name=f"Merged: {', '.join(p.name for p in policies)}",
            extends=(),  # Already resolved
            default_effect=last.default_effect,
            rules=tuple(all_rules),
        )

    def _apply_child_to_parent(
        self,
        parent: PolicyDocument,
        child: PolicyDocument,
    ) -> PolicyDocument:
        """Apply child policy on top of parent.

        Child rules override parent rules with the same ID.
        New rules are added.

        Args:
            parent: Parent policy.
            child: Child policy to apply.

        Returns:
            Combined PolicyDocument.
        """
        # Build map of parent rules by ID
        parent_rules = {r.id: r for r in parent.rules}

        # Apply child rules (override or add)
        for rule in child.rules:
            parent_rules[rule.id] = rule

        # Combine and sort by priority (descending)
        all_rules = sorted(
            parent_rules.values(),
            key=lambda r: r.priority,
            reverse=True,
        )

        return PolicyDocument(
            version=child.version,
            name=child.name,
            extends=(),  # Already resolved
            default_effect=child.default_effect,
            rules=tuple(all_rules),
        )


# =============================================================================
# Module-level Functions
# =============================================================================

_loader: PolicyLoader | None = None


def get_loader() -> PolicyLoader:
    """Get the shared loader instance.

    Returns:
        PolicyLoader singleton.
    """
    global _loader
    if _loader is None:
        _loader = PolicyLoader()
    return _loader


def load_policy() -> PolicyDocument:
    """Load the effective policy (convenience function).

    Discovers and loads policy from standard locations.

    Returns:
        Composed PolicyDocument.
    """
    return get_loader().load()


def load_policy_file(path: Path | str) -> PolicyDocument:
    """Load a specific policy file (convenience function).

    Args:
        path: Path to policy file.

    Returns:
        PolicyDocument.
    """
    return get_loader().load_file(path)
