"""Policy integration for capabilities.

Bridges the capability system with the policy engine,
enabling policy-based access control for capability execution.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel

from elle.capabilities.models import CapabilitySpec, RiskLevel
from elle.policy.models import PolicyEvaluationResult

if TYPE_CHECKING:
    from elle.capabilities.protocol import Capability

logger = logging.getLogger(__name__)


def _risk_to_policy_risk(risk: RiskLevel) -> str:
    """Convert capability RiskLevel to policy RiskLevel.

    Args:
        risk: Capability risk level.

    Returns:
        Policy risk level string.
    """
    # Policy engine uses "low", "medium", "high"
    # Capability uses "none", "low", "medium", "high", "critical"
    mapping = {
        "none": "low",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "critical": "high",  # Map critical to high for policy
    }
    return mapping.get(risk, "medium")


def evaluate_capability(
    capability: Capability,
    input: BaseModel,
    audit: bool = True,
) -> PolicyEvaluationResult:
    """Evaluate if a capability execution is allowed.

    Uses the policy engine to check whether the capability
    can be executed based on its metadata.

    Args:
        capability: The capability to evaluate.
        input: The input parameters.
        audit: Whether to log the evaluation.

    Returns:
        PolicyEvaluationResult from the policy engine.
    """
    try:
        from elle.policy import (
            PolicyEvaluationRequest,
            evaluate,
        )

        spec = capability.spec

        # Build policy request from capability spec
        request = PolicyEvaluationRequest(
            operation_type="capability",
            command=spec.name,  # Use capability name for rule matching
            domain=f"capability.{spec.domain}",
            risk_level=_risk_to_policy_risk(spec.risk),
            requires_privilege=spec.requires_privilege,
        )

        return evaluate(request, audit=audit)

    except ImportError:
        logger.warning("Policy module not available, allowing capability")
        # Return a permissive result if policy module unavailable
        return _create_allow_result()
    except Exception as e:
        logger.error(f"Policy evaluation failed: {e}")
        # On error, default to requiring confirmation
        return _create_confirm_result(str(e))


def capability_requires_confirmation(spec: CapabilitySpec) -> bool:
    """Check if this capability type requires user confirmation.

    Based on the capability's risk level and privilege requirements.

    Args:
        spec: The capability specification.

    Returns:
        True if confirmation is required.
    """
    # High/critical risk always requires confirmation
    if spec.risk in ("high", "critical"):
        return True

    # Privileged operations require confirmation
    if spec.requires_privilege:
        return True

    # Side effects that are not reversible require confirmation
    return any(not effect.reversible for effect in spec.side_effects)


def capability_requires_privilege_check(spec: CapabilitySpec) -> bool:
    """Check if this capability requires privilege verification.

    Args:
        spec: The capability specification.

    Returns:
        True if privilege check is needed.
    """
    return spec.requires_privilege


def _create_allow_result() -> PolicyEvaluationResult:
    """Create an allow result when policy is unavailable.

    Returns:
        A PolicyEvaluationResult that allows execution.
    """
    try:
        from elle.policy import PolicyEffect, PolicyEvaluationResult

        return PolicyEvaluationResult(
            allowed=True,
            effect=PolicyEffect.ALLOW,
            matched_rules=(),
            requires_confirmation=False,
            requires_justification=False,
            requires_preview=False,
        )
    except ImportError:
        # If we can't import, create a minimal compatible object
        from datetime import datetime

        class _MinimalResult:
            allowed = True
            effect = "allow"
            matched_rules = ()
            requires_confirmation = False
            requires_justification = False
            requires_preview = False
            is_blocked = False
            should_proceed = True
            message = None
            justification_prompt = None
            evaluated_at = datetime.now()
            evaluation_time_ms = 0.0

        return _MinimalResult()  # type: ignore


def _create_confirm_result(message: str) -> PolicyEvaluationResult:
    """Create a result requiring confirmation.

    Args:
        message: Message explaining why confirmation is needed.

    Returns:
        A PolicyEvaluationResult requiring confirmation.
    """
    try:
        from elle.policy import PolicyEffect, PolicyEvaluationResult

        return PolicyEvaluationResult(
            allowed=True,
            effect=PolicyEffect.REQUIRE_CONFIRMATION,
            matched_rules=(),
            requires_confirmation=True,
            requires_justification=False,
            requires_preview=False,
            message=message,
        )
    except ImportError:
        from datetime import datetime

        class _MinimalResult:
            allowed = True
            effect = "require_confirmation"
            matched_rules = ()
            requires_confirmation = True
            requires_justification = False
            requires_preview = False
            is_blocked = False
            should_proceed = True
            message = message
            justification_prompt = None
            evaluated_at = datetime.now()
            evaluation_time_ms = 0.0

        return _MinimalResult()  # type: ignore


class CapabilityPolicyResult(BaseModel):
    """Result of capability policy evaluation.

    A simplified view of policy evaluation specific to capabilities.
    """

    allowed: bool
    requires_confirmation: bool
    requires_preview: bool
    message: str | None = None
    matched_rules: tuple[str, ...] = ()
    is_blocked: bool = False

    @classmethod
    def from_policy_result(cls, result) -> CapabilityPolicyResult:
        """Create from a PolicyEvaluationResult.

        Args:
            result: PolicyEvaluationResult from policy engine.

        Returns:
            CapabilityPolicyResult.
        """
        return cls(
            allowed=getattr(result, "allowed", True),
            requires_confirmation=getattr(result, "requires_confirmation", False),
            requires_preview=getattr(result, "requires_preview", False),
            message=getattr(result, "message", None),
            matched_rules=getattr(result, "matched_rules", ()),
            is_blocked=getattr(result, "is_blocked", False),
        )
