"""UI action executor with incident integration.

Executes UI task plans with full incident tracking,
adaptation on failure, and recipe confidence updates.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from elle.atspi.client import ATSPIClient, get_client
from elle.atspi.matcher import UIAdaptor, get_adaptor
from elle.atspi.models import (
    ActionResult,
    ExecutionOutcome,
    ExecutionResult,
    UIAction,
    UIRecipe,
    UITaskPlan,
)
from elle.atspi.store import (
    create_execution,
    finalize_execution,
    get_recipe,
    update_recipe,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Executor
# =============================================================================


class UIExecutor:
    """Executes UI actions with adaptation and incident logging.

    Provides full execution lifecycle:
    1. Create incident at start
    2. Execute each action with verification
    3. Adapt on failures using fuzzy matching
    4. Finalize incident with outcome
    5. Update recipe confidence

    Usage:
        executor = UIExecutor()
        result = await executor.execute_plan(plan)
    """

    def __init__(
        self,
        client: ATSPIClient | None = None,
        adaptor: UIAdaptor | None = None,
        create_incidents: bool = True,
    ) -> None:
        """Initialize the executor.

        Args:
            client: AT-SPI client.
            adaptor: UI adaptor for self-healing.
            create_incidents: Whether to create incidents.
        """
        self._client = client
        self._adaptor = adaptor
        self._create_incidents = create_incidents

    @property
    def client(self) -> ATSPIClient:
        """Get the AT-SPI client."""
        if self._client is None:
            self._client = get_client()
        return self._client

    @property
    def adaptor(self) -> UIAdaptor:
        """Get the UI adaptor."""
        if self._adaptor is None:
            self._adaptor = get_adaptor()
        return self._adaptor

    async def execute_plan(
        self,
        plan: UITaskPlan,
        adapt_on_failure: bool = True,
        dry_run: bool = False,
    ) -> ExecutionResult:
        """Execute a task plan.

        Args:
            plan: Task plan to execute.
            adapt_on_failure: Whether to try adaptation on failures.
            dry_run: If True, simulate without actual actions.

        Returns:
            ExecutionResult with outcome and details.
        """
        start_time = time.time()
        started_at = datetime.utcnow()

        # Get recipe if available
        recipe = None
        if plan.recipe_id:
            recipe = get_recipe(plan.recipe_id)

        # Create execution record
        execution = create_execution(
            recipe_id=plan.recipe_id or "unknown",
            task_request=plan.user_request,
            actions=list(plan.actions),
            incident_id=plan.incident_id,
        )

        # Create incident if enabled
        incident_id = await self._create_incident(plan) if self._create_incidents else None

        # Find application window
        app = self.client.get_application(plan.app_name)
        if not app:
            return self._build_failure_result(
                plan,
                started_at,
                start_time,
                f"Application '{plan.app_name}' not found or not accessible",
                incident_id,
            )

        window = self._get_main_window(app)
        if not window:
            return self._build_failure_result(
                plan,
                started_at,
                start_time,
                f"No window found for '{plan.app_name}'",
                incident_id,
            )

        # Execute actions
        action_results: list[ActionResult] = []
        adaptations_made = 0
        adaptation_notes: list[str] = []
        failed = False
        failed_at = None

        for i, action in enumerate(plan.actions):
            result = await self._execute_action(
                action=action,
                action_index=i,
                window=window,
                recipe=recipe,
                adapt_on_failure=adapt_on_failure,
                dry_run=dry_run,
            )

            action_results.append(result)

            if result.adapted:
                adaptations_made += 1
                method = result.adaptation_method or "unknown"
                adaptation_notes.append(
                    f"Action {i+1}: Found '{action.target_name}' via {method}"
                )

            if not result.success:
                failed = True
                failed_at = i

                # Attach failure to incident
                if incident_id:
                    await self._attach_action_to_incident(
                        incident_id, result, failed=True
                    )

                break

            # Attach success to incident
            if incident_id:
                await self._attach_action_to_incident(incident_id, result)

            # Wait between actions
            if action.wait_ms > 0 and not dry_run:
                time.sleep(action.wait_ms / 1000)

        # Determine outcome
        if failed:
            outcome: ExecutionOutcome = "failed"
            success = False
        elif adaptations_made > 0:
            outcome = "adapted"
            success = True
        else:
            outcome = "success"
            success = True

        # Calculate timing
        total_duration_ms = int((time.time() - start_time) * 1000)
        completed_at = datetime.utcnow()

        # Update recipe confidence
        if recipe:
            updated_recipe = recipe.with_updated_confidence(success)
            update_recipe(
                recipe.recipe_id,
                confidence=updated_recipe.confidence,
                last_success_at=updated_recipe.last_success_at,
                failure_count=updated_recipe.failure_count,
                success_count=updated_recipe.success_count,
            )

        # Finalize execution record
        finalize_execution(
            execution.execution_id,
            outcome=outcome,
            adaptation_notes="; ".join(adaptation_notes) if adaptation_notes else None,
        )

        # Finalize incident
        if incident_id:
            await self._finalize_incident(incident_id, outcome, success)

        # Generate user explanation
        user_explanation = self._generate_explanation(
            plan, action_results, outcome, adaptation_notes
        )

        return ExecutionResult(
            task_id=plan.task_id,
            plan=plan,
            outcome=outcome,
            success=success,
            action_results=tuple(action_results),
            actions_completed=sum(1 for r in action_results if r.success),
            actions_total=len(plan.actions),
            adaptations_made=adaptations_made,
            adaptation_notes=tuple(adaptation_notes),
            error=action_results[-1].error if failed and action_results else None,
            failed_at_action=failed_at,
            total_duration_ms=total_duration_ms,
            started_at=started_at,
            completed_at=completed_at,
            incident_id=incident_id,
            user_explanation=user_explanation,
        )

    def execute_plan_sync(
        self,
        plan: UITaskPlan,
        adapt_on_failure: bool = True,
        dry_run: bool = False,
    ) -> ExecutionResult:
        """Synchronous version of execute_plan.

        Args:
            plan: Task plan to execute.
            adapt_on_failure: Whether to try adaptation.
            dry_run: Simulate without actions.

        Returns:
            ExecutionResult.
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(
            self.execute_plan(plan, adapt_on_failure, dry_run)
        )

    async def _execute_action(
        self,
        action: UIAction,
        action_index: int,
        window: Any,
        recipe: UIRecipe | None,
        adapt_on_failure: bool,
        dry_run: bool,
    ) -> ActionResult:
        """Execute a single UI action.

        Args:
            action: Action to execute.
            action_index: Index in plan.
            window: Application window.
            recipe: Optional recipe.
            adapt_on_failure: Try adaptation.
            dry_run: Simulate only.

        Returns:
            ActionResult.
        """
        start_time = time.time()

        # Find element
        match_result, explanation = self.adaptor.adapt_and_find(
            root=window,
            target_name=action.target_name,
            target_role=action.target_role,
            expected_path=action.target_path,
            fallback_patterns=action.fallback_patterns,
            sibling_context=(),
            recipe_elements=recipe.elements if recipe else (),
        )

        if not match_result.found:
            duration_ms = int((time.time() - start_time) * 1000)
            return ActionResult(
                action=action,
                action_index=action_index,
                success=False,
                error=match_result.error or f"Element '{action.target_name}' not found",
                element_found=False,
                duration_ms=duration_ms,
            )

        element_found = True
        adapted = match_result.method != "direct_path"
        adaptation_method = match_result.method if adapted else None

        # Dry run - skip actual action
        if dry_run:
            duration_ms = int((time.time() - start_time) * 1000)
            return ActionResult(
                action=action,
                action_index=action_index,
                success=True,
                element_found=True,
                element_path_used=match_result.actual_path,
                state_after=match_result.element.states if match_result.element else (),
                adapted=adapted,
                adaptation_method=adaptation_method,
                duration_ms=duration_ms,
            )

        # Get accessible element
        accessible = self.client._follow_path(window, match_result.actual_path)
        if not accessible:
            duration_ms = int((time.time() - start_time) * 1000)
            return ActionResult(
                action=action,
                action_index=action_index,
                success=False,
                error="Failed to get accessible for found element",
                element_found=True,
                element_path_used=match_result.actual_path,
                adapted=adapted,
                adaptation_method=adaptation_method,
                duration_ms=duration_ms,
            )

        # Execute action based on type
        action_success = False
        error = None

        try:
            if action.action_type in ("click", "toggle"):
                action_success = self.client.do_action(accessible, "click")
                if not action_success:
                    action_success = self.client.do_action(accessible, "activate")
                if not action_success:
                    action_success = self.client.do_action(accessible, "toggle")

            elif action.action_type == "type":
                if action.text_input:
                    action_success = self.client.type_text(accessible, action.text_input)
                else:
                    error = "No text_input provided for type action"

            elif action.action_type == "focus":
                action_success = self.client.focus_element(accessible)

            elif action.action_type == "select":
                action_success = self.client.do_action(accessible, "activate")

            else:
                error = f"Unknown action type: {action.action_type}"

        except Exception as e:
            error = str(e)
            action_success = False

        # Verify state if requested
        state_after = ()
        verification_passed = None

        if action_success:
            # Wait a bit for state to update
            time.sleep(0.05)
            state_after = self.client.get_state(accessible)

            if action.verify_state:
                verification_passed = action.verify_state.lower() in [
                    s.lower() for s in state_after
                ]
                if not verification_passed:
                    # Check for common variations
                    if action.verify_state.lower() == "unchecked":
                        verification_passed = "checked" not in [s.lower() for s in state_after]
                    elif action.verify_state.lower() == "off":
                        verification_passed = "checked" not in [s.lower() for s in state_after]

        duration_ms = int((time.time() - start_time) * 1000)

        return ActionResult(
            action=action,
            action_index=action_index,
            success=action_success and (verification_passed is None or verification_passed),
            error=error,
            element_found=element_found,
            element_path_used=match_result.actual_path,
            state_after=state_after,
            verification_passed=verification_passed,
            adapted=adapted,
            adaptation_method=adaptation_method,
            duration_ms=duration_ms,
        )

    def _get_main_window(self, app: Any) -> Any | None:
        """Get main window from application.

        Args:
            app: AT-SPI application.

        Returns:
            Window accessible.
        """
        try:
            n_children = app.get_child_count() if hasattr(app, "get_child_count") else 0

            for i in range(n_children):
                child = app.get_child_at_index(i) if hasattr(app, "get_child_at_index") else None
                if child:
                    role = child.get_role() if hasattr(child, "get_role") else None
                    role_name = str(role).lower() if role else ""
                    if "window" in role_name or "frame" in role_name:
                        return child

            if n_children > 0:
                return app.get_child_at_index(0)

        except Exception as e:
            logger.debug(f"Error getting window: {e}")

        return None

    async def _create_incident(self, plan: UITaskPlan) -> str | None:
        """Create an incident for this execution.

        Args:
            plan: Task plan.

        Returns:
            Incident ID.
        """
        try:
            from elle.daemon.incidents.store import create_incident_draft

            incident = create_incident_draft(
                title=f"GUI automation: {plan.user_request}",
                domain="gui",  # type: ignore  # New domain
                severity="info",
                trigger_source="gui_automation",  # type: ignore
            )
            return incident.incident_id

        except Exception as e:
            logger.debug(f"Could not create incident: {e}")
            return None

    async def _attach_action_to_incident(
        self,
        incident_id: str,
        result: ActionResult,
        failed: bool = False,
    ) -> None:
        """Attach action result to incident.

        Args:
            incident_id: Incident UUID.
            result: Action result.
            failed: Whether action failed.
        """
        try:
            from elle.daemon.incidents.store import append_action

            append_action(
                incident_id=incident_id,
                kind="gui",  # type: ignore
                command=f"gui.{result.action.action_type} '{result.action.target_name}'",
                payload={
                    "target_name": result.action.target_name,
                    "target_role": result.action.target_role,
                    "adapted": result.adapted,
                    "adaptation_method": result.adaptation_method,
                },
                success=result.success,
                stdout="; ".join(result.state_after) if result.state_after else None,
                stderr=result.error,
                duration_ms=result.duration_ms,
            )

        except Exception as e:
            logger.debug(f"Could not attach action to incident: {e}")

    async def _finalize_incident(
        self,
        incident_id: str,
        outcome: ExecutionOutcome,
        success: bool,
    ) -> None:
        """Finalize incident with outcome.

        Args:
            incident_id: Incident UUID.
            outcome: Execution outcome.
            success: Whether execution succeeded.
        """
        try:
            from elle.daemon.incidents.store import finalize_outcome

            incident_outcome = "improved" if success else "no_change"
            finalize_outcome(
                incident_id=incident_id,
                outcome=incident_outcome,
            )

        except Exception as e:
            logger.debug(f"Could not finalize incident: {e}")

    def _build_failure_result(
        self,
        plan: UITaskPlan,
        started_at: datetime,
        start_time: float,
        error: str,
        incident_id: str | None,
    ) -> ExecutionResult:
        """Build a failure result.

        Args:
            plan: Task plan.
            started_at: Start datetime.
            start_time: Start time for duration.
            error: Error message.
            incident_id: Incident ID.

        Returns:
            ExecutionResult.
        """
        total_duration_ms = int((time.time() - start_time) * 1000)

        return ExecutionResult(
            task_id=plan.task_id,
            plan=plan,
            outcome="failed",
            success=False,
            action_results=(),
            actions_completed=0,
            actions_total=len(plan.actions),
            error=error,
            total_duration_ms=total_duration_ms,
            started_at=started_at,
            completed_at=datetime.utcnow(),
            incident_id=incident_id,
            user_explanation=f"Failed: {error}",
        )

    def _generate_explanation(
        self,
        plan: UITaskPlan,
        results: list[ActionResult],
        outcome: ExecutionOutcome,
        adaptation_notes: list[str],
    ) -> str:
        """Generate user-friendly explanation.

        Args:
            plan: Task plan.
            results: Action results.
            outcome: Execution outcome.
            adaptation_notes: Adaptation descriptions.

        Returns:
            Explanation string.
        """
        if outcome == "success":
            return f"Completed: {plan.expected_outcome}"

        if outcome == "adapted":
            notes = " ".join(adaptation_notes[:2])
            return f"Completed with adaptation: {notes}"

        if outcome == "failed":
            failed_result = next((r for r in results if not r.success), None)
            if failed_result:
                return f"Failed at '{failed_result.action.target_name}': {failed_result.error}"
            return "Failed to complete request"

        return f"Outcome: {outcome}"


# =============================================================================
# Module-level instance
# =============================================================================


_executor: UIExecutor | None = None


def get_executor() -> UIExecutor:
    """Get the shared UIExecutor instance."""
    global _executor
    if _executor is None:
        _executor = UIExecutor()
    return _executor
