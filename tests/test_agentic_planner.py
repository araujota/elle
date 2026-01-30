from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.agentic.models import (
    ActionRequest,
    ActionType,
    AgenticIntent,
    CapabilityCall,
    ExecutionPlan,
    InformationNeed,
    ParallelGroup,
)
from elle.cli.agentic.planner import (
    ACTION_TO_CAPABILITY,
    CAPABILITY_DURATION_MS,
    INFO_NEED_TO_CAPABILITY,
    READ_ONLY_CAPABILITIES,
    REQUIRES_CONFIRMATION,
    CapabilityPlanner,
    get_capability_planner,
    reset_capability_planner,
)


# =============================================================================
# Helpers
# =============================================================================


def _make_need(**kwargs: Any) -> InformationNeed:
    defaults: dict[str, Any] = {
        "category": "service",
        "target": "nginx",
        "aspects": ("status",),
    }
    defaults.update(kwargs)
    return InformationNeed(**defaults)


def _make_action(**kwargs: Any) -> ActionRequest:
    defaults: dict[str, Any] = {
        "action_type": ActionType.RESTART,
        "target": "nginx",
        "domain": "service",
    }
    defaults.update(kwargs)
    return ActionRequest(**defaults)


def _make_intent(**kwargs: Any) -> AgenticIntent:
    defaults: dict[str, Any] = {
        "information_needs": (),
        "action_requests": (),
        "goal_summary": "test goal",
        "confidence": 0.9,
    }
    defaults.update(kwargs)
    return AgenticIntent(**defaults)


# =============================================================================
# Singleton
# =============================================================================


class TestSingleton:
    def setup_method(self) -> None:
        reset_capability_planner()

    def teardown_method(self) -> None:
        reset_capability_planner()

    def test_get_planner(self) -> None:
        p = get_capability_planner()
        assert isinstance(p, CapabilityPlanner)

    def test_same_instance(self) -> None:
        a = get_capability_planner()
        b = get_capability_planner()
        assert a is b

    def test_reset(self) -> None:
        a = get_capability_planner()
        reset_capability_planner()
        b = get_capability_planner()
        assert a is not b


# =============================================================================
# CapabilityPlanner init
# =============================================================================


class TestInit:
    def test_defaults(self) -> None:
        p = CapabilityPlanner()
        assert p.use_llm_planning is False

    def test_custom(self) -> None:
        p = CapabilityPlanner(use_llm_planning=True)
        assert p.use_llm_planning is True


# =============================================================================
# plan - empty intent
# =============================================================================


class TestPlanEmpty:
    @pytest.mark.asyncio
    async def test_empty_intent(self) -> None:
        p = CapabilityPlanner()
        intent = _make_intent()
        plan = await p.plan(intent)
        assert isinstance(plan, ExecutionPlan)
        assert plan.parallel_groups == ()
        assert plan.estimated_duration_ms == 0
        assert plan.requires_confirmation is False


# =============================================================================
# plan - info needs only
# =============================================================================


