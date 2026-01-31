"""GoalEvaluator - Determines if execution achieved the user's intent.

Evaluates whether the collected evidence and executed actions satisfy
both the information needs AND the action requests from the user's intent.

Also provides gap identification for the agentic loop to replan if needed.
"""

from __future__ import annotations

import logging
from typing import Any

from elle.cli.agentic.models import (
    AgenticIntent,
    EvaluationResult,
    EvaluationStatus,
    ExecutionEvidence,
    ExecutionResult,
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

# Capabilities that indicate action completion
ACTION_CAPABILITIES: dict[str, frozenset[str]] = {
    "restart": frozenset({"service.restart", "docker.restart"}),
    "start": frozenset({"service.start", "docker.start"}),
    "stop": frozenset({"service.stop", "docker.stop"}),
    "install": frozenset({"package.install"}),
    "remove": frozenset({"package.remove", "docker.remove"}),
    "update": frozenset({"package.upgrade"}),
    "enable": frozenset({"service.enable"}),
    "disable": frozenset({"service.disable"}),
    "fix": frozenset({"service.restart", "service.start"}),  # Fix often means restart/start
}


# =============================================================================
# GoalEvaluator (NEW - Unified)
# =============================================================================


class GoalEvaluator:
    """Evaluates whether execution achieved the user's intent.

    Checks both:
    1. Were information needs satisfied? (did we gather the info?)
    2. Were actions completed? (did the mutations succeed?)

    This is used by the agentic loop to determine whether to:
    - Return a response (satisfied or failed)
    - Replan and try again (needs_more)
    """

    def __init__(self, use_llm_evaluation: bool = False) -> None:
        """Initialize the evaluator.

        Args:
            use_llm_evaluation: Whether to use LLM for complex evaluations.
        """
        self.use_llm_evaluation = use_llm_evaluation

    async def evaluate(
        self,
        intent: AgenticIntent,
        result: ExecutionResult,
    ) -> EvaluationResult:
        """Evaluate if execution achieved the intent.

        Args:
            intent: The user's intent.
            result: The execution result.

        Returns:
            EvaluationResult with status and gap analysis.
        """
        if not result.evidence:
            return EvaluationResult(
                status=EvaluationStatus.FAILED,
                info_satisfied=False,
                actions_completed=False,
                gaps=("No capabilities were executed",),
                confidence=1.0,
                rationale="No evidence collected",
            )

        # Check information needs
        info_satisfied, info_gaps = self._check_info_needs(intent, result)

        # Check action completion
        actions_completed, action_gaps = self._check_actions(intent, result)

        # Combine gaps
        all_gaps = info_gaps + action_gaps

        # Determine overall status
        if info_satisfied and actions_completed:
            status = EvaluationStatus.SATISFIED
            rationale = "All information gathered and actions completed"
        elif not result.any_succeeded:
            status = EvaluationStatus.FAILED
            rationale = "All capabilities failed"
        elif all_gaps:
            status = EvaluationStatus.NEEDS_MORE
            rationale = f"Missing: {', '.join(all_gaps[:3])}"
        else:
            status = EvaluationStatus.SATISFIED
            rationale = "Partial success - some capabilities succeeded"

        # For complex cases, optionally use LLM
        if self.use_llm_evaluation and status == EvaluationStatus.NEEDS_MORE:
            try:
                llm_result = await self._llm_evaluate(intent, result)
                if llm_result:
                    return llm_result
            except Exception as e:
                logger.debug(f"LLM evaluation failed: {e}")

        # Suggest capabilities that might help
        suggested = self._suggest_capabilities(intent, result, all_gaps)

        return EvaluationResult(
            status=status,
            info_satisfied=info_satisfied,
            actions_completed=actions_completed,
            gaps=tuple(all_gaps),
            suggested_capabilities=suggested,
            confidence=self._calculate_confidence(result),
            rationale=rationale,
        )

    def _check_info_needs(
        self,
        intent: AgenticIntent,
        result: ExecutionResult,
    ) -> tuple[bool, list[str]]:
        """Check if information needs were satisfied.

        Args:
            intent: The user's intent.
            result: The execution result.

        Returns:
            Tuple of (all_satisfied, list_of_gaps).
        """
        if not intent.information_needs:
            return True, []

        gaps: list[str] = []
        satisfied_count = 0

        for need in intent.information_needs:
            # Find evidence that matches this need
            matching = self._find_matching_evidence(need, result)

            if matching and any(e.success for e in matching):
                satisfied_count += 1
            else:
                gap = f"{need.category}"
                if need.target:
                    gap += f" for '{need.target}'"
                gaps.append(gap)

        all_satisfied = satisfied_count == len(intent.information_needs)
        return all_satisfied, gaps

    def _check_actions(
        self,
        intent: AgenticIntent,
        result: ExecutionResult,
    ) -> tuple[bool, list[str]]:
        """Check if requested actions were completed.

        Args:
            intent: The user's intent.
            result: The execution result.

        Returns:
            Tuple of (all_completed, list_of_gaps).
        """
        if not intent.action_requests:
            return True, []

        gaps: list[str] = []
        completed_count = 0

        for action in intent.action_requests:
            # Find evidence that matches this action
            action_type = action.action_type.value
            action_caps = ACTION_CAPABILITIES.get(action_type, frozenset())

            # Also check for domain.action_type pattern
            action_caps = action_caps | {f"{action.domain}.{action_type}"}

            matching = [
                e
                for e in result.evidence
                if e.capability in action_caps and self._args_match_target(e.args, action.target)
            ]

            if matching and any(e.success for e in matching):
                completed_count += 1
            else:
                gaps.append(f"{action_type} {action.target}")

        all_completed = completed_count == len(intent.action_requests)
        return all_completed, gaps

    def _find_matching_evidence(
        self,
        need: InformationNeed,
        result: ExecutionResult,
    ) -> list[ExecutionEvidence]:
        """Find evidence that matches an information need.

        Args:
            need: The information need.
            result: The execution result.

        Returns:
            List of matching evidence.
        """
        # Map categories to capability prefixes
        category_prefixes = {
            "service": ("service.",),
            "file": ("file.",),
            "package": ("package.",),
            "docker": ("docker.",),
            "network": ("network.",),
            "config": ("config.", "file.read"),
            "system": ("system.",),
        }

        prefixes = category_prefixes.get(need.category, ())

        matching: list[ExecutionEvidence] = []
        for evidence in result.evidence:
            # Check capability prefix
            if any(evidence.capability.startswith(p) for p in prefixes):
                # Check target if specified
                if not need.target or self._args_match_target(evidence.args, need.target):
                    matching.append(evidence)

        return matching

    def _args_match_target(self, args: dict[str, Any], target: str) -> bool:
        """Check if capability args match a target.

        Args:
            args: Capability arguments.
            target: Target to match.

        Returns:
            True if args contain the target.
        """
        if not target:
            return True

        target_lower = target.lower()
        for value in args.values():
            if isinstance(value, str) and value.lower() == target_lower:
                return True

        return False

    def _calculate_confidence(self, result: ExecutionResult) -> float:
        """Calculate confidence in the evaluation.

        Args:
            result: The execution result.

        Returns:
            Confidence score between 0 and 1.
        """
        if not result.evidence:
            return 0.0

        success_rate = result.success_count / len(result.evidence)
        return min(1.0, success_rate + 0.1) if success_rate > 0.5 else success_rate

    def _suggest_capabilities(
        self,
        intent: AgenticIntent,
        result: ExecutionResult,
        gaps: list[str],
    ) -> tuple[str, ...]:
        """Suggest capabilities that might fill gaps.

        Args:
            intent: The user's intent.
            result: The execution result.
            gaps: Identified gaps.

        Returns:
            Tuple of suggested capability names.
        """
        suggestions: list[str] = []
        executed = {e.capability for e in result.evidence}

        # Suggest based on failed or missing info needs
        for need in intent.information_needs:
            category_caps = {
                "service": ["service.status", "service.logs"],
                "file": ["file.read", "file.stat"],
                "package": ["package.info"],
                "docker": ["docker.list", "docker.inspect", "docker.logs"],
                "network": ["network.listeners"],
                "system": ["system.info", "system.resources"],
            }

            for cap in category_caps.get(need.category, []):
                if cap not in executed:
                    suggestions.append(cap)

        return tuple(suggestions[:5])

    async def _llm_evaluate(
        self,
        intent: AgenticIntent,
        result: ExecutionResult,
    ) -> EvaluationResult | None:
        """Use LLM for complex evaluation.

        Args:
            intent: The user's intent.
            result: The execution result.

        Returns:
            EvaluationResult or None if LLM unavailable.
        """
        # Placeholder for LLM-based evaluation
        # Can be implemented for complex cases where rule-based
        # evaluation is insufficient
        return None


# =============================================================================
# Module-level singletons
# =============================================================================

_goal_evaluator: GoalEvaluator | None = None


def get_goal_evaluator() -> GoalEvaluator:
    """Get the shared goal evaluator instance.

    Returns:
        The GoalEvaluator singleton.
    """
    global _goal_evaluator
    if _goal_evaluator is None:
        _goal_evaluator = GoalEvaluator()
    return _goal_evaluator


def reset_evaluator() -> None:
    """Reset the shared evaluator instance."""
    global _goal_evaluator
    _goal_evaluator = None
