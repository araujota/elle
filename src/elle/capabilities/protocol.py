"""Capability protocol definition.

Defines the interface that all capabilities must implement.
Uses Protocol for structural subtyping to allow flexible implementations.
"""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from elle.capabilities.models import (
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    RollbackResult,
    VerificationResult,
)

# Type variables for generic capability typing
# TInput is contravariant (used in method parameters)
# TOutput is covariant (used in return types)
TInput = TypeVar("TInput", bound=BaseModel, contravariant=True)
TOutput = TypeVar("TOutput", bound=BaseModel, covariant=True)


@runtime_checkable
class Capability(Protocol[TInput, TOutput]):
    """Protocol that all capabilities must implement.

    A capability is a named, typed, policy-governed operation with:
    - Metadata (spec) describing what it does
    - dry_run() to preview changes
    - run() to execute the operation
    - verify() to confirm success
    - rollback() to undo changes

    Capabilities provide a structured alternative to raw shell commands,
    enabling policy checks on semantics rather than strings.

    Example implementation:
        class ServiceRestartCapability:
            @property
            def spec(self) -> CapabilitySpec:
                return CapabilitySpec(
                    name="service.restart",
                    summary="Restart a systemd service",
                    domain="service",
                    risk="medium",
                    requires_privilege=True,
                    ...
                )

            def dry_run(self, input: ServiceRestartInput) -> DryRunResult:
                ...

            def run(self, input: ServiceRestartInput) -> CapabilityResult:
                ...
    """

    @property
    def spec(self) -> CapabilitySpec:
        """Return capability metadata.

        The spec describes everything about this capability:
        - Name and summary
        - Domain and risk level
        - Side effects
        - Privilege requirements
        - Input/output schemas

        Returns:
            CapabilitySpec with all metadata.
        """
        ...

    def dry_run(self, input: TInput) -> DryRunResult:
        """Preview what would happen without executing.

        This method should:
        - Check current system state
        - Validate the input
        - Determine what commands would run
        - Generate diffs if applicable
        - Estimate risk level

        Args:
            input: Typed input parameters.

        Returns:
            DryRunResult with preview information.
        """
        ...

    def run(self, input: TInput) -> CapabilityResult:
        """Execute the capability.

        Preconditions:
        - Policy must have been checked
        - dry_run should have been shown to user (if required)

        This method should:
        - Execute the operation
        - Capture evidence for incident recording
        - Handle errors gracefully

        Args:
            input: Typed input parameters.

        Returns:
            CapabilityResult with execution outcome.
        """
        ...

    def verify(self, input: TInput) -> VerificationResult:
        """Verify the operation achieved its goal.

        Called after run() to confirm state change occurred.
        Should check actual system state against expected state.

        Args:
            input: Same input used for run().

        Returns:
            VerificationResult with pass/fail and state comparison.
        """
        ...

    def rollback(self, input: TInput, result: CapabilityResult) -> RollbackResult:
        """Reverse the operation if possible.

        Uses evidence from run() to undo changes.
        Not all capabilities support rollback.

        Args:
            input: Same input used for run().
            result: Result from the run() that should be undone.

        Returns:
            RollbackResult with rollback outcome.
        """
        ...


class BaseCapability(Generic[TInput, TOutput]):
    """Base class providing default implementations for capabilities.

    Provides sensible defaults for verify() and rollback() that
    subclasses can override. Subclasses must implement:
    - spec property
    - dry_run()
    - run()

    Type Parameters:
        TInput: The Pydantic model type for capability input
        TOutput: The Pydantic model type for capability output
    """

    @property
    def spec(self) -> CapabilitySpec:
        """Return capability metadata. Must be overridden."""
        raise NotImplementedError("Subclasses must implement spec property")

    def dry_run(self, input: TInput) -> DryRunResult:
        """Preview changes. Must be overridden."""
        raise NotImplementedError("Subclasses must implement dry_run()")

    def run(self, input: TInput) -> CapabilityResult:
        """Execute capability. Must be overridden."""
        raise NotImplementedError("Subclasses must implement run()")

    def verify(self, input: TInput) -> VerificationResult:
        """Default verification - returns passed with no checks.

        Subclasses should override for meaningful verification.
        """
        return VerificationResult(
            passed=True,
            checks_performed=(),
            actual_state={},
            expected_state={},
            discrepancies=(),
        )

    def rollback(
        self,
        input: TInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Default rollback - returns failure (not supported).

        Subclasses should override to provide rollback functionality.
        """
        return RollbackResult(
            success=False,
            error="Rollback not supported for this capability",
            rolled_back=(),
            failed_to_rollback=(),
            commands_executed=(),
        )
