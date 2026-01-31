from __future__ import annotations

from typing import Any

import pytest

from elle.cli.agentic.evaluator import (
    GoalEvaluator,
    get_goal_evaluator,
    reset_evaluator,
)
from elle.cli.agentic.models import (
    ActionRequest,
    ActionType,
    AgenticIntent,
    CapabilityCall,
    EvaluationStatus,
    ExecutionEvidence,
    ExecutionPlan,
    ExecutionResult,
    InformationNeed,
    ParallelGroup,
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

    def test_get_goal_evaluator(self) -> None:
        ev = get_goal_evaluator()
        assert isinstance(ev, GoalEvaluator)

    def test_get_goal_evaluator_same(self) -> None:
        a = get_goal_evaluator()
        b = get_goal_evaluator()
        assert a is b

    def test_reset_clears(self) -> None:
        a = get_goal_evaluator()
        reset_evaluator()
        b = get_goal_evaluator()
        assert a is not b


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
        evidence = _make_evidence(
            capability="service.status", args={"service": "nginx"}, success=False, error="timeout"
        )
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
        evidence = _make_evidence(
            capability="service.restart", args={"service": "nginx"}, success=False, error="denied"
        )
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
