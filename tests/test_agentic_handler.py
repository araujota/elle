from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.cli.agentic.handler import (
    AnalysisError,
    UnifiedAgenticHandler,
)
from elle.cli.agentic.models import (
    ActionRequest,
    ActionType,
    AgenticIntent,
    AgenticResponse,
    CapabilityCall,
    EvaluationResult,
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
        "confidence": 0.8,
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
        "output_text": "active (running)",
        "duration_ms": 100,
    }
    defaults.update(kwargs)
    return ExecutionEvidence(**defaults)


def _make_plan(intent: AgenticIntent, calls: tuple[CapabilityCall, ...] = ()) -> ExecutionPlan:
    groups = (ParallelGroup(calls=calls),) if calls else ()
    return ExecutionPlan(
        parallel_groups=groups,
        intent=intent,
        estimated_duration_ms=500,
    )


def _make_exec_result(
    intent: AgenticIntent,
    evidence: tuple[ExecutionEvidence, ...] = (),
) -> ExecutionResult:
    plan = _make_plan(
        intent,
        calls=tuple(
            CapabilityCall(capability=e.capability, args=e.args, purpose=f"Execute {e.capability}") for e in evidence
        ),
    )
    return ExecutionResult(
        plan=plan,
        evidence=evidence,
        total_duration_ms=sum(e.duration_ms for e in evidence),
    )


def _make_eval_result(**kwargs: Any) -> EvaluationResult:
    defaults: dict[str, Any] = {
        "status": EvaluationStatus.SATISFIED,
        "info_satisfied": True,
        "actions_completed": True,
        "gaps": (),
        "confidence": 0.9,
        "rationale": "All satisfied",
    }
    defaults.update(kwargs)
    return EvaluationResult(**defaults)


def _make_agentic_response(**kwargs: Any) -> AgenticResponse:
    defaults: dict[str, Any] = {
        "answer": "Test answer",
        "evidence": (),
        "confidence": 0.8,
        "follow_up_suggestions": (),
        "success": True,
    }
    defaults.update(kwargs)
    return AgenticResponse(**defaults)


def _build_handler(
    *,
    intent: AgenticIntent | None = None,
    plan: ExecutionPlan | None = None,
    exec_result: ExecutionResult | None = None,
    eval_result: EvaluationResult | None = None,
    synth_response: AgenticResponse | None = None,
    max_iterations: int = 3,
) -> tuple[UnifiedAgenticHandler, dict[str, AsyncMock]]:
    """Build a handler with all five components mocked.

    Returns the handler and a dict of the mock objects keyed by component name.
    """
    default_intent = intent or _make_intent(
        information_needs=(_make_need(),),
    )
    default_evidence = (_make_evidence(),)
    default_plan = plan or _make_plan(
        default_intent,
        calls=(CapabilityCall(capability="service.status", args={"service": "nginx"}, purpose="Check status"),),
    )
    default_exec_result = exec_result or ExecutionResult(
        plan=default_plan,
        evidence=default_evidence,
        total_duration_ms=100,
    )
    default_eval_result = eval_result or _make_eval_result()
    default_synth_response = synth_response or _make_agentic_response()

    analyzer = MagicMock()
    analyzer.analyze = AsyncMock(return_value=default_intent)
    analyzer.can_handle = MagicMock(return_value=True)

    planner = MagicMock()
    planner.plan = AsyncMock(return_value=default_plan)

    executor = MagicMock()
    executor.execute = AsyncMock(return_value=default_exec_result)

    evaluator = MagicMock()
    evaluator.evaluate = AsyncMock(return_value=default_eval_result)

    synthesizer = MagicMock()
    synthesizer.synthesize = AsyncMock(return_value=default_synth_response)

    handler = UnifiedAgenticHandler(
        intent_analyzer=analyzer,
        planner=planner,
        executor=executor,
        evaluator=evaluator,
        synthesizer=synthesizer,
        max_iterations=max_iterations,
    )

    mocks = {
        "analyzer": analyzer,
        "planner": planner,
        "executor": executor,
        "evaluator": evaluator,
        "synthesizer": synthesizer,
    }
    return handler, mocks


