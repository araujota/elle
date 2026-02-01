"""Tests for elle.cli.planner.service -- Plan execution service.

Covers ~20 test classes spanning: initialization, context building, plan
generation/parsing, pipeline orchestration, step execution (command and
capability paths), rollback, validation checks, preflight, incident
recording/finalization, decision record storage, and the module-level
singleton.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from elle.cli.planner.models import (
    CheckResult,
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
    capability: str | None = None,
    capability_input: dict | None = None,
) -> PlanStep:
    return PlanStep(
        command=command,
        explanation="Test step",
        risk_level=risk,
        requires_privilege=privileged,
        can_fail=can_fail,
        capability=capability,
        capability_input=capability_input,
    )


def _make_plan(
    title: str = "Test Plan",
    steps: list | tuple | None = None,
    checks: list | tuple | None = None,
    rollback: list | tuple | None = None,
    risks: tuple[str, ...] = (),
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
    )


def _make_plan_result(
    plan: CommandPlan | None = None,
    context: PlanContext | None = None,
    incident_id: str | None = None,
    outcome: PlanOutcome = PlanOutcome.CANCELLED,
) -> PlanResult:
    if context is None:
        context = PlanContext(request=TaskRequest(request="test request"))
    return PlanResult(
        context=context,
        plan=plan,
        outcome=outcome,
        incident_id=incident_id,
    )


def _make_step_result(
    success: bool = True,
    step_index: int = 0,
    command: str = "echo hello",
) -> StepResult:
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


def _mock_run_safe(
    success: bool = True,
    exit_code: int = 0,
    stdout: str = "ok",
    stderr: str = "",
) -> MagicMock:
    """Create a mock return value for run_safe."""
    result = MagicMock()
    result.success = success
    result.exit_code = exit_code
    result.stdout = stdout
    result.stderr = stderr
    return result


# =============================================================================
# 1. PlannerService.__init__
# =============================================================================


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
        assert svc.use_capabilities is False
        assert svc.use_preflight is False


# =============================================================================
# 2. Lazy property accessors
# =============================================================================


class TestLazyProperties:
    def test_capability_executor_disabled(self):
        svc = PlannerService(use_capabilities=False)
        assert svc.capability_executor is None

    def test_preflight_disabled(self):
        svc = PlannerService(use_preflight=False)
        assert svc.preflight_validator is None

    @patch("elle.capabilities.get_executor")
    def test_capability_executor_import_success(self, mock_get):
        mock_exec = MagicMock()
        mock_get.return_value = mock_exec
        svc = PlannerService(use_capabilities=True)
        assert svc.capability_executor is mock_exec

    @patch("elle.capabilities.get_executor", side_effect=ImportError("not installed"))
    def test_capability_executor_import_error(self, mock_get):
        svc = PlannerService(use_capabilities=True)
        assert svc.capability_executor is None

    @patch("elle.ops.preflight.get_validator")
    def test_preflight_validator_import_success(self, mock_get):
        mock_val = MagicMock()
        mock_get.return_value = mock_val
        svc = PlannerService(use_preflight=True)
        assert svc.preflight_validator is mock_val

    @patch("elle.ops.preflight.get_validator", side_effect=ImportError("not installed"))
    def test_preflight_validator_import_error(self, mock_get):
        svc = PlannerService(use_preflight=True)
        assert svc.preflight_validator is None


# =============================================================================
# 3. _is_docker_task / _is_network_task
# =============================================================================


class TestTaskDetection:
    def test_docker_by_keyword(self):
        svc = PlannerService()
        assert svc._is_docker_task("restart docker container", ()) is True

    def test_docker_compose_keyword(self):
        svc = PlannerService()
        assert svc._is_docker_task("run compose up", ()) is True

    def test_docker_image_keyword(self):
        svc = PlannerService()
        assert svc._is_docker_task("pull image from registry", ()) is True

    def test_docker_by_entity(self):
        svc = PlannerService()
        assert svc._is_docker_task("restart something", ("docker",)) is True

    def test_not_docker(self):
        svc = PlannerService()
        assert svc._is_docker_task("restart nginx", ()) is False

    def test_network_by_keyword_firewall(self):
        svc = PlannerService()
        assert svc._is_network_task("configure firewall rules", ()) is True

    def test_network_by_keyword_wireguard(self):
        svc = PlannerService()
        assert svc._is_network_task("set up wireguard vpn", ()) is True

    def test_network_by_keyword_ufw(self):
        svc = PlannerService()
        assert svc._is_network_task("add ufw rule for port 80", ()) is True

    def test_network_by_keyword_dns(self):
        svc = PlannerService()
        assert svc._is_network_task("check dns resolution", ()) is True

    def test_network_by_keyword_port(self):
        svc = PlannerService()
        assert svc._is_network_task("open port 443", ()) is True

    def test_network_by_entity_network(self):
        svc = PlannerService()
        assert svc._is_network_task("configure something", ("network",)) is True

    def test_network_by_entity_wireguard(self):
        svc = PlannerService()
        assert svc._is_network_task("configure something", ("wireguard",)) is True

    def test_not_network(self):
        svc = PlannerService()
        assert svc._is_network_task("restart nginx", ()) is False


# =============================================================================
# 4. _parse_plan_response
# =============================================================================


class TestParsePlanResponse:
    def test_full_response(self):
        svc = PlannerService()
        response = {
            "title": "Install nginx",
            "explanation": "Install the nginx package",
            "steps": [
                {
                    "command": "apt install nginx",
                    "explanation": "Install",
                    "risk_level": "low",
                    "requires_privilege": True,
                    "can_fail": True,
                },
            ],
            "checks": [
                {"command": "nginx -t", "description": "Verify config", "expected": "ok"},
            ],
            "rollback": [
                {"command": "apt remove nginx", "explanation": "Remove nginx", "requires_privilege": True},
            ],
            "risks": ["May conflict with Apache"],
            "grounded_in": ["nginx(1)"],
        }
        plan = svc._parse_plan_response(response)
        assert isinstance(plan, CommandPlan)
        assert plan.title == "Install nginx"
        assert plan.step_count == 1
        assert plan.steps[0].requires_privilege is True
        assert plan.steps[0].can_fail is True
        assert plan.has_checks is True
        assert plan.checks[0].expected == "ok"
        assert plan.has_rollback is True
        assert plan.rollback[0].requires_privilege is True
        assert plan.risks == ("May conflict with Apache",)
        assert plan.grounded_in == ("nginx(1)",)
        assert plan.requires_privilege is True

    def test_empty_response(self):
        svc = PlannerService()
        response = {}
        plan = svc._parse_plan_response(response)
        assert plan.title == "System Task"
        assert plan.explanation == ""
        assert plan.step_count == 0
        assert plan.risks == ()
        assert plan.grounded_in == ()

    def test_privilege_detection_from_steps(self):
        svc = PlannerService()
        response = {
            "steps": [
                {"command": "ls", "explanation": "list", "risk_level": "low"},
                {"command": "systemctl restart", "explanation": "restart", "requires_privilege": True},
            ],
        }
        plan = svc._parse_plan_response(response)
        assert plan.requires_privilege is True

    def test_defaults_for_missing_step_fields(self):
        svc = PlannerService()
        response = {
            "steps": [{"command": "echo hi", "explanation": "say hi"}],
        }
        plan = svc._parse_plan_response(response)
        assert plan.steps[0].risk_level == "medium"
        assert plan.steps[0].requires_privilege is False
        assert plan.steps[0].can_fail is False


# =============================================================================
# 5. _parse_package_list
# =============================================================================


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

    def test_filters_known_apt_options(self):
        svc = PlannerService()
        result = svc._parse_package_list("--yes nginx --force-yes curl")
        assert result == ("nginx", "curl")

    def test_filters_version_specifiers(self):
        svc = PlannerService()
        result = svc._parse_package_list("nginx =1.18 curl >2.0 htop <3.0")
        assert result == ("nginx", "curl", "htop")

    def test_empty(self):
        svc = PlannerService()
        result = svc._parse_package_list("")
        assert result == ()

    def test_double_dash_filtered(self):
        svc = PlannerService()
        result = svc._parse_package_list("-- nginx")
        assert "--" not in result
        assert "nginx" in result


# =============================================================================
# 6. _extract_package_operations
# =============================================================================


class TestExtractPackageOperations:
    def test_apt_install(self):
        svc = PlannerService()
        plan = _make_plan(steps=[_make_step(command="apt install nginx")])
        ops = svc._extract_package_operations(plan)
        assert len(ops) == 1
        assert ops[0][1] == "install"
        assert "nginx" in ops[0][0]

    def test_apt_get_install(self):
        svc = PlannerService()
        plan = _make_plan(steps=[_make_step(command="apt-get install curl")])
        ops = svc._extract_package_operations(plan)
        assert len(ops) == 1
        assert "curl" in ops[0][0]

    def test_dpkg_install(self):
        svc = PlannerService()
        plan = _make_plan(steps=[_make_step(command="dpkg install mypackage.deb")])
        ops = svc._extract_package_operations(plan)
        assert len(ops) == 1
        assert ops[0][1] == "install"

    def test_apt_upgrade(self):
        svc = PlannerService()
        plan = _make_plan(steps=[_make_step(command="apt upgrade")])
        ops = svc._extract_package_operations(plan)
        assert len(ops) == 1
        assert ops[0][1] == "upgrade"

    def test_apt_dist_upgrade(self):
        svc = PlannerService()
        plan = _make_plan(steps=[_make_step(command="apt-get dist-upgrade")])
        ops = svc._extract_package_operations(plan)
        assert len(ops) == 1
        assert ops[0][1] == "upgrade"

    def test_apt_full_upgrade(self):
        svc = PlannerService()
        plan = _make_plan(steps=[_make_step(command="apt full-upgrade")])
        ops = svc._extract_package_operations(plan)
        assert len(ops) == 1
        assert ops[0][1] == "upgrade"

    def test_apt_remove(self):
        svc = PlannerService()
        plan = _make_plan(steps=[_make_step(command="apt remove nginx")])
        ops = svc._extract_package_operations(plan)
        assert len(ops) == 1
        assert ops[0][1] == "remove"

    def test_apt_purge(self):
        svc = PlannerService()
        plan = _make_plan(steps=[_make_step(command="apt purge nginx")])
        ops = svc._extract_package_operations(plan)
        assert len(ops) == 1
        assert ops[0][1] == "remove"

    def test_no_package_ops(self):
        svc = PlannerService()
        plan = _make_plan(steps=[_make_step(command="echo hello")])
        ops = svc._extract_package_operations(plan)
        assert ops == []

    def test_null_command_step(self):
        svc = PlannerService()
        step = PlanStep(
            command=None,
            explanation="Capability step",
            risk_level="low",
            capability="service.restart",
        )
        plan = _make_plan(steps=[step])
        ops = svc._extract_package_operations(plan)
        assert ops == []


# =============================================================================
# 7. _record_action_safe
# =============================================================================


class TestRecordActionSafe:
    def test_no_incident_id_logs_debug(self, caplog):
        svc = PlannerService()
        step_result = _make_step_result()
        with caplog.at_level(logging.DEBUG):
            svc._record_action_safe(None, "echo", step_result)
        assert "No incident_id" in caplog.text

    @patch("elle.cli.planner.service.PlannerService._record_action")
    def test_with_incident_id(self, mock_record):
        svc = PlannerService()
        step_result = _make_step_result()
        svc._record_action_safe("inc-123", "echo", step_result)
        mock_record.assert_called_once_with(
            "inc-123",
            "echo",
            step_result,
            is_rollback=False,
        )

    @patch("elle.cli.planner.service.PlannerService._record_action")
    def test_with_rollback_flag(self, mock_record):
        svc = PlannerService()
        step_result = _make_step_result()
        svc._record_action_safe("inc-123", "undo", step_result, is_rollback=True)
        mock_record.assert_called_once_with(
            "inc-123",
            "undo",
            step_result,
            is_rollback=True,
        )

    @patch(
        "elle.cli.planner.service.PlannerService._record_action",
        side_effect=RuntimeError("DB error"),
    )
    def test_exception_caught(self, mock_record, caplog):
        svc = PlannerService()
        step_result = _make_step_result()
        with caplog.at_level(logging.WARNING):
            svc._record_action_safe("inc-123", "echo", step_result)
        assert "Failed to record action" in caplog.text


# =============================================================================
# 8. generate_plan
# =============================================================================


class TestGeneratePlan:
    @patch("elle.cli.planner.service.get_llm")
    def test_llm_not_available(self, mock_get_llm):
        llm = MagicMock()
        llm.is_available.return_value = False
        mock_get_llm.return_value = llm

        svc = PlannerService()
        context = PlanContext(request=TaskRequest(request="test"))
        assert svc.generate_plan(context) is None

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

    @patch("elle.cli.planner.service.get_llm", side_effect=RuntimeError("LLM error"))
    def test_llm_import_exception(self, _mock):
        svc = PlannerService()
        context = PlanContext(request=TaskRequest(request="test"))
        assert svc.generate_plan(context) is None

    @patch("elle.cli.planner.service.get_llm")
    def test_chat_json_raises(self, mock_get_llm):
        llm = MagicMock()
        llm.is_available.return_value = True
        llm.chat_json.side_effect = ValueError("Bad JSON")
        mock_get_llm.return_value = llm

        svc = PlannerService()
        context = PlanContext(request=TaskRequest(request="test"))
        assert svc.generate_plan(context) is None


# =============================================================================
# 9. build_context
# =============================================================================


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
        mock_docker.return_value = DockerState(docker_available=True)
        svc = PlannerService()
        session = _make_session()
        ctx = svc.build_context("restart docker container", session)
        mock_docker.assert_called_once()
        assert ctx.docker_state is not None
        assert ctx.docker_state.docker_available is True

    @patch("elle.cli.planner.service.PlannerService._search_man_vault", return_value=())
    @patch("elle.cli.planner.service.PlannerService._search_prior_plans", return_value=())
    @patch("elle.cli.planner.service.PlannerService._get_network_state")
    def test_network_task(self, mock_net, mock_prior, mock_man):
        mock_net.return_value = NetworkState(firewall_active=True)
        svc = PlannerService()
        session = _make_session()
        ctx = svc.build_context("configure firewall", session)
        mock_net.assert_called_once()
        assert ctx.network_state is not None
        assert ctx.network_state.firewall_active is True

    @patch("elle.cli.planner.service.PlannerService._search_man_vault", return_value=())
    @patch("elle.cli.planner.service.PlannerService._search_prior_plans", return_value=())
    def test_with_entities(self, mock_prior, mock_man):
        svc = PlannerService()
        session = _make_session()
        ctx = svc.build_context("restart nginx", session, entities=("nginx", "service"))
        assert ctx.request.entities == ("nginx", "service")


# =============================================================================
# 10. execute_plan
# =============================================================================


class TestExecutePlan:
    @patch("elle.cli.planner.service.run_safe")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    def test_success(self, mock_record, mock_run):
        mock_run.return_value = _mock_run_safe(success=True)

        svc = PlannerService(use_incident_vault=False, use_capabilities=False, use_preflight=False)
        plan = _make_plan()
        pr = _make_plan_result(plan=plan)
        result = svc.execute_plan(pr, _make_session(), skip_preflight=True)
        assert result.outcome == PlanOutcome.SUCCESS
        assert result.approved is True

    def test_no_plan_returns_error(self):
        svc = PlannerService(use_incident_vault=False, use_preflight=False)
        pr = _make_plan_result(plan=None)
        result = svc.execute_plan(pr, _make_session())
        assert result.outcome == PlanOutcome.ERROR

    @patch("elle.cli.planner.service.run_safe")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    def test_failure_stops_by_default(self, mock_record, mock_run):
        mock_run.return_value = _mock_run_safe(success=False, exit_code=1, stderr="err")

        svc = PlannerService(use_incident_vault=False, use_capabilities=False, use_preflight=False)
        plan = _make_plan(steps=[_make_step(), _make_step()])
        pr = _make_plan_result(plan=plan)
        result = svc.execute_plan(pr, _make_session(), skip_preflight=True)
        assert result.outcome == PlanOutcome.FAILED
        assert len(result.step_results) == 1

    @patch("elle.cli.planner.service.run_safe")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    def test_can_fail_continues(self, mock_record, mock_run):
        mock_run.side_effect = [
            _mock_run_safe(success=False, exit_code=1, stderr="err"),
            _mock_run_safe(success=True),
        ]

        svc = PlannerService(use_incident_vault=False, use_capabilities=False, use_preflight=False)
        plan = _make_plan(steps=[_make_step(can_fail=True), _make_step()])
        pr = _make_plan_result(plan=plan)
        result = svc.execute_plan(pr, _make_session(), skip_preflight=True)
        assert result.outcome == PlanOutcome.PARTIAL
        assert len(result.step_results) == 2

    @patch("elle.cli.planner.service.run_safe")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    def test_stop_on_failure_false_runs_all(self, mock_record, mock_run):
        mock_run.side_effect = [
            _mock_run_safe(success=False, exit_code=1),
            _mock_run_safe(success=True),
        ]

        svc = PlannerService(use_incident_vault=False, use_capabilities=False, use_preflight=False)
        plan = _make_plan(steps=[_make_step(), _make_step()])
        pr = _make_plan_result(plan=plan)
        result = svc.execute_plan(pr, _make_session(), stop_on_failure=False, skip_preflight=True)
        assert len(result.step_results) == 2
        assert result.outcome == PlanOutcome.PARTIAL

    @patch("elle.cli.planner.service.run_safe")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    def test_with_checks_passing(self, mock_record, mock_run):
        mock_run.return_value = _mock_run_safe(success=True, stdout="ok")

        svc = PlannerService(use_incident_vault=False, use_capabilities=False, use_preflight=False)
        plan = _make_plan(
            checks=[ValidationCheck(command="echo check", description="verify")],
        )
        pr = _make_plan_result(plan=plan)
        result = svc.execute_plan(pr, _make_session(), skip_preflight=True)
        assert result.outcome == PlanOutcome.SUCCESS
        assert len(result.check_results) == 1
        assert result.check_results[0].passed is True

    @patch("elle.cli.planner.service.run_safe")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    def test_check_failure_downgrades_to_partial(self, mock_record, mock_run):
        """A failing check downgrades SUCCESS to PARTIAL."""
        mock_run.side_effect = [
            _mock_run_safe(success=True, stdout="installed"),
            _mock_run_safe(success=True, stdout="inactive"),
        ]
        svc = PlannerService(use_incident_vault=False, use_capabilities=False, use_preflight=False)
        plan = _make_plan(
            checks=[ValidationCheck(command="check", description="verify", expected="^active$")],
        )
        pr = _make_plan_result(plan=plan)
        result = svc.execute_plan(pr, _make_session(), skip_preflight=True)
        assert result.outcome == PlanOutcome.PARTIAL
        assert result.check_results[0].passed is False

    @patch("elle.cli.planner.service.PlannerService._execute_step")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    @patch("elle.cli.planner.service.PlannerService._create_incident", return_value="INC-NEW")
    def test_creates_incident_if_missing(self, mock_create, mock_record, mock_exec):
        """When incident_id is None and use_incident_vault=True, one is created."""
        mock_exec.return_value = _make_step_result(success=True)
        svc = PlannerService(use_incident_vault=True, use_capabilities=False, use_preflight=False)
        plan = _make_plan()
        pr = _make_plan_result(plan=plan, incident_id=None)
        result = svc.execute_plan(pr, _make_session(), skip_preflight=True)
        mock_create.assert_called_once()
        assert result.incident_id == "INC-NEW"

    @patch("elle.cli.planner.service.PlannerService._execute_step")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    @patch("elle.cli.planner.service.PlannerService._create_incident", side_effect=RuntimeError("DB"))
    def test_incident_creation_failure_continues(self, mock_create, mock_record, mock_exec):
        """When incident creation fails during execution, it continues."""
        mock_exec.return_value = _make_step_result(success=True)
        svc = PlannerService(use_incident_vault=True, use_capabilities=False, use_preflight=False)
        plan = _make_plan()
        pr = _make_plan_result(plan=plan, incident_id=None)
        result = svc.execute_plan(pr, _make_session(), skip_preflight=True)
        assert result.outcome == PlanOutcome.SUCCESS

    def test_preflight_blocks_execution(self):
        """When preflight returns can_proceed=False, execution is blocked."""
        svc = PlannerService(use_incident_vault=False, use_capabilities=False, use_preflight=True)
        svc._preflight_validator = MagicMock()
        with patch.object(svc, "run_preflight_for_plan", return_value=(False, "BLOCKED")):
            plan = _make_plan(steps=[_make_step(command="apt install foo")])
            pr = _make_plan_result(plan=plan)
            result = svc.execute_plan(pr, _make_session())
            assert result.outcome == PlanOutcome.ERROR

    @patch("elle.cli.planner.service.run_safe")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    def test_all_steps_fail_gives_failed(self, mock_record, mock_run):
        """When all steps fail, outcome is FAILED."""
        mock_run.return_value = _mock_run_safe(success=False, exit_code=1)
        svc = PlannerService(use_incident_vault=False, use_capabilities=False, use_preflight=False)
        plan = _make_plan(steps=[_make_step(can_fail=True)])
        pr = _make_plan_result(plan=plan)
        result = svc.execute_plan(pr, _make_session(), skip_preflight=True)
        assert result.outcome == PlanOutcome.FAILED


# =============================================================================
# 11. _execute_step routing
# =============================================================================


class TestExecuteStepRouting:
    """Tests for _execute_step dispatch logic."""

    def test_step_with_capability_routes_to_capability_step(self):
        """Step with explicit capability routes to _execute_capability_step."""
        svc = PlannerService(use_capabilities=True)
        svc._capability_executor = MagicMock()
        step = _make_step(
            command=None,
            capability="service.restart",
            capability_input={"service": "nginx"},
        )
        with patch.object(svc, "_execute_capability_step", return_value=_make_step_result()) as mock_cap:
            svc._execute_step(step, 0, _make_session())
            mock_cap.assert_called_once_with(step, 0)

    @patch("elle.cli.planner.command_mapper.map_command_to_capability")
    def test_command_mapped_to_capability(self, mock_map):
        """Command successfully mapped to capability uses capability path."""
        mock_map.return_value = MagicMock(
            success=True,
            capability="service.restart",
            capability_input={"service": "nginx"},
        )
        svc = PlannerService(use_capabilities=True)
        svc._capability_executor = MagicMock()
        step = _make_step(command="systemctl restart nginx")
        with patch.object(svc, "_execute_capability_step", return_value=_make_step_result()) as mock_cap:
            svc._execute_step(step, 0, _make_session())
            mock_cap.assert_called_once()

    @patch("elle.cli.planner.command_mapper.map_command_to_capability")
    @patch("elle.cli.planner.service.run_safe")
    def test_unmapped_command_falls_back_to_raw(self, mock_run, mock_map):
        """Command without mapping falls back to raw command execution."""
        mock_map.return_value = MagicMock(success=False, capability=None, unmapped_reason="No match")
        mock_run.return_value = _mock_run_safe(success=True)
        svc = PlannerService(use_capabilities=True)
        svc._capability_executor = MagicMock()
        step = _make_step(command="echo hello")
        result = svc._execute_step(step, 0, _make_session())
        assert result.success is True
        mock_run.assert_called_once()

    @patch("elle.cli.planner.service.run_safe")
    def test_no_executor_uses_command_step(self, mock_run):
        """Without capability executor, command execution is used directly."""
        mock_run.return_value = _mock_run_safe(success=True)
        svc = PlannerService(use_capabilities=False)
        step = _make_step(command="echo hello")
        result = svc._execute_step(step, 0, _make_session())
        assert result.success is True


# =============================================================================
# 12. _execute_command_step
# =============================================================================


class TestExecuteCommandStep:
    @patch("elle.cli.planner.service.run_safe")
    def test_captures_output(self, mock_run):
        mock_run.return_value = _mock_run_safe(success=True, exit_code=0, stdout="result", stderr="warn")
        svc = PlannerService()
        step = _make_step(command="ls -la")
        result = svc._execute_command_step(step, 0, _make_session())
        assert result.success is True
        assert result.stdout == "result"
        assert result.stderr == "warn"
        assert result.exit_code == 0
        assert result.step_index == 0
        assert result.duration_ms is not None

    @patch("elle.cli.planner.service.run_safe")
    def test_failure_captured(self, mock_run):
        mock_run.return_value = _mock_run_safe(success=False, exit_code=127, stdout="", stderr="not found")
        svc = PlannerService()
        step = _make_step(command="nonexistent_cmd")
        result = svc._execute_command_step(step, 1, _make_session())
        assert result.success is False
        assert result.exit_code == 127
        assert result.step_index == 1

    @patch("elle.cli.planner.service.run_safe")
    def test_privilege_flag_preserved(self, mock_run):
        mock_run.return_value = _mock_run_safe(success=True)
        svc = PlannerService()
        step = _make_step(command="systemctl restart", privileged=True)
        result = svc._execute_command_step(step, 0, _make_session())
        assert result.privileged is True


# =============================================================================
# 13. _execute_capability_step
# =============================================================================


class TestExecuteCapabilityStep:
    def test_capability_not_found(self):
        """When capability is not in registry, returns error StepResult."""
        mock_executor = MagicMock()
        mock_executor.registry.get.return_value = None
        svc = PlannerService(use_capabilities=True)
        svc._capability_executor = mock_executor

        step = _make_step(
            command=None,
            capability="service.nonexistent",
            capability_input={},
        )
        result = svc._execute_capability_step(step, 0)
        assert result.success is False
        assert result.exit_code == 1

    def test_executor_none_returns_error(self):
        """When capability executor is None, returns error StepResult."""
        svc = PlannerService(use_capabilities=False)
        step = _make_step(
            command=None,
            capability="service.restart",
            capability_input={"service": "nginx"},
        )
        result = svc._execute_capability_step(step, 0)
        assert result.success is False
        assert "not initialized" in result.stderr or result.exit_code == 1


# =============================================================================
# 14. _execute_rollback_step
# =============================================================================


class TestExecuteRollbackStep:
    @patch("elle.cli.planner.service.run_safe")
    def test_rollback_step_execution(self, mock_run):
        mock_run.return_value = _mock_run_safe(success=True, stdout="rolled back")
        svc = PlannerService()
        rb_step = RollbackStep(command="apt remove nginx", explanation="Undo install")
        result = svc._execute_rollback_step(rb_step, 0, _make_session())
        assert result.success is True
        assert result.command == "apt remove nginx"
        assert result.duration_ms is not None

    @patch("elle.cli.planner.service.run_safe")
    def test_rollback_step_failure(self, mock_run):
        mock_run.return_value = _mock_run_safe(success=False, exit_code=1, stderr="denied")
        svc = PlannerService()
        rb_step = RollbackStep(
            command="undo something",
            explanation="Undo",
            requires_privilege=True,
        )
        result = svc._execute_rollback_step(rb_step, 2, _make_session())
        assert result.success is False
        assert result.privileged is True
        assert result.step_index == 2


# =============================================================================
# 15. rollback_plan
# =============================================================================


class TestRollbackPlan:
    @patch("elle.cli.planner.service.run_safe")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    def test_rollback_executes_and_records(self, mock_record, mock_run):
        mock_run.return_value = _mock_run_safe(success=True)
        svc = PlannerService(use_incident_vault=False, use_preflight=False)
        plan = _make_plan(
            rollback=[RollbackStep(command="undo", explanation="undo step")],
        )
        pr = _make_plan_result(plan=plan, incident_id="inc-rb")
        result = svc.rollback_plan(pr, _make_session())
        assert result.outcome == PlanOutcome.ROLLED_BACK
        assert len(result.rollback_results) == 1
        # Verify is_rollback=True was passed
        call_kwargs = mock_record.call_args
        assert call_kwargs[1].get("is_rollback") is True

    def test_no_rollback_available(self):
        svc = PlannerService(use_incident_vault=False, use_preflight=False)
        plan = _make_plan(rollback=[])
        pr = _make_plan_result(plan=plan)
        result = svc.rollback_plan(pr, _make_session())
        assert result.outcome == PlanOutcome.CANCELLED  # Unchanged

    def test_no_plan(self):
        svc = PlannerService(use_incident_vault=False, use_preflight=False)
        pr = _make_plan_result(plan=None)
        result = svc.rollback_plan(pr, _make_session())
        assert result.plan is None


# =============================================================================
# 16. _run_checks
# =============================================================================


class TestRunChecks:
    @patch("elle.cli.planner.service.run_safe")
    def test_check_passes_no_expected(self, mock_run):
        mock_run.return_value = _mock_run_safe(success=True, stdout="running")
        svc = PlannerService()
        checks = (ValidationCheck(command="check", description="basic"),)
        results = svc._run_checks(checks, _make_session())
        assert results[0].passed is True
        assert results[0].actual is None  # No expected pattern => actual is None

    @patch("elle.cli.planner.service.run_safe")
    def test_check_passes_with_matching_expected(self, mock_run):
        mock_run.return_value = _mock_run_safe(success=True, stdout="active")
        svc = PlannerService()
        checks = (ValidationCheck(command="check", description="verify", expected="active"),)
        results = svc._run_checks(checks, _make_session())
        assert results[0].passed is True
        assert results[0].actual == "active"

    @patch("elle.cli.planner.service.run_safe")
    def test_check_fails_pattern_mismatch(self, mock_run):
        mock_run.return_value = _mock_run_safe(success=True, stdout="inactive")
        svc = PlannerService()
        checks = (ValidationCheck(command="check", description="verify", expected="^active$"),)
        results = svc._run_checks(checks, _make_session())
        assert results[0].passed is False
        assert results[0].expected == "^active$"
        assert results[0].actual == "inactive"

    @patch("elle.cli.planner.service.run_safe")
    def test_check_fails_command_failure(self, mock_run):
        mock_run.return_value = _mock_run_safe(success=False, exit_code=3)
        svc = PlannerService()
        checks = (ValidationCheck(command="false", description="fail"),)
        results = svc._run_checks(checks, _make_session())
        assert results[0].passed is False

    @patch("elle.cli.planner.service.run_safe")
    def test_multiple_checks(self, mock_run):
        mock_run.side_effect = [
            _mock_run_safe(success=True, stdout="ok"),
            _mock_run_safe(success=False, exit_code=1),
        ]
        svc = PlannerService()
        checks = (
            ValidationCheck(command="check1", description="first"),
            ValidationCheck(command="check2", description="second"),
        )
        results = svc._run_checks(checks, _make_session())
        assert len(results) == 2
        assert results[0].passed is True
        assert results[1].passed is False
        assert results[0].check_index == 0
        assert results[1].check_index == 1


# =============================================================================
# 17. finalize
# =============================================================================


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

    @patch(
        "elle.cli.planner.service.PlannerService._finalize_incident",
        side_effect=RuntimeError("DB fail"),
    )
    def test_finalize_exception_caught(self, mock_finalize, caplog):
        svc = PlannerService(use_incident_vault=True)
        pr = _make_plan_result(incident_id="inc-123")
        with caplog.at_level(logging.WARNING):
            result = svc.finalize(pr)
        assert "Failed to finalize incident" in caplog.text
        assert result is pr

    def test_finalize_disabled_vault_with_incident_id(self):
        """When use_incident_vault=False, finalize does not call _finalize_incident."""
        svc = PlannerService(use_incident_vault=False)
        pr = _make_plan_result(incident_id="inc-ignore")
        with patch.object(svc, "_finalize_incident") as mock_fin:
            svc.finalize(pr)
            mock_fin.assert_not_called()


# =============================================================================
# 18. _finalize_incident
# =============================================================================


class TestFinalizeIncident:
    @patch("elle.cli.planner.service.PlannerService._store_decision_record")
    @patch("elle.daemon.incidents.finalize_outcome")
    @patch("elle.daemon.incidents.update_incident")
    def test_maps_success_to_improved(self, mock_update, mock_finalize, mock_store):
        svc = PlannerService(use_incident_vault=True)
        plan = _make_plan()
        result = _make_plan_result(plan=plan, incident_id="INC-F1")
        result = result.with_outcome(PlanOutcome.SUCCESS)
        svc._finalize_incident(result)
        call_kwargs = mock_finalize.call_args
        assert call_kwargs[1]["outcome"] == "improved"
        mock_update.assert_called_once()
        mock_store.assert_called_once()

    @patch("elle.cli.planner.service.PlannerService._store_decision_record")
    @patch("elle.daemon.incidents.finalize_outcome")
    @patch("elle.daemon.incidents.update_incident")
    def test_maps_partial_to_partial(self, mock_update, mock_finalize, mock_store):
        svc = PlannerService(use_incident_vault=True)
        plan = _make_plan()
        result = _make_plan_result(plan=plan, incident_id="INC-F2")
        result = result.with_outcome(PlanOutcome.PARTIAL)
        svc._finalize_incident(result)
        assert mock_finalize.call_args[1]["outcome"] == "partial"

    @patch("elle.cli.planner.service.PlannerService._store_decision_record")
    @patch("elle.daemon.incidents.finalize_outcome")
    @patch("elle.daemon.incidents.update_incident")
    def test_maps_failed_to_no_change(self, mock_update, mock_finalize, mock_store):
        svc = PlannerService(use_incident_vault=True)
        plan = _make_plan()
        result = _make_plan_result(plan=plan, incident_id="INC-F3")
        result = result.with_outcome(PlanOutcome.FAILED)
        svc._finalize_incident(result)
        assert mock_finalize.call_args[1]["outcome"] == "no_change"

    @patch("elle.cli.planner.service.PlannerService._store_decision_record")
    @patch("elle.daemon.incidents.finalize_outcome")
    @patch("elle.daemon.incidents.update_incident")
    def test_maps_rolled_back_to_no_change(self, mock_update, mock_finalize, mock_store):
        svc = PlannerService(use_incident_vault=True)
        plan = _make_plan()
        result = _make_plan_result(plan=plan, incident_id="INC-F4")
        result = result.with_outcome(PlanOutcome.ROLLED_BACK)
        svc._finalize_incident(result)
        assert mock_finalize.call_args[1]["outcome"] == "no_change"

    @patch("elle.daemon.incidents.finalize_outcome")
    def test_no_incident_id_returns_early(self, mock_finalize):
        svc = PlannerService(use_incident_vault=True)
        result = _make_plan_result(plan=None, incident_id=None)
        svc._finalize_incident(result)
        mock_finalize.assert_not_called()

    @patch("elle.cli.planner.service.PlannerService._store_decision_record")
    @patch("elle.daemon.incidents.finalize_outcome")
    @patch("elle.daemon.incidents.update_incident")
    def test_includes_check_results(self, mock_update, mock_finalize, mock_store):
        svc = PlannerService(use_incident_vault=True)
        plan = _make_plan()
        result = _make_plan_result(plan=plan, incident_id="INC-F5")
        result = result.with_outcome(PlanOutcome.SUCCESS)
        result = result.with_check_results(
            (
                CheckResult(check_index=0, command="check1", passed=True, output="ok"),
                CheckResult(check_index=1, command="check2", passed=False, output="fail"),
            )
        )
        svc._finalize_incident(result)
        verification_steps = mock_finalize.call_args[1]["verification_steps"]
        assert "[PASS] check1" in verification_steps
        assert "[FAIL] check2" in verification_steps

    @patch("elle.daemon.incidents.finalize_outcome")
    def test_no_plan_skips_update(self, mock_finalize):
        """When plan is None, update_incident is not called."""
        svc = PlannerService(use_incident_vault=True)
        result = _make_plan_result(plan=None, incident_id="INC-F6")
        result = result.with_outcome(PlanOutcome.ERROR)
        svc._finalize_incident(result)
        mock_finalize.assert_called_once()
        assert mock_finalize.call_args[1]["outcome"] == "no_change"


# =============================================================================
# 19. run_preflight_for_plan
# =============================================================================


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

    def test_preflight_exception_returns_true(self):
        """When preflight raises, returns (True, error message)."""
        svc = PlannerService(use_preflight=True)
        svc._preflight_validator = MagicMock()
        plan = _make_plan(steps=[_make_step(command="apt install nginx")])
        # The method imports from elle.ops.preflight internally, so mock that
        with patch(
            "elle.cli.planner.service.PlannerService._extract_package_operations",
            return_value=[(("nginx",), "install")],
        ):
            with patch(
                "elle.ops.preflight.format_result_for_display",
                side_effect=ImportError("not installed"),
            ):
                can_proceed, msg = svc.run_preflight_for_plan(plan)
        assert can_proceed is True
        assert msg is not None
        assert "skipped" in msg.lower() or "not installed" in msg.lower()


# =============================================================================
# 20. run_planning_pipeline
# =============================================================================


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

    @patch("elle.cli.planner.service.PlannerService._create_incident", return_value="INC-P1")
    @patch("elle.cli.planner.service.get_llm")
    def test_pipeline_with_incident(self, mock_get_llm, mock_create):
        """Pipeline creates incident when use_incident_vault=True."""
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.chat_json.return_value = {
            "title": "Test",
            "steps": [{"command": "echo", "explanation": "e", "risk_level": "low"}],
        }
        mock_get_llm.return_value = mock_llm

        svc = PlannerService(use_man_vault=False, use_incident_vault=True)
        session = _make_session()
        result = svc.run_planning_pipeline("test", session)
        assert result.incident_id == "INC-P1"
        mock_create.assert_called_once()

    @patch(
        "elle.cli.planner.service.PlannerService._create_incident",
        side_effect=RuntimeError("DB error"),
    )
    @patch("elle.cli.planner.service.get_llm")
    def test_pipeline_incident_creation_failure(self, mock_get_llm, mock_create, caplog):
        """When incident creation fails, pipeline continues."""
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.chat_json.return_value = {
            "title": "Test",
            "steps": [{"command": "echo", "explanation": "e", "risk_level": "low"}],
        }
        mock_get_llm.return_value = mock_llm

        svc = PlannerService(use_man_vault=False, use_incident_vault=True)
        session = _make_session()
        with caplog.at_level(logging.WARNING):
            result = svc.run_planning_pipeline("test", session)
        assert result.incident_id is None
        assert result.plan is not None
        assert "Failed to create incident" in caplog.text

    @patch("elle.cli.planner.service.get_llm")
    def test_pipeline_entities_forwarded(self, mock_get_llm):
        """Entities are forwarded through the pipeline."""
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.chat_json.return_value = {
            "title": "Test",
            "steps": [{"command": "echo", "explanation": "e", "risk_level": "low"}],
        }
        mock_get_llm.return_value = mock_llm

        svc = PlannerService(use_man_vault=False, use_incident_vault=False)
        session = _make_session()
        result = svc.run_planning_pipeline("restart nginx", session, entities=("nginx",))
        assert result.context.request.entities == ("nginx",)


# =============================================================================
# 21. get_planner_service / reset_planner_service
# =============================================================================


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


# =============================================================================
# 22. _search_man_vault fallback paths
# =============================================================================


class TestSearchManVault:
    def test_both_fallbacks_fail_returns_empty(self):
        """When daemon API and direct store both fail, returns ()."""
        svc = PlannerService(use_man_vault=True)
        with patch(
            "elle.cli.daemon_client.get_daemon_client",
            side_effect=ImportError("no client"),
        ):
            with patch(
                "elle.daemon.manvault.search",
                side_effect=ImportError("not installed"),
            ):
                result = svc._search_man_vault("nginx", ())
                assert result == ()

    def test_direct_store_exception_returns_empty(self):
        """Generic exception in direct store returns ()."""
        svc = PlannerService(use_man_vault=True)
        with patch(
            "elle.cli.daemon_client.get_daemon_client",
            side_effect=RuntimeError("no daemon"),
        ):
            with patch(
                "elle.daemon.manvault.search",
                side_effect=RuntimeError("store error"),
            ):
                result = svc._search_man_vault("nginx", ())
                assert result == ()


# =============================================================================
# 23. _search_prior_plans fallback paths
# =============================================================================


class TestSearchPriorPlans:
    def test_both_fallbacks_fail_returns_empty(self):
        """When daemon API and direct store both fail, returns ()."""
        svc = PlannerService(use_incident_vault=True)
        with patch(
            "elle.cli.daemon_client.get_daemon_client",
            side_effect=ImportError("no client"),
        ):
            with patch(
                "elle.daemon.incidents.get_prior_art",
                side_effect=ImportError("not installed"),
            ):
                result = svc._search_prior_plans("restart nginx", ())
                assert result == ()

    def test_direct_store_exception_returns_empty(self):
        """Generic exception in direct store returns ()."""
        svc = PlannerService(use_incident_vault=True)
        with patch(
            "elle.cli.daemon_client.get_daemon_client",
            side_effect=RuntimeError("no daemon"),
        ):
            with patch(
                "elle.daemon.incidents.get_prior_art",
                side_effect=RuntimeError("store error"),
            ):
                result = svc._search_prior_plans("restart nginx", ())
                assert result == ()


# =============================================================================
# 24. _get_docker_state / _get_network_state daemon failures
# =============================================================================


class TestGetDockerState:
    @patch("elle.cli.daemon_client.get_daemon_client")
    def test_daemon_failure_returns_unavailable(self, mock_get_client):
        mock_get_client.side_effect = RuntimeError("Daemon not running")
        svc = PlannerService()
        state = svc._get_docker_state()
        assert state.docker_available is False


class TestGetNetworkState:
    @patch("elle.cli.daemon_client.get_daemon_client")
    def test_daemon_failure_returns_default(self, mock_get_client):
        mock_get_client.side_effect = RuntimeError("Daemon not running")
        svc = PlannerService()
        state = svc._get_network_state()
        assert state.firewall_active is False
        assert state.interfaces == ()


# =============================================================================
# 25. _record_action (direct call)
# =============================================================================


class TestRecordAction:
    @patch("elle.daemon.incidents.append_action")
    def test_records_shell_action(self, mock_append):
        svc = PlannerService()
        step_result = _make_step_result(success=True)
        svc._record_action("INC-R1", "echo hello", step_result)
        mock_append.assert_called_once_with(
            "INC-R1",
            kind="shell",
            command="echo hello",
            exit_code=0,
            stdout="output",
            stderr="",
            success=True,
        )

    @patch("elle.daemon.incidents.append_action")
    def test_records_rollback_action(self, mock_append):
        svc = PlannerService()
        step_result = _make_step_result(success=True)
        svc._record_action("INC-R2", "undo cmd", step_result, is_rollback=True)
        mock_append.assert_called_once()
        call_kwargs = mock_append.call_args
        assert call_kwargs[1]["kind"] == "rollback"

    @patch("elle.daemon.incidents.append_action")
    def test_truncates_long_output(self, mock_append):
        svc = PlannerService()
        long_output = "x" * 1000
        step_result = StepResult(
            step_index=0,
            command="cmd",
            exit_code=0,
            stdout=long_output,
            stderr=long_output,
            success=True,
            duration_ms=10,
            executed_at=datetime.now(),
        )
        svc._record_action("INC-R3", "cmd", step_result)
        call_kwargs = mock_append.call_args
        assert len(call_kwargs[1]["stdout"]) == 500
        assert len(call_kwargs[1]["stderr"]) == 500


# =============================================================================
# 26. _create_incident
# =============================================================================


class TestCreateIncident:
    @patch("elle.daemon.incidents.create_incident_draft")
    def test_creates_draft_and_returns_id(self, mock_create):
        mock_incident = MagicMock()
        mock_incident.incident_id = "INC-C1"
        mock_create.return_value = mock_incident

        svc = PlannerService()
        ctx = PlanContext(request=TaskRequest(request="install nginx"))
        result = svc._create_incident("install nginx", ctx)
        assert result == "INC-C1"
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["domain"] == "task"
        assert call_kwargs["severity"] == "info"
        assert call_kwargs["trigger_source"] == "system_task"

    @patch("elle.daemon.incidents.create_incident_draft")
    def test_truncates_long_title(self, mock_create):
        mock_incident = MagicMock()
        mock_incident.incident_id = "INC-C2"
        mock_create.return_value = mock_incident

        svc = PlannerService()
        long_request = "a" * 200
        ctx = PlanContext(request=TaskRequest(request=long_request))
        svc._create_incident(long_request, ctx)
        call_kwargs = mock_create.call_args[1]
        # Title is "Task: " + first 50 chars
        assert len(call_kwargs["title"]) <= 56  # "Task: " (6) + 50


# =============================================================================
# 27. _store_decision_record
# =============================================================================


class TestStoreDecisionRecord:
    @patch("elle.daemon.incidents.store.store_decision_record")
    @patch("elle.daemon.incidents.models.DecisionRecord")
    @patch("elle.daemon.incidents.models.Provenance")
    @patch("elle.daemon.incidents.models.ConfidenceBreakdown")
    def test_stores_decision_with_plan(self, mock_conf, mock_prov, mock_dr, mock_store):
        """When plan and incident_id exist, stores decision record."""
        from elle.cli.planner.models import ManDocContext, PriorPlanContext

        svc = PlannerService()
        plan = _make_plan(title="Install nginx")
        context = PlanContext(
            request=TaskRequest(request="install nginx"),
            man_docs=(ManDocContext(name="nginx", section="8", snippet="snippet text", flags_used=()),),
            prior_plans=(
                PriorPlanContext(
                    incident_id="prior-1",
                    title="Old plan",
                    outcome="improved",
                    plan_summary="worked",
                    commands_executed=("apt install nginx",),
                    rollback_used=False,
                    score=0.8,
                    days_ago=5,
                ),
            ),
        )
        result = _make_plan_result(plan=plan, incident_id="INC-DR1", context=context)
        svc._store_decision_record(result)
        mock_store.assert_called_once()

    def test_no_plan_returns_early(self):
        svc = PlannerService()
        result = _make_plan_result(plan=None, incident_id="INC-DR2")
        # Should not raise
        svc._store_decision_record(result)

    def test_no_incident_id_returns_early(self):
        svc = PlannerService()
        result = _make_plan_result(plan=_make_plan(), incident_id=None)
        # Should not raise
        svc._store_decision_record(result)

    @patch(
        "elle.daemon.incidents.store.store_decision_record",
        side_effect=RuntimeError("DB error"),
    )
    def test_store_exception_caught(self, mock_store, caplog):
        """Exceptions during store are caught and logged."""
        svc = PlannerService()
        plan = _make_plan()
        result = _make_plan_result(plan=plan, incident_id="INC-DR3")
        with caplog.at_level(logging.WARNING):
            svc._store_decision_record(result)
        assert "Failed to store decision record" in caplog.text

    @patch("elle.daemon.incidents.store.store_decision_record", side_effect=ImportError("no module"))
    def test_import_error_caught(self, mock_store, caplog):
        """ImportError during store is caught gracefully."""
        svc = PlannerService()
        plan = _make_plan()
        result = _make_plan_result(plan=plan, incident_id="INC-DR4")
        with caplog.at_level(logging.DEBUG):
            svc._store_decision_record(result)


# =============================================================================
# 28. _search_man_vault with successful daemon API
# =============================================================================


class TestSearchManVaultDaemonSuccess:
    def test_daemon_api_returns_docs(self):
        """When daemon API returns results, they are used."""
        mock_client = MagicMock()

        async def mock_is_available():
            return True

        async def mock_search(query, limit):
            return [
                {"command": "nginx", "section": "8", "snippet": "web server"},
            ]

        mock_client.is_daemon_available = mock_is_available
        mock_client.search_manvault = mock_search

        with patch("elle.cli.daemon_client.get_daemon_client", return_value=mock_client):
            svc = PlannerService(use_man_vault=True)
            result = svc._search_man_vault("nginx config", ("nginx",))
            assert len(result) == 1
            assert result[0].name == "nginx"

    def test_entities_appended_to_query(self):
        """Entities are appended to the search query."""
        mock_client = MagicMock()

        async def mock_is_available():
            return False

        mock_client.is_daemon_available = mock_is_available

        with patch("elle.cli.daemon_client.get_daemon_client", return_value=mock_client):
            with patch("elle.daemon.manvault.search", return_value=[]) as mock_search:
                svc = PlannerService(use_man_vault=True)
                svc._search_man_vault("config", ("nginx", "web"))
                call_args = mock_search.call_args
                assert "nginx" in call_args[0][0]
                assert "web" in call_args[0][0]


# =============================================================================
# 29. _search_prior_plans with successful daemon API
# =============================================================================


class TestSearchPriorPlansDaemonSuccess:
    def test_daemon_api_returns_plans(self):
        """When daemon API returns results, they are used."""
        mock_client = MagicMock()

        async def mock_is_available():
            return True

        async def mock_search(query, limit):
            return [
                {
                    "incident_id": "prior-1",
                    "title": "Old task",
                    "outcome": "improved",
                    "summary": "Fixed it",
                    "score": 0.9,
                },
            ]

        mock_client.is_daemon_available = mock_is_available
        mock_client.search_incidents = mock_search

        with patch("elle.cli.daemon_client.get_daemon_client", return_value=mock_client):
            svc = PlannerService(use_incident_vault=True)
            result = svc._search_prior_plans("restart nginx", ())
            assert len(result) == 1
            assert result[0].title == "Old task"


# =============================================================================
# 30. _search_prior_plans direct store fallback success
# =============================================================================


class TestSearchPriorPlansDirectStore:
    def test_direct_store_returns_plans(self):
        """When daemon fails but direct store works, plans are returned."""
        with patch(
            "elle.cli.daemon_client.get_daemon_client",
            side_effect=RuntimeError("no daemon"),
        ):
            with patch(
                "elle.daemon.incidents.get_prior_art",
                return_value=[
                    {
                        "incident_id": "direct-1",
                        "title": "Direct plan",
                        "outcome": "improved",
                        "summary": "it worked",
                        "score": 0.7,
                        "days_ago": 3,
                        "successful_actions": [{"command": "systemctl restart nginx"}],
                    }
                ],
            ):
                svc = PlannerService(use_incident_vault=True)
                result = svc._search_prior_plans("restart nginx", ())
                assert len(result) == 1
                assert result[0].incident_id == "direct-1"
                assert "systemctl restart nginx" in result[0].commands_executed


# =============================================================================
# 31. run_preflight_for_plan with validation
# =============================================================================


class TestRunPreflightValidation:
    @patch("elle.ops.preflight.format_result_for_display", return_value="OK")
    @patch("elle.ops.preflight.models.PreflightStatus")
    def test_preflight_passes(self, mock_status, mock_format):
        """When preflight validation passes, returns (True, message)."""
        svc = PlannerService(use_preflight=True)
        mock_validator = MagicMock()
        mock_result = MagicMock()
        mock_result.status = MagicMock()
        mock_result.status.value = "passed"
        # Make sure BLOCKED comparison fails (so can_proceed stays True)
        type(mock_result.status).__eq__ = lambda self, other: False
        mock_validator.validate.return_value = mock_result
        svc._preflight_validator = mock_validator

        plan = _make_plan(steps=[_make_step(command="apt install nginx")])
        with patch.object(
            svc,
            "_extract_package_operations",
            return_value=[(("nginx",), "install")],
        ):
            can_proceed, msg = svc.run_preflight_for_plan(plan)
        assert can_proceed is True
        assert msg is not None


# =============================================================================
# 32. Additional _is_network_task / _is_docker_task edge cases
# =============================================================================


class TestTaskDetectionAdditional:
    def test_docker_volume_keyword(self):
        svc = PlannerService()
        assert svc._is_docker_task("manage volume for db", ()) is True

    def test_network_connectivity_keyword(self):
        svc = PlannerService()
        assert svc._is_network_task("check connectivity", ()) is True

    def test_network_route_keyword(self):
        svc = PlannerService()
        assert svc._is_network_task("add route to subnet", ()) is True

    def test_network_vpn_keyword(self):
        svc = PlannerService()
        assert svc._is_network_task("configure vpn tunnel", ()) is True

    def test_network_iptables_keyword(self):
        svc = PlannerService()
        assert svc._is_network_task("flush iptables rules", ()) is True

    def test_network_wg_keyword(self):
        svc = PlannerService()
        assert svc._is_network_task("wg show", ()) is True

    def test_docker_not_in_entities_returns_false(self):
        svc = PlannerService()
        assert svc._is_docker_task("random text", ("nginx",)) is False

    def test_network_not_in_entities_returns_false(self):
        svc = PlannerService()
        assert svc._is_network_task("random text", ("nginx",)) is False


# =============================================================================
# 33. execute_plan - preflight with skip_preflight=True
# =============================================================================


class TestExecutePlanSkipPreflight:
    @patch("elle.cli.planner.service.run_safe")
    @patch("elle.cli.planner.service.PlannerService._record_action_safe")
    def test_skip_preflight_bypasses_validation(self, mock_record, mock_run):
        """When skip_preflight=True, preflight is not run."""
        mock_run.return_value = _mock_run_safe(success=True)
        svc = PlannerService(use_incident_vault=False, use_capabilities=False, use_preflight=True)
        svc._preflight_validator = MagicMock()
        plan = _make_plan(steps=[_make_step(command="apt install nginx")])
        pr = _make_plan_result(plan=plan)
        with patch.object(svc, "run_preflight_for_plan") as mock_preflight:
            result = svc.execute_plan(pr, _make_session(), skip_preflight=True)
            mock_preflight.assert_not_called()
        assert result.outcome == PlanOutcome.SUCCESS


# =============================================================================
# 34. _execute_step with no command (command is None)
# =============================================================================


class TestExecuteStepNullCommand:
    @patch("elle.cli.planner.service.run_safe")
    def test_null_command_step_no_capability(self, mock_run):
        """Step with no command and no capability falls back to command step with empty string."""
        mock_run.return_value = _mock_run_safe(success=True)
        svc = PlannerService(use_capabilities=False)
        step = PlanStep(
            command=None,
            explanation="No-op step",
            risk_level="low",
        )
        result = svc._execute_step(step, 0, _make_session())
        # Falls through to _execute_command_step with empty command
        assert result is not None