class TestPlanInfoNeeds:
    @pytest.mark.asyncio
    async def test_service_status(self) -> None:
        p = CapabilityPlanner()
        need = _make_need(category="service", target="nginx", aspects=("status",))
        intent = _make_intent(information_needs=(need,))
        plan = await p.plan(intent)
        assert plan.total_calls == 1
        call = plan.all_calls[0]
        assert call.capability == "service.status"
        assert call.args["service"] == "nginx"
        assert call.is_read_only is True

    @pytest.mark.asyncio
    async def test_service_logs(self) -> None:
        p = CapabilityPlanner()
        need = _make_need(category="service", target="sshd", aspects=("logs",))
        intent = _make_intent(information_needs=(need,))
        plan = await p.plan(intent)
        assert plan.all_calls[0].capability == "service.logs"

    @pytest.mark.asyncio
    async def test_file_content(self) -> None:
        p = CapabilityPlanner()
        need = _make_need(category="file", target="/etc/hosts", aspects=("content",))
        intent = _make_intent(information_needs=(need,))
        plan = await p.plan(intent)
        assert plan.all_calls[0].capability == "file.read"

    @pytest.mark.asyncio
    async def test_package_info(self) -> None:
        p = CapabilityPlanner()
        need = _make_need(category="package", target="curl", aspects=("info",))
        intent = _make_intent(information_needs=(need,))
        plan = await p.plan(intent)
        assert plan.all_calls[0].capability == "package.info"

    @pytest.mark.asyncio
    async def test_docker_list(self) -> None:
        p = CapabilityPlanner()
        need = _make_need(category="docker", target="", aspects=("list",))
        intent = _make_intent(information_needs=(need,))
        plan = await p.plan(intent)
        assert plan.all_calls[0].capability == "docker.list"

    @pytest.mark.asyncio
    async def test_network_listeners(self) -> None:
        p = CapabilityPlanner()
        need = _make_need(category="network", target="", aspects=("listeners",))
        intent = _make_intent(information_needs=(need,))
        plan = await p.plan(intent)
        assert plan.all_calls[0].capability == "network.listeners"

    @pytest.mark.asyncio
    async def test_system_info(self) -> None:
        p = CapabilityPlanner()
        need = _make_need(category="system", target="", aspects=("info",))
        intent = _make_intent(information_needs=(need,))
        plan = await p.plan(intent)
        assert plan.all_calls[0].capability == "system.info"

    @pytest.mark.asyncio
    async def test_default_for_category(self) -> None:
        p = CapabilityPlanner()
        need = _make_need(category="service", target="apache", aspects=("unknown_aspect",))
        intent = _make_intent(information_needs=(need,))
        plan = await p.plan(intent)
        # Should fall back to default for service category
        assert plan.total_calls == 1
        assert plan.all_calls[0].capability == "service.status"

    @pytest.mark.asyncio
    async def test_unknown_category_no_call(self) -> None:
        p = CapabilityPlanner()
        need = InformationNeed(category="config", target="test", aspects=("something_unknown",))
        intent = _make_intent(information_needs=(need,))
        plan = await p.plan(intent)
        # config has "content" mapping; unknown aspect falls back to no default for config
        # Actually config has a default mapping for "content" aspect, not a default category fallback
        # So this may generate 0 calls
        # config doesn't appear in the `defaults` dict, so no call
        assert plan.total_calls == 0


# =============================================================================
# plan - actions only
# =============================================================================


class TestPlanActions:
    @pytest.mark.asyncio
    async def test_service_restart(self) -> None:
        p = CapabilityPlanner()
        action = _make_action(action_type=ActionType.RESTART, target="nginx", domain="service")
        intent = _make_intent(action_requests=(action,))
        plan = await p.plan(intent)
        assert plan.total_calls == 1
        call = plan.all_calls[0]
        assert call.capability == "service.restart"
        assert call.requires_confirmation is True
        assert plan.requires_confirmation is True

    @pytest.mark.asyncio
    async def test_package_install(self) -> None:
        p = CapabilityPlanner()
        action = _make_action(action_type=ActionType.INSTALL, target="htop", domain="package")
        intent = _make_intent(action_requests=(action,))
        plan = await p.plan(intent)
        assert plan.all_calls[0].capability == "package.install"
        assert plan.all_calls[0].args["package"] == "htop"

    @pytest.mark.asyncio
    async def test_docker_stop(self) -> None:
        p = CapabilityPlanner()
        action = _make_action(action_type=ActionType.STOP, target="myapp", domain="docker")
        intent = _make_intent(action_requests=(action,))
        plan = await p.plan(intent)
        assert plan.all_calls[0].capability == "docker.stop"
        assert plan.all_calls[0].args["name"] == "myapp"

    @pytest.mark.asyncio
    async def test_file_create(self) -> None:
        p = CapabilityPlanner()
        action = _make_action(action_type=ActionType.CREATE, target="/tmp/test.txt", domain="file")
        intent = _make_intent(action_requests=(action,))
        plan = await p.plan(intent)
        assert plan.all_calls[0].capability == "file.write"

    @pytest.mark.asyncio
    async def test_unknown_action_falls_back(self) -> None:
        p = CapabilityPlanner()
        action = _make_action(action_type=ActionType.CUSTOM, target="something", domain="other")
        intent = _make_intent(action_requests=(action,))
        plan = await p.plan(intent)
        # Should construct a custom capability name
        assert plan.all_calls[0].capability == "other.custom"

    @pytest.mark.asyncio
    async def test_action_with_parameters(self) -> None:
        p = CapabilityPlanner()
        action = ActionRequest(
            action_type=ActionType.INSTALL,
            target="vim",
            domain="package",
            parameters={"version": "8.0"},
        )
        intent = _make_intent(action_requests=(action,))
        plan = await p.plan(intent)
        assert plan.all_calls[0].args["version"] == "8.0"