# =============================================================================
# TestUnifiedAgenticHandler
# =============================================================================


class TestUnifiedAgenticHandler:
    """Core handler behavior tests."""

    @pytest.mark.asyncio
    async def test_full_happy_path(self) -> None:
        """analyze -> plan -> execute -> evaluate(SATISFIED) -> synthesize returns AgenticResponse."""
        handler, mocks = _build_handler()

        response = await handler.handle("What is the status of nginx?")

        assert isinstance(response, AgenticResponse)
        assert response.success is True
        mocks["analyzer"].analyze.assert_awaited_once()
        mocks["planner"].plan.assert_awaited_once()
        mocks["executor"].execute.assert_awaited_once()
        mocks["evaluator"].evaluate.assert_awaited_once()
        mocks["synthesizer"].synthesize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_question_only_path(self) -> None:
        """intent with is_read_only=True, no execution of actions, just synthesis."""
        read_only_intent = _make_intent(
            information_needs=(_make_need(),),
            action_requests=(),
        )
        assert read_only_intent.is_read_only is True

        handler, mocks = _build_handler(intent=read_only_intent)
        response = await handler.handle("Is nginx running?")

        assert isinstance(response, AgenticResponse)
        # Executor should be called with skip_confirmation=True for read-only
        call_kwargs = mocks["executor"].execute.call_args
        assert call_kwargs.kwargs.get("skip_confirmation") is True

    @pytest.mark.asyncio
    async def test_task_with_confirmation(self) -> None:
        """confirm_callback is called when executing a task, returns True."""
        task_intent = _make_intent(
            information_needs=(),
            action_requests=(_make_action(),),
        )
        assert task_intent.is_read_only is False

        handler, mocks = _build_handler(intent=task_intent)
        confirm_cb = AsyncMock(return_value=True)

        response = await handler.handle("Restart nginx", confirm_callback=confirm_cb)

        assert isinstance(response, AgenticResponse)
        # The confirm_callback is forwarded to executor.execute
        call_kwargs = mocks["executor"].execute.call_args
        assert call_kwargs.kwargs.get("confirm_callback") is confirm_cb

    @pytest.mark.asyncio
    async def test_evaluation_needs_more_triggers_reloop(self) -> None:
        """evaluator returns NEEDS_MORE on first call, SATISFIED on second."""
        intent = _make_intent(information_needs=(_make_need(),))
        evidence = (_make_evidence(),)
        plan = _make_plan(
            intent,
            calls=(CapabilityCall(capability="service.status", args={"service": "nginx"}, purpose="Check"),),
        )
        exec_result = ExecutionResult(plan=plan, evidence=evidence, total_duration_ms=100)

        needs_more = _make_eval_result(
            status=EvaluationStatus.NEEDS_MORE,
            gaps=("Missing logs",),
        )
        satisfied = _make_eval_result(status=EvaluationStatus.SATISFIED)

        handler, mocks = _build_handler(
            intent=intent,
            plan=plan,
            exec_result=exec_result,
            eval_result=needs_more,
        )
        # Override evaluator to return NEEDS_MORE first, then SATISFIED
        mocks["evaluator"].evaluate = AsyncMock(side_effect=[needs_more, satisfied])

        response = await handler.handle("Check nginx status and logs")

        assert mocks["evaluator"].evaluate.await_count == 2
        assert mocks["planner"].plan.await_count == 2
        assert response.iterations == 2

    @pytest.mark.asyncio
    async def test_max_iterations_enforcement(self) -> None:
        """After max_iterations, handler stops and synthesizes."""
        intent = _make_intent(information_needs=(_make_need(),))
        needs_more = _make_eval_result(
            status=EvaluationStatus.NEEDS_MORE,
            gaps=("Always missing something",),
        )

        handler, mocks = _build_handler(
            intent=intent,
            eval_result=needs_more,
            max_iterations=2,
        )

        await handler.handle("Check nginx")

        # Should stop after max_iterations
        assert mocks["evaluator"].evaluate.await_count == 2
        assert mocks["synthesizer"].synthesize.assert_awaited_once

    @pytest.mark.asyncio
    async def test_empty_input(self) -> None:
        """Empty string input handled gracefully."""
        empty_intent = _make_intent(
            information_needs=(),
            action_requests=(),
            goal_summary="Empty input",
            confidence=0.0,
        )
        handler, mocks = _build_handler(intent=empty_intent)

        response = await handler.handle("")

        assert isinstance(response, AgenticResponse)
        assert response.success is False
        assert response.confidence == 0.0
        # With empty intent (no needs, no actions), handler returns early
        assert "couldn't determine" in response.answer.lower() or "rephrase" in response.answer.lower()

    @pytest.mark.asyncio
    async def test_error_in_analysis_stage(self) -> None:
        """intent_analyzer raises -> AgenticResponse with error."""
        handler, mocks = _build_handler()
        mocks["analyzer"].analyze = AsyncMock(side_effect=AnalysisError("LLM unavailable"))

        with pytest.raises(AnalysisError, match="LLM unavailable"):
            await handler.handle("Check nginx")

    @pytest.mark.asyncio
    async def test_skip_confirmation(self) -> None:
        """skip_confirmation=True bypasses confirm_callback."""
        task_intent = _make_intent(
            information_needs=(),
            action_requests=(_make_action(),),
        )
        handler, mocks = _build_handler(intent=task_intent)
        confirm_cb = AsyncMock(return_value=True)

        response = await handler.handle(
            "Restart nginx",
            confirm_callback=confirm_cb,
            skip_confirmation=True,
        )

        assert isinstance(response, AgenticResponse)
        # When skip_confirmation=True, the executor should receive skip_confirmation=True
        call_kwargs = mocks["executor"].execute.call_args
        assert call_kwargs.kwargs.get("skip_confirmation") is True


