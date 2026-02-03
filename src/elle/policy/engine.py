"""Policy Engine - Core evaluation logic.

The PolicyEngine evaluates requests against loaded policies and
returns decisions with required actions.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from elle.policy.audit import log_evaluation
from elle.policy.loader import load_policy
from elle.policy.matchers import match_condition
from elle.policy.models import (
    PolicyDocument,
    PolicyEffect,
    PolicyEvaluationRequest,
    PolicyEvaluationResult,
    PolicyRule,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Policy Engine
# =============================================================================


class PolicyEngine:
    """Evaluates requests against policy rules.

    The engine:
    1. Loads policies from standard locations
    2. Evaluates requests against all rules (by priority)
    3. Returns the first matching rule's effect
    4. Logs all evaluations for audit
    """

    def __init__(self, policy: PolicyDocument | None = None) -> None:
        """Initialize the engine.

        Args:
            policy: Optional pre-loaded policy. If not provided,
                   policy will be loaded from standard locations.
        """
        self._policy = policy
        self._initialized = policy is not None

    @property
    def policy(self) -> PolicyDocument:
        """Get the loaded policy (lazy loading)."""
        if not self._initialized:
            self._policy = load_policy()
            self._initialized = True
        return self._policy  # type: ignore

    def _get_sorted_rules(self) -> list[PolicyRule]:
        """Get rules sorted by priority (descending).

        Higher priority rules are evaluated first.

        Returns:
            List of rules sorted by priority.
        """
        return sorted(self.policy.rules, key=lambda r: r.priority, reverse=True)

    def reload(self) -> None:
        """Reload policy from disk.

        Call this after policy files have been modified.
        """
        self._policy = load_policy()
        self._initialized = True
        logger.info(f"Reloaded policy: {self._policy.name} with {len(self._policy.rules)} rules")

    def evaluate(
        self,
        request: PolicyEvaluationRequest,
        *,
        audit: bool = True,
    ) -> PolicyEvaluationResult:
        """Evaluate a request against the policy.

        Args:
            request: The evaluation request with context.
            audit: Whether to log the evaluation.

        Returns:
            PolicyEvaluationResult with decision and required actions.
        """
        start_time = time.perf_counter()

        # Find matching rules (evaluate in priority order)
        matched_rules: list[str] = []
        effective_rule: PolicyRule | None = None

        for rule in self._get_sorted_rules():
            if not rule.enabled:
                continue

            if self._rule_matches(rule, request):
                matched_rules.append(rule.id)

                # First match wins (rules are sorted by priority)
                if effective_rule is None:
                    effective_rule = rule

        # Determine effect
        if effective_rule is not None:
            effect = effective_rule.effect
            message = effective_rule.message
            justification_prompt = effective_rule.justification_prompt
        else:
            effect = self.policy.default_effect
            message = None
            justification_prompt = None

        # Build result
        allowed = effect != PolicyEffect.DENY
        requires_confirmation = effect == PolicyEffect.REQUIRE_CONFIRMATION
        requires_justification = effect == PolicyEffect.REQUIRE_JUSTIFICATION
        requires_preview = effect == PolicyEffect.REQUIRE_PREVIEW

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        result = PolicyEvaluationResult(
            allowed=allowed,
            effect=effect,
            matched_rules=tuple(matched_rules),
            requires_confirmation=requires_confirmation,
            requires_justification=requires_justification,
            requires_preview=requires_preview,
            message=message,
            justification_prompt=justification_prompt,
            evaluated_at=datetime.now(timezone.utc),
            evaluation_time_ms=elapsed_ms,
        )

        # Log to audit trail
        if audit:
            log_evaluation(request, result)

        logger.debug(
            f"Policy evaluation: effect={effect.value}, matched={len(matched_rules)} rules, time={elapsed_ms:.2f}ms"
        )

        return result

    def evaluate_command(
        self,
        command: str,
        *,
        requires_privilege: bool = False,
        audit: bool = True,
    ) -> PolicyEvaluationResult:
        """Evaluate a shell command against the policy.

        Convenience method for command-only evaluation.

        Args:
            command: The shell command to evaluate.
            requires_privilege: Whether the command needs elevated privileges.
            audit: Whether to log the evaluation.

        Returns:
            PolicyEvaluationResult.
        """
        request = PolicyEvaluationRequest(
            operation_type="command",
            command=command,
            requires_privilege=requires_privilege,
        )
        return self.evaluate(request, audit=audit)

    def evaluate_path(
        self,
        path: str,
        operation_type: str = "file_write",
        *,
        audit: bool = True,
    ) -> PolicyEvaluationResult:
        """Evaluate a file path access against the policy.

        Convenience method for path-only evaluation.

        Args:
            path: The file path to evaluate.
            operation_type: Type of operation (file_read, file_write, etc.).
            audit: Whether to log the evaluation.

        Returns:
            PolicyEvaluationResult.
        """
        request = PolicyEvaluationRequest(
            operation_type=operation_type,  # type: ignore
            path=path,
        )
        return self.evaluate(request, audit=audit)

    def _rule_matches(
        self,
        rule: PolicyRule,
        request: PolicyEvaluationRequest,
    ) -> bool:
        """Check if a rule matches a request.

        Args:
            rule: The rule to check.
            request: The request to match against.

        Returns:
            True if the rule matches.
        """
        if not rule.conditions:
            return False

        results = [match_condition(cond, request) for cond in rule.conditions]

        if rule.match_all:
            return all(results)
        else:
            return any(results)

    def record_justification(
        self,
        result: PolicyEvaluationResult,
        justification: str,
    ) -> None:
        """Record a user-provided justification for an action.

        Args:
            result: The evaluation result that required justification.
            justification: User's justification text.
        """
        # Re-log with justification
        # Note: We need the original request, which we don't have here
        # In practice, this should be called alongside the original evaluate
        logger.info(f"Justification recorded for rules {result.matched_rules}: {justification}")

    def record_confirmation(
        self,
        result: PolicyEvaluationResult,
        confirmed: bool,
    ) -> None:
        """Record whether user confirmed an action.

        Args:
            result: The evaluation result that required confirmation.
            confirmed: Whether user confirmed.
        """
        logger.info(f"Confirmation {'granted' if confirmed else 'denied'} for rules {result.matched_rules}")


# =============================================================================
# Module-level Functions
# =============================================================================

_engine: PolicyEngine | None = None
_engine_lock = threading.Lock()


def get_policy_engine() -> PolicyEngine:
    """Get the shared policy engine instance (thread-safe).

    Returns:
        PolicyEngine singleton.
    """
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            _engine = PolicyEngine()
        return _engine


def evaluate(
    request: PolicyEvaluationRequest,
    *,
    audit: bool = True,
) -> PolicyEvaluationResult:
    """Evaluate a request against the policy (convenience function).

    Args:
        request: The evaluation request.
        audit: Whether to log the evaluation.

    Returns:
        PolicyEvaluationResult.
    """
    return get_policy_engine().evaluate(request, audit=audit)


def evaluate_command(
    command: str,
    *,
    requires_privilege: bool = False,
    audit: bool = True,
) -> PolicyEvaluationResult:
    """Evaluate a shell command (convenience function).

    Args:
        command: The command to evaluate.
        requires_privilege: Whether privileged.
        audit: Whether to log.

    Returns:
        PolicyEvaluationResult.
    """
    return get_policy_engine().evaluate_command(
        command,
        requires_privilege=requires_privilege,
        audit=audit,
    )


def evaluate_path(
    path: str,
    operation_type: str = "file_write",
    *,
    audit: bool = True,
) -> PolicyEvaluationResult:
    """Evaluate a file path access (convenience function).

    Args:
        path: The path to evaluate.
        operation_type: Type of operation.
        audit: Whether to log.

    Returns:
        PolicyEvaluationResult.
    """
    return get_policy_engine().evaluate_path(
        path,
        operation_type=operation_type,
        audit=audit,
    )


def reload_policy() -> None:
    """Reload policy from disk (convenience function)."""
    get_policy_engine().reload()