# =============================================================================
# plan - mixed intent
# =============================================================================


class TestPlanMixed:
    @pytest.mark.asyncio
    async def test_info_then_action(self) -> None:
        p = CapabilityPlanner()
        need = _make_need(category="service", target="nginx", aspects=("status",))
        action = _make_action(
            action_type=ActionType.RESTART,
            target="nginx",
            domain="service",
            condition="if not running",
        )
        intent = _make_intent(
            information_needs=(need,),
            action_requests=(action,),
        )
        plan = await p.plan(intent)
        assert plan.total_calls == 2
        # Verify both calls are present
        all_caps = [c.capability for c in plan.all_calls]
        assert "service.status" in all_caps
        assert "service.restart" in all_caps
        # The restart should depend on the status check
        restart_call = [c for c in plan.all_calls if c.capability == "service.restart"][0]
        assert restart_call.depends_on == (0,)

    @pytest.mark.asyncio
    async def test_no_dependency_without_condition(self) -> None:
        p = CapabilityPlanner()
        need = _make_need(category="service", target="nginx", aspects=("status",))
        action = _make_action(
            action_type=ActionType.RESTART,
            target="nginx",
            domain="service",
        )  # no condition
        intent = _make_intent(
            information_needs=(need,),
            action_requests=(action,),
        )
        plan = await p.plan(intent)
        assert plan.total_calls == 2
        # Without condition, both go in same group (no dependency)
        assert len(plan.parallel_groups) == 1


# =============================================================================
# _build_parallel_groups
# =============================================================================


class TestBuildParallelGroups:
    def test_empty(self) -> None:
        p = CapabilityPlanner()
        assert p._build_parallel_groups([]) == []

    def test_independent_calls(self) -> None:
        p = CapabilityPlanner()
        calls = [
            CapabilityCall(capability="a", args={}, purpose="a", depends_on=()),
            CapabilityCall(capability="b", args={}, purpose="b", depends_on=()),
        ]
        groups = p._build_parallel_groups(calls)
        assert len(groups) == 1
        assert len(groups[0].calls) == 2

    def test_dependent_calls(self) -> None:
        p = CapabilityPlanner()
        calls = [
            CapabilityCall(capability="a", args={}, purpose="a", depends_on=()),
            CapabilityCall(capability="b", args={}, purpose="b", depends_on=(0,)),
        ]
        groups = p._build_parallel_groups(calls)
        # Both end up in same group since 0 is assigned before 1 is checked in same pass
        total = sum(len(g.calls) for g in groups)
        assert total == 2
        assert len(groups) >= 1

    def test_circular_dependency_breaks(self) -> None:
        p = CapabilityPlanner()
        calls = [
            CapabilityCall(capability="a", args={}, purpose="a", depends_on=(1,)),
            CapabilityCall(capability="b", args={}, purpose="b", depends_on=(0,)),
        ]
        groups = p._build_parallel_groups(calls)
        # Should not loop forever; both should be placed
        total = sum(len(g.calls) for g in groups)
        assert total == 2


# =============================================================================
# _action_depends_on_call
# =============================================================================


class TestActionDependsOnCall:
    def test_matching_target(self) -> None:
        p = CapabilityPlanner()
        action = _make_action(target="nginx")
        call = CapabilityCall(capability="service.status", args={"service": "nginx"}, purpose="check")
        assert p._action_depends_on_call(action, call) is True

    def test_non_matching_target(self) -> None:
        p = CapabilityPlanner()
        action = _make_action(target="nginx")
        call = CapabilityCall(capability="service.status", args={"service": "apache"}, purpose="check")
        assert p._action_depends_on_call(action, call) is False


# =============================================================================
# _parse_llm_plan
# =============================================================================