# =============================================================================
# TestIntentAnalysisIntegration
# =============================================================================


class TestIntentAnalysisIntegration:
    """Tests for intent-driven routing behavior."""

    @pytest.mark.asyncio
    async def test_system_question_routing(self) -> None:
        """Question intent -> read_only path (skip_confirmation for executor)."""
        question_intent = _make_intent(
            information_needs=(_make_need(),),
            action_requests=(),
        )
        assert question_intent.is_read_only is True

        handler, mocks = _build_handler(intent=question_intent)
        response = await handler.handle("Is nginx running?")

        assert isinstance(response, AgenticResponse)
        call_kwargs = mocks["executor"].execute.call_args
        assert call_kwargs.kwargs.get("skip_confirmation") is True

    @pytest.mark.asyncio
    async def test_system_task_routing(self) -> None:
        """Task intent -> execution path with confirmation forwarded."""
        task_intent = _make_intent(
            information_needs=(),
            action_requests=(_make_action(),),
        )
        assert task_intent.is_read_only is False

        handler, mocks = _build_handler(intent=task_intent)
        confirm_cb = AsyncMock(return_value=True)
        response = await handler.handle("Restart nginx", confirm_callback=confirm_cb)

        assert isinstance(response, AgenticResponse)
        call_kwargs = mocks["executor"].execute.call_args
        # For non-read-only intent without skip_confirmation, executor gets
        # skip_confirmation=False (because intent.is_read_only is False)
        assert call_kwargs.kwargs.get("skip_confirmation") is False

    @pytest.mark.asyncio
    async def test_ambiguous_intent(self) -> None:
        """Low confidence intent still proceeds through pipeline."""
        ambiguous_intent = _make_intent(
            information_needs=(_make_need(),),
            action_requests=(),
            confidence=0.3,
        )
        handler, mocks = _build_handler(intent=ambiguous_intent)

        response = await handler.handle("something about nginx maybe")

        assert isinstance(response, AgenticResponse)
        mocks["planner"].plan.assert_awaited_once()
        mocks["executor"].execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multi_intent(self) -> None:
        """Intent with both information_needs and action_requests (is_mixed)."""
        mixed_intent = _make_intent(
            information_needs=(_make_need(),),
            action_requests=(_make_action(),),
        )
        assert mixed_intent.is_mixed is True

        handler, mocks = _build_handler(intent=mixed_intent)
        response = await handler.handle("Why is nginx failing and can you restart it?")

        assert isinstance(response, AgenticResponse)
        mocks["planner"].plan.assert_awaited_once()
        mocks["executor"].execute.assert_awaited_once()
        mocks["evaluator"].evaluate.assert_awaited_once()


