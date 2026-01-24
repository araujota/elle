"""Policy-specific exception classes.

Custom exceptions for policy loading, parsing, and evaluation.
"""

from __future__ import annotations


class PolicyError(Exception):
    """Base exception for all policy-related errors."""

    pass


class PolicyLoadError(PolicyError):
    """Raised when a policy file cannot be loaded."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Failed to load policy from {path}: {reason}")


class PolicyParseError(PolicyError):
    """Raised when a policy file has invalid syntax or structure."""

    def __init__(self, path: str, reason: str, line: int | None = None) -> None:
        self.path = path
        self.reason = reason
        self.line = line
        location = f" at line {line}" if line else ""
        super().__init__(f"Failed to parse policy {path}{location}: {reason}")


class PolicyValidationError(PolicyError):
    """Raised when a policy fails semantic validation."""

    def __init__(self, rule_id: str, reason: str) -> None:
        self.rule_id = rule_id
        self.reason = reason
        super().__init__(f"Invalid rule '{rule_id}': {reason}")


class PolicyEvaluationError(PolicyError):
    """Raised when policy evaluation fails unexpectedly."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Policy evaluation failed: {reason}")


class PolicyCircularExtendError(PolicyError):
    """Raised when policy files create a circular extends dependency."""

    def __init__(self, chain: tuple[str, ...]) -> None:
        self.chain = chain
        super().__init__(f"Circular policy extends: {' -> '.join(chain)}")
