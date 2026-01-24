"""Tests for planner models."""

import pytest
from pathlib import Path

from elle.cli.planner.models import (
    CheckResult,
    CommandPlan,
    ManDocContext,
    PlanContext,
    PlanOutcome,
    PlanResult,
    PlanStep,
    PlanVerification,
    PriorPlanContext,
    RollbackStep,
    StepResult,
    StepVerification,
    TaskRequest,
    ValidationCheck,
)


class TestTaskRequest:
    """Tests for TaskRequest model."""

    def test_basic_creation(self):
        """Test basic task request creation."""
        request = TaskRequest(request="open port 80")
        assert request.request == "open port 80"
        assert request.cwd == Path.cwd()
        assert request.entities == ()

    def test_with_entities(self):
        """Test task request with entities."""
        request = TaskRequest(
            request="restart nginx",
            entities=("nginx", "service"),
        )
        assert request.entities == ("nginx", "service")

    def test_immutable(self):
        """Test that TaskRequest is immutable."""
        request = TaskRequest(request="test")
        with pytest.raises(Exception):
            request.request = "changed"


class TestPlanStep:
    """Tests for PlanStep model."""

    def test_basic_creation(self):
        """Test basic step creation."""
        step = PlanStep(
            command="ufw allow 80/tcp",
            explanation="Allow HTTP traffic",
            risk_level="medium",
        )
        assert step.command == "ufw allow 80/tcp"
        assert step.risk_level == "medium"
        assert step.requires_privilege is False

    def test_with_privilege(self):
        """Test step requiring privilege."""
        step = PlanStep(
            command="systemctl restart nginx",
            explanation="Restart nginx",
            risk_level="medium",
            requires_privilege=True,
        )
        assert step.requires_privilege is True

    def test_risk_levels(self):
        """Test valid risk levels."""
        for risk in ["low", "medium", "high"]:
            step = PlanStep(
                command="test",
                explanation="test",
                risk_level=risk,
            )
            assert step.risk_level == risk


class TestValidationCheck:
    """Tests for ValidationCheck model."""

    def test_basic_creation(self):
        """Test basic check creation."""
        check = ValidationCheck(
            command="ufw status",
            description="Verify firewall status",
        )
        assert check.command == "ufw status"
        assert check.expected is None

    def test_with_expected(self):
        """Test check with expected pattern."""
        check = ValidationCheck(
            command="ufw status",
            description="Verify port 80 is open",
            expected="80/tcp.*ALLOW",
        )
        assert check.expected == "80/tcp.*ALLOW"


class TestRollbackStep:
    """Tests for RollbackStep model."""

    def test_basic_creation(self):
        """Test basic rollback step."""
        rb = RollbackStep(
            command="ufw delete allow 80/tcp",
            explanation="Remove HTTP rule",
        )
        assert rb.command == "ufw delete allow 80/tcp"
        assert rb.requires_privilege is False


class TestCommandPlan:
    """Tests for CommandPlan model."""

    def test_basic_creation(self):
        """Test basic plan creation."""
        plan = CommandPlan(
            title="Open Port 80",
            explanation="Allow HTTP traffic through firewall",
            steps=(
                PlanStep(
                    command="ufw allow 80/tcp",
                    explanation="Add firewall rule",
                    risk_level="medium",
                ),
            ),
        )
        assert plan.title == "Open Port 80"
        assert plan.step_count == 1

    def test_overall_risk_high(self):
        """Test that high-risk step sets overall risk to high."""
        plan = CommandPlan(
            title="Test",
            explanation="Test",
            steps=(
                PlanStep(command="ls", explanation="List", risk_level="low"),
                PlanStep(command="rm -rf", explanation="Delete", risk_level="high"),
            ),
        )
        assert plan.overall_risk == "high"

    def test_overall_risk_medium(self):
        """Test overall risk with medium steps."""
        plan = CommandPlan(
            title="Test",
            explanation="Test",
            steps=(
                PlanStep(command="ls", explanation="List", risk_level="low"),
                PlanStep(command="apt install", explanation="Install", risk_level="medium"),
            ),
        )
        assert plan.overall_risk == "medium"

    def test_overall_risk_low(self):
        """Test overall risk with only low-risk steps."""
        plan = CommandPlan(
            title="Test",
            explanation="Test",
            steps=(
                PlanStep(command="ls", explanation="List", risk_level="low"),
            ),
        )
        assert plan.overall_risk == "low"

    def test_has_rollback(self):
        """Test has_rollback property."""
        plan_no_rb = CommandPlan(
            title="Test",
            explanation="Test",
            steps=(PlanStep(command="ls", explanation="List", risk_level="low"),),
        )
        assert plan_no_rb.has_rollback is False

        plan_with_rb = CommandPlan(
            title="Test",
            explanation="Test",
            steps=(PlanStep(command="ls", explanation="List", risk_level="low"),),
            rollback=(RollbackStep(command="true", explanation="No-op"),),
        )
        assert plan_with_rb.has_rollback is True

    def test_has_checks(self):
        """Test has_checks property."""
        plan_no_checks = CommandPlan(
            title="Test",
            explanation="Test",
            steps=(PlanStep(command="ls", explanation="List", risk_level="low"),),
        )
        assert plan_no_checks.has_checks is False

        plan_with_checks = CommandPlan(
            title="Test",
            explanation="Test",
            steps=(PlanStep(command="ls", explanation="List", risk_level="low"),),
            checks=(ValidationCheck(command="ls", description="Verify"),),
        )
        assert plan_with_checks.has_checks is True