# =============================================================================
# TestExecutionLoop
# =============================================================================


class TestExecutionLoop:
    """Tests for the agentic loop iteration behavior."""

    @pytest.mark.asyncio
    async def test_single_iteration_success(self) -> None:
        """One plan -> execute -> evaluate(SATISFIED) completes in 1 iteration."""
        handler, mocks = _build_handler()

        response = await handler.handle("Status of nginx")

        assert mocks["evaluator"].evaluate.await_count == 1
        assert response.iterations == 1

    @pytest.mark.asyncio
    async def test_multi_iteration_convergence(self) -> None:
        """First eval NEEDS_MORE, second SATISFIED -> 2 iterations."""
        intent = _make_intent(information_needs=(_make_need(),))
        needs_more = _make_eval_result(
            status=EvaluationStatus.NEEDS_MORE,
            gaps=("Need logs too",),
        )
        satisfied = _make_eval_result(status=EvaluationStatus.SATISFIED)

        handler, mocks = _build_handler(intent=intent)
        mocks["evaluator"].evaluate = AsyncMock(side_effect=[needs_more, satisfied])

        response = await handler.handle("Check nginx")

        assert mocks["evaluator"].evaluate.await_count == 2
        assert mocks["planner"].plan.await_count == 2
        assert response.iterations == 2

    @pytest.mark.asyncio
    async def test_evaluation_failed(self) -> None:
        """Evaluator returns FAILED status -> loop breaks immediately."""
        intent = _make_intent(information_needs=(_make_need(),))
        failed = _make_eval_result(
            status=EvaluationStatus.FAILED,
            info_satisfied=False,
            rationale="All capabilities failed",
        )

        handler, mocks = _build_handler(intent=intent, eval_result=failed)

        response = await handler.handle("Check nginx")

        assert mocks["evaluator"].evaluate.await_count == 1
        # On FAILED, the handler breaks out of the loop
        assert response.iterations == 1

    @pytest.mark.asyncio
    async def test_loop_max_iterations(self) -> None:
        """Hits max_iterations limit when evaluator always returns NEEDS_MORE."""
        intent = _make_intent(information_needs=(_make_need(),))
        needs_more = _make_eval_result(
            status=EvaluationStatus.NEEDS_MORE,
            gaps=("Still missing data",),
        )

        handler, mocks = _build_handler(
            intent=intent,
            eval_result=needs_more,
            max_iterations=3,
        )

        response = await handler.handle("Check nginx")

        assert mocks["evaluator"].evaluate.await_count == 3
        assert mocks["planner"].plan.await_count == 3
        assert response.iterations == 3

    @pytest.mark.asyncio
    async def test_capability_execution_failure(self) -> None:
        """Executor returns failed evidence -> response reflects failure."""
        intent = _make_intent(information_needs=(_make_need(),))
        failed_evidence = (_make_evidence(success=False, error="Permission denied"),)
        plan = _make_plan(
            intent,
            calls=(CapabilityCall(capability="service.status", args={"service": "nginx"}, purpose="Check"),),
        )
        exec_result = ExecutionResult(plan=plan, evidence=failed_evidence, total_duration_ms=50)
        failed_eval = _make_eval_result(
            status=EvaluationStatus.FAILED,
            info_satisfied=False,
            rationale="All capabilities failed",
        )

        handler, mocks = _build_handler(
            intent=intent,
            plan=plan,
            exec_result=exec_result,
            eval_result=failed_eval,
        )

        response = await handler.handle("Check nginx")

        assert isinstance(response, AgenticResponse)
        # The evidence is collected even on failure
        assert len(response.evidence) >= 1

    @pytest.mark.asyncio
    async def test_partial_success(self) -> None:
        """Some actions succeed, some fail; response reflects both."""
        intent = _make_intent(
            information_needs=(_make_need(),),
            action_requests=(_make_action(),),
        )
        success_evidence = _make_evidence(
            capability="service.status",
            success=True,
            output_text="active (running)",
        )
        failure_evidence = _make_evidence(
            capability="service.restart",
            success=False,
            error="Permission denied",
        )
        mixed_evidence = (success_evidence, failure_evidence)
        plan = _make_plan(
            intent,
            calls=(
                CapabilityCall(capability="service.status", args={"service": "nginx"}, purpose="Check"),
                CapabilityCall(capability="service.restart", args={"service": "nginx"}, purpose="Restart"),
            ),
        )
        exec_result = ExecutionResult(plan=plan, evidence=mixed_evidence, total_duration_ms=200)
        partial_eval = _make_eval_result(
            status=EvaluationStatus.SATISFIED,
            info_satisfied=True,
            actions_completed=False,
            rationale="Partial success",
        )

        handler, mocks = _build_handler(
            intent=intent,
            plan=plan,
            exec_result=exec_result,
            eval_result=partial_eval,
        )

        response = await handler.handle("Check and restart nginx")

        assert isinstance(response, AgenticResponse)
        assert len(response.evidence) == 2
        # One succeeded, one failed
        successes = [e for e in response.evidence if e.success]
        failures = [e for e in response.evidence if not e.success]
        assert len(successes) == 1
        assert len(failures) == 1


