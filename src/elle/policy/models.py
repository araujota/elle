"""Pydantic models for the Policy Engine.

Defines data structures for policy rules, conditions, evaluation
requests, and results.
"""

from __future__ import annotations

from datetime import datetime, time
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Enums
# =============================================================================


class PolicyEffect(str, Enum):
    """Effect to apply when a rule matches."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"
    REQUIRE_JUSTIFICATION = "require_justification"
    REQUIRE_PREVIEW = "require_preview"
    AUDIT = "audit"


class MatchType(str, Enum):
    """Type of pattern matching for conditions."""

    EXACT = "exact"
    GLOB = "glob"
    REGEX = "regex"
    PREFIX = "prefix"


class RiskLevel(str, Enum):
    """Risk level classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# =============================================================================
# Time Window
# =============================================================================


class TimeWindow(BaseModel):
    """A time window (e.g., maintenance window).

    Defines a range of hours during which operations are allowed or denied.
    Uses 24-hour format (00:00-23:59).
    """

    model_config = ConfigDict(frozen=True)

    start: time = Field(description="Start time in 24-hour format")
    end: time = Field(description="End time in 24-hour format")

    def contains(self, t: time) -> bool:
        """Check if a given time falls within this window.

        Handles windows that span midnight (e.g., 22:00-06:00).
        """
        if self.start <= self.end:
            # Normal window (same day)
            return self.start <= t <= self.end
        else:
            # Window spans midnight
            return t >= self.start or t <= self.end


# =============================================================================
# Condition
# =============================================================================


class Condition(BaseModel):
    """A single condition for a policy rule.

    Conditions can match against:
    - Commands (by pattern)
    - File paths (by pattern)
    - Domains (e.g., network.*, service.*)
    - Risk levels
    - Privilege requirements
    - Time windows
    """

    model_config = ConfigDict(frozen=True)

    # Pattern matching fields (at least one should be set)
    command: str | None = Field(
        default=None,
        description="Command pattern to match",
    )
    path: str | None = Field(
        default=None,
        description="File path pattern to match",
    )
    domain: str | None = Field(
        default=None,
        description="Domain pattern (e.g., network.*, service.*)",
    )
    intent: str | None = Field(
        default=None,
        description="Intent type to match (e.g., shell_passthrough)",
    )

    # Attribute matching
    risk_level: RiskLevel | None = Field(
        default=None,
        description="Minimum risk level to match",
    )
    requires_privilege: bool | None = Field(
        default=None,
        description="Match operations that require elevated privileges",
    )

    # Match configuration
    match_type: MatchType = Field(
        default=MatchType.GLOB,
        description="How to match patterns",
    )

    # Time-based conditions
    allowed_hours: tuple[TimeWindow, ...] | None = Field(
        default=None,
        description="Time windows when this condition matches",
    )

    # Negation
    negate: bool = Field(
        default=False,
        description="Negate the match result",
    )


# =============================================================================
# Policy Rule
# =============================================================================


