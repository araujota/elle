"""Tests for elle.cli.planner.renderer -- Plan rendering/display."""

from __future__ import annotations

from datetime import datetime

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
from elle.cli.planner.renderer import (
    BOX_CHARS,
    COMPACT_BOX,
    _strip_ansi,
    render_box,
    render_check_result,
    render_confirmation_prompt,
    render_execution_summary,
    render_plan,
    render_plan_result,
    render_step_progress,
    render_step_result,
    risk_badge,
    risk_color,
)
from elle.cli.terminal.renderer import Colors

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(command="echo hello", risk="low", privileged=False, can_fail=False):
    return PlanStep(
        command=command,
        explanation="Test step explanation",
        risk_level=risk,
        requires_privilege=privileged,
        can_fail=can_fail,
    )


def _make_plan(title="Test Plan", steps=None, checks=None, rollback=None, risks=(), grounded_in=()):
    if steps is None:
        steps = (_make_step(),)
    return CommandPlan(
        title=title,
        explanation="Test explanation",
        steps=tuple(steps),
        checks=tuple(checks or []),
        rollback=tuple(rollback or []),
        risks=risks,
        grounded_in=grounded_in,
    )


def _make_step_result(success=True, step_index=0, command="echo hello", exit_code=None, stderr=""):
    if exit_code is None:
        exit_code = 0 if success else 1
    return StepResult(
        step_index=step_index,
        command=command,
        exit_code=exit_code,
        stdout="output",
        stderr=stderr,
        success=success,
        duration_ms=100,
        executed_at=datetime.now(),
    )


def _make_check_result(passed=True, command="echo check"):
    return CheckResult(
        check_index=0,
        command=command,
        passed=passed,
        output="result",
    )


def _make_plan_result(
    plan=None,
    outcome=PlanOutcome.CANCELLED,
    step_results=(),
    check_results=(),
    rollback_results=(),
    incident_id=None,
    verification=None,
):
    context = PlanContext(request=TaskRequest(request="test"))
    return PlanResult(
        context=context,
        plan=plan,
        outcome=outcome,
        step_results=tuple(step_results),
        check_results=tuple(check_results),
        rollback_results=tuple(rollback_results),
        incident_id=incident_id,
        verification=verification,
    )


# ---------------------------------------------------------------------------
# risk_color / risk_badge
# ---------------------------------------------------------------------------


class TestRiskColor:
    def test_low(self):
        assert risk_color("low") == Colors.GREEN

    def test_medium(self):
        assert risk_color("medium") == Colors.YELLOW

    def test_high(self):
        assert risk_color("high") == Colors.RED

    def test_unknown(self):
        result = risk_color("unknown")
        assert result == Colors.RESET


class TestRiskBadge:
    def test_low(self):
        result = risk_badge("low")
        assert "LOW" in result
        assert Colors.RESET in result

    def test_medium(self):
        result = risk_badge("medium")
        assert "MEDIUM" in result

    def test_high(self):
        result = risk_badge("high")
        assert "HIGH" in result


# ---------------------------------------------------------------------------
# _strip_ansi
# ---------------------------------------------------------------------------


class TestStripAnsi:
    def test_no_ansi(self):
        assert _strip_ansi("hello world") == "hello world"

    def test_with_ansi(self):
        assert _strip_ansi(f"{Colors.RED}error{Colors.RESET}") == "error"

    def test_empty(self):
        assert _strip_ansi("") == ""


# ---------------------------------------------------------------------------
# render_box
# ---------------------------------------------------------------------------


class TestRenderBox:
    def test_basic_box(self):
        result = render_box("Title", ["Line 1", "Line 2"])
        assert "Title" in result
        assert "Line 1" in result

    def test_rounded(self):
        result = render_box("T", ["content"], rounded=True)
        assert COMPACT_BOX["top_left"] in result

    def test_square(self):
        result = render_box("T", ["content"], rounded=False)
        assert BOX_CHARS["top_left"] in result

    def test_long_line_truncated(self):
        long_line = "x" * 200
        result = render_box("T", [long_line], width=50)
        assert "..." in result

    def test_custom_width(self):
        result = render_box("T", ["short"], width=40)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# render_plan
