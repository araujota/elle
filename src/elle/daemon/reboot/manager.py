"""Reboot Manager - Orchestrates safe reboot operations.

Manages the complete reboot lifecycle:
1. Pre-reboot: Save intent, capture snapshot, configure GRUB
2. Reboot execution via Polkit
3. Post-reboot: Verify system health, confirm or rollback

SAFETY-CRITICAL:
- No silent reboots - always explicit confirmation
- GRUB fallback on boot failure
- Maximum 3 verification retries
- 60-second user intervention window
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from elle.daemon.reboot.grub import (
    clear_grub_oneshot,
    confirm_boot_success,
    get_boot_id,
    get_grub_state,
    has_rebooted_since,
    prepare_rollback,
    set_grub_oneshot,
    trigger_rollback_reboot,
)
from elle.daemon.reboot.models import (
    AUTO_ROLLBACK_DELAY_SEC,
    STALE_REBOOT_THRESHOLD_HOURS,
    PendingVerification,
    RebootIntent,
    RebootReason,
)
from elle.daemon.reboot.store import (
    cancel_intent,
    create_intent,
    get_active_intent,
    get_intent,
    get_intents_by_status,
    mark_rebooting,
    update_intent_status,
    update_verification_result,
)
from elle.daemon.reboot.verifier import (
    collect_failure_diagnostics,
    run_verification_with_retry,
)

logger = logging.getLogger(__name__)


def _notify_reboot(status: str, goal: str, intent_id: str, details: str | None = None) -> None:
    """Send a reboot notification (non-blocking)."""
    try:
        from elle.daemon.notifications import notify_reboot_status

        notify_reboot_status(status, goal, intent_id, details)
    except ImportError:
        logger.debug("Notifications module not available")
    except Exception as e:
        logger.warning(f"Failed to send reboot notification: {e}")


class RebootManagerError(Exception):
    """Error during reboot management operation."""

    pass


class RebootManager:
    """Orchestrates safe reboot operations with verification and rollback.

    Usage:
        manager = RebootManager()

        # Create reboot intent
        intent = await manager.create_reboot_intent(
            goal="Install kernel security updates",
            task_description="Update to kernel 6.8.0-45",
            reason="kernel_update",
            verifications=[...],
        )

        # Execute reboot (with user confirmation)
        if confirmed:
            await manager.execute_reboot(intent.id)

        # On next boot, daemon calls:
        await manager.check_pending_reboots()
    """

    def __init__(self) -> None:
        """Initialize the RebootManager."""
        self._rollback_task: asyncio.Task[Any] | None = None
        self._intervention_event = asyncio.Event()

    # =========================================================================
    # Pre-Reboot Flow
    # =========================================================================

    async def create_reboot_intent(
        self,
        goal: str,
        task_description: str,
        reason: RebootReason,
        reason_detail: str | None = None,
        incident_id: str | None = None,
        session_history: list[str] | None = None,
        plan_json: dict[str, Any] | None = None,
        grub_entry: str | None = None,
        verifications: list[PendingVerification] | None = None,
    ) -> RebootIntent:
        """Create a new reboot intent.

        Captures the current system state and prepares for safe reboot.

        Args:
            goal: High-level goal of this reboot.
            task_description: Detailed task description.
            reason: Why reboot is needed.
            reason_detail: Additional detail about reason.
            incident_id: Linked incident ID.
            session_history: Commands executed before reboot.
            plan_json: The plan being executed.
            grub_entry: Specific GRUB entry to boot.
            verifications: Post-boot verification checks.

        Returns:
            Created RebootIntent.

        Raises:
            RebootManagerError: If another reboot is already pending.
        """
        # Check for existing active reboot
        active = get_active_intent()
        if active:
            raise RebootManagerError(f"Another reboot is already in progress: {active.goal} (status={active.status})")

        # Capture current system state
        boot_id = get_boot_id()
        grub_state = get_grub_state()
        pre_snapshot = await self._capture_snapshot()

        # Create the intent
        intent = create_intent(
            goal=goal,
            task_description=task_description,
            reason=reason,
            reason_detail=reason_detail,
            incident_id=incident_id,
            session_history=session_history,
            plan_json=plan_json,
            pre_snapshot_json=pre_snapshot,
            boot_id=boot_id,
            grub_entry=grub_entry or grub_state.default_entry,
            grub_default_saved=grub_state.default_entry,
            grub_state=grub_state,
            verifications=verifications,
        )

        logger.info(f"Created reboot intent: {intent.id} - {goal}")
        return intent

    async def execute_reboot(
        self,
        intent_id: str,
        countdown_seconds: int = 5,
        on_countdown: Callable[[int], None] | None = None,
    ) -> None:
        """Execute a pending reboot.

        This is the point of no return - after calling this, system will reboot.

        Args:
            intent_id: ID of the reboot intent to execute.
            countdown_seconds: Countdown before reboot (for user awareness).
            on_countdown: Optional callback(seconds_remaining) for UI.

        Raises:
            RebootManagerError: If intent not found or not in valid state.
        """
        intent = get_intent(intent_id)
        if not intent:
            raise RebootManagerError(f"Reboot intent not found: {intent_id}")

        if intent.status != "pending":
            raise RebootManagerError(f"Cannot execute reboot in status: {intent.status}")

        logger.info(f"Executing reboot: {intent.goal}")

        # Prepare GRUB for potential rollback
        await prepare_rollback(intent.grub_default_saved)

        # Set GRUB one-shot boot if specified
        if intent.grub_entry:
            result = await set_grub_oneshot(intent.grub_entry)
            if not result.success:
                logger.warning(f"Failed to set GRUB one-shot: {result.error}")
                # Continue anyway - will boot to default

        # Mark intent as rebooting (last write before reboot)
        current_boot_id = get_boot_id()
        intent = mark_rebooting(intent_id, current_boot_id or "unknown")
        if not intent:
            raise RebootManagerError("Failed to mark intent as rebooting")

        # Countdown
        for remaining in range(countdown_seconds, 0, -1):
            if on_countdown:
                on_countdown(remaining)
            await asyncio.sleep(1)

        # Execute reboot via Polkit
        await self._execute_system_reboot()

    async def cancel_pending_reboot(
        self,
        intent_id: str,
    ) -> RebootIntent | None:
        """Cancel a pending reboot.

        Args:
            intent_id: ID of the reboot intent to cancel.

        Returns:
            Cancelled intent, or None if not cancellable.
        """
        intent = cancel_intent(intent_id)
        if intent:
            logger.info(f"Cancelled reboot intent: {intent_id}")

            # Clear any GRUB one-shot that was set
            await clear_grub_oneshot()

        return intent

    # =========================================================================
    # Post-Reboot Recovery
    # =========================================================================

    async def check_pending_reboots(self) -> RebootIntent | None:
        """Check for and process pending reboot intents.

        Called by daemon on startup to detect post-reboot state.

        Returns:
            The processed intent if any, None otherwise.
        """
        # Look for intents in 'rebooting' status
        pending = get_intents_by_status("rebooting", limit=1)
        if not pending:
            return None

        intent = pending[0]
        current_boot_id = get_boot_id()

        # Check if we actually rebooted
        if not has_rebooted_since(intent.boot_id):
            # Reboot never happened - mark as cancelled
            logger.warning(f"Reboot intent {intent.id} never executed, marking cancelled")
            return update_intent_status(
                intent.id,
                status="cancelled",
                outcome_detail="Reboot was never executed",
            )

        logger.info(f"Detected post-reboot state, processing intent: {intent.id}")

        # Update with new boot ID
        update_intent_status(
            intent.id,
            status="verifying",
            new_boot_id=current_boot_id,
        )

        # Capture post-reboot snapshot
        post_snapshot = await self._capture_snapshot()
        update_intent_status(
            intent.id,
            status="verifying",
            post_snapshot_json=post_snapshot,
        )

        # Refresh intent
        intent = get_intent(intent.id)
        if not intent:
            return None

        # Run verification
        return await self._run_post_boot_verification(intent)

    async def _run_post_boot_verification(
        self,
        intent: RebootIntent,
    ) -> RebootIntent:
        """Run post-boot verification and handle outcome.

        Args:
            intent: The reboot intent to verify.

        Returns:
            Updated intent with outcome.
        """
        logger.info(f"Running post-boot verification for: {intent.id}")

        # Run verification with retry
        success, result = await run_verification_with_retry(intent)

        # Update verification results in database
        if result and result.results:
            for check_result in result.results:
                if check_result.get("verification_id"):
                    update_verification_result(
                        verification_id=check_result["verification_id"],
                        exit_code=check_result["exit_code"],
                        stdout=check_result.get("stdout"),
                        stderr=check_result.get("stderr"),
                        passed=check_result["passed"],
                    )

        if success:
            return await self._handle_verification_success(intent)
        else:
            return await self._handle_verification_failure(intent, result)

    async def _handle_verification_success(
        self,
        intent: RebootIntent,
    ) -> RebootIntent:
        """Handle successful verification.

        Confirms GRUB boot and marks intent as completed.

        Args:
            intent: The verified reboot intent.

        Returns:
            Updated intent.
        """
        logger.info(f"Verification passed for: {intent.id}")

        # Confirm boot in GRUB (make current config permanent)
        grub_result = await confirm_boot_success()
        if not grub_result.success:
            logger.warning(f"GRUB confirmation failed: {grub_result.error}")
            # Continue anyway - boot is already successful

        # Clear one-shot
        await clear_grub_oneshot()

        # Mark as completed
        updated = update_intent_status(
            intent.id,
            status="completed",
            outcome="improved",
            outcome_detail="All verification checks passed, boot confirmed",
            verification_attempts=intent.verification_attempts + 1,
        )

        logger.info(f"Reboot completed successfully: {intent.goal}")

        # Send success notification
        _notify_reboot("success", intent.goal, intent.id)

        return updated or intent

    async def _handle_verification_failure(
        self,
        intent: RebootIntent,
        result: Any,
    ) -> RebootIntent:
        """Handle verification failure.

        Collects comprehensive diagnostics and creates/updates incident
        for LLM-assisted retry. Manages rollback based on severity.

        Args:
            intent: The failed reboot intent.
            result: Verification result.

        Returns:
            Updated intent.
        """
        logger.error(f"Verification failed for: {intent.id}")

        # Collect comprehensive diagnostics for LLM retry
        verification_results = list(result.results) if result and result.results else []
        diagnostics = await collect_failure_diagnostics(intent, verification_results)

        # Store diagnostics in the intent for later retrieval
        self._last_diagnostics = diagnostics

        # Create or update incident with failure details
        _incident_id = await self._create_or_update_failure_incident(intent, diagnostics)

        # Build detailed outcome message
        outcome_parts = []
        if result and result.error:
            outcome_parts.append(f"Verification failed: {result.error}")
        if diagnostics.likely_causes:
            outcome_parts.append(f"Likely causes: {'; '.join(diagnostics.likely_causes[:3])}")
        if diagnostics.failed_services:
            outcome_parts.append(f"Failed services: {', '.join(diagnostics.failed_services[:5])}")
        if diagnostics.read_only_mounts:
            outcome_parts.append(f"Read-only mounts: {', '.join(diagnostics.read_only_mounts)}")

        outcome_detail = " | ".join(outcome_parts) if outcome_parts else "Unknown failure"

        # Check if this is a critical failure (requires immediate rollback)
        if result and result.critical_failure:
            return await self._initiate_rollback(intent, outcome_detail)

        # Non-critical failure - start intervention window
        logger.warning(f"Starting {AUTO_ROLLBACK_DELAY_SEC}s intervention window before rollback")

        # Mark as failed with pending rollback
        update_intent_status(
            intent.id,
            status="failed",
            outcome="failed",
            outcome_detail=outcome_detail[:2000],  # Truncate if too long
            verification_attempts=intent.verification_attempts + 1,
        )

        # Send failure notification
        _notify_reboot("failed", intent.goal, intent.id, outcome_detail[:200])

        # Start rollback timer (can be cancelled by user)
        self._rollback_task = asyncio.create_task(self._delayed_rollback(intent))

        return get_intent(intent.id) or intent

    async def _create_or_update_failure_incident(
        self,
        intent: RebootIntent,
        diagnostics: Any,
    ) -> str | None:
        """Create or update an incident for the failed reboot.

        The incident captures all diagnostic information so the LLM can
        propose a fix when ELLE resumes after rollback.

        Args:
            intent: The failed reboot intent.
            diagnostics: FailureDiagnostics with collected information.

        Returns:
            Incident ID if created/updated successfully.
        """
        try:
            from elle.daemon.incidents.snapshot import collect_snapshot
            from elle.daemon.incidents.store import (
                append_action,
                create_incident_draft,
                get_incident,
            )

            # Build symptoms from diagnostics
            symptoms = []
            if diagnostics.failed_checks:
                for check in diagnostics.failed_checks[:5]:
                    symptoms.append(f"Verification failed: {check.get('check_type')} - {check.get('check_command')}")
            if diagnostics.dmesg_errors:
                symptoms.extend(diagnostics.dmesg_errors[:5])
            if diagnostics.journal_errors:
                symptoms.extend(diagnostics.journal_errors[:5])

            # Determine domain based on failure type
            domain = "service"  # Default
            if diagnostics.read_only_mounts or diagnostics.missing_mounts:
                domain = "fs"
            elif diagnostics.interfaces_down or diagnostics.network_errors:
                domain = "net"
            elif diagnostics.missing_modules or diagnostics.module_errors:
                domain = "driver"
            elif intent.reason == "kernel_update":
                domain = "kernel"

            # Check if we're updating an existing incident
            if intent.incident_id:
                # Update existing incident
                incident = get_incident(intent.incident_id)
                if incident:
                    # Add the failure as an action
                    append_action(
                        incident.incident_id,
                        kind="reboot_failed",
                        command=f"Reboot for: {intent.goal}",
                        stdout=diagnostics.to_llm_context(),
                        stderr="\n".join(diagnostics.journal_errors[:20]),
                        exit_code=1,
                        success=False,
                    )
                    return intent.incident_id

            # Create new incident
            incident = create_incident_draft(
                title=f"Reboot failed: {intent.goal}",
                domain=domain,
                severity="error",
                trigger_source="reboot_failure",
                trigger_command=f"Reboot intent: {intent.id}",
                symptoms=tuple(symptoms),
                hypothesis=f"The reboot for '{intent.goal}' failed verification. "
                f"Likely causes: {'; '.join(diagnostics.likely_causes[:3]) if diagnostics.likely_causes else 'Unknown'}",
            )

            # Attach pre-reboot snapshot if available
            if intent.pre_snapshot_json:
                from elle.daemon.incidents.models import SystemSnapshot
                from elle.daemon.incidents.store import attach_snapshot

                try:
                    pre_snapshot = SystemSnapshot(**intent.pre_snapshot_json)
                    attach_snapshot(incident.incident_id, "pre", pre_snapshot)
                except Exception:
                    pass

            # Attach current snapshot
            try:
                current_snapshot = collect_snapshot()
                from elle.daemon.incidents.store import attach_snapshot

                attach_snapshot(incident.incident_id, "post", current_snapshot)
            except Exception:
                pass

            # Add the failure details as an action
            append_action(
                incident.incident_id,
                kind="reboot_failed",
                command=f"Reboot for: {intent.goal}",
                stdout=diagnostics.to_llm_context(),
                stderr="\n".join(diagnostics.journal_errors[:20]),
                exit_code=1,
                success=False,
            )

            # Update intent with incident ID
            update_intent_status(
                intent.id,
                status=intent.status,
                incident_id=incident.incident_id,
            )

            logger.info(f"Created failure incident: {incident.incident_id}")
            return incident.incident_id

        except ImportError:
            logger.debug("Incident module not available, skipping incident creation")
            return None
        except Exception as e:
            logger.warning(f"Failed to create failure incident: {e}")
            return None

    def get_last_diagnostics(self) -> Any | None:
        """Get diagnostics from the last failed reboot.

        Returns:
            FailureDiagnostics if available, None otherwise.
        """
        return getattr(self, "_last_diagnostics", None)

    def get_llm_retry_context(self) -> str | None:
        """Get formatted context for LLM retry after failed reboot.

        Call this when ELLE resumes after a rollback to get the
        diagnostic context needed for the LLM to propose a fix.

        Returns:
            Formatted string for LLM prompt injection, or None.
        """
        diagnostics = self.get_last_diagnostics()
        if diagnostics:
            return diagnostics.to_llm_context()
        return None

    async def _initiate_rollback(
        self,
        intent: RebootIntent,
        reason: str | None,
    ) -> RebootIntent:
        """Initiate immediate rollback.

        Args:
            intent: The failed reboot intent.
            reason: Reason for rollback.

        Returns:
            Updated intent.
        """
        logger.error(f"Initiating rollback for: {intent.id} - {reason}")

        # Set rollback target
        fallback = intent.grub_default_saved or "0"

        # Configure GRUB for rollback
        result = await trigger_rollback_reboot(fallback)
        if not result.success:
            logger.error(f"Failed to configure rollback: {result.error}")
            # Mark as failed, manual intervention needed
            return (
                update_intent_status(
                    intent.id,
                    status="failed",
                    outcome="failed",
                    outcome_detail=f"Rollback configuration failed: {result.error}",
                )
                or intent
            )

        # Mark as rolling back
        update_intent_status(
            intent.id,
            status="rolled_back",
            outcome="rolled_back",
            outcome_detail=f"Automatic rollback to: {fallback}. Reason: {reason}",
        )

        # Send rollback notification
        _notify_reboot("rollback", intent.goal, intent.id, reason)

        # Execute rollback reboot
        logger.warning("Executing rollback reboot!")
        await self._execute_system_reboot()

        return get_intent(intent.id) or intent

    async def _delayed_rollback(
        self,
        intent: RebootIntent,
    ) -> None:
        """Wait for intervention window then rollback.

        Can be cancelled by calling cancel_pending_rollback().

        Args:
            intent: The failed reboot intent.
        """
        try:
            # Wait for intervention or timeout
            try:
                await asyncio.wait_for(
                    self._intervention_event.wait(),
                    timeout=AUTO_ROLLBACK_DELAY_SEC,
                )
                # Intervention occurred - don't rollback
                logger.info("Rollback cancelled by user intervention")
                return
            except TimeoutError:
                pass

            # Timeout - proceed with rollback
            await self._initiate_rollback(
                intent,
                f"No user intervention within {AUTO_ROLLBACK_DELAY_SEC}s",
            )

        except asyncio.CancelledError:
            logger.info("Rollback task cancelled")

    async def cancel_pending_rollback(self) -> bool:
        """Cancel a pending automatic rollback.

        Returns:
            True if rollback was cancelled.
        """
        if self._rollback_task and not self._rollback_task.done():
            self._intervention_event.set()
            await asyncio.sleep(0.1)  # Let task notice
            self._intervention_event.clear()
            return True
        return False

    async def force_confirm_boot(
        self,
        intent_id: str,
    ) -> RebootIntent | None:
        """Manually confirm boot success despite verification failure.

        Use when user knows the system is working but checks failed.

        Args:
            intent_id: ID of the reboot intent.

        Returns:
            Updated intent.
        """
        intent = get_intent(intent_id)
        if not intent:
            return None

        # Cancel any pending rollback
        await self.cancel_pending_rollback()

        # Confirm GRUB
        await confirm_boot_success()
        await clear_grub_oneshot()

        # Mark as completed (user override)
        return update_intent_status(
            intent_id,
            status="completed",
            outcome="improved",
            outcome_detail="Manually confirmed by user (verification overridden)",
        )

    async def force_rollback(
        self,
        intent_id: str,
    ) -> RebootIntent | None:
        """Manually trigger rollback.

        Args:
            intent_id: ID of the reboot intent.

        Returns:
            Updated intent.
        """
        intent = get_intent(intent_id)
        if not intent:
            return None

        return await self._initiate_rollback(intent, "User requested rollback")

    # =========================================================================
    # Stale Intent Cleanup
    # =========================================================================

    async def cleanup_stale_intents(self) -> int:
        """Clean up stale reboot intents.

        Marks intents that have been in 'rebooting' status for too long.

        Returns:
            Number of intents cleaned up.
        """
        threshold = datetime.utcnow() - timedelta(hours=STALE_REBOOT_THRESHOLD_HOURS)
        intents = get_intents_by_status("rebooting")
        cleaned = 0

        for intent in intents:
            if intent.updated_at < threshold:
                update_intent_status(
                    intent.id,
                    status="failed",
                    outcome="failed",
                    outcome_detail=(f"Stale intent - no boot detected within {STALE_REBOOT_THRESHOLD_HOURS} hours"),
                )
                cleaned += 1
                logger.warning(f"Cleaned up stale reboot intent: {intent.id}")

        return cleaned

    # =========================================================================
    # Helper Methods
    # =========================================================================

    async def _capture_snapshot(self) -> dict[str, Any]:
        """Capture current system snapshot.

        Returns:
            Snapshot as dictionary.
        """
        try:
            from elle.daemon.incidents.snapshot import collect_snapshot

            snapshot = collect_snapshot()
            return snapshot.model_dump()
        except ImportError:
            logger.debug("Snapshot module not available")
            return {}
        except Exception as e:
            logger.warning(f"Failed to capture snapshot: {e}")
            return {}

    async def _execute_system_reboot(self) -> None:
        """Execute system reboot via Polkit.

        This function does not return - system will reboot.
        """
        from elle.security.polkit_helper import PolkitAction, get_helper

        helper = get_helper()

        logger.warning("Executing system reboot!")

        result = await helper._run_pkexec(
            ["systemctl", "reboot"],
            PolkitAction.MANAGE_SERVICES,
        )

        if not result.success:
            raise RebootManagerError(f"Failed to execute reboot: {result.error}")

        # Should not reach here - system should be rebooting
        await asyncio.sleep(30)


# =============================================================================
# Module-level singleton
# =============================================================================

_manager: RebootManager | None = None


def get_manager() -> RebootManager:
    """Get the global RebootManager instance.

    Returns:
        RebootManager singleton.
    """
    global _manager
    if _manager is None:
        _manager = RebootManager()
    return _manager