class PolicyRule(BaseModel):
    """A single policy rule.

    Rules define conditions under which certain effects (allow, deny,
    require_confirmation, etc.) should apply.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Unique rule identifier")
    name: str = Field(description="Human-readable rule name")

    # What this rule matches
    conditions: tuple[Condition, ...] = Field(
        description="Conditions that must be met for this rule to apply",
    )
    match_all: bool = Field(
        default=True,
        description="If True, all conditions must match (AND). If False, any (OR).",
    )

    # What to do when matched
    effect: PolicyEffect = Field(description="Effect to apply when rule matches")

    # User-facing messages
    message: str | None = Field(
        default=None,
        description="Message to show user when rule applies",
    )
    justification_prompt: str | None = Field(
        default=None,
        description="Prompt for require_justification effect",
    )

    # Rule metadata
    priority: int = Field(
        default=0,
        description="Higher priority rules are evaluated first",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this rule is active",
    )


# =============================================================================
# Policy Document
# =============================================================================


class PolicyDocument(BaseModel):
    """A complete policy document.

    Represents the contents of a policy file with all rules and metadata.
    """

    model_config = ConfigDict(frozen=True)

    version: str = Field(
        default="1.0",
        description="Policy format version",
    )
    name: str = Field(
        default="Unnamed Policy",
        description="Human-readable policy name",
    )
    extends: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Parent policies to inherit from",
    )
    default_effect: PolicyEffect = Field(
        default=PolicyEffect.ALLOW,
        description="Default effect when no rules match",
    )
    rules: tuple[PolicyRule, ...] = Field(
        default_factory=tuple,
        description="Policy rules",
    )


# =============================================================================
# Evaluation Request
# =============================================================================


OperationType = Literal[
    "command",  # Shell command execution
    "file_read",  # Reading a file
    "file_write",  # Writing/creating a file
    "file_delete",  # Deleting a file
    "config_edit",  # Editing configuration
    "plan_execute",  # Executing a plan
    "task",  # System task
    "question",  # System question
    "capability",  # Capability execution
]


class PolicyEvaluationRequest(BaseModel):
    """Request to evaluate a policy.

    Contains all context needed to evaluate policy rules against
    a pending operation.
    """

    model_config = ConfigDict(frozen=True)

    operation_type: OperationType = Field(description="Type of operation")
    command: str | None = Field(
        default=None,
        description="Command being executed",
    )
    path: str | None = Field(
        default=None,
        description="File path being accessed",
    )
    domain: str | None = Field(
        default=None,
        description="Domain of the operation",
    )
    intent: str | None = Field(
        default=None,
        description="Classified intent",
    )
    risk_level: RiskLevel | None = Field(
        default=None,
        description="Assessed risk level",
    )
    requires_privilege: bool = Field(
        default=False,
        description="Whether operation requires privileges",
    )
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="Time of evaluation",
    )


# =============================================================================
# Evaluation Result
# =============================================================================


class PolicyEvaluationResult(BaseModel):
    """Result of policy evaluation.

    Contains the decision and all matched rules with their effects.
    """

    model_config = ConfigDict(frozen=True)

    # Overall decision
    allowed: bool = Field(description="Whether the operation is allowed")
    effect: PolicyEffect = Field(description="The applied effect")

    # Match information
    matched_rules: tuple[str, ...] = Field(
        default_factory=tuple,
        description="IDs of rules that matched",
    )

    # Required actions
    requires_confirmation: bool = Field(
        default=False,
        description="User confirmation required",
    )
    requires_justification: bool = Field(
        default=False,
        description="User justification required",
    )
    requires_preview: bool = Field(
        default=False,
        description="Preview before execution required",
    )

    # User messages
    message: str | None = Field(
        default=None,
        description="Message to display to user",
    )
    justification_prompt: str | None = Field(
        default=None,
        description="Prompt for justification",
    )

    # Metadata
    evaluated_at: datetime = Field(default_factory=datetime.now)
    evaluation_time_ms: float = Field(
        default=0.0,
        description="Time taken to evaluate in milliseconds",
    )

    @property
    def should_proceed(self) -> bool:
        """Whether execution should proceed (possibly with user interaction)."""
        return self.allowed or self.requires_confirmation or self.requires_justification

    @property
    def is_blocked(self) -> bool:
        """Whether the operation is completely blocked."""
        return not self.allowed and self.effect == PolicyEffect.DENY


# =============================================================================
# Audit Entry
# =============================================================================


class PolicyAuditEntry(BaseModel):
    """Audit log entry for policy evaluation.

    Records all policy decisions for transparency and debugging.
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime = Field(default_factory=datetime.now)
    request: PolicyEvaluationRequest
    result: PolicyEvaluationResult

    # Optional additional context
    user_confirmed: bool | None = Field(
        default=None,
        description="Whether user confirmed (if confirmation was required)",
    )
    justification: str | None = Field(
        default=None,
        description="User-provided justification",
    )
    session_id: str | None = Field(
        default=None,
        description="Session identifier",
    )