# ---------------------------------------------------------------------------


class TestRenderPlan:
    def test_basic_plan(self):
        plan = _make_plan()
        result = render_plan(plan)
        assert "Test Plan" in result
        assert "Steps:" in result

    def test_plan_with_checks(self):
        checks = [ValidationCheck(command="echo check", description="Verify")]
        plan = _make_plan(checks=checks)
        result = render_plan(plan)
        assert "Validation Checks:" in result

    def test_plan_with_rollback(self):
        rollback = [RollbackStep(command="undo", explanation="Undo step")]
        plan = _make_plan(rollback=rollback)
        result = render_plan(plan)
        assert "Rollback" in result

    def test_plan_with_risks(self):
        plan = _make_plan(risks=("Risk A", "Risk B"))
        result = render_plan(plan)
        assert "Risk A" in result

    def test_plan_with_grounded_in(self):
        plan = _make_plan(grounded_in=("systemctl(1)",))
        result = render_plan(plan)
        assert "systemctl(1)" in result

    def test_plan_requires_privilege(self):
        steps = [_make_step(privileged=True)]
        plan = CommandPlan(
            title="Test Plan",
            explanation="Test explanation",
            steps=tuple(steps),
            requires_privilege=True,
        )
        result = render_plan(plan)
        assert "PRIVILEGES REQUIRED" in result

    def test_plan_with_verification(self):
        plan = _make_plan()
        verification = PlanVerification(
            is_valid=True,
            warnings=("Watch out",),
            errors=("Something wrong",),
        )
        result = render_plan(plan, verification)
        assert "Watch out" in result
        assert "Something wrong" in result

    def test_plan_high_risk_steps(self):
        steps = [_make_step(risk="high")]
        plan = _make_plan(steps=steps)
        result = render_plan(plan)
        assert "HIGH" in result


# ---------------------------------------------------------------------------
# render_confirmation_prompt
# ---------------------------------------------------------------------------


class TestRenderConfirmationPrompt:
    def test_auto_approve(self):
        plan = _make_plan()
        result = render_confirmation_prompt(plan, auto_approve=True)
        assert "low-risk" in result

    def test_high_risk(self):
        plan = _make_plan(steps=[_make_step(risk="high")])
        result = render_confirmation_prompt(plan)
        assert "HIGH-RISK" in result
        assert "yes" in result

    def test_medium_risk(self):
        plan = _make_plan(steps=[_make_step(risk="medium")])
        result = render_confirmation_prompt(plan)
        assert "modify" in result

    def test_low_risk(self):
        plan = _make_plan()
        result = render_confirmation_prompt(plan)
        assert "Y/n" in result


# ---------------------------------------------------------------------------
# render_step_progress
# ---------------------------------------------------------------------------


class TestRenderStepProgress:
    def test_basic(self):
        result = render_step_progress(0, 3, "echo hello")
        assert "[1/3]" in result
        assert "echo hello" in result


# ---------------------------------------------------------------------------
# render_step_result
# ---------------------------------------------------------------------------


class TestRenderStepResult:
    def test_success(self):
        sr = _make_step_result(success=True)
        result = render_step_result(sr)
        assert "OK" in result

    def test_failure(self):
        sr = _make_step_result(success=False, exit_code=1, stderr="permission denied\nmore info")
        result = render_step_result(sr)
        assert "FAILED" in result
        assert "permission denied" in result

    def test_failure_no_stderr(self):
        sr = _make_step_result(success=False, exit_code=2, stderr="")
        result = render_step_result(sr)
        assert "FAILED" in result


# ---------------------------------------------------------------------------
# render_check_result
# ---------------------------------------------------------------------------


class TestRenderCheckResult:
    def test_pass(self):
        cr = _make_check_result(passed=True)
        result = render_check_result(cr)
        assert "PASS" in result

    def test_fail(self):
        cr = _make_check_result(passed=False)
        result = render_check_result(cr)
        assert "FAIL" in result


# ---------------------------------------------------------------------------
# render_execution_summary
# ---------------------------------------------------------------------------