class TestStepVerification:
    """Tests for StepVerification model."""

    def test_valid_step(self):
        """Test valid step verification."""
        result = StepVerification(
            step_index=0,
            command="ls -la",
            is_valid=True,
            checks_passed=("Denylist check passed", "Command parses correctly"),
        )
        assert result.is_valid is True
        assert len(result.checks_passed) == 2

    def test_invalid_step(self):
        """Test invalid step verification."""
        result = StepVerification(
            step_index=0,
            command="rm -rf /",
            is_valid=False,
            checks_failed=("Denylist: Destructive command blocked",),
        )
        assert result.is_valid is False
        assert len(result.checks_failed) == 1


class TestPlanVerification:
    """Tests for PlanVerification model."""

    def test_valid_plan(self):
        """Test valid plan verification."""
        result = PlanVerification(
            is_valid=True,
            step_results=(
                StepVerification(step_index=0, command="ls", is_valid=True),
            ),
        )
        assert result.is_valid is True

    def test_invalid_plan_with_errors(self):
        """Test invalid plan with errors."""
        result = PlanVerification(
            is_valid=False,
            errors=("Step 1: Denylist blocked",),
            rollback_required=True,
        )
        assert result.is_valid is False
        assert result.rollback_required is True


class TestStepResult:
    """Tests for StepResult model."""

    def test_successful_step(self):
        """Test successful step result."""
        result = StepResult(
            step_index=0,
            command="ls",
            exit_code=0,
            stdout="file1\nfile2",
            success=True,
        )
        assert result.success is True
        assert result.exit_code == 0

    def test_failed_step(self):
        """Test failed step result."""
        result = StepResult(
            step_index=0,
            command="cat /nonexistent",
            exit_code=1,
            stderr="No such file",
            success=False,
        )
        assert result.success is False


class TestCheckResult:
    """Tests for CheckResult model."""

    def test_passed_check(self):
        """Test passed check result."""
        result = CheckResult(
            check_index=0,
            command="ufw status",
            passed=True,
            output="Status: active",
        )
        assert result.passed is True

    def test_failed_check(self):
        """Test failed check result."""
        result = CheckResult(
            check_index=0,
            command="ufw status",
            passed=False,
            output="Status: inactive",
            expected="active",
            actual="inactive",
        )
        assert result.passed is False


class TestPlanOutcome:
    """Tests for PlanOutcome enum."""

    def test_all_outcomes(self):
        """Test all outcome values exist."""
        assert PlanOutcome.SUCCESS.value == "success"
        assert PlanOutcome.PARTIAL.value == "partial"
        assert PlanOutcome.FAILED.value == "failed"
        assert PlanOutcome.ROLLED_BACK.value == "rolled_back"
        assert PlanOutcome.CANCELLED.value == "cancelled"
        assert PlanOutcome.ERROR.value == "error"


class TestPlanResult:
    """Tests for PlanResult model."""

    def test_basic_creation(self):
        """Test basic result creation."""
        context = PlanContext(
            request=TaskRequest(request="test task"),
        )
        result = PlanResult(context=context)
        assert result.plan is None
        assert result.outcome == PlanOutcome.CANCELLED

    def test_with_plan(self):
        """Test result with plan."""
        context = PlanContext(request=TaskRequest(request="test"))
        plan = CommandPlan(
            title="Test",
            explanation="Test plan",
            steps=(PlanStep(command="ls", explanation="List", risk_level="low"),),
        )
        result = PlanResult(context=context).with_plan(plan)
        assert result.plan == plan

    def test_with_outcome(self):
        """Test result with outcome."""
        context = PlanContext(request=TaskRequest(request="test"))
        result = PlanResult(context=context).with_outcome(PlanOutcome.SUCCESS)
        assert result.outcome == PlanOutcome.SUCCESS

    def test_is_executable(self):
        """Test is_executable property."""
        context = PlanContext(request=TaskRequest(request="test"))
        plan = CommandPlan(
            title="Test",
            explanation="Test",
            steps=(PlanStep(command="ls", explanation="List", risk_level="low"),),
        )
        verification = PlanVerification(is_valid=True)

        result = (
            PlanResult(context=context)
            .with_plan(plan)
            .with_verification(verification)
        )
        assert result.is_executable is True

    def test_step_counts(self):
        """Test step counting properties."""
        context = PlanContext(request=TaskRequest(request="test"))
        result = PlanResult(
            context=context,
            step_results=(
                StepResult(step_index=0, command="ls", exit_code=0, success=True),
                StepResult(step_index=1, command="cat", exit_code=1, success=False),
            ),
        )
        assert result.steps_succeeded == 1
        assert result.steps_failed == 1


class TestManDocContext:
    """Tests for ManDocContext model."""

    def test_creation(self):
        """Test man doc context creation."""
        doc = ManDocContext(
            name="ufw",
            section="8",
            snippet="ufw - program for managing a netfilter firewall",
        )
        assert doc.name == "ufw"
        assert doc.section == "8"


class TestPriorPlanContext:
    """Tests for PriorPlanContext model."""

    def test_creation(self):
        """Test prior plan context creation."""
        prior = PriorPlanContext(
            incident_id="abc123",
            title="Open port 80",
            outcome="success",
            commands_executed=("ufw allow 80/tcp",),
        )
        assert prior.incident_id == "abc123"
        assert len(prior.commands_executed) == 1
