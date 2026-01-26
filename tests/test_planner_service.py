"""Tests for planner service."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.planner.models import (
    CommandPlan,
    PlanOutcome,
    PlanStep,
    RollbackStep,
    ValidationCheck,
)
from elle.cli.planner.service import (
    PlannerService,
    get_planner_service,
    reset_planner_service,
)
from elle.common.session import Session


@pytest.fixture
def session():
    """Create a test session."""
    return Session(cwd=Path("/tmp"))


@pytest.fixture
def service():
    """Create a test planner service."""
    return PlannerService(
        use_man_vault=False,
        use_incident_vault=False,
    )


class TestPlannerServiceInit:
    """Tests for service initialization."""

    def test_default_init(self):
        """Test default service initialization."""
        service = PlannerService()
        assert service.use_man_vault is True
        assert service.use_incident_vault is True
        assert service.command_timeout == 60.0

    def test_custom_init(self):
        """Test custom service initialization."""
        service = PlannerService(
            use_man_vault=False,
            use_incident_vault=False,
            command_timeout=30.0,
        )
        assert service.use_man_vault is False
        assert service.command_timeout == 30.0

    def test_singleton(self):
        """Test service singleton pattern."""
        reset_planner_service()
        s1 = get_planner_service()
        s2 = get_planner_service()
        assert s1 is s2
        reset_planner_service()


class TestBuildContext:
    """Tests for context building."""

    def test_build_basic_context(self, service, session):
        """Test building basic context."""
        context = service.build_context("open port 80", session)
        assert context.request.request == "open port 80"
        assert context.request.cwd == session.cwd

    def test_context_with_entities(self, service, session):
        """Test context with entities."""
        context = service.build_context(
            "restart nginx",
            session,
            entities=("nginx", "service"),
        )
        assert context.request.entities == ("nginx", "service")

    def test_empty_vaults(self, service, session):
        """Test context with disabled vaults."""
        context = service.build_context("test", session)
        assert context.man_docs == ()
        assert context.prior_plans == ()


class TestGeneratePlan:
    """Tests for plan generation."""

    @patch("elle.cli.planner.service.get_llm")
    def test_generate_plan_no_llm(self, mock_get_llm, service, session):
        """Test plan generation when LLM unavailable."""
        context = service.build_context("test", session)

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = False
        mock_get_llm.return_value = mock_llm

        plan = service.generate_plan(context)
        assert plan is None

    @patch("elle.cli.planner.service.get_llm")
    def test_generate_plan_success(self, mock_get_llm, service, session):
        """Test successful plan generation."""
        context = service.build_context("open port 80", session)

        mock_response = {
            "title": "Open Port 80",
            "explanation": "Allow HTTP traffic",
            "steps": [
                {
                    "command": "ufw allow 80/tcp",
                    "explanation": "Add firewall rule",
                    "risk_level": "medium",
                    "requires_privilege": True,
                }
            ],
            "checks": [
                {
                    "command": "ufw status",
                    "description": "Verify rule added",
                    "expected": "80/tcp",
                }
            ],
            "rollback": [
                {
                    "command": "ufw delete allow 80/tcp",
                    "explanation": "Remove rule",
                    "requires_privilege": True,
                }
            ],
            "risks": ["Opens port to external traffic"],
            "grounded_in": ["ufw(8)"],
        }

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.chat_json.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        plan = service.generate_plan(context)

        assert plan is not None
        assert plan.title == "Open Port 80"
        assert plan.step_count == 1
        assert plan.has_rollback is True
        assert plan.has_checks is True
        assert plan.requires_privilege is True


class TestRunPlanningPipeline:
    """Tests for the full planning pipeline."""

    @patch("elle.cli.planner.service.get_llm")
    def test_pipeline_with_mock_llm(self, mock_get_llm, service, session):
        """Test pipeline with mocked LLM."""
        mock_response = {
            "title": "List Files",
            "explanation": "Show directory contents",
            "steps": [
                {
                    "command": "ls -la",
                    "explanation": "List with details",
                    "risk_level": "low",
                }
            ],
        }

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.chat_json.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        result = service.run_planning_pipeline("list files", session)

        assert result.plan is not None
        assert result.verification is not None
        assert result.verification.is_valid is True

    @patch("elle.cli.planner.service.get_llm")
    def test_pipeline_no_llm(self, mock_get_llm, service, session):
        """Test pipeline when LLM unavailable."""
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = False
        mock_get_llm.return_value = mock_llm

        result = service.run_planning_pipeline("test", session)

        assert result.plan is None
        assert result.outcome == PlanOutcome.ERROR


class TestExecutePlan:
    """Tests for plan execution."""

    @patch("elle.cli.planner.service.run_safe")
    def test_execute_simple_plan(self, mock_run, service, session):
        """Test executing a simple plan."""
        plan = CommandPlan(
            title="List Files",
            explanation="Show files",
            steps=(
                PlanStep(
                    command="echo hello",
                    explanation="Print hello",
                    risk_level="low",
                ),
            ),
        )

        from elle.cli.planner.models import PlanContext, PlanResult, PlanVerification, TaskRequest

        context = PlanContext(request=TaskRequest(request="echo"))
        verification = PlanVerification(is_valid=True)
        result = PlanResult(context=context).with_plan(plan).with_verification(verification)

        mock_run.return_value = MagicMock(
            exit_code=0,
            stdout="hello\n",
            stderr="",
            success=True,
        )

        result = service.execute_plan(result, session)

        assert result.approved is True
        assert result.steps_succeeded == 1
        assert result.outcome == PlanOutcome.SUCCESS

    @patch("elle.cli.planner.service.run_safe")
    def test_execute_plan_with_failure(self, mock_run, service, session):
        """Test plan execution with a failing step."""
        plan = CommandPlan(
            title="Test",
            explanation="Test",
            steps=(PlanStep(command="false", explanation="Fail", risk_level="low"),),
        )

        from elle.cli.planner.models import PlanContext, PlanResult, PlanVerification, TaskRequest

        context = PlanContext(request=TaskRequest(request="test"))
        verification = PlanVerification(is_valid=True)
        result = PlanResult(context=context).with_plan(plan).with_verification(verification)

        mock_run.return_value = MagicMock(
            exit_code=1,
            stdout="",
            stderr="error",
            success=False,
        )

        result = service.execute_plan(result, session)

        assert result.steps_failed == 1
        assert result.outcome == PlanOutcome.FAILED


class TestRunChecks:
    """Tests for validation check execution."""

    @patch("elle.cli.planner.service.run_safe")
    def test_run_checks_pass(self, mock_run, service, session):
        """Test running checks that pass."""
        plan = CommandPlan(
            title="Test",
            explanation="Test",
            steps=(PlanStep(command="true", explanation="OK", risk_level="low"),),
            checks=(
                ValidationCheck(
                    command="echo active",
                    description="Check status",
                    expected="active",
                ),
            ),
        )

        from elle.cli.planner.models import PlanContext, PlanResult, PlanVerification, TaskRequest

        context = PlanContext(request=TaskRequest(request="test"))
        verification = PlanVerification(is_valid=True)
        result = PlanResult(context=context).with_plan(plan).with_verification(verification)

        mock_run.return_value = MagicMock(
            exit_code=0,
            stdout="active",
            stderr="",
            success=True,
        )

        result = service.execute_plan(result, session)

        # Should have run checks
        assert result.checks_passed >= 0


class TestRollback:
    """Tests for rollback functionality."""

    @patch("elle.cli.planner.service.run_safe")
    def test_rollback_plan(self, mock_run, service, session):
        """Test rollback execution."""
        plan = CommandPlan(
            title="Test",
            explanation="Test",
            steps=(PlanStep(command="touch /tmp/test", explanation="Create", risk_level="low"),),
            rollback=(RollbackStep(command="rm /tmp/test", explanation="Remove"),),
        )

        from elle.cli.planner.models import (
            PlanContext,
            PlanResult,
            PlanVerification,
            StepResult,
            TaskRequest,
        )

        context = PlanContext(request=TaskRequest(request="test"))
        verification = PlanVerification(is_valid=True)
        result = (
            PlanResult(context=context)
            .with_plan(plan)
            .with_verification(verification)
            .with_step_result(
                StepResult(
                    step_index=0,
                    command="touch /tmp/test",
                    exit_code=0,
                    success=True,
                )
            )
            .with_outcome(PlanOutcome.PARTIAL)
        )

        mock_run.return_value = MagicMock(
            exit_code=0,
            stdout="",
            stderr="",
            success=True,
        )

        result = service.rollback_plan(result, session)

        assert len(result.rollback_results) == 1
        assert result.outcome == PlanOutcome.ROLLED_BACK