class TestRenderExecutionSummary:
    def test_success(self):
        pr = _make_plan_result(
            plan=_make_plan(),
            outcome=PlanOutcome.SUCCESS,
            step_results=[_make_step_result(success=True)],
        )
        result = render_execution_summary(pr)
        assert "SUCCESS" in result
        assert "1 succeeded" in result

    def test_failed(self):
        pr = _make_plan_result(
            plan=_make_plan(),
            outcome=PlanOutcome.FAILED,
            step_results=[_make_step_result(success=False)],
        )
        result = render_execution_summary(pr)
        assert "FAILED" in result

    def test_with_checks(self):
        pr = _make_plan_result(
            plan=_make_plan(),
            outcome=PlanOutcome.SUCCESS,
            step_results=[_make_step_result()],
            check_results=[_make_check_result(passed=True)],
        )
        result = render_execution_summary(pr)
        assert "Checks:" in result

    def test_with_rollback(self):
        pr = _make_plan_result(
            plan=_make_plan(),
            outcome=PlanOutcome.ROLLED_BACK,
            rollback_results=[_make_step_result()],
        )
        result = render_execution_summary(pr)
        assert "Rollback:" in result

    def test_with_incident_id(self):
        pr = _make_plan_result(
            plan=_make_plan(),
            outcome=PlanOutcome.SUCCESS,
            incident_id="inc-12345678-abcd",
        )
        result = render_execution_summary(pr)
        assert "inc-1234" in result

    def test_partial_outcome(self):
        pr = _make_plan_result(
            plan=_make_plan(),
            outcome=PlanOutcome.PARTIAL,
        )
        result = render_execution_summary(pr)
        assert "PARTIAL" in result

    def test_error_outcome(self):
        pr = _make_plan_result(
            plan=_make_plan(),
            outcome=PlanOutcome.ERROR,
        )
        result = render_execution_summary(pr)
        assert "ERROR" in result


# ---------------------------------------------------------------------------
# render_plan_result
# ---------------------------------------------------------------------------


class TestRenderPlanResult:
    def test_no_plan(self):
        pr = _make_plan_result(plan=None)
        result = render_plan_result(pr)
        assert "Failed to generate plan" in result

    def test_with_plan(self):
        pr = _make_plan_result(plan=_make_plan(), outcome=PlanOutcome.CANCELLED)
        result = render_plan_result(pr)
        assert "Test Plan" in result

    def test_with_verification_valid(self):
        verification = PlanVerification(is_valid=True)
        pr = _make_plan_result(
            plan=_make_plan(),
            outcome=PlanOutcome.CANCELLED,
            verification=verification,
        )
        result = render_plan_result(pr)
        assert "verified successfully" in result

    def test_with_verification_invalid(self):
        verification = PlanVerification(is_valid=False, errors=("Bad command",))
        pr = _make_plan_result(
            plan=_make_plan(),
            outcome=PlanOutcome.CANCELLED,
            verification=verification,
        )
        result = render_plan_result(pr)
        assert "verification failed" in result
        assert "Bad command" in result

    def test_with_step_results(self):
        pr = _make_plan_result(
            plan=_make_plan(),
            outcome=PlanOutcome.SUCCESS,
            step_results=[_make_step_result()],
        )
        result = render_plan_result(pr)
        assert "Execution Results:" in result

    def test_with_check_results(self):
        pr = _make_plan_result(
            plan=_make_plan(),
            outcome=PlanOutcome.SUCCESS,
            check_results=[_make_check_result()],
        )
        result = render_plan_result(pr)
        assert "Validation Results:" in result

    def test_cancelled_no_summary(self):
        pr = _make_plan_result(
            plan=_make_plan(),
            outcome=PlanOutcome.CANCELLED,
        )
        result = render_plan_result(pr)
        assert "Execution Summary" not in result

    def test_success_includes_summary(self):
        pr = _make_plan_result(
            plan=_make_plan(),
            outcome=PlanOutcome.SUCCESS,
            step_results=[_make_step_result()],
        )
        result = render_plan_result(pr)
        assert "Execution Summary" in result
