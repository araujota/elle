from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from elle.cli.agentic.models import (
    ActionRequest,
    ActionType,
    AgenticIntent,
    CapabilityCall,
    EvaluationResult,
    EvaluationStatus,
    ExecutionEvidence,
    ExecutionPlan,
    ExecutionResult,
    GatheredEvidence,
    GatherPlan,
    GatherResult,
    InformationNeed,
    ParallelGroup,
)
from elle.cli.agentic.evaluator import (
    GoalEvaluator,
    SufficiencyEvaluator,
    get_evaluator,
    get_goal_evaluator,
    reset_evaluator,
)


# =============================================================================
# Helpers
# =============================================================================


def _make_intent(**kwargs: Any) -> AgenticIntent:
    defaults: dict[str, Any] = {
        "information_needs": (),
        "action_requests": (),
        "goal_summary": "test goal",
        "confidence": 0.9,
    }
    defaults.update(kwargs)
    return AgenticIntent(**defaults)


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


def _make_evidence(**kwargs: Any) -> ExecutionEvidence:
    defaults: dict[str, Any] = {
        "capability": "service.status",
        "args": {"service": "nginx"},
        "success": True,
        "duration_ms": 100,
    }
    defaults.update(kwargs)
    return ExecutionEvidence(**defaults)


def _make_exec_result(evidence_list: list[ExecutionEvidence]) -> ExecutionResult:
    intent = _make_intent()
    plan = ExecutionPlan(
        parallel_groups=(ParallelGroup(calls=(CapabilityCall(capability="a", args={}, purpose="a"),)),),
        intent=intent,
        estimated_duration_ms=100,
    )
    return ExecutionResult(
        plan=plan,
        evidence=tuple(evidence_list),
        total_duration_ms=100,
    )


# =============================================================================
# Singletons
# =============================================================================


class TestSingletons:
    def setup_method(self) -> None:
        reset_evaluator()

    def teardown_method(self) -> None:
        reset_evaluator()

    def test_get_evaluator(self) -> None:
        ev = get_evaluator()
        assert isinstance(ev, SufficiencyEvaluator)

    def test_get_evaluator_same(self) -> None:
        a = get_evaluator()
        b = get_evaluator()
        assert a is b

    def test_get_goal_evaluator(self) -> None:
        ev = get_goal_evaluator()
        assert isinstance(ev, GoalEvaluator)

    def test_reset_clears_both(self) -> None:
        a = get_evaluator()
        b = get_goal_evaluator()
        reset_evaluator()
        c = get_evaluator()
        d = get_goal_evaluator()
        assert a is not c
        assert b is not d


# =============================================================================
# GoalEvaluator - evaluate
# =============================================================================


class TestGoalEvaluatorEvaluate:
    @pytest.mark.asyncio
    async def test_no_evidence(self) -> None:
        ev = GoalEvaluator()
        intent = _make_intent()
        result = _make_exec_result([])
        # Build result with no evidence
        result_empty = ExecutionResult(
            plan=result.plan,
            evidence=(),
            total_duration_ms=0,
        )
        evaluation = await ev.evaluate(intent, result_empty)
        assert evaluation.status == EvaluationStatus.FAILED
        assert "No capabilities" in evaluation.gaps[0]

    @pytest.mark.asyncio
    async def test_info_satisfied(self) -> None:
        ev = GoalEvaluator()
        need = _make_need(category="service", target="nginx")
        intent = _make_intent(information_needs=(need,))
        evidence = _make_evidence(capability="service.status", args={"service": "nginx"}, success=True)
        result = _make_exec_result([evidence])

        evaluation = await ev.evaluate(intent, result)
        assert evaluation.status == EvaluationStatus.SATISFIED
        assert evaluation.info_satisfied is True

    @pytest.mark.asyncio
    async def test_info_not_satisfied(self) -> None:
        ev = GoalEvaluator()
        need = _make_need(category="service", target="nginx")
        intent = _make_intent(information_needs=(need,))
        evidence = _make_evidence(capability="service.status", args={"service": "nginx"}, success=False, error="timeout")
        result = _make_exec_result([evidence])

        evaluation = await ev.evaluate(intent, result)
        assert evaluation.info_satisfied is False
        assert len(evaluation.gaps) > 0

    @pytest.mark.asyncio
    async def test_action_completed(self) -> None:
        ev = GoalEvaluator()
        action = _make_action(action_type=ActionType.RESTART, target="nginx", domain="service")
        intent = _make_intent(action_requests=(action,))
        evidence = _make_evidence(capability="service.restart", args={"service": "nginx"}, success=True)
        result = _make_exec_result([evidence])

        evaluation = await ev.evaluate(intent, result)
        assert evaluation.actions_completed is True

    @pytest.mark.asyncio
    async def test_action_not_completed(self) -> None:
        ev = GoalEvaluator()
        action = _make_action(action_type=ActionType.RESTART, target="nginx", domain="service")
        intent = _make_intent(action_requests=(action,))
        evidence = _make_evidence(capability="service.restart", args={"service": "nginx"}, success=False, error="denied")
        result = _make_exec_result([evidence])

        evaluation = await ev.evaluate(intent, result)
        assert evaluation.actions_completed is False
        assert "restart nginx" in evaluation.gaps[0]

    @pytest.mark.asyncio
    async def test_all_failed_status(self) -> None:
        ev = GoalEvaluator()
        need = _make_need()
        intent = _make_intent(information_needs=(need,))
        ev1 = _make_evidence(success=False, error="fail1")
        ev2 = _make_evidence(capability="service.logs", success=False, error="fail2")
        result = _make_exec_result([ev1, ev2])

        evaluation = await ev.evaluate(intent, result)
        assert evaluation.status == EvaluationStatus.FAILED

    @pytest.mark.asyncio
    async def test_needs_more_status(self) -> None:
        ev = GoalEvaluator()
        need1 = _make_need(category="service", target="nginx")
        need2 = _make_need(category="file", target="/etc/hosts")
        intent = _make_intent(information_needs=(need1, need2))
        # Only service evidence, no file evidence
        evidence = _make_evidence(capability="service.status", args={"service": "nginx"}, success=True)
        result = _make_exec_result([evidence])

        evaluation = await ev.evaluate(intent, result)
        assert evaluation.status == EvaluationStatus.NEEDS_MORE


