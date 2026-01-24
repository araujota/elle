"""Capability Executor - orchestrates the capability lifecycle.

Provides the main entry point for executing capabilities with:
- Policy checks
- Dry run previews
- User confirmation
- Execution
- Verification
- Incident recording
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from pydantic import BaseModel

from elle.capabilities.exceptions import (
    CapabilityCancelledError,
    CapabilityDeniedError,
    CapabilityExecutionError,
    CapabilityInputError,
    CapabilityNotFoundError,
)
from elle.capabilities.models import (
    CapabilityEvidence,
    CapabilityResult,
    DryRunResult,
    VerificationResult,
)
from elle.capabilities.policy import (
    capability_requires_confirmation,
    evaluate_capability,
)
from elle.capabilities.registry import CapabilityRegistry, get_registry

if TYPE_CHECKING:
    from elle.capabilities.protocol import Capability
    from elle.daemon.incidents.store import IncidentStore

logger = logging.getLogger(__name__)


# Type alias for confirmation callback
ConfirmCallback = Callable[[DryRunResult], Awaitable[bool]]


class CapabilityExecutor:
    """Orchestrates the capability lifecycle.

    Handles the complete flow:
    1. Resolve capability from registry
    2. Validate input against schema
    3. Check policy
    4. Run dry_run()
    5. Get user confirmation (if required)
    6. Execute run()
    7. Run verify()
    8. Record to incident vault
    9. Return result (or rollback on failure)

    Usage:
        executor = CapabilityExecutor()

        result = await executor.execute(
            "service.restart",
            ServiceRestartInput(service="nginx"),
            confirm_callback=my_confirm_function,
        )

        if result.success:
            print("Service restarted")
    """

    def __init__(
        self,
        registry: CapabilityRegistry | None = None,
        incident_store: "IncidentStore | None" = None,
    ) -> None:
        """Initialize the executor.

        Args:
            registry: Capability registry (uses default if None).
            incident_store: Incident store for recording (optional).
        """
        self.registry = registry or get_registry()
        self.incident_store = incident_store

    async def execute(
        self,
        capability_name: str,
        input: BaseModel,
        *,
        incident_id: str | None = None,
        require_confirmation: bool = True,
        confirm_callback: ConfirmCallback | None = None,
        skip_verification: bool = False,
        auto_rollback_on_verify_fail: bool = False,
    ) -> CapabilityResult:
        """Execute a capability with full lifecycle.

        Args:
            capability_name: Name of the capability (e.g., 'service.restart').
            input: Typed input parameters.
            incident_id: Optional incident ID for recording.
            require_confirmation: Whether to require user confirmation.
            confirm_callback: Async function to get user confirmation.
            skip_verification: Whether to skip post-execution verification.
            auto_rollback_on_verify_fail: Whether to auto-rollback on verify failure.

        Returns:
            CapabilityResult with execution outcome.

        Raises:
            CapabilityNotFoundError: If capability not found.
            CapabilityInputError: If input validation fails.
            CapabilityDeniedError: If policy denies execution.
            CapabilityCancelledError: If user cancels.
            CapabilityExecutionError: If execution fails.
        """
        start_time = time.time()

        # 1. Resolve capability from registry
        capability = self.registry.get(capability_name)
        if not capability:
            raise CapabilityNotFoundError(capability_name)

        spec = capability.spec
        logger.info(f"Executing capability: {spec.name}")

        # 2. Validate input against schema
        if spec.input_schema_name:
            # Input should already be validated by Pydantic
            if not isinstance(input, BaseModel):
                raise CapabilityInputError(
                    f"Input must be a Pydantic model, got {type(input)}"
                )

        # 3. Check policy
        policy_result = evaluate_capability(capability, input)
        if hasattr(policy_result, "is_blocked") and policy_result.is_blocked:
            message = getattr(policy_result, "message", "Capability denied by policy")
            raise CapabilityDeniedError(
                message,
                capability_name=spec.name,
            )

        # 4. Dry run
        try:
            dry_run_result = capability.dry_run(input)
        except Exception as e:
            logger.error(f"Dry run failed: {e}")
            dry_run_result = DryRunResult(
                is_valid=False,
                validation_errors=(str(e),),
                preview_text=f"Dry run failed: {e}",
            )

        # Check if dry run indicates invalid operation
        if not dry_run_result.is_valid:
            return CapabilityResult(
                success=False,
                error="; ".join(dry_run_result.validation_errors),
                warnings=(),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale="Dry run validation failed",
                ),
            )

        # 5. Confirmation
        needs_confirm = (
            require_confirmation
            and (
                getattr(policy_result, "requires_confirmation", False)
                or capability_requires_confirmation(spec)
                or dry_run_result.requires_confirmation
            )
        )

        if needs_confirm:
            if confirm_callback:
                try:
                    confirmed = await confirm_callback(dry_run_result)
                    if not confirmed:
                        raise CapabilityCancelledError("User cancelled")
                except CapabilityCancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Confirmation callback failed: {e}")
                    raise CapabilityCancelledError(
                        f"Confirmation failed: {e}"
                    ) from e
            else:
                # No callback provided but confirmation required
                logger.warning(
                    f"Capability {spec.name} requires confirmation but no callback provided"
                )

        # 6. Execute
        try:
            result = capability.run(input)
        except Exception as e:
            logger.exception(f"Capability execution failed: {e}")
            raise CapabilityExecutionError(
                str(e),
                capability_name=spec.name,
            ) from e

        # 7. Verify
        if not skip_verification and result.success:
            try:
                verification = capability.verify(input)
            except Exception as e:
                logger.warning(f"Verification failed with exception: {e}")
                verification = VerificationResult(
                    passed=False,
                    discrepancies=(str(e),),
                )

            # Update result with verification info
            updated_evidence = CapabilityEvidence(
                commands_executed=result.evidence.commands_executed,
                files_modified=result.evidence.files_modified,
                verification_passed=verification.passed,
                verification_details=str(verification.discrepancies)
                if verification.discrepancies
                else "All checks passed",
                man_vault_citations=result.evidence.man_vault_citations,
                rationale=result.evidence.rationale,
            )
            result = CapabilityResult(
                success=result.success,
                output=result.output,
                error=result.error,
                warnings=result.warnings,
                side_effects_applied=result.side_effects_applied,
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=updated_evidence,
            )

            # 8. Auto-rollback on verification failure (optional)
            if not verification.passed and auto_rollback_on_verify_fail:
                logger.warning(
                    f"Verification failed for {spec.name}, attempting rollback"
                )
                try:
                    rollback_result = capability.rollback(input, result)
                    if rollback_result.success:
                        logger.info(f"Rollback succeeded for {spec.name}")
                    else:
                        logger.error(
                            f"Rollback failed for {spec.name}: {rollback_result.error}"
                        )
                except Exception as e:
                    logger.exception(f"Rollback failed with exception: {e}")

        # 9. Record to incident vault
        if self.incident_store and incident_id:
            try:
                await self._record_action(incident_id, capability, input, result)
            except Exception as e:
                logger.warning(f"Failed to record action to incident vault: {e}")

        return result

    async def dry_run(
        self,
        capability_name: str,
        input: BaseModel,
    ) -> DryRunResult:
        """Run only the dry_run phase of a capability.

        Args:
            capability_name: Name of the capability.
            input: Typed input parameters.

        Returns:
            DryRunResult with preview information.

        Raises:
            CapabilityNotFoundError: If capability not found.
        """
        capability = self.registry.get(capability_name)
        if not capability:
            raise CapabilityNotFoundError(capability_name)

        return capability.dry_run(input)

    async def verify(
        self,
        capability_name: str,
        input: BaseModel,
    ) -> VerificationResult:
        """Run only the verification phase of a capability.

        Args:
            capability_name: Name of the capability.
            input: Typed input parameters.

        Returns:
            VerificationResult with state comparison.

        Raises:
            CapabilityNotFoundError: If capability not found.
        """
        capability = self.registry.get(capability_name)
        if not capability:
            raise CapabilityNotFoundError(capability_name)

        return capability.verify(input)

    async def _record_action(
        self,
        incident_id: str,
        capability: "Capability",
        input: BaseModel,
        result: CapabilityResult,
    ) -> None:
        """Record capability execution to incident vault.

        Args:
            incident_id: The incident ID.
            capability: The capability that was executed.
            input: The input parameters.
            result: The execution result.
        """
        try:
            from elle.daemon.incidents.models import IncidentAction
            from elle.daemon.incidents.store import append_action

            spec = capability.spec

            # Create action record
            append_action(
                incident_id,
                kind="capability",
                command=spec.name,
                payload={
                    "input": input.model_dump() if hasattr(input, "model_dump") else {},
                    "spec_name": spec.name,
                    "spec_domain": spec.domain,
                    "spec_risk": spec.risk,
                },
                stdout=result.evidence.verification_details,
                stderr=result.error,
                success=result.success,
            )

        except ImportError:
            logger.debug("Incident store not available")
        except Exception as e:
            logger.warning(f"Failed to record action: {e}")


# =============================================================================
# Module-level convenience functions
# =============================================================================

_executor: CapabilityExecutor | None = None


def get_executor() -> CapabilityExecutor:
    """Get the shared executor instance.

    Returns:
        The global CapabilityExecutor singleton.
    """
    global _executor
    if _executor is None:
        _executor = CapabilityExecutor()
    return _executor


def reset_executor() -> None:
    """Reset the global executor instance.

    Useful for testing.
    """
    global _executor
    _executor = None


async def execute_capability(
    capability_name: str,
    input: BaseModel,
    **kwargs,
) -> CapabilityResult:
    """Execute a capability (convenience function).

    Args:
        capability_name: Name of the capability.
        input: Typed input parameters.
        **kwargs: Additional arguments passed to execute().

    Returns:
        CapabilityResult.
    """
    return await get_executor().execute(capability_name, input, **kwargs)