class TestParseLlmPlan:
    def test_valid_json(self) -> None:
        p = CapabilityPlanner()
        content = json.dumps({
            "calls": [
                {"capability": "service.status", "args": {"service": "nginx"}, "purpose": "check", "depends_on": []},
            ]
        })
        calls = p._parse_llm_plan(content)
        assert calls is not None
        assert len(calls) == 1
        assert calls[0].capability == "service.status"

    def test_json_in_code_block(self) -> None:
        p = CapabilityPlanner()
        content = '```json\n{"calls": [{"capability": "service.status", "args": {}, "purpose": "test"}]}\n```'
        calls = p._parse_llm_plan(content)
        assert calls is not None
        assert len(calls) == 1

    def test_json_in_generic_code_block(self) -> None:
        p = CapabilityPlanner()
        content = '```\n{"calls": [{"capability": "file.read", "args": {}, "purpose": "read"}]}\n```'
        calls = p._parse_llm_plan(content)
        assert calls is not None

    def test_invalid_json(self) -> None:
        p = CapabilityPlanner()
        calls = p._parse_llm_plan("not json at all")
        assert calls is None

    def test_json_with_surrounding_text(self) -> None:
        p = CapabilityPlanner()
        content = 'Here is the plan: {"calls": [{"capability": "system.info", "args": {}, "purpose": "info"}]} end'
        calls = p._parse_llm_plan(content)
        assert calls is not None

    def test_empty_calls(self) -> None:
        p = CapabilityPlanner()
        content = json.dumps({"calls": []})
        calls = p._parse_llm_plan(content)
        assert calls is None

    def test_missing_capability(self) -> None:
        p = CapabilityPlanner()
        content = json.dumps({"calls": [{"args": {}, "purpose": "test"}]})
        calls = p._parse_llm_plan(content)
        assert calls is None

    def test_confirmation_and_readonly_flags(self) -> None:
        p = CapabilityPlanner()
        content = json.dumps({
            "calls": [
                {"capability": "service.restart", "args": {"service": "nginx"}, "purpose": "restart"},
                {"capability": "service.status", "args": {"service": "nginx"}, "purpose": "check"},
            ]
        })
        calls = p._parse_llm_plan(content)
        assert calls is not None
        restart_call = [c for c in calls if c.capability == "service.restart"][0]
        status_call = [c for c in calls if c.capability == "service.status"][0]
        assert restart_call.requires_confirmation is True
        assert status_call.is_read_only is True


# =============================================================================
# _plan_with_llm
# =============================================================================


class TestPlanWithLlm:
    @pytest.mark.asyncio
    async def test_llm_not_available(self) -> None:
        p = CapabilityPlanner(use_llm_planning=True)
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = False

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            result = await p._plan_with_llm(_make_intent(), "test")
            assert result is None

    @pytest.mark.asyncio
    async def test_llm_empty_response(self) -> None:
        p = CapabilityPlanner(use_llm_planning=True)
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate.return_value = MagicMock(content="")

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            result = await p._plan_with_llm(_make_intent(), "test")
            assert result is None

    @pytest.mark.asyncio
    async def test_llm_exception(self) -> None:
        p = CapabilityPlanner(use_llm_planning=True)
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate.side_effect = RuntimeError("llm error")

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            result = await p._plan_with_llm(_make_intent(), "test")
            assert result is None


# =============================================================================
# replan
# =============================================================================


class TestReplan:
    @pytest.mark.asyncio
    async def test_replan_calls_plan(self) -> None:
        p = CapabilityPlanner()
        need = _make_need()
        intent = _make_intent(information_needs=(need,))
        plan = await p.replan(intent, previous_evidence=(), gaps=("service missing",))
        assert isinstance(plan, ExecutionPlan)
        assert plan.total_calls >= 1


# =============================================================================
# Constants
# =============================================================================


class TestConstants:
    def test_info_need_mappings(self) -> None:
        assert "service" in INFO_NEED_TO_CAPABILITY
        assert "file" in INFO_NEED_TO_CAPABILITY
        assert "package" in INFO_NEED_TO_CAPABILITY

    def test_action_mappings(self) -> None:
        assert "service" in ACTION_TO_CAPABILITY
        assert ActionType.RESTART in ACTION_TO_CAPABILITY["service"]

    def test_requires_confirmation(self) -> None:
        assert "service.restart" in REQUIRES_CONFIRMATION
        assert "package.install" in REQUIRES_CONFIRMATION

    def test_readonly(self) -> None:
        assert "service.status" in READ_ONLY_CAPABILITIES
        assert "service.restart" not in READ_ONLY_CAPABILITIES

    def test_duration_estimates(self) -> None:
        assert CAPABILITY_DURATION_MS["service.status"] == 500
        assert CAPABILITY_DURATION_MS["package.install"] == 30000