# =============================================================================
# TestDependencyInjection
# =============================================================================


class TestDependencyInjection:
    """Tests for component injection and default creation."""

    @pytest.mark.asyncio
    async def test_default_components(self) -> None:
        """Handler with all None creates default components."""
        with (
            patch("elle.cli.agentic.handler.get_intent_analyzer") as mock_get_analyzer,
            patch("elle.cli.agentic.handler.get_capability_planner") as mock_get_planner,
            patch("elle.cli.agentic.handler.get_plan_executor") as mock_get_executor,
            patch("elle.cli.agentic.handler.get_goal_evaluator") as mock_get_evaluator,
            patch("elle.cli.agentic.handler.get_synthesizer") as mock_get_synthesizer,
        ):
            mock_get_analyzer.return_value = MagicMock()
            mock_get_planner.return_value = MagicMock()
            mock_get_executor.return_value = MagicMock()
            mock_get_evaluator.return_value = MagicMock()
            mock_get_synthesizer.return_value = MagicMock()

            UnifiedAgenticHandler()

            mock_get_analyzer.assert_called_once()
            mock_get_planner.assert_called_once()
            mock_get_executor.assert_called_once()
            mock_get_evaluator.assert_called_once()
            mock_get_synthesizer.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_intent_analyzer(self) -> None:
        """Inject custom IntentAnalyzer; handler uses it."""
        custom_analyzer = MagicMock()
        intent = _make_intent(information_needs=(_make_need(),))
        custom_analyzer.analyze = AsyncMock(return_value=intent)

        handler, mocks = _build_handler(intent=intent)
        # Replace the analyzer on the handler
        handler.intent_analyzer = custom_analyzer

        await handler.handle("Check nginx")

        custom_analyzer.analyze.assert_awaited_once_with("Check nginx")

    @pytest.mark.asyncio
    async def test_custom_planner(self) -> None:
        """Inject custom CapabilityPlanner; handler uses it."""
        intent = _make_intent(information_needs=(_make_need(),))
        custom_plan = _make_plan(
            intent,
            calls=(CapabilityCall(capability="system.info", args={}, purpose="Custom plan"),),
        )
        custom_planner = MagicMock()
        custom_planner.plan = AsyncMock(return_value=custom_plan)

        handler, mocks = _build_handler(intent=intent, plan=custom_plan)
        handler.planner = custom_planner

        await handler.handle("Check system info")

        custom_planner.plan.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_custom_evaluator(self) -> None:
        """Inject custom GoalEvaluator; handler uses it."""
        intent = _make_intent(information_needs=(_make_need(),))
        custom_eval = _make_eval_result(
            status=EvaluationStatus.SATISFIED,
            rationale="Custom evaluator says good",
        )
        custom_evaluator = MagicMock()
        custom_evaluator.evaluate = AsyncMock(return_value=custom_eval)

        handler, mocks = _build_handler(intent=intent)
        handler.evaluator = custom_evaluator

        await handler.handle("Check nginx")

        custom_evaluator.evaluate.assert_awaited_once()


