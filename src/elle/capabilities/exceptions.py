"""Exceptions for the Capabilities system.

Defines a hierarchy of exceptions for capability-related errors.
"""

from __future__ import annotations


class CapabilityError(Exception):
    """Base exception for all capability errors."""

    pass


class CapabilityNotFoundError(CapabilityError):
    """Raised when a capability cannot be found in the registry."""

    def __init__(self, capability_name: str) -> None:
        self.capability_name = capability_name
        super().__init__(f"Capability not found: {capability_name}")


class CapabilityInputError(CapabilityError):
    """Raised when capability input validation fails."""

    def __init__(self, message: str, field: str | None = None) -> None:
        self.field = field
        super().__init__(message)


class CapabilityDeniedError(CapabilityError):
    """Raised when policy denies capability execution."""

    def __init__(
        self,
        message: str,
        capability_name: str | None = None,
        rule_id: str | None = None,
    ) -> None:
        self.capability_name = capability_name
        self.rule_id = rule_id
        super().__init__(message)


class CapabilityCancelledError(CapabilityError):
    """Raised when user cancels capability execution."""

    def __init__(self, message: str = "User cancelled execution") -> None:
        super().__init__(message)


class CapabilityExecutionError(CapabilityError):
    """Raised when capability execution fails."""

    def __init__(
        self,
        message: str,
        capability_name: str | None = None,
        exit_code: int | None = None,
        stderr: str | None = None,
    ) -> None:
        self.capability_name = capability_name
        self.exit_code = exit_code
        self.stderr = stderr
        super().__init__(message)


class CapabilityRegistrationError(CapabilityError):
    """Raised when capability registration fails."""

    def __init__(self, message: str, capability_name: str | None = None) -> None:
        self.capability_name = capability_name
        super().__init__(message)