# =============================================================================
# GoalEvaluator - helpers
# =============================================================================


class TestGoalEvaluatorHelpers:
    def test_args_match_target_empty(self) -> None:
        ev = GoalEvaluator()
        assert ev._args_match_target({"service": "nginx"}, "") is True

    def test_args_match_target_match(self) -> None:
        ev = GoalEvaluator()
        assert ev._args_match_target({"service": "nginx"}, "nginx") is True

    def test_args_match_target_case_insensitive(self) -> None:
        ev = GoalEvaluator()
        assert ev._args_match_target({"service": "Nginx"}, "nginx") is True

    def test_args_match_target_no_match(self) -> None:
        ev = GoalEvaluator()
        assert ev._args_match_target({"service": "apache"}, "nginx") is False

    def test_calculate_confidence_empty(self) -> None:
        ev = GoalEvaluator()
        result = _make_exec_result([])
        result_empty = ExecutionResult(plan=result.plan, evidence=(), total_duration_ms=0)
        assert ev._calculate_confidence(result_empty) == 0.0

    def test_calculate_confidence_high_success(self) -> None:
        ev = GoalEvaluator()
        evidence = [
            _make_evidence(success=True),
            _make_evidence(success=True),
        ]
        result = _make_exec_result(evidence)
        conf = ev._calculate_confidence(result)
        assert conf > 0.5

    def test_calculate_confidence_low_success(self) -> None:
        ev = GoalEvaluator()
        evidence = [
            _make_evidence(success=False, error="a"),
            _make_evidence(success=False, error="b"),
            _make_evidence(success=True),
        ]
        result = _make_exec_result(evidence)
        conf = ev._calculate_confidence(result)
        # 1/3 success rate <= 0.5, so confidence = success_rate
        assert conf < 0.5

    def test_suggest_capabilities(self) -> None:
        ev = GoalEvaluator()
        need = _make_need(category="service", target="nginx")
        intent = _make_intent(information_needs=(need,))
        evidence = [_make_evidence(capability="service.status", success=True)]
        result = _make_exec_result(evidence)
        suggestions = ev._suggest_capabilities(intent, result, ["service for 'nginx'"])
        # service.status already executed, so service.logs should be suggested
        assert "service.logs" in suggestions

    @pytest.mark.asyncio
    async def test_llm_evaluate_returns_none(self) -> None:
        ev = GoalEvaluator()
        result = await ev._llm_evaluate(_make_intent(), _make_exec_result([]))
        assert result is None


# =============================================================================
# GoalEvaluator - find_matching_evidence
# =============================================================================


class TestFindMatchingEvidence:
    def test_matching_by_category(self) -> None:
        ev = GoalEvaluator()
        need = _make_need(category="service", target="")
        evidence = [
            _make_evidence(capability="service.status", args={}, success=True),
        ]
        result = _make_exec_result(evidence)
        matched = ev._find_matching_evidence(need, result)
        assert len(matched) == 1

    def test_no_match(self) -> None:
        ev = GoalEvaluator()
        need = _make_need(category="file", target="/etc/hosts")
        evidence = [
            _make_evidence(capability="service.status", args={"service": "nginx"}, success=True),
        ]
        result = _make_exec_result(evidence)
        matched = ev._find_matching_evidence(need, result)
        assert len(matched) == 0


# =============================================================================
# SufficiencyEvaluator
# =============================================================================


def _make_gathered_evidence(**kwargs: Any) -> GatheredEvidence:
    defaults: dict[str, Any] = {
        "capability": "service.status",
        "args": {"service": "nginx"},
        "success": True,
        "output": "active",
        "duration_ms": 100,
    }
    defaults.update(kwargs)
    return GatheredEvidence(**defaults)