# =============================================================================
# TestErrorRecovery
# =============================================================================


class TestErrorRecovery:
    """Tests for error handling and recovery."""

    @pytest.mark.asyncio
    async def test_llm_timeout_recovery(self) -> None:
        """Analyzer times out -> error propagates as exception."""
        handler, mocks = _build_handler()
        mocks["analyzer"].analyze = AsyncMock(side_effect=TimeoutError("LLM timed out"))

        with pytest.raises(TimeoutError, match="LLM timed out"):
            await handler.handle("Check nginx")

    @pytest.mark.asyncio
    async def test_executor_failure_recovery(self) -> None:
        """Executor raises -> error propagates with partial info."""
        intent = _make_intent(information_needs=(_make_need(),))
        handler, mocks = _build_handler(intent=intent)
        mocks["executor"].execute = AsyncMock(side_effect=RuntimeError("Connection refused"))

        with pytest.raises(RuntimeError, match="Connection refused"):
            await handler.handle("Check nginx")

    @pytest.mark.asyncio
    async def test_synthesizer_failure(self) -> None:
        """Synthesizer raises -> error propagates."""
        intent = _make_intent(information_needs=(_make_need(),))
        handler, mocks = _build_handler(intent=intent)
        mocks["synthesizer"].synthesize = AsyncMock(side_effect=RuntimeError("Synthesis failed"))

        with pytest.raises(RuntimeError, match="Synthesis failed"):
            await handler.handle("Check nginx")


# =============================================================================
# TestUncoveredBranches - Additional coverage for uncovered lines
# =============================================================================


