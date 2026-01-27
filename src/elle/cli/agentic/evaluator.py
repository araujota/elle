"""SufficiencyEvaluator - Determines if gathered evidence is sufficient.

Checks whether the collected evidence provides enough information to
answer the user's question, and identifies gaps if not.
"""

from __future__ import annotations

import logging

from elle.cli.agentic.models import (
    GatherResult,
    InformationNeed,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Sufficiency Rules
# =============================================================================

# Minimum requirements for different categories
CATEGORY_MIN_REQUIREMENTS: dict[str, list[str]] = {
    "service": ["status"],  # At least need status
    "file": ["content"],  # At least need content
    "package": ["info"],  # At least need info
    "docker": ["list", "status"],  # Need list or status
    "network": ["listeners"],  # Need listeners
    "config": ["content"],  # Need content
    "system": ["info", "resources"],  # Need info or resources
}


# =============================================================================
# SufficiencyEvaluator
# =============================================================================


class SufficiencyEvaluator:
    """Evaluates whether gathered evidence is sufficient to answer a question.

    Uses rule-based checks to determine if the core information needs
    are satisfied by the collected evidence.
    """

    def evaluate(self, result: GatherResult) -> bool:
        """Evaluate if the gathered evidence is sufficient.

        Args:
            result: The gather result to evaluate.

        Returns:
            True if evidence is sufficient to answer the question.
        """
        if not result.evidence:
            return False

        # Must have at least one successful evidence item
        successful = [e for e in result.evidence if e.success]
        if not successful:
            return False

        # Check if core needs are satisfied
        return self._check_core_needs(result)

    def _check_core_needs(self, result: GatherResult) -> bool:
        """Check if core information needs are satisfied.

        Args:
            result: The gather result.

        Returns:
            True if core needs are satisfied.
        """
        if not result.plan.needs:
            # No specific needs - any successful evidence is sufficient
            return any(e.success for e in result.evidence)

        # For each need, check if at least one related capability succeeded
        for need in result.plan.needs:
            if not self._need_satisfied(need, result):
                return False

        return True

    def _need_satisfied(
        self,
        need: InformationNeed,
        result: GatherResult,
    ) -> bool:
        """Check if a specific need is satisfied by the evidence.

        Args:
            need: The information need.
            result: The gather result.

        Returns:
            True if the need is satisfied.
        """
        # Find capabilities that match this need
        matching_capabilities = self._get_matching_capabilities(need)

        # Check if any matching capability succeeded
        for evidence in result.evidence:
            if evidence.capability in matching_capabilities and evidence.success:
                return True

        return False

    def _get_matching_capabilities(self, need: InformationNeed) -> set[str]:
        """Get capabilities that satisfy a need.

        Args:
            need: The information need.

        Returns:
            Set of capability names that could satisfy the need.
        """
        capabilities: set[str] = set()

        # Map needs to capabilities based on category
        category_caps = {
            "service": {"service.status", "service.logs"},
            "file": {"file.read", "file.stat", "file.diff"},
            "package": {"package.info", "package.detect-conflicts"},
            "docker": {"docker.list", "docker.inspect", "docker.logs"},
            "network": {"network.listeners", "network.connections"},
            "config": {"config.preview", "file.read"},
            "system": {"system.info", "system.resources"},
        }

        if need.category in category_caps:
            capabilities.update(category_caps[need.category])

        return capabilities

    def identify_gaps(
        self,
        question: str,
        result: GatherResult,
    ) -> tuple[InformationNeed, ...]:
        """Identify what additional information is needed.

        Args:
            question: The original question.
            result: The gather result.

        Returns:
            Tuple of additional InformationNeed objects.
        """
        if not result.plan.needs:
            return ()

        gaps: list[InformationNeed] = []

        for need in result.plan.needs:
            if not self._need_satisfied(need, result):
                # The need wasn't satisfied - add it as a gap
                gaps.append(need)

        return tuple(gaps)

    def get_unsatisfied_aspects(self, result: GatherResult) -> list[str]:
        """Get a list of aspects that weren't satisfied.

        Args:
            result: The gather result.

        Returns:
            List of descriptions of unsatisfied aspects.
        """
        unsatisfied: list[str] = []

        for need in result.plan.needs:
            if not self._need_satisfied(need, result):
                desc = f"{need.category}"
                if need.target:
                    desc += f" for '{need.target}'"
                if need.aspects:
                    desc += f" ({', '.join(need.aspects)})"
                unsatisfied.append(desc)

        # Also add any errors from the result
        for evidence in result.evidence:
            if not evidence.success and evidence.error:
                unsatisfied.append(f"{evidence.capability}: {evidence.error}")

        return unsatisfied


# =============================================================================
# Module-level singleton
# =============================================================================

_evaluator: SufficiencyEvaluator | None = None


def get_evaluator() -> SufficiencyEvaluator:
    """Get the shared evaluator instance.

    Returns:
        The SufficiencyEvaluator singleton.
    """
    global _evaluator
    if _evaluator is None:
        _evaluator = SufficiencyEvaluator()
    return _evaluator


def reset_evaluator() -> None:
    """Reset the shared evaluator instance."""
    global _evaluator
    _evaluator = None