def _make_gather_result(
    evidence: list[GatheredEvidence],
    needs: list[InformationNeed] | None = None,
    sufficient: bool = True,
) -> GatherResult:
    if needs is None:
        needs = [_make_need()]
    calls = [CapabilityCall(capability="service.status", args={"service": "nginx"}, purpose="check")]
    plan = GatherPlan(needs=tuple(needs), calls=tuple(calls), estimated_duration_ms=100)
    return GatherResult(
        plan=plan,
        evidence=tuple(evidence),
        sufficient=sufficient,
    )


class TestSufficiencyEvaluator:
    def test_no_evidence(self) -> None:
        ev = SufficiencyEvaluator()
        result = _make_gather_result([], needs=[_make_need()])
        assert ev.evaluate(result) is False

    def test_no_successful_evidence(self) -> None:
        ev = SufficiencyEvaluator()
        evidence = [_make_gathered_evidence(success=False, error="fail")]
        result = _make_gather_result(evidence)
        assert ev.evaluate(result) is False

    def test_successful_no_needs(self) -> None:
        ev = SufficiencyEvaluator()
        evidence = [_make_gathered_evidence(success=True)]
        result = _make_gather_result(evidence, needs=[])
        assert ev.evaluate(result) is True

    def test_need_satisfied(self) -> None:
        ev = SufficiencyEvaluator()
        need = _make_need(category="service", target="nginx")
        evidence = [_make_gathered_evidence(capability="service.status", success=True)]
        result = _make_gather_result(evidence, needs=[need])
        assert ev.evaluate(result) is True

    def test_need_not_satisfied(self) -> None:
        ev = SufficiencyEvaluator()
        need = _make_need(category="file", target="/etc/hosts")
        evidence = [_make_gathered_evidence(capability="service.status", success=True)]
        result = _make_gather_result(evidence, needs=[need])
        assert ev.evaluate(result) is False


# =============================================================================
# SufficiencyEvaluator - identify_gaps
# =============================================================================


class TestIdentifyGaps:
    def test_no_gaps(self) -> None:
        ev = SufficiencyEvaluator()
        need = _make_need(category="service")
        evidence = [_make_gathered_evidence(capability="service.status", success=True)]
        result = _make_gather_result(evidence, needs=[need])
        gaps = ev.identify_gaps("what is nginx status?", result)
        assert len(gaps) == 0

    def test_with_gaps(self) -> None:
        ev = SufficiencyEvaluator()
        need = _make_need(category="file", target="/etc/hosts")
        evidence = [_make_gathered_evidence(capability="service.status", success=True)]
        result = _make_gather_result(evidence, needs=[need])
        gaps = ev.identify_gaps("show /etc/hosts", result)
        assert len(gaps) == 1
        assert gaps[0].category == "file"

    def test_no_needs_no_gaps(self) -> None:
        ev = SufficiencyEvaluator()
        evidence = [_make_gathered_evidence(success=True)]
        result = _make_gather_result(evidence, needs=[])
        gaps = ev.identify_gaps("test", result)
        assert len(gaps) == 0


# =============================================================================
# SufficiencyEvaluator - get_unsatisfied_aspects
# =============================================================================


class TestGetUnsatisfiedAspects:
    def test_all_satisfied(self) -> None:
        ev = SufficiencyEvaluator()
        need = _make_need(category="service")
        evidence = [_make_gathered_evidence(capability="service.status", success=True)]
        result = _make_gather_result(evidence, needs=[need])
        unsatisfied = ev.get_unsatisfied_aspects(result)
        assert len(unsatisfied) == 0

    def test_unsatisfied_with_aspects(self) -> None:
        ev = SufficiencyEvaluator()
        need = _make_need(category="file", target="/etc/hosts", aspects=("content",))
        evidence = [_make_gathered_evidence(capability="service.status", success=True)]
        result = _make_gather_result(evidence, needs=[need])
        unsatisfied = ev.get_unsatisfied_aspects(result)
        assert len(unsatisfied) >= 1
        assert "file" in unsatisfied[0]

    def test_error_evidence_included(self) -> None:
        ev = SufficiencyEvaluator()
        need = _make_need(category="service")
        evidence = [
            _make_gathered_evidence(capability="service.status", success=True),
            _make_gathered_evidence(capability="service.logs", success=False, error="permission denied"),
        ]
        result = _make_gather_result(evidence, needs=[need])
        unsatisfied = ev.get_unsatisfied_aspects(result)
        # The need is satisfied but error evidence is included
        assert any("permission denied" in u for u in unsatisfied)


# =============================================================================
# SufficiencyEvaluator - _get_matching_capabilities
# =============================================================================


class TestGetMatchingCapabilities:
    def test_service(self) -> None:
        ev = SufficiencyEvaluator()
        need = _make_need(category="service")
        caps = ev._get_matching_capabilities(need)
        assert "service.status" in caps
        assert "service.logs" in caps

    def test_docker(self) -> None:
        ev = SufficiencyEvaluator()
        need = _make_need(category="docker", target="")
        caps = ev._get_matching_capabilities(need)
        assert "docker.list" in caps

    def test_unknown_category(self) -> None:
        ev = SufficiencyEvaluator()
        need = InformationNeed(category="system", target="", aspects=("info",))
        caps = ev._get_matching_capabilities(need)
        assert "system.info" in caps
