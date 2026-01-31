"""Tests for elle.cli.planner.service -- Plan execution service."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from elle.cli.planner.models import (
    CommandPlan,
    DockerState,
    NetworkState,
    PlanContext,
    PlanOutcome,
    PlanResult,
    PlanStep,
    RollbackStep,
    StepResult,
    TaskRequest,
    ValidationCheck,
)
from elle.cli.planner.service import (
    PlannerService,
    get_planner_service,
    reset_planner_service,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session():
    """Create a mock Session."""
    session = MagicMock()
    session.cwd = Path("/tmp")
    return session


def _make_step(command="echo hello", risk="low", privileged=False, can_fail=False):
    return PlanStep(
        command=command,
        explanation="Test step",
        risk_level=risk,
        requires_privilege=privileged,
        can_fail=can_fail,
    )


def _make_plan(title="Test Plan", steps=None, checks=None, rollback=None, risks=()):
    if steps is None:
        steps = (_make_step(),)
    return CommandPlan(
        title=title,
        explanation="Test explanation",
        steps=tuple(steps),
        checks=tuple(checks or []),
        rollback=tuple(rollback or []),
        risks=risks,
    )


def _make_plan_result(plan=None, context=None, incident_id=None, outcome=PlanOutcome.CANCELLED):
    if context is None:
        context = PlanContext(request=TaskRequest(request="test request"))
    return PlanResult(
        context=context,
        plan=plan,
        outcome=outcome,
        incident_id=incident_id,
    )


def _make_step_result(success=True, step_index=0, command="echo hello"):
    return StepResult(
        step_index=step_index,
        command=command,
        exit_code=0 if success else 1,
        stdout="output",
        stderr="" if success else "error",
        success=success,
        duration_ms=100,
        executed_at=datetime.now(),
    )


# ---------------------------------------------------------------------------
# PlannerService.__init__
# ---------------------------------------------------------------------------


class TestPlannerServiceInit:
    def test_defaults(self):
        svc = PlannerService()
        assert svc.use_man_vault is True
        assert svc.use_incident_vault is True
        assert svc.command_timeout == 60.0
        assert svc.use_capabilities is True
        assert svc.use_preflight is True

    def test_custom(self):
        svc = PlannerService(
            use_man_vault=False,
            use_incident_vault=False,
            command_timeout=120.0,
            use_capabilities=False,
            use_preflight=False,
        )
        assert svc.use_man_vault is False
        assert svc.command_timeout == 120.0


# ---------------------------------------------------------------------------
# capability_executor and preflight_validator properties
# ---------------------------------------------------------------------------


class TestLazyProperties:
    def test_capability_executor_disabled(self):
        svc = PlannerService(use_capabilities=False)
        assert svc.capability_executor is None

    def test_preflight_disabled(self):
        svc = PlannerService(use_preflight=False)
        assert svc.preflight_validator is None


# ---------------------------------------------------------------------------
# _is_docker_task / _is_network_task
# ---------------------------------------------------------------------------


class TestTaskDetection:
    def test_docker_by_keyword(self):
        svc = PlannerService()
        assert svc._is_docker_task("restart docker container", ()) is True

    def test_docker_by_entity(self):
        svc = PlannerService()
        assert svc._is_docker_task("restart something", ("docker",)) is True

    def test_not_docker(self):
        svc = PlannerService()
        assert svc._is_docker_task("restart nginx", ()) is False

    def test_network_by_keyword(self):
        svc = PlannerService()
        assert svc._is_network_task("configure firewall rules", ()) is True
        assert svc._is_network_task("set up wireguard vpn", ()) is True
        assert svc._is_network_task("add ufw rule for port 80", ()) is True

    def test_network_by_entity(self):
        svc = PlannerService()
        assert svc._is_network_task("configure something", ("network",)) is True

    def test_not_network(self):
        svc = PlannerService()
        assert svc._is_network_task("restart nginx", ()) is False


# ---------------------------------------------------------------------------
# _parse_plan_response
# ---------------------------------------------------------------------------


class TestParsePlanResponse:
    def test_basic_response(self):
        svc = PlannerService()
        response = {
            "title": "Install nginx",
            "explanation": "Install the nginx package",
            "steps": [
                {"command": "apt install nginx", "explanation": "Install", "risk_level": "low"},
            ],
            "checks": [
                {"command": "nginx -t", "description": "Verify config"},
            ],
            "rollback": [
                {"command": "apt remove nginx", "explanation": "Remove nginx"},
            ],
            "risks": ["May conflict with Apache"],
            "grounded_in": ["nginx(1)"],
        }
        plan = svc._parse_plan_response(response)
        assert isinstance(plan, CommandPlan)
        assert plan.title == "Install nginx"
        assert plan.step_count == 1
        assert plan.has_checks is True
        assert plan.has_rollback is True

    def test_empty_response(self):
        svc = PlannerService()
        response = {}
        plan = svc._parse_plan_response(response)
        assert plan.title == "System Task"
        assert plan.step_count == 0

    def test_privilege_detection(self):
        svc = PlannerService()
        response = {
            "steps": [
                {"command": "cmd", "explanation": "e", "requires_privilege": True},
            ],
        }
        plan = svc._parse_plan_response(response)
        assert plan.requires_privilege is True


# ---------------------------------------------------------------------------
# _parse_package_list
# ---------------------------------------------------------------------------


class TestParsePackageList:
    def test_basic(self):
        svc = PlannerService()
        result = svc._parse_package_list("nginx curl wget")
        assert result == ("nginx", "curl", "wget")

    def test_filters_flags(self):
        svc = PlannerService()
        result = svc._parse_package_list("-y nginx --no-install-recommends")
        assert "nginx" in result
        assert "-y" not in result

    def test_filters_version_specifiers(self):
        svc = PlannerService()
        result = svc._parse_package_list("nginx =1.18")
        assert "nginx" in result
        assert "=1.18" not in result

    def test_empty(self):
        svc = PlannerService()
        result = svc._parse_package_list("")
        assert result == ()


# ---------------------------------------------------------------------------
# _extract_package_operations
# ---------------------------------------------------------------------------


class TestExtractPackageOperations:
    def test_apt_install(self):
        svc = PlannerService()
        plan = _make_plan(steps=[_make_step(command="apt install nginx")])
        ops = svc._extract_package_operations(plan)
        assert len(ops) == 1
        assert ops[0][1] == "install"

    def test_apt_get_install(self):
        svc = PlannerService()
        plan = _make_plan(steps=[_make_step(command="apt-get install curl")])
        ops = svc._extract_package_operations(plan)
        assert len(ops) == 1

    def test_apt_upgrade(self):
        svc = PlannerService()
        plan = _make_plan(steps=[_make_step(command="apt upgrade")])
        ops = svc._extract_package_operations(plan)
        assert len(ops) == 1
        assert ops[0][1] == "upgrade"

    def test_apt_remove(self):
        svc = PlannerService()
        plan = _make_plan(steps=[_make_step(command="apt remove nginx")])
        ops = svc._extract_package_operations(plan)
        assert len(ops) == 1
        assert ops[0][1] == "remove"

    def test_no_package_ops(self):
        svc = PlannerService()
        plan = _make_plan(steps=[_make_step(command="echo hello")])
        ops = svc._extract_package_operations(plan)
        assert ops == []


# ---------------------------------------------------------------------------
# _record_action_safe
# ---------------------------------------------------------------------------


class TestRecordActionSafe:
    def test_no_incident_id(self):
        svc = PlannerService()
        step_result = _make_step_result()
        # Should not raise
        svc._record_action_safe(None, "echo", step_result)

    @patch("elle.cli.planner.service.PlannerService._record_action")
    def test_with_incident_id(self, mock_record):
        svc = PlannerService()
        step_result = _make_step_result()
        svc._record_action_safe("inc-123", "echo", step_result)
        mock_record.assert_called_once()

    @patch("elle.cli.planner.service.PlannerService._record_action", side_effect=Exception("fail"))
    def test_exception_caught(self, mock_record):
        svc = PlannerService()
        step_result = _make_step_result()
        # Should not raise
        svc._record_action_safe("inc-123", "echo", step_result)


# ---------------------------------------------------------------------------
# generate_plan
# ---------------------------------------------------------------------------


class TestGeneratePlan:
    @patch("elle.cli.planner.service.get_llm")
    def test_llm_not_available(self, mock_get_llm):
        llm = MagicMock()
        llm.is_available.return_value = False
        mock_get_llm.return_value = llm

        svc = PlannerService()
        context = PlanContext(request=TaskRequest(request="test"))
        result = svc.generate_plan(context)
        assert result is None

    @patch("elle.cli.planner.service.get_llm")
    def test_llm_returns_plan(self, mock_get_llm):
        llm = MagicMock()
        llm.is_available.return_value = True
        llm.chat_json.return_value = {
            "title": "Test Plan",
            "explanation": "Do stuff",
            "steps": [{"command": "echo hi", "explanation": "Say hi", "risk_level": "low"}],
        }
        mock_get_llm.return_value = llm

        svc = PlannerService()
        context = PlanContext(request=TaskRequest(request="test"))
        plan = svc.generate_plan(context)
        assert isinstance(plan, CommandPlan)
        assert plan.title == "Test Plan"

    @patch("elle.cli.planner.service.get_llm", side_effect=Exception("LLM error"))
    def test_llm_exception(self, mock_get_llm):
        svc = PlannerService()
        context = PlanContext(request=TaskRequest(request="test"))
        result = svc.generate_plan(context)
        assert result is None


# ---------------------------------------------------------------------------
# build_context
# ---------------------------------------------------------------------------


class TestBuildContext:
    @patch("elle.cli.planner.service.PlannerService._search_man_vault", return_value=())
    @patch("elle.cli.planner.service.PlannerService._search_prior_plans", return_value=())
    def test_basic_context(self, mock_prior, mock_man):
        svc = PlannerService()
        session = _make_session()
        ctx = svc.build_context("test request", session)
        assert ctx.request.request == "test request"

    @patch("elle.cli.planner.service.PlannerService._search_man_vault", return_value=())
    @patch("elle.cli.planner.service.PlannerService._search_prior_plans", return_value=())
    def test_no_vaults(self, mock_prior, mock_man):
        svc = PlannerService(use_man_vault=False, use_incident_vault=False)
        session = _make_session()
        svc.build_context("test", session)
        mock_man.assert_not_called()
        mock_prior.assert_not_called()

    @patch("elle.cli.planner.service.PlannerService._search_man_vault", return_value=())
    @patch("elle.cli.planner.service.PlannerService._search_prior_plans", return_value=())
    @patch("elle.cli.planner.service.PlannerService._get_docker_state")
    def test_docker_task(self, mock_docker, mock_prior, mock_man):
        mock_docker.return_value = DockerState()
        svc = PlannerService()
        session = _make_session()
        svc.build_context("restart docker container", session)
        mock_docker.assert_called_once()

    @patch("elle.cli.planner.service.PlannerService._search_man_vault", return_value=())
    @patch("elle.cli.planner.service.PlannerService._search_prior_plans", return_value=())
    @patch("elle.cli.planner.service.PlannerService._get_network_state")
    def test_network_task(self, mock_net, mock_prior, mock_man):
        mock_net.return_value = NetworkState()
        svc = PlannerService()
        session = _make_session()
        svc.build_context("configure firewall", session)
        mock_net.assert_called_once()

    @patch("elle.cli.planner.service.PlannerService._search_man_vault", return_value=())
    @patch("elle.cli.planner.service.PlannerService._search_prior_plans", return_value=())
    def test_with_entities(self, mock_prior, mock_man):
        svc = PlannerService()
        session = _make_session()
        ctx = svc.build_context("restart nginx", session, entities=("nginx", "service"))
        assert ctx.request.entities == ("nginx", "service")


# ---------------------------------------------------------------------------
# execute_plan
# ---------------------------------------------------------------------------


class TestExecutePlan:
    @patch("elle.cli.planner.service.run_safe")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    def test_success(self, mock_record, mock_run):
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        mock_result.success = True
        mock_run.return_value = mock_result

        svc = PlannerService(use_incident_vault=False, use_capabilities=False, use_preflight=False)
        plan = _make_plan()
        pr = _make_plan_result(plan=plan)
        result = svc.execute_plan(pr, _make_session(), skip_preflight=True)
        assert result.outcome == PlanOutcome.SUCCESS

    def test_no_plan(self):
        svc = PlannerService(use_incident_vault=False, use_preflight=False)
        pr = _make_plan_result(plan=None)
        result = svc.execute_plan(pr, _make_session())
        assert result.outcome == PlanOutcome.ERROR

    @patch("elle.cli.planner.service.run_safe")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    def test_failure_stops(self, mock_record, mock_run):
        mock_result = MagicMock()
        mock_result.exit_code = 1
        mock_result.stdout = ""
        mock_result.stderr = "error"
        mock_result.success = False
        mock_run.return_value = mock_result

        svc = PlannerService(use_incident_vault=False, use_capabilities=False, use_preflight=False)
        plan = _make_plan(steps=[_make_step(), _make_step()])
        pr = _make_plan_result(plan=plan)
        result = svc.execute_plan(pr, _make_session(), skip_preflight=True)
        assert result.outcome == PlanOutcome.FAILED
        # Should stop after first failure
        assert len(result.step_results) == 1

    @patch("elle.cli.planner.service.run_safe")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    def test_can_fail_continues(self, mock_record, mock_run):
        fail_result = MagicMock()
        fail_result.exit_code = 1
        fail_result.stdout = ""
        fail_result.stderr = "err"
        fail_result.success = False

        success_result = MagicMock()
        success_result.exit_code = 0
        success_result.stdout = "ok"
        success_result.stderr = ""
        success_result.success = True

        mock_run.side_effect = [fail_result, success_result]

        svc = PlannerService(use_incident_vault=False, use_capabilities=False, use_preflight=False)
        plan = _make_plan(steps=[_make_step(can_fail=True), _make_step()])
        pr = _make_plan_result(plan=plan)
        result = svc.execute_plan(pr, _make_session(), skip_preflight=True)
        assert result.outcome == PlanOutcome.PARTIAL
        assert len(result.step_results) == 2

    @patch("elle.cli.planner.service.run_safe")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    def test_with_checks(self, mock_record, mock_run):
        success_result = MagicMock()
        success_result.exit_code = 0
        success_result.stdout = "ok"
        success_result.stderr = ""
        success_result.success = True
        mock_run.return_value = success_result

        svc = PlannerService(use_incident_vault=False, use_capabilities=False, use_preflight=False)
        plan = _make_plan(
            checks=[ValidationCheck(command="echo check", description="verify")],
        )
        pr = _make_plan_result(plan=plan)
        result = svc.execute_plan(pr, _make_session(), skip_preflight=True)
        assert result.outcome == PlanOutcome.SUCCESS
        assert len(result.check_results) == 1


# ---------------------------------------------------------------------------
# rollback_plan
# ---------------------------------------------------------------------------


class TestRollbackPlan:
    @patch("elle.cli.planner.service.run_safe")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    def test_rollback(self, mock_record, mock_run):
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        mock_result.success = True
        mock_run.return_value = mock_result

        svc = PlannerService(use_incident_vault=False, use_preflight=False)
        plan = _make_plan(
            rollback=[RollbackStep(command="undo", explanation="undo step")],
        )
        pr = _make_plan_result(plan=plan)
        result = svc.rollback_plan(pr, _make_session())
        assert result.outcome == PlanOutcome.ROLLED_BACK

    def test_no_rollback_available(self):
        svc = PlannerService(use_incident_vault=False, use_preflight=False)
        plan = _make_plan(rollback=[])
        pr = _make_plan_result(plan=plan)
        result = svc.rollback_plan(pr, _make_session())
        assert result.outcome == PlanOutcome.CANCELLED  # No change

    def test_no_plan(self):
        svc = PlannerService(use_incident_vault=False, use_preflight=False)
        pr = _make_plan_result(plan=None)
        result = svc.rollback_plan(pr, _make_session())
        assert result.plan is None


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------


class TestFinalize:
    def test_finalize_no_incident(self):
        svc = PlannerService(use_incident_vault=False)
        pr = _make_plan_result()
        result = svc.finalize(pr)
        assert result is pr

    @patch("elle.cli.planner.service.PlannerService._finalize_incident")
    def test_finalize_with_incident(self, mock_finalize):
        svc = PlannerService(use_incident_vault=True)
        pr = _make_plan_result(incident_id="inc-123")
        svc.finalize(pr)
        mock_finalize.assert_called_once_with(pr)

    @patch("elle.cli.planner.service.PlannerService._finalize_incident", side_effect=Exception("fail"))
    def test_finalize_exception_caught(self, mock_finalize):
        svc = PlannerService(use_incident_vault=True)
        pr = _make_plan_result(incident_id="inc-123")
        # Should not raise
        result = svc.finalize(pr)
        assert result is pr


# ---------------------------------------------------------------------------
# run_preflight_for_plan
# ---------------------------------------------------------------------------


class TestRunPreflightForPlan:
    def test_no_validator(self):
        svc = PlannerService(use_preflight=False)
        plan = _make_plan()
        can_proceed, msg = svc.run_preflight_for_plan(plan)
        assert can_proceed is True
        assert msg is None

    def test_no_package_ops(self):
        svc = PlannerService()
        svc._preflight_validator = MagicMock()
        plan = _make_plan(steps=[_make_step(command="echo hello")])
        can_proceed, msg = svc.run_preflight_for_plan(plan)
        assert can_proceed is True
        assert msg is None


# ---------------------------------------------------------------------------
# run_planning_pipeline
# ---------------------------------------------------------------------------


class TestRunPlanningPipeline:
    @patch("elle.cli.planner.service.get_llm")
    def test_pipeline_no_llm(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = False
        mock_get_llm.return_value = mock_llm

        svc = PlannerService(use_man_vault=False, use_incident_vault=False)
        session = _make_session()
        result = svc.run_planning_pipeline("test", session)
        assert result.plan is None
        assert result.outcome == PlanOutcome.ERROR

    @patch("elle.cli.planner.service.get_llm")
    def test_pipeline_success(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.chat_json.return_value = {
            "title": "List Files",
            "explanation": "Show directory contents",
            "steps": [
                {"command": "ls -la", "explanation": "List with details", "risk_level": "low"},
            ],
        }
        mock_get_llm.return_value = mock_llm

        svc = PlannerService(use_man_vault=False, use_incident_vault=False)
        session = _make_session()
        result = svc.run_planning_pipeline("list files", session)
        assert result.plan is not None
        assert result.verification is not None


# ---------------------------------------------------------------------------
# get_planner_service / reset_planner_service
# ---------------------------------------------------------------------------


class TestServiceSingleton:
    def test_get_returns_instance(self):
        reset_planner_service()
        svc = get_planner_service()
        assert isinstance(svc, PlannerService)

    def test_returns_same_instance(self):
        reset_planner_service()
        svc1 = get_planner_service()
        svc2 = get_planner_service()
        assert svc1 is svc2

    def test_reset_clears(self):
        reset_planner_service()
        svc1 = get_planner_service()
        reset_planner_service()
        svc2 = get_planner_service()
        assert svc1 is not svc2
