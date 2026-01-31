from __future__ import annotations

"""Tests for elle.cli.planner.interactive -- Interactive plan approval and execution UI.

Covers ~20 test classes spanning: InteractiveState enum, PlannerInteractive
initialization and state management, render_preview (all branches), render_confirmation,
handle_input dispatch, preview/confirm/rollback input handlers, run_interactive_planner
(all branches including keyboard interrupt, rollback flows, high-risk prompts),
and run_noninteractive_planner (all branches).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from elle.cli.planner.interactive import (
    InteractiveState,
    PlannerInteractive,
    run_interactive_planner,
    run_noninteractive_planner,
)
from elle.cli.planner.models import (
    CheckResult,
    CommandPlan,
    PlanContext,
    PlanOutcome,
    PlanResult,
    PlanStep,
    PlanVerification,
    RollbackStep,
    StepResult,
    TaskRequest,
    ValidationCheck,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_session(cwd: str = "/tmp") -> MagicMock:
    """Create a mock Session with a working directory."""
    session = MagicMock()
    session.cwd = Path(cwd)
    return session


def _make_step(
    command: str = "echo hello",
    risk: str = "low",
    privileged: bool = False,
    can_fail: bool = False,
) -> PlanStep:
    return PlanStep(
        command=command,
        explanation="Test step",
        risk_level=risk,
        requires_privilege=privileged,
        can_fail=can_fail,
    )


def _make_plan(
    title: str = "Test Plan",
    steps: list | tuple | None = None,
    checks: list | tuple | None = None,
    rollback: list | tuple | None = None,
    risks: tuple[str, ...] = (),
    requires_privilege: bool = False,
) -> CommandPlan:
    if steps is None:
        steps = (_make_step(),)
    return CommandPlan(
        title=title,
        explanation="Test explanation",
        steps=tuple(steps),
        checks=tuple(checks or []),
        rollback=tuple(rollback or []),
        risks=risks,
        requires_privilege=requires_privilege,
    )


def _make_verification(
    is_valid: bool = True,
    errors: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> PlanVerification:
    return PlanVerification(
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
    )


def _make_plan_result(
    plan: CommandPlan | None = None,
    context: PlanContext | None = None,
    verification: PlanVerification | None = None,
    outcome: PlanOutcome = PlanOutcome.CANCELLED,
    step_results: tuple[StepResult, ...] = (),
    check_results: tuple[CheckResult, ...] = (),
    rollback_results: tuple[StepResult, ...] = (),
    incident_id: str | None = None,
) -> PlanResult:
    if context is None:
        context = PlanContext(request=TaskRequest(request="test request"))
    return PlanResult(
        context=context,
        plan=plan,
        verification=verification,
        outcome=outcome,
        step_results=step_results,
        check_results=check_results,
        rollback_results=rollback_results,
        incident_id=incident_id,
    )


def _make_step_result(
    success: bool = True,
    step_index: int = 0,
    command: str = "echo hello",
    exit_code: int | None = None,
    stderr: str = "",
) -> StepResult:
    from datetime import datetime

    return StepResult(
        step_index=step_index,
        command=command,
        exit_code=exit_code if exit_code is not None else (0 if success else 1),
        stdout="output",
        stderr=stderr if not success else "",
        success=success,
        duration_ms=100,
        executed_at=datetime.now(),
    )


# =============================================================================
# 1. InteractiveState Enum
# =============================================================================


class TestInteractiveState:
    """Verify all enum members exist and are distinct."""

    def test_preview_state(self):
        assert InteractiveState.PREVIEW is not None

    def test_confirm_state(self):
        assert InteractiveState.CONFIRM is not None

    def test_executing_state(self):
        assert InteractiveState.EXECUTING is not None

    def test_checking_state(self):
        assert InteractiveState.CHECKING is not None

    def test_rollback_prompt_state(self):
        assert InteractiveState.ROLLBACK_PROMPT is not None

    def test_rolling_back_state(self):
        assert InteractiveState.ROLLING_BACK is not None

    def test_complete_state(self):
        assert InteractiveState.COMPLETE is not None

    def test_all_states_distinct(self):
        values = [s.value for s in InteractiveState]
        assert len(values) == len(set(values))

    def test_total_count(self):
        assert len(InteractiveState) == 7


# =============================================================================
# 2. PlannerInteractive.__init__
# =============================================================================


class TestPlannerInteractiveInit:
    def test_initial_state_is_preview(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        assert ui.state == InteractiveState.PREVIEW

    def test_auto_approve_initially_false(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        assert ui.auto_approve is False

    def test_stores_result(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        assert ui.result is result

    def test_stores_session(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        assert ui.session is session


# =============================================================================
# 3. render_preview -- no plan
# =============================================================================


class TestRenderPreviewNoPlan:
    def test_no_plan_returns_error(self):
        result = _make_plan_result(plan=None)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output = ui.render_preview()
        assert "No plan generated" in output


# =============================================================================
# 4. render_preview -- with plan and valid verification
# =============================================================================


class TestRenderPreviewValidVerification:
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(True, "Low risk"))
    @patch("elle.cli.planner.interactive.render_plan", return_value="[rendered plan]")
    def test_auto_approve_sets_flag_and_shows_message(self, mock_render, mock_can_auto):
        plan = _make_plan()
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output = ui.render_preview()
        assert ui.auto_approve is True
        assert "Low-risk operation" in output
        assert "can proceed automatically" in output

    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, "Needs privilege"))
    @patch("elle.cli.planner.interactive.render_plan", return_value="[rendered plan]")
    def test_not_auto_approvable_does_not_set_flag(self, mock_render, mock_can_auto):
        plan = _make_plan()
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output = ui.render_preview()
        assert ui.auto_approve is False
        assert "Low-risk operation" not in output


# =============================================================================
# 5. render_preview -- with plan and invalid verification
# =============================================================================


class TestRenderPreviewInvalidVerification:
    @patch("elle.cli.planner.interactive.render_plan", return_value="[rendered plan]")
    def test_shows_validation_errors(self, mock_render):
        plan = _make_plan()
        verification = _make_verification(
            is_valid=False,
            errors=("Error A", "Error B"),
        )
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output = ui.render_preview()
        assert "validation errors" in output
        assert "Error A" in output
        assert "Error B" in output


# =============================================================================
# 6. render_preview -- with plan but no verification
# =============================================================================


class TestRenderPreviewNoVerification:
    @patch("elle.cli.planner.interactive.render_plan", return_value="[rendered plan]")
    def test_no_verification_renders_plan_only(self, mock_render):
        plan = _make_plan()
        result = _make_plan_result(plan=plan, verification=None)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output = ui.render_preview()
        assert "[rendered plan]" in output
        # No auto-approve or error messages
        assert "validation errors" not in output
        assert "Low-risk operation" not in output


# =============================================================================
# 7. render_confirmation
# =============================================================================


class TestRenderConfirmation:
    def test_no_plan_returns_empty(self):
        result = _make_plan_result(plan=None)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        assert ui.render_confirmation() == ""

    @patch("elle.cli.planner.interactive.render_confirmation_prompt", return_value="Proceed?")
    def test_with_plan_calls_renderer(self, mock_render):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output = ui.render_confirmation()
        assert output == "Proceed?"
        mock_render.assert_called_once_with(plan, False)

    @patch("elle.cli.planner.interactive.render_confirmation_prompt", return_value="Auto proceed?")
    def test_passes_auto_approve_flag(self, mock_render):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        ui.auto_approve = True
        output = ui.render_confirmation()
        assert output == "Auto proceed?"
        mock_render.assert_called_once_with(plan, True)


# =============================================================================
# 8. handle_input -- dispatch logic
# =============================================================================


class TestHandleInputDispatch:
    def test_preview_state_dispatches(self):
        plan = _make_plan()
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        ui.state = InteractiveState.PREVIEW
        with patch.object(ui, "_handle_preview_input", return_value=("preview output", True)) as mock_h:
            output, cont = ui.handle_input("y")
            mock_h.assert_called_once_with("y")

    def test_confirm_state_dispatches(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        ui.state = InteractiveState.CONFIRM
        with patch.object(ui, "_handle_confirm_input", return_value=("confirm output", False)) as mock_h:
            output, cont = ui.handle_input("n")
            mock_h.assert_called_once_with("n")

    def test_rollback_prompt_state_dispatches(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        ui.state = InteractiveState.ROLLBACK_PROMPT
        with patch.object(ui, "_handle_rollback_prompt", return_value=(None, False)) as mock_h:
            output, cont = ui.handle_input("y")
            mock_h.assert_called_once_with("y")

    def test_unhandled_state_returns_none_false(self):
        """States not handled in dispatch return (None, False)."""
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        ui.state = InteractiveState.EXECUTING
        output, cont = ui.handle_input("anything")
        assert output is None
        assert cont is False

    def test_input_is_stripped_and_lowered(self):
        plan = _make_plan()
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        ui.state = InteractiveState.PREVIEW
        with patch.object(ui, "_handle_preview_input", return_value=("ok", True)) as mock_h:
            ui.handle_input("  YES  ")
            mock_h.assert_called_once_with("yes")


# =============================================================================
# 9. _handle_preview_input -- invalid verification
# =============================================================================


class TestHandlePreviewInputInvalid:
    def test_invalid_verification_cancels(self):
        plan = _make_plan()
        verification = _make_verification(is_valid=False, errors=("bad step",))
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output, cont = ui._handle_preview_input("")
        assert cont is False
        assert "cancelled" in output.lower()
        assert "validation errors" in output.lower()
        assert ui.result.outcome == PlanOutcome.CANCELLED


# =============================================================================
# 10. _handle_preview_input -- valid verification
# =============================================================================


class TestHandlePreviewInputValid:
    @patch("elle.cli.planner.interactive.render_confirmation_prompt", return_value="Confirm?")
    def test_valid_transitions_to_confirm(self, mock_prompt):
        plan = _make_plan()
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output, cont = ui._handle_preview_input("")
        assert ui.state == InteractiveState.CONFIRM
        assert cont is True

    def test_no_verification_transitions_to_confirm(self):
        """When verification is None, preview still transitions to confirm."""
        plan = _make_plan()
        result = _make_plan_result(plan=plan, verification=None)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output, cont = ui._handle_preview_input("")
        assert ui.state == InteractiveState.CONFIRM
        assert cont is True


# =============================================================================
# 11. _handle_confirm_input -- approved
# =============================================================================


class TestHandleConfirmInputApproved:
    def test_empty_input_approves(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output, cont = ui._handle_confirm_input("")
        assert output is None
        assert cont is False

    def test_y_approves(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output, cont = ui._handle_confirm_input("y")
        assert output is None
        assert cont is False

    def test_yes_approves(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output, cont = ui._handle_confirm_input("yes")
        assert output is None
        assert cont is False


# =============================================================================
# 12. _handle_confirm_input -- cancelled
# =============================================================================


class TestHandleConfirmInputCancelled:
    def test_n_cancels(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output, cont = ui._handle_confirm_input("n")
        assert cont is False
        assert "cancelled" in output.lower()
        assert ui.result.outcome == PlanOutcome.CANCELLED

    def test_no_cancels(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output, cont = ui._handle_confirm_input("no")
        assert cont is False
        assert ui.result.outcome == PlanOutcome.CANCELLED


# =============================================================================
# 13. _handle_confirm_input -- review
# =============================================================================


class TestHandleConfirmInputReview:
    @patch("elle.cli.planner.interactive.render_plan", return_value="[plan]")
    def test_r_returns_to_preview(self, mock_render):
        plan = _make_plan()
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        ui.state = InteractiveState.CONFIRM
        output, cont = ui._handle_confirm_input("r")
        assert ui.state == InteractiveState.PREVIEW
        assert cont is True
        assert output is not None


# =============================================================================
# 14. _handle_confirm_input -- invalid input
# =============================================================================


class TestHandleConfirmInputInvalid:
    def test_invalid_input_asks_again(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        output, cont = ui._handle_confirm_input("xyz")
        assert cont is True
        assert "enter 'y'" in output.lower()


# =============================================================================
# 15. _handle_rollback_prompt
# =============================================================================


class TestHandleRollbackPrompt:
    def test_yes_signals_rollback(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        ui.state = InteractiveState.ROLLBACK_PROMPT
        output, cont = ui._handle_rollback_prompt("y")
        assert output is None
        assert cont is False

    def test_yes_full_signals_rollback(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        ui.state = InteractiveState.ROLLBACK_PROMPT
        output, cont = ui._handle_rollback_prompt("yes")
        assert output is None
        assert cont is False

    def test_no_skips_rollback(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        ui.state = InteractiveState.ROLLBACK_PROMPT
        output, cont = ui._handle_rollback_prompt("n")
        assert output is None
        assert cont is False

    def test_no_full_skips_rollback(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        ui.state = InteractiveState.ROLLBACK_PROMPT
        output, cont = ui._handle_rollback_prompt("no")
        assert output is None
        assert cont is False

    def test_invalid_input_reprompts(self):
        plan = _make_plan()
        result = _make_plan_result(plan=plan)
        session = _make_session()
        ui = PlannerInteractive(result, session)
        ui.state = InteractiveState.ROLLBACK_PROMPT
        output, cont = ui._handle_rollback_prompt("maybe")
        assert cont is True
        assert "Rollback" in output


# =============================================================================
# 16. run_interactive_planner -- no plan
# =============================================================================


class TestRunInteractivePlannerNoPlan:
    @patch("elle.cli.planner.interactive.print")
    def test_no_plan_returns_error(self, mock_print):
        result = _make_plan_result(plan=None)
        session = _make_session()
        final = run_interactive_planner(result, session)
        assert final.outcome == PlanOutcome.ERROR
        # Verify it printed the error
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "No plan" in printed


# =============================================================================
# 17. run_interactive_planner -- invalid verification
# =============================================================================


class TestRunInteractivePlannerInvalidVerification:
    @patch("elle.cli.planner.interactive.print")
    def test_invalid_verification_cancels(self, mock_print):
        plan = _make_plan()
        verification = _make_verification(
            is_valid=False,
            errors=("Step 1 blocked",),
        )
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        final = run_interactive_planner(result, session)
        assert final.outcome == PlanOutcome.CANCELLED
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "verification" in printed.lower() or "Step 1 blocked" in printed


# =============================================================================
# 18. run_interactive_planner -- auto-approve low risk
# =============================================================================


class TestRunInteractivePlannerAutoApprove:
    @patch("elle.cli.planner.interactive.render_execution_summary", return_value="[summary]")
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(True, "Low risk"))
    @patch("builtins.input", return_value="")
    @patch("elle.cli.planner.interactive.print")
    def test_auto_approve_with_enter(self, mock_print, mock_input, mock_can_auto, mock_get_svc, mock_summary):
        plan = _make_plan()
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        mock_service = MagicMock()
        executed_result = result.with_outcome(PlanOutcome.SUCCESS)
        mock_service.execute_plan.return_value = executed_result
        mock_service.finalize.return_value = executed_result
        mock_get_svc.return_value = mock_service

        final = run_interactive_planner(result, session)
        assert final.outcome == PlanOutcome.SUCCESS
        # Prompt should mention "Press Enter"
        prompt_text = mock_input.call_args[0][0]
        assert "enter" in prompt_text.lower() or "Enter" in prompt_text


# =============================================================================
# 19. run_interactive_planner -- user cancels with 'n'
# =============================================================================


class TestRunInteractivePlannerUserCancels:
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, "Medium risk"))
    @patch("builtins.input", return_value="n")
    @patch("elle.cli.planner.interactive.print")
    def test_user_says_n(self, mock_print, mock_input, mock_can_auto):
        plan = _make_plan(steps=[_make_step(risk="medium")])
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        final = run_interactive_planner(result, session)
        assert final.outcome == PlanOutcome.CANCELLED
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "Cancelled" in printed

    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, "Medium risk"))
    @patch("builtins.input", return_value="no")
    @patch("elle.cli.planner.interactive.print")
    def test_user_says_no(self, mock_print, mock_input, mock_can_auto):
        plan = _make_plan(steps=[_make_step(risk="medium")])
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        final = run_interactive_planner(result, session)
        assert final.outcome == PlanOutcome.CANCELLED


# =============================================================================
# 20. run_interactive_planner -- keyboard interrupt
# =============================================================================


class TestRunInteractivePlannerKeyboardInterrupt:
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, "reason"))
    @patch("builtins.input", side_effect=KeyboardInterrupt)
    @patch("elle.cli.planner.interactive.print")
    def test_keyboard_interrupt_cancels(self, mock_print, mock_input, mock_can_auto):
        plan = _make_plan()
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        final = run_interactive_planner(result, session)
        assert final.outcome == PlanOutcome.CANCELLED

    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, "reason"))
    @patch("builtins.input", side_effect=EOFError)
    @patch("elle.cli.planner.interactive.print")
    def test_eof_error_cancels(self, mock_print, mock_input, mock_can_auto):
        plan = _make_plan()
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        final = run_interactive_planner(result, session)
        assert final.outcome == PlanOutcome.CANCELLED


# =============================================================================
# 21. run_interactive_planner -- high-risk prompt
# =============================================================================


class TestRunInteractivePlannerHighRisk:
    @patch("elle.cli.planner.interactive.render_execution_summary", return_value="[summary]")
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, "High risk"))
    @patch("builtins.input", return_value="yes")
    @patch("elle.cli.planner.interactive.print")
    def test_high_risk_requires_yes(self, mock_print, mock_input, mock_can_auto, mock_get_svc, mock_summary):
        plan = _make_plan(steps=[_make_step(risk="high")])
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        mock_service = MagicMock()
        executed_result = result.with_outcome(PlanOutcome.SUCCESS)
        mock_service.execute_plan.return_value = executed_result
        mock_service.finalize.return_value = executed_result
        mock_get_svc.return_value = mock_service

        final = run_interactive_planner(result, session)
        assert final.outcome == PlanOutcome.SUCCESS
        # Prompt should mention HIGH-RISK
        prompt_text = mock_input.call_args[0][0]
        assert "HIGH-RISK" in prompt_text or "high" in prompt_text.lower()

    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, "High risk"))
    @patch("builtins.input", return_value="y")
    @patch("elle.cli.planner.interactive.print")
    def test_high_risk_rejects_y(self, mock_print, mock_input, mock_can_auto):
        """High-risk requires full 'yes', not just 'y'."""
        plan = _make_plan(steps=[_make_step(risk="high")])
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        final = run_interactive_planner(result, session)
        assert final.outcome == PlanOutcome.CANCELLED
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "must type 'yes'" in printed.lower() or "cancelled" in printed.lower()


# =============================================================================
# 22. run_interactive_planner -- medium-risk prompt
# =============================================================================


class TestRunInteractivePlannerMediumRisk:
    @patch("elle.cli.planner.interactive.render_execution_summary", return_value="[summary]")
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, "Medium risk"))
    @patch("builtins.input", return_value="y")
    @patch("elle.cli.planner.interactive.print")
    def test_medium_risk_accepts_y(self, mock_print, mock_input, mock_can_auto, mock_get_svc, mock_summary):
        plan = _make_plan(steps=[_make_step(risk="medium")])
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        mock_service = MagicMock()
        executed_result = result.with_outcome(PlanOutcome.SUCCESS)
        mock_service.execute_plan.return_value = executed_result
        mock_service.finalize.return_value = executed_result
        mock_get_svc.return_value = mock_service

        final = run_interactive_planner(result, session)
        assert final.outcome == PlanOutcome.SUCCESS


# =============================================================================
# 23. run_interactive_planner -- execution with step results
# =============================================================================


class TestRunInteractivePlannerExecution:
    @patch("elle.cli.planner.interactive.render_execution_summary", return_value="[summary]")
    @patch("elle.cli.planner.interactive.render_step_result", return_value="[step ok]")
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, ""))
    @patch("builtins.input", return_value="y")
    @patch("elle.cli.planner.interactive.print")
    def test_step_results_rendered(
        self,
        mock_print,
        mock_input,
        mock_can_auto,
        mock_get_svc,
        mock_step_render,
        mock_summary,
    ):
        plan = _make_plan()
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        mock_service = MagicMock()
        step_result = _make_step_result(success=True)
        executed = result.with_outcome(PlanOutcome.SUCCESS)
        executed = PlanResult(
            context=executed.context,
            plan=executed.plan,
            verification=executed.verification,
            approved=True,
            step_results=(step_result,),
            outcome=PlanOutcome.SUCCESS,
        )
        mock_service.execute_plan.return_value = executed
        mock_service.finalize.return_value = executed
        mock_get_svc.return_value = mock_service

        final = run_interactive_planner(result, session)
        assert final.outcome == PlanOutcome.SUCCESS
        mock_step_render.assert_called_once()


# =============================================================================
# 24. run_interactive_planner -- execution with check results
# =============================================================================


class TestRunInteractivePlannerCheckResults:
    @patch("elle.cli.planner.interactive.render_execution_summary", return_value="[summary]")
    @patch("elle.cli.planner.interactive.render_check_result", return_value="[check ok]")
    @patch("elle.cli.planner.interactive.render_step_result", return_value="[step ok]")
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, ""))
    @patch("builtins.input", return_value="y")
    @patch("elle.cli.planner.interactive.print")
    def test_check_results_rendered(
        self,
        mock_print,
        mock_input,
        mock_can_auto,
        mock_get_svc,
        mock_step_render,
        mock_check_render,
        mock_summary,
    ):
        plan = _make_plan(
            checks=[ValidationCheck(command="check1", description="verify")],
        )
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        mock_service = MagicMock()
        check_result = CheckResult(check_index=0, command="check1", passed=True, output="ok")
        executed = PlanResult(
            context=result.context,
            plan=result.plan,
            verification=result.verification,
            approved=True,
            step_results=(),
            check_results=(check_result,),
            outcome=PlanOutcome.SUCCESS,
        )
        mock_service.execute_plan.return_value = executed
        mock_service.finalize.return_value = executed
        mock_get_svc.return_value = mock_service

        run_interactive_planner(result, session)
        mock_check_render.assert_called_once_with(check_result)


# =============================================================================
# 25. run_interactive_planner -- failed with rollback
# =============================================================================


class TestRunInteractivePlannerRollback:
    @patch("elle.cli.planner.interactive.render_execution_summary", return_value="[summary]")
    @patch("elle.cli.planner.interactive.render_step_result", return_value="[rollback step]")
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, ""))
    @patch("elle.cli.planner.interactive.print")
    def test_failure_prompts_rollback_and_executes(
        self,
        mock_print,
        mock_can_auto,
        mock_get_svc,
        mock_step_render,
        mock_summary,
    ):
        plan = _make_plan(
            rollback=[RollbackStep(command="undo", explanation="Undo step")],
        )
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        mock_service = MagicMock()
        failed_result = PlanResult(
            context=result.context,
            plan=plan,
            verification=verification,
            approved=True,
            step_results=(),
            outcome=PlanOutcome.FAILED,
        )
        rb_step_result = _make_step_result(success=True, command="undo")
        rolled_back_result = PlanResult(
            context=result.context,
            plan=plan,
            verification=verification,
            approved=True,
            step_results=(),
            rollback_results=(rb_step_result,),
            outcome=PlanOutcome.ROLLED_BACK,
        )
        mock_service.execute_plan.return_value = failed_result
        mock_service.rollback_plan.return_value = rolled_back_result
        mock_service.finalize.return_value = rolled_back_result
        mock_get_svc.return_value = mock_service

        # First input is "y" to proceed, second is "y" to rollback
        with patch("builtins.input", side_effect=["y", "y"]):
            run_interactive_planner(result, session)

        mock_service.rollback_plan.assert_called_once()

    @patch("elle.cli.planner.interactive.render_execution_summary", return_value="[summary]")
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, ""))
    @patch("elle.cli.planner.interactive.print")
    def test_failure_user_declines_rollback(
        self,
        mock_print,
        mock_can_auto,
        mock_get_svc,
        mock_summary,
    ):
        plan = _make_plan(
            rollback=[RollbackStep(command="undo", explanation="Undo step")],
        )
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        mock_service = MagicMock()
        failed_result = PlanResult(
            context=result.context,
            plan=plan,
            verification=verification,
            approved=True,
            step_results=(),
            outcome=PlanOutcome.FAILED,
        )
        mock_service.execute_plan.return_value = failed_result
        mock_service.finalize.return_value = failed_result
        mock_get_svc.return_value = mock_service

        with patch("builtins.input", side_effect=["y", "n"]):
            run_interactive_planner(result, session)

        mock_service.rollback_plan.assert_not_called()

    @patch("elle.cli.planner.interactive.render_execution_summary", return_value="[summary]")
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, ""))
    @patch("elle.cli.planner.interactive.print")
    def test_failure_rollback_keyboard_interrupt(
        self,
        mock_print,
        mock_can_auto,
        mock_get_svc,
        mock_summary,
    ):
        """KeyboardInterrupt during rollback prompt is caught gracefully."""
        plan = _make_plan(
            rollback=[RollbackStep(command="undo", explanation="Undo step")],
        )
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        mock_service = MagicMock()
        failed_result = PlanResult(
            context=result.context,
            plan=plan,
            verification=verification,
            approved=True,
            step_results=(),
            outcome=PlanOutcome.FAILED,
        )
        mock_service.execute_plan.return_value = failed_result
        mock_service.finalize.return_value = failed_result
        mock_get_svc.return_value = mock_service

        with patch("builtins.input", side_effect=["y", KeyboardInterrupt]):
            run_interactive_planner(result, session)

        # Should still finalize despite interrupt
        mock_service.finalize.assert_called_once()


# =============================================================================
# 26. run_interactive_planner -- failed without rollback available
# =============================================================================


class TestRunInteractivePlannerFailedNoRollback:
    @patch("elle.cli.planner.interactive.render_execution_summary", return_value="[summary]")
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, ""))
    @patch("builtins.input", return_value="y")
    @patch("elle.cli.planner.interactive.print")
    def test_failure_no_rollback_skips_prompt(
        self,
        mock_print,
        mock_input,
        mock_can_auto,
        mock_get_svc,
        mock_summary,
    ):
        """When plan fails but has no rollback, rollback prompt is skipped."""
        plan = _make_plan(rollback=[])
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        mock_service = MagicMock()
        failed_result = PlanResult(
            context=result.context,
            plan=plan,
            verification=verification,
            approved=True,
            step_results=(),
            outcome=PlanOutcome.FAILED,
        )
        mock_service.execute_plan.return_value = failed_result
        mock_service.finalize.return_value = failed_result
        mock_get_svc.return_value = mock_service

        run_interactive_planner(result, session)
        # Only one input call (for the initial confirmation), not a rollback prompt
        assert mock_input.call_count == 1
        mock_service.rollback_plan.assert_not_called()


# =============================================================================
# 27. run_interactive_planner -- step explanations
# =============================================================================


class TestRunInteractivePlannerStepExplanations:
    @patch("elle.cli.planner.interactive.render_execution_summary", return_value="[summary]")
    @patch("elle.cli.planner.interactive.render_step_result", return_value="[step]")
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, ""))
    @patch("builtins.input", return_value="y")
    @patch("elle.cli.planner.interactive.print")
    def test_step_explanation_passed_to_renderer(
        self,
        mock_print,
        mock_input,
        mock_can_auto,
        mock_get_svc,
        mock_step_render,
        mock_summary,
    ):
        """Verify step explanation is correctly passed to render_step_result."""
        step = _make_step(command="echo hi")
        plan = _make_plan(steps=[step])
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        step_res = _make_step_result(success=True)
        mock_service = MagicMock()
        executed = PlanResult(
            context=result.context,
            plan=plan,
            verification=verification,
            approved=True,
            step_results=(step_res,),
            outcome=PlanOutcome.SUCCESS,
        )
        mock_service.execute_plan.return_value = executed
        mock_service.finalize.return_value = executed
        mock_get_svc.return_value = mock_service

        run_interactive_planner(result, session)
        # render_step_result called with explanation from the step
        mock_step_render.assert_called_once_with(step_res, "Test step")


# =============================================================================
# 28. run_noninteractive_planner -- no plan
# =============================================================================


class TestRunNoninteractivePlannerNoPlan:
    def test_no_plan_returns_error(self):
        result = _make_plan_result(plan=None)
        session = _make_session()
        final = run_noninteractive_planner(result, session)
        assert final.outcome == PlanOutcome.ERROR


# =============================================================================
# 29. run_noninteractive_planner -- invalid verification
# =============================================================================


class TestRunNoninteractivePlannerInvalidVerification:
    def test_invalid_verification_cancels(self):
        plan = _make_plan()
        verification = _make_verification(is_valid=False, errors=("Blocked",))
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        final = run_noninteractive_planner(result, session)
        assert final.outcome == PlanOutcome.CANCELLED


# =============================================================================
# 30. run_noninteractive_planner -- auto-approve
# =============================================================================


class TestRunNoninteractivePlannerAutoApprove:
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(True, "Low risk"))
    def test_auto_approve_executes(self, mock_can_auto, mock_get_svc):
        plan = _make_plan()
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        mock_service = MagicMock()
        executed = result.with_outcome(PlanOutcome.SUCCESS)
        mock_service.execute_plan.return_value = executed
        mock_service.finalize.return_value = executed
        mock_get_svc.return_value = mock_service

        final = run_noninteractive_planner(result, session)
        assert final.outcome == PlanOutcome.SUCCESS
        mock_service.execute_plan.assert_called_once()
        mock_service.finalize.assert_called_once()


# =============================================================================
# 31. run_noninteractive_planner -- explicit auto_approve=True
# =============================================================================


class TestRunNoninteractivePlannerExplicitAutoApprove:
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, "Medium risk"))
    def test_explicit_auto_approve_overrides(self, mock_can_auto, mock_get_svc):
        """When auto_approve=True passed explicitly, executes even if can_auto_approve is False."""
        plan = _make_plan(steps=[_make_step(risk="medium")])
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        mock_service = MagicMock()
        executed = result.with_outcome(PlanOutcome.SUCCESS)
        mock_service.execute_plan.return_value = executed
        mock_service.finalize.return_value = executed
        mock_get_svc.return_value = mock_service

        final = run_noninteractive_planner(result, session, auto_approve=True)
        assert final.outcome == PlanOutcome.SUCCESS
        mock_service.execute_plan.assert_called_once()


# =============================================================================
# 32. run_noninteractive_planner -- no approval possible
# =============================================================================


class TestRunNoninteractivePlannerNoApproval:
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, "Needs privilege"))
    def test_no_approval_returns_unmodified(self, mock_can_auto):
        """When neither auto_approve flag nor can_auto_approve, returns result unchanged."""
        plan = _make_plan()
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()
        final = run_noninteractive_planner(result, session, auto_approve=False)
        assert final is result
        assert final.outcome == PlanOutcome.CANCELLED


# =============================================================================
# 33. run_interactive_planner -- EOFError during rollback prompt
# =============================================================================


class TestRunInteractivePlannerRollbackEOF:
    @patch("elle.cli.planner.interactive.render_execution_summary", return_value="[summary]")
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, ""))
    @patch("elle.cli.planner.interactive.print")
    def test_eof_during_rollback_prompt(
        self,
        mock_print,
        mock_can_auto,
        mock_get_svc,
        mock_summary,
    ):
        """EOFError during rollback prompt is caught gracefully."""
        plan = _make_plan(
            rollback=[RollbackStep(command="undo", explanation="Undo")],
        )
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        mock_service = MagicMock()
        failed_result = PlanResult(
            context=result.context,
            plan=plan,
            verification=verification,
            approved=True,
            step_results=(),
            outcome=PlanOutcome.FAILED,
        )
        mock_service.execute_plan.return_value = failed_result
        mock_service.finalize.return_value = failed_result
        mock_get_svc.return_value = mock_service

        with patch("builtins.input", side_effect=["y", EOFError]):
            run_interactive_planner(result, session)

        # Should still finalize
        mock_service.finalize.assert_called_once()


# =============================================================================
# 34. run_interactive_planner -- plan becomes None after execution
# =============================================================================


class TestRunInteractivePlannerPlanNoneAfterExec:
    @patch("elle.cli.planner.interactive.render_execution_summary", return_value="[summary]")
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, ""))
    @patch("builtins.input", return_value="y")
    @patch("elle.cli.planner.interactive.print")
    def test_plan_none_after_execution_skips_step_rendering(
        self,
        mock_print,
        mock_input,
        mock_can_auto,
        mock_get_svc,
        mock_summary,
    ):
        """If result.plan is None after execution, step rendering is skipped."""
        plan = _make_plan()
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        mock_service = MagicMock()
        # Return result with plan=None (edge case)
        executed = PlanResult(
            context=result.context,
            plan=None,
            verification=verification,
            approved=True,
            step_results=(_make_step_result(success=True),),
            outcome=PlanOutcome.SUCCESS,
        )
        mock_service.execute_plan.return_value = executed
        mock_service.finalize.return_value = executed
        mock_get_svc.return_value = mock_service

        with patch("elle.cli.planner.interactive.render_step_result") as mock_step_render:
            run_interactive_planner(result, session)
            mock_step_render.assert_not_called()


# =============================================================================
# 35. run_interactive_planner -- low risk default prompt
# =============================================================================


class TestRunInteractivePlannerLowRiskDefaultPrompt:
    @patch("elle.cli.planner.interactive.render_execution_summary", return_value="[summary]")
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(False, "Not auto"))
    @patch("builtins.input", return_value="y")
    @patch("elle.cli.planner.interactive.print")
    def test_low_risk_non_auto_prompt(
        self,
        mock_print,
        mock_input,
        mock_can_auto,
        mock_get_svc,
        mock_summary,
    ):
        """Low-risk plan that is NOT auto-approvable uses default prompt."""
        plan = _make_plan(steps=[_make_step(risk="low")])
        verification = _make_verification(is_valid=True)
        result = _make_plan_result(plan=plan, verification=verification)
        session = _make_session()

        mock_service = MagicMock()
        executed = result.with_outcome(PlanOutcome.SUCCESS)
        mock_service.execute_plan.return_value = executed
        mock_service.finalize.return_value = executed
        mock_get_svc.return_value = mock_service

        run_interactive_planner(result, session)
        prompt_text = mock_input.call_args[0][0]
        assert "Proceed" in prompt_text or "Y/n" in prompt_text


# =============================================================================
# 36. run_noninteractive_planner -- no verification
# =============================================================================


class TestRunNoninteractivePlannerNoVerification:
    @patch("elle.cli.planner.interactive.get_planner_service")
    @patch("elle.cli.planner.interactive.can_auto_approve", return_value=(True, "Low risk"))
    def test_no_verification_with_auto_approve(self, mock_can_auto, mock_get_svc):
        """When verification is None, it doesn't block execution."""
        plan = _make_plan()
        result = _make_plan_result(plan=plan, verification=None)
        session = _make_session()

        mock_service = MagicMock()
        executed = result.with_outcome(PlanOutcome.SUCCESS)
        mock_service.execute_plan.return_value = executed
        mock_service.finalize.return_value = executed
        mock_get_svc.return_value = mock_service

        final = run_noninteractive_planner(result, session)
        assert final.outcome == PlanOutcome.SUCCESS
