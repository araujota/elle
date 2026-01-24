"""System Task Planner Service.

Orchestrates the complete planning pipeline:
1. Build context (Man Vault, prior art)
2. Generate plan via LLM
3. Verify plan safety
4. Execute with rollback support
5. Run validation checks
6. Record incident outcome
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Import get_llm at module level for easier mocking
def get_llm():
    """Get the LLM instance (lazy import)."""
    from elle.rag.llm import get_llm as _get_llm
    return _get_llm()

from elle.cli.planner.models import (
    CheckResult,
    CommandPlan,
    ManDocContext,
    PlanContext,
    PlanOutcome,
    PlanResult,
    PlanStep,
    PriorPlanContext,
    RollbackStep,
    StepResult,
    TaskRequest,
    ValidationCheck,
)
from elle.cli.planner.prompts import BASE_SYSTEM_PROMPT, build_planner_prompt, get_schema_hint
from elle.cli.planner.verifier import verify_plan
from elle.cli.subprocess_runner import RunMode, run_safe
from elle.common.session import Session

logger = logging.getLogger(__name__)


class PlannerService:
    """Orchestrates system task planning and execution.

    Handles the complete flow from natural language request
    to verified plan to safe execution with rollback.
    """

    def __init__(
        self,
        use_man_vault: bool = True,
        use_incident_vault: bool = True,
        command_timeout: float = 60.0,
    ) -> None:
        """Initialize the planner service.

        Args:
            use_man_vault: Whether to use Man Vault for documentation.
            use_incident_vault: Whether to use Incident Vault for prior art.
            command_timeout: Timeout for command execution in seconds.
        """
        self.use_man_vault = use_man_vault
        self.use_incident_vault = use_incident_vault
        self.command_timeout = command_timeout

    def build_context(
        self,
        request: str,
        session: Session,
        entities: tuple[str, ...] = (),
    ) -> PlanContext:
        """Build planning context from request and vaults.

        Args:
            request: The user's task request.
            session: Current session state.
            entities: Detected entities from classification.

        Returns:
            Complete PlanContext for LLM.
        """
        task_request = TaskRequest(
            request=request,
            cwd=session.cwd,
            entities=entities,
        )

        man_docs: tuple[ManDocContext, ...] = ()
        prior_plans: tuple[PriorPlanContext, ...] = ()

        # Search Man Vault for relevant documentation
        if self.use_man_vault:
            man_docs = self._search_man_vault(request, entities)

        # Search Incident Vault for similar past tasks
        if self.use_incident_vault:
            prior_plans = self._search_prior_plans(request, entities)

        return PlanContext(
            request=task_request,
            man_docs=man_docs,
            prior_plans=prior_plans,
        )

    def generate_plan(self, context: PlanContext) -> CommandPlan | None:
        """Generate a command plan using LLM.

        Args:
            context: Complete planning context.

        Returns:
            CommandPlan or None if LLM unavailable.
        """
        try:
            llm = get_llm()
            if not llm.is_available():
                logger.warning("LLM not available for plan generation")
                return None

            prompt = build_planner_prompt(context)
            schema_hint = get_schema_hint()

            response = llm.chat_json(
                messages=[
                    {"role": "system", "content": BASE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                schema=schema_hint,
                temperature=0.3,  # Lower temp for more deterministic plans
            )

            return self._parse_plan_response(response)

        except Exception as e:
            logger.error(f"Plan generation failed: {e}")
            return None

    def run_planning_pipeline(
        self,
        request: str,
        session: Session,
        entities: tuple[str, ...] = (),
    ) -> PlanResult:
        """Run the complete planning pipeline.

        Steps:
        1. Build context
        2. Generate plan
        3. Verify plan

        Does NOT execute - that requires approval.

        Args:
            request: The user's task request.
            session: Current session state.
            entities: Detected entities from classification.

        Returns:
            PlanResult ready for approval and execution.
        """
        # Build context
        context = self.build_context(request, session, entities)
        result = PlanResult(context=context)

        # Create incident draft
        if self.use_incident_vault:
            try:
                incident_id = self._create_incident(request, context)
                result = result.with_incident_id(incident_id)
            except Exception as e:
                logger.warning(f"Failed to create incident: {e}")

        # Generate plan
        plan = self.generate_plan(context)
        if plan is None:
            logger.warning("Plan generation failed, no fallback available")
            return result.with_outcome(PlanOutcome.ERROR)

        result = result.with_plan(plan)

        # Verify plan
        verification = verify_plan(plan)
        result = result.with_verification(verification)

        return result

    def execute_plan(
        self,
        result: PlanResult,
        session: Session,
        stop_on_failure: bool = True,
    ) -> PlanResult:
        """Execute an approved plan.

        Args:
            result: Approved PlanResult.
            session: Current session state.
            stop_on_failure: Whether to stop on first failure.

        Returns:
            Updated PlanResult with execution results.
        """
        if not result.plan:
            return result.with_outcome(PlanOutcome.ERROR)

        result = result.with_approved()
        plan = result.plan
        all_succeeded = True

        # Execute each step
        for i, step in enumerate(plan.steps):
            step_result = self._execute_step(step, i, session)
            result = result.with_step_result(step_result)

            # Record action in incident
            if result.incident_id:
                try:
                    self._record_action(
                        result.incident_id,
                        step.command,
                        step_result,
                    )
                except Exception as e:
                    logger.warning(f"Failed to record action: {e}")

            if not step_result.success:
                all_succeeded = False
                if stop_on_failure and not step.can_fail:
                    logger.warning(f"Step {i + 1} failed, stopping execution")
                    break

        # Determine outcome before checks
        if all_succeeded:
            outcome = PlanOutcome.SUCCESS
        elif result.steps_succeeded > 0:
            outcome = PlanOutcome.PARTIAL
        else:
            outcome = PlanOutcome.FAILED

        result = result.with_outcome(outcome)

        # Run validation checks if we succeeded or partially succeeded
        if outcome in (PlanOutcome.SUCCESS, PlanOutcome.PARTIAL) and plan.has_checks:
            check_results = self._run_checks(plan.checks, session)
            result = result.with_check_results(check_results)

            # Update outcome based on checks
            if result.checks_failed > 0:
                result = result.with_outcome(PlanOutcome.PARTIAL)

        return result

    def rollback_plan(
        self,
        result: PlanResult,
        session: Session,
    ) -> PlanResult:
        """Execute rollback for a plan.

        Args:
            result: PlanResult to rollback.
            session: Current session state.

        Returns:
            Updated PlanResult with rollback results.
        """
        if not result.plan or not result.plan.has_rollback:
            logger.warning("No rollback available")
            return result

        rollback_results: list[StepResult] = []

        for i, rb in enumerate(result.plan.rollback):
            rb_result = self._execute_rollback_step(rb, i, session)
            rollback_results.append(rb_result)

            # Record rollback action
            if result.incident_id:
                try:
                    self._record_action(
                        result.incident_id,
                        rb.command,
                        rb_result,
                        is_rollback=True,
                    )
                except Exception:
                    pass

        result = result.with_rollback_results(tuple(rollback_results))
        result = result.with_outcome(PlanOutcome.ROLLED_BACK)

        return result

    def finalize(self, result: PlanResult) -> PlanResult:
        """Finalize the plan execution and record outcome.

        Args:
            result: Completed PlanResult.

        Returns:
            Final PlanResult.
        """
        if result.incident_id and self.use_incident_vault:
            try:
                self._finalize_incident(result)
            except Exception as e:
                logger.warning(f"Failed to finalize incident: {e}")

        return result

    # =========================================================================
    # Private Methods
    # =========================================================================

    def _search_man_vault(
        self,
        request: str,
        entities: tuple[str, ...],
    ) -> tuple[ManDocContext, ...]:
        """Search Man Vault for relevant documentation.

        Args:
            request: The task request.
            entities: Detected entities.

        Returns:
            Tuple of ManDocContext.
        """
        try:
            from elle.daemon.manvault import search

            # Build query from request and entities
            query = request
            if entities:
                query += " " + " ".join(entities)

            results = search(query, k=4, search_type="hybrid")

            docs = []
            for r in results:
                docs.append(ManDocContext(
                    name=r.name,
                    section=r.section,
                    snippet=r.snippet,
                    flags_used=(),  # Could extract flags from snippet
                ))

            return tuple(docs)

        except ImportError:
            logger.debug("Man Vault not available")
            return ()
        except Exception as e:
            logger.warning(f"Man Vault search failed: {e}")
            return ()

    def _search_prior_plans(
        self,
        request: str,
        entities: tuple[str, ...],
    ) -> tuple[PriorPlanContext, ...]:
        """Search Incident Vault for similar past plans.

        Args:
            request: The task request.
            entities: Detected entities.

        Returns:
            Tuple of PriorPlanContext.
        """
        try:
            from elle.daemon.incidents import get_prior_art

            # Build query
            query = request
            if entities:
                query += " " + " ".join(entities)

            prior = get_prior_art(
                query=query,
                k=3,
                include_actions=True,
            )

            contexts = []
            for art in prior:
                commands = []
                for action in art.get("successful_actions", []):
                    if action.get("command"):
                        commands.append(action["command"])

                contexts.append(PriorPlanContext(
                    incident_id=art["incident_id"],
                    title=art["title"],
                    outcome=art["outcome"],
                    plan_summary=art.get("summary", ""),
                    commands_executed=tuple(commands),
                    rollback_used=False,  # Could track this
                    score=art.get("score", 0.0),
                    days_ago=art.get("days_ago", 0),
                ))

            return tuple(contexts)

        except ImportError:
            logger.debug("Incident Vault not available")
            return ()
        except Exception as e:
            logger.warning(f"Prior plan search failed: {e}")
            return ()

    def _parse_plan_response(self, response: dict) -> CommandPlan:
        """Parse LLM response into CommandPlan.

        Args:
            response: JSON response from LLM.

        Returns:
            Parsed CommandPlan.
        """
        # Parse steps
        steps = []
        for step_data in response.get("steps", []):
            steps.append(PlanStep(
                command=step_data["command"],
                explanation=step_data["explanation"],
                risk_level=step_data.get("risk_level", "medium"),
                requires_privilege=step_data.get("requires_privilege", False),
                can_fail=step_data.get("can_fail", False),
            ))

        # Parse checks
        checks = []
        for check_data in response.get("checks", []):
            checks.append(ValidationCheck(
                command=check_data["command"],
                description=check_data["description"],
                expected=check_data.get("expected"),
            ))

        # Parse rollback
        rollback = []
        for rb_data in response.get("rollback", []):
            rollback.append(RollbackStep(
                command=rb_data["command"],
                explanation=rb_data["explanation"],
                requires_privilege=rb_data.get("requires_privilege", False),
            ))

        # Determine if privilege is required
        requires_privilege = any(s.requires_privilege for s in steps)

        return CommandPlan(
            title=response.get("title", "System Task"),
            explanation=response.get("explanation", ""),
            steps=tuple(steps),
            checks=tuple(checks),
            rollback=tuple(rollback),
            risks=tuple(response.get("risks", [])),
            requires_privilege=requires_privilege,
            grounded_in=tuple(response.get("grounded_in", [])),
        )

    def _execute_step(
        self,
        step: PlanStep,
        step_index: int,
        session: Session,
    ) -> StepResult:
        """Execute a single plan step.

        Args:
            step: The step to execute.
            step_index: Index of the step.
            session: Current session.

        Returns:
            StepResult with execution details.
        """
        start_time = time.time()

        result = run_safe(
            step.command,
            cwd=session.cwd,
            timeout=self.command_timeout,
            mode=RunMode.CAPTURE,
        )

        duration_ms = int((time.time() - start_time) * 1000)

        return StepResult(
            step_index=step_index,
            command=step.command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            success=result.success,
            privileged=step.requires_privilege,
            duration_ms=duration_ms,
            executed_at=datetime.utcnow(),
        )

    def _execute_rollback_step(
        self,
        step: RollbackStep,
        step_index: int,
        session: Session,
    ) -> StepResult:
        """Execute a rollback step.

        Args:
            step: The rollback step.
            step_index: Index of the step.
            session: Current session.

        Returns:
            StepResult with execution details.
        """
        start_time = time.time()

        result = run_safe(
            step.command,
            cwd=session.cwd,
            timeout=self.command_timeout,
            mode=RunMode.CAPTURE,
        )

        duration_ms = int((time.time() - start_time) * 1000)

        return StepResult(
            step_index=step_index,
            command=step.command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            success=result.success,
            privileged=step.requires_privilege,
            duration_ms=duration_ms,
            executed_at=datetime.utcnow(),
        )

    def _run_checks(
        self,
        checks: tuple[ValidationCheck, ...],
        session: Session,
    ) -> tuple[CheckResult, ...]:
        """Run validation checks.

        Args:
            checks: Validation checks to run.
            session: Current session.

        Returns:
            Tuple of CheckResult.
        """
        results = []

        for i, check in enumerate(checks):
            result = run_safe(
                check.command,
                cwd=session.cwd,
                timeout=30.0,
                mode=RunMode.CAPTURE,
            )

            passed = result.success
            actual = result.stdout.strip()

            # Check expected pattern if provided
            if passed and check.expected:
                if not re.search(check.expected, actual, re.IGNORECASE):
                    passed = False

            results.append(CheckResult(
                check_index=i,
                command=check.command,
                passed=passed,
                output=actual,
                expected=check.expected,
                actual=actual if check.expected else None,
            ))

        return tuple(results)

    def _create_incident(
        self,
        request: str,
        context: PlanContext,
    ) -> str:
        """Create an incident for this task.

        Args:
            request: The task request.
            context: Planning context.

        Returns:
            Incident ID.
        """
        from elle.daemon.incidents import create_incident_draft

        incident = create_incident_draft(
            title=f"Task: {request[:50]}",
            domain="task",
            severity="info",
            trigger_source="system_task",
            trigger_command=request,
        )

        return incident.incident_id

    def _record_action(
        self,
        incident_id: str,
        command: str,
        step_result: StepResult,
        is_rollback: bool = False,
    ) -> None:
        """Record an action in the incident.

        Args:
            incident_id: The incident ID.
            command: The command executed.
            step_result: Result of execution.
            is_rollback: Whether this is a rollback action.
        """
        from elle.daemon.incidents import append_action

        kind = "rollback" if is_rollback else "shell"

        append_action(
            incident_id,
            kind=kind,
            command=command,
            exit_code=step_result.exit_code,
            stdout=step_result.stdout[:500],
            stderr=step_result.stderr[:500],
            success=step_result.success,
        )

    def _finalize_incident(self, result: PlanResult) -> None:
        """Finalize the incident with outcome.

        Args:
            result: Final PlanResult.
        """
        from elle.daemon.incidents import finalize_outcome, update_incident

        if not result.incident_id:
            return

        # Map plan outcome to incident outcome
        outcome_map = {
            PlanOutcome.SUCCESS: "improved",
            PlanOutcome.PARTIAL: "partial",
            PlanOutcome.FAILED: "no_change",
            PlanOutcome.ROLLED_BACK: "no_change",
            PlanOutcome.CANCELLED: "no_change",
            PlanOutcome.ERROR: "no_change",
        }
        outcome = outcome_map.get(result.outcome, "unknown")

        # Build verification steps list
        verification_steps = []
        for check in result.check_results:
            status = "PASS" if check.passed else "FAIL"
            verification_steps.append(f"[{status}] {check.command}")

        # Update with summary
        if result.plan:
            update_incident(
                result.incident_id,
                summary=result.plan.explanation,
                decision={
                    "plan_title": result.plan.title,
                    "steps": [s.command for s in result.plan.steps],
                    "risks": list(result.plan.risks),
                },
            )

        finalize_outcome(
            result.incident_id,
            outcome=outcome,
            verification_steps=verification_steps,
            root_cause=None,  # Could be set by user
        )


# =============================================================================
# Module-level Service Instance
# =============================================================================

_service: PlannerService | None = None


def get_planner_service() -> PlannerService:
    """Get the shared planner service instance.

    Returns:
        The PlannerService singleton.
    """
    global _service
    if _service is None:
        _service = PlannerService()
    return _service


def reset_planner_service() -> None:
    """Reset the shared service instance (for testing)."""
    global _service
    _service = None