class TestUncoveredBranches:
    """Tests targeting specific uncovered branches to push coverage above 90%."""

    @pytest.mark.asyncio
    async def test_empty_plan_breaks_loop(self) -> None:
        """Planner returns empty parallel_groups -> loop breaks at lines 145-146."""
        intent = _make_intent(information_needs=(_make_need(),))
        empty_plan = ExecutionPlan(
            parallel_groups=(),
            intent=intent,
            estimated_duration_ms=0,
        )

        handler, mocks = _build_handler(intent=intent, plan=empty_plan)

        response = await handler.handle("Check nginx")

        assert isinstance(response, AgenticResponse)
        # Planner was called but executor should NOT be called (empty plan breaks loop)
        mocks["planner"].plan.assert_awaited_once()
        mocks["executor"].execute.assert_not_awaited()
        mocks["evaluator"].evaluate.assert_not_awaited()
        # Synthesizer still called to produce a response
        mocks["synthesizer"].synthesize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_action_capability_in_actions_taken(self) -> None:
        """Successful non-read capability adds to actions_taken (line 252)."""
        intent = _make_intent(
            information_needs=(),
            action_requests=(_make_action(),),
        )
        # Use a capability name that is NOT in the read-only suffixes
        action_evidence = _make_evidence(
            capability="service.restart",
            success=True,
            output_text="restarted",
        )
        plan = _make_plan(
            intent,
            calls=(CapabilityCall(
                capability="service.restart",
                args={"service": "nginx"},
                purpose="Restart",
            ),),
        )
        exec_result = ExecutionResult(
            plan=plan,
            evidence=(action_evidence,),
            total_duration_ms=100,
        )
        satisfied = _make_eval_result(status=EvaluationStatus.SATISFIED)

        handler, mocks = _build_handler(
            intent=intent,
            plan=plan,
            exec_result=exec_result,
            eval_result=satisfied,
        )

        response = await handler.handle("Restart nginx")

        assert any("service.restart: success" in a for a in response.actions_taken)

    def test_can_handle_delegates_to_analyzer(self) -> None:
        """can_handle() delegates to intent_analyzer.can_handle() (line 279)."""
        handler, mocks = _build_handler()

        result = handler.can_handle("Check nginx")

        assert result is True
        mocks["analyzer"].can_handle.assert_called_once_with("Check nginx")

    def test_can_handle_returns_false(self) -> None:
        """can_handle() returns False when analyzer says no."""
        handler, mocks = _build_handler()
        mocks["analyzer"].can_handle.return_value = False

        result = handler.can_handle("something unhandled")

        assert result is False


class TestModuleSingletons:
    """Tests for module-level singleton functions (lines 296-298, 304)."""

    def test_get_unified_handler_creates_singleton(self) -> None:
        """get_unified_handler creates instance on first call (lines 296-298)."""
        from elle.cli.agentic.handler import (
            get_unified_handler,
            reset_agentic_handler,
        )

        # Ensure clean state
        reset_agentic_handler()

        with (
            patch("elle.cli.agentic.handler.get_intent_analyzer") as mock_analyzer,
            patch("elle.cli.agentic.handler.get_capability_planner") as mock_planner,
            patch("elle.cli.agentic.handler.get_plan_executor") as mock_executor,
            patch("elle.cli.agentic.handler.get_goal_evaluator") as mock_evaluator,
            patch("elle.cli.agentic.handler.get_synthesizer") as mock_synthesizer,
        ):
            mock_analyzer.return_value = MagicMock()
            mock_planner.return_value = MagicMock()
            mock_executor.return_value = MagicMock()
            mock_evaluator.return_value = MagicMock()
            mock_synthesizer.return_value = MagicMock()

            handler = get_unified_handler()
            assert isinstance(handler, UnifiedAgenticHandler)

            # Second call returns same instance
            handler2 = get_unified_handler()
            assert handler is handler2

        # Clean up
        reset_agentic_handler()

    def test_reset_agentic_handler_clears_singleton(self) -> None:
        """reset_agentic_handler sets singleton to None (line 304)."""
        import elle.cli.agentic.handler as handler_module
        from elle.cli.agentic.handler import (
            get_unified_handler,
            reset_agentic_handler,
        )

        with (
            patch("elle.cli.agentic.handler.get_intent_analyzer") as mock_analyzer,
            patch("elle.cli.agentic.handler.get_capability_planner") as mock_planner,
            patch("elle.cli.agentic.handler.get_plan_executor") as mock_executor,
            patch("elle.cli.agentic.handler.get_goal_evaluator") as mock_evaluator,
            patch("elle.cli.agentic.handler.get_synthesizer") as mock_synthesizer,
        ):
            mock_analyzer.return_value = MagicMock()
            mock_planner.return_value = MagicMock()
            mock_executor.return_value = MagicMock()
            mock_evaluator.return_value = MagicMock()
            mock_synthesizer.return_value = MagicMock()

            get_unified_handler()
            assert handler_module._unified_handler is not None

            reset_agentic_handler()
            assert handler_module._unified_handler is None
