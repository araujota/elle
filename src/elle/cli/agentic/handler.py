"""UnifiedAgenticHandler - Single orchestrator for questions AND tasks.

This is the main entry point for the unified agentic execution system.
It handles ALL user requests through capability execution:

1. Analyze intent (what does the user want?)
2. Plan capabilities (how do we get there?)
3. Execute with parallel groups (do it)
4. Evaluate results (did we achieve the goal?)
5. Loop if needed (replan and retry)
6. Synthesize response (explain what happened)

Replaces the separate question/task paths with a unified approach.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

# Legacy imports for backwards compatibility
from elle.cli.agentic.analyzer import InformationNeedAnalyzer, get_analyzer
from elle.cli.agentic.evaluator import (
    GoalEvaluator,
    SufficiencyEvaluator,
    get_evaluator,
    get_goal_evaluator,
)
from elle.cli.agentic.executor import GatherExecutor, get_executor
from elle.cli.agentic.intent_analyzer import IntentAnalyzer, get_intent_analyzer
from elle.cli.agentic.models import (
    AgenticIntent,
    AgenticResponse,
    EvaluationStatus,
    ExecutionEvidence,
    GatheredEvidence,
    GatherResult,
)
from elle.cli.agentic.plan_executor import PlanExecutor, get_plan_executor
from elle.cli.agentic.planner import CapabilityPlanner, get_capability_planner
from elle.cli.agentic.selector import CapabilitySelector, get_selector
from elle.cli.agentic.synthesizer import ResponseSynthesizer, get_synthesizer

logger = logging.getLogger(__name__)


# Type alias for confirmation callback
ConfirmCallback = Callable[[str, str], Awaitable[bool]]


# =============================================================================
# UnifiedAgenticHandler (NEW)
# =============================================================================


class UnifiedAgenticHandler:
    """Unified handler for questions AND tasks through capability execution.

    This is the new orchestrator that replaces the separate question/task paths.
    It implements the full agentic loop:

    1. Analyze: Extract intent (info needs + actions)
    2. Plan: Map intent to capability calls with dependencies
    3. Execute: Run capabilities through proper executor
    4. Evaluate: Check if goal was achieved
    5. Loop: Replan if needed (up to max_iterations)
    6. Synthesize: Generate response with provenance

    Usage:
        handler = UnifiedAgenticHandler()
        response = await handler.handle("Why is nginx failing and can you restart it?")
    """

    def __init__(
        self,
        intent_analyzer: IntentAnalyzer | None = None,
        planner: CapabilityPlanner | None = None,
        executor: PlanExecutor | None = None,
        evaluator: GoalEvaluator | None = None,
        synthesizer: ResponseSynthesizer | None = None,
        max_iterations: int = 3,
    ) -> None:
        """Initialize the unified handler.

        Args:
            intent_analyzer: Intent analyzer (uses default if None).
            planner: Capability planner (uses default if None).
            executor: Plan executor (uses default if None).
            evaluator: Goal evaluator (uses default if None).
            synthesizer: Response synthesizer (uses default if None).
            max_iterations: Maximum agentic loop iterations.
        """
        self.intent_analyzer = intent_analyzer or get_intent_analyzer()
        self.planner = planner or get_capability_planner()
        self.executor = executor or get_plan_executor()
        self.evaluator = evaluator or get_goal_evaluator()
        self.synthesizer = synthesizer or get_synthesizer()
        self.max_iterations = max_iterations

    async def handle(
        self,
        user_input: str,
        *,
        confirm_callback: ConfirmCallback | None = None,
        skip_confirmation: bool = False,
    ) -> AgenticResponse:
        """Handle a user request (question OR task OR both).

        This is the main entry point. It orchestrates the full agentic loop
        to gather information and/or perform actions as needed.

        Args:
            user_input: The user's input (question, task, or mixed).
            confirm_callback: Optional callback for user confirmation.
            skip_confirmation: Skip all confirmations (use with caution).

        Returns:
            AgenticResponse with answer, evidence, and provenance.
        """
        logger.debug(f"Handling request: {user_input[:100]}...")

        # 1. ANALYZE: Extract intent
        intent = await self.intent_analyzer.analyze(user_input)

        if not intent.information_needs and not intent.action_requests:
            logger.debug("No actionable intent identified")
            return AgenticResponse(
                answer="I couldn't determine what you're asking for. Could you rephrase your question or request?",
                evidence=(),
                confidence=0.0,
                follow_up_suggestions=(),
                intent=intent,
                success=False,
            )

        logger.debug(f"Intent: {len(intent.information_needs)} info needs, {len(intent.action_requests)} actions")

        # 2. AGENTIC LOOP: Plan → Execute → Evaluate → Replan if needed
        all_evidence: list[ExecutionEvidence] = []
        final_evaluation = None
        iterations = 0

        for iteration in range(self.max_iterations):
            iterations = iteration + 1
            logger.debug(f"Agentic iteration {iterations}/{self.max_iterations}")

            # 2a. PLAN: Create execution plan
            plan = await self.planner.plan(intent, user_input)

            if not plan.parallel_groups:
                logger.debug("No capabilities selected")
                break

            logger.debug(f"Plan: {plan.total_calls} capabilities in {len(plan.parallel_groups)} groups")

            # 2b. EXECUTE: Run the plan
            result = await self.executor.execute(
                plan,
                confirm_callback=confirm_callback,
                skip_confirmation=skip_confirmation or intent.is_read_only,
            )

            # Collect evidence
            all_evidence.extend(result.evidence)

            # 2c. EVALUATE: Check if goal was achieved
            evaluation = await self.evaluator.evaluate(intent, result)
            final_evaluation = evaluation

            logger.debug(f"Evaluation: {evaluation.status.value}")

            # 2d. Check if we're done
            if evaluation.status == EvaluationStatus.SATISFIED:
                logger.debug("Goal satisfied")
                break

            if evaluation.status == EvaluationStatus.FAILED:
                logger.debug("Goal failed")
                break

            # 2e. REPLAN if needed
            if evaluation.status == EvaluationStatus.NEEDS_MORE:
                if iteration < self.max_iterations - 1:
                    logger.debug(f"Replanning to fill gaps: {evaluation.gaps[:3]}")
                    # For now, we just retry with the same intent
                    # A more sophisticated approach would analyze failures
                    # and adjust the plan
                    continue
                else:
                    logger.debug("Max iterations reached")
                    break

        # 3. SYNTHESIZE: Generate response
        response = await self._synthesize_response(
            user_input=user_input,
            intent=intent,
            evidence=tuple(all_evidence),
            evaluation=final_evaluation,
            iterations=iterations,
        )

        return response

    async def _synthesize_response(
        self,
        user_input: str,
        intent: AgenticIntent,
        evidence: tuple[ExecutionEvidence, ...],
        evaluation: Any,
        iterations: int,
    ) -> AgenticResponse:
        """Synthesize a response from execution results.

        Args:
            user_input: Original user input.
            intent: The analyzed intent.
            evidence: All collected evidence.
            evaluation: Final evaluation result.
            iterations: Number of iterations.

        Returns:
            AgenticResponse with synthesized answer.
        """
        # Convert ExecutionEvidence to GatheredEvidence for synthesizer
        # (backwards compatibility with existing synthesizer)
        gathered_evidence = tuple(
            GatheredEvidence(
                capability=e.capability,
                args=e.args,
                success=e.success,
                output=e.output_text or (str(e.output) if e.output else None),
                error=e.error,
                duration_ms=e.duration_ms,
                timestamp=e.timestamp,
            )
            for e in evidence
        )

        # Build a GatherResult for the synthesizer
        from elle.cli.agentic.models import CapabilityCall, GatherPlan

        calls = tuple(
            CapabilityCall(
                capability=e.capability,
                args=e.args,
                purpose=f"Executed {e.capability}",
            )
            for e in evidence
        )

        gather_result = GatherResult(
            plan=GatherPlan(
                needs=intent.information_needs,
                calls=calls,
                estimated_duration_ms=sum(e.duration_ms for e in evidence),
            ),
            evidence=gathered_evidence,
            sufficient=evaluation.status == EvaluationStatus.SATISFIED if evaluation else False,
            missing=evaluation.gaps if evaluation else (),
        )

        # Use the synthesizer to generate the answer
        synth_response = await self.synthesizer.synthesize(user_input, gather_result)

        # Build actions_taken summary
        actions_taken: list[str] = []
        for e in evidence:
            if e.success and not e.capability.endswith((".status", ".logs", ".info", ".list", ".read")):
                actions_taken.append(f"{e.capability}: success")
            elif not e.success:
                actions_taken.append(f"{e.capability}: failed - {e.error or 'unknown error'}")

        # Determine overall success
        success = evaluation.status == EvaluationStatus.SATISFIED if evaluation else any(e.success for e in evidence)

        return AgenticResponse(
            answer=synth_response.answer,
            evidence=gathered_evidence,  # Return as GatheredEvidence for compatibility
            confidence=synth_response.confidence,
            follow_up_suggestions=synth_response.follow_up_suggestions,
            intent=intent,
            actions_taken=tuple(actions_taken),
            success=success,
            iterations=iterations,
        )

    def can_handle(self, user_input: str) -> bool:
        """Quick check if this handler can process the input.

        Args:
            user_input: The user's input.

        Returns:
            True if the handler might be able to process this input.
        """
        return self.intent_analyzer.can_handle(user_input)


# =============================================================================
# AgenticQuestionHandler (Legacy - Backwards Compatible)
# =============================================================================


class AgenticQuestionHandler:
    """Handles system questions agentically.

    Orchestrates the analysis → selection → execution → synthesis pipeline
    to answer questions by actually gathering system information.

    DEPRECATED: Use UnifiedAgenticHandler for new code. This is kept for
    backwards compatibility.
    """

    def __init__(
        self,
        analyzer: InformationNeedAnalyzer | None = None,
        selector: CapabilitySelector | None = None,
        executor: GatherExecutor | None = None,
        evaluator: SufficiencyEvaluator | None = None,
        synthesizer: ResponseSynthesizer | None = None,
        max_iterations: int = 2,
    ) -> None:
        """Initialize the handler.

        Args:
            analyzer: Information need analyzer (uses default if None).
            selector: Capability selector (uses default if None).
            executor: Gather executor (uses default if None).
            evaluator: Sufficiency evaluator (uses default if None).
            synthesizer: Response synthesizer (uses default if None).
            max_iterations: Maximum gather-evaluate iterations.
        """
        self.analyzer = analyzer or get_analyzer()
        self.selector = selector or get_selector()
        self.executor = executor or get_executor()
        self.evaluator = evaluator or get_evaluator()
        self.synthesizer = synthesizer or get_synthesizer()
        self.max_iterations = max_iterations

    async def handle(self, question: str) -> AgenticResponse:
        """Handle a system question agentically.

        Args:
            question: The user's question.

        Returns:
            AgenticResponse with the answer and evidence.
        """
        logger.debug(f"Handling question: {question[:100]}")

        # 1. Analyze what information is needed
        needs = self.analyzer.analyze(question)

        if not needs:
            logger.debug("No information needs identified")
            return AgenticResponse(
                answer="I couldn't determine what information you're asking about. Could you rephrase your question?",
                evidence=(),
                confidence=0.0,
                follow_up_suggestions=(),
            )

        logger.debug(f"Identified {len(needs)} information needs")

        # 2. Select capabilities to gather information
        plan = self.selector.select(needs)

        if not plan.calls:
            logger.debug("No capabilities selected")
            return AgenticResponse(
                answer="I don't have the ability to gather that information. "
                "This may be outside my current capabilities.",
                evidence=(),
                confidence=0.0,
                follow_up_suggestions=(),
            )

        logger.debug(f"Selected {len(plan.calls)} capabilities")

        # 3. Execute and gather evidence (with retry loop)
        result: GatherResult | None = None

        for iteration in range(self.max_iterations):
            logger.debug(f"Gather iteration {iteration + 1}/{self.max_iterations}")

            result = await self.executor.execute(plan)

            # 4. Check if sufficient
            if result.sufficient:
                logger.debug("Evidence is sufficient")
                break

            # 5. Try to identify gaps and gather more
            if iteration < self.max_iterations - 1:
                additional_needs = self.evaluator.identify_gaps(question, result)

                if not additional_needs:
                    logger.debug("No additional needs identified")
                    break

                logger.debug(f"Identified {len(additional_needs)} additional needs")
                plan = self.selector.select(additional_needs)

                if not plan.calls:
                    logger.debug("No additional capabilities available")
                    break

        if result is None:
            return AgenticResponse(
                answer="I encountered an error while gathering information.",
                evidence=(),
                confidence=0.0,
                follow_up_suggestions=(),
            )

        # 6. Synthesize response
        logger.debug("Synthesizing response")
        response = await self.synthesizer.synthesize(question, result)

        logger.debug(f"Generated response with confidence {response.confidence:.2f}")
        return response

    def can_handle(self, question: str) -> bool:
        """Check if this handler can handle a question.

        A question can be handled if it matches patterns that indicate
        a request for system information.

        Args:
            question: The question to check.

        Returns:
            True if the handler can potentially handle this question.
        """
        needs = self.analyzer.analyze(question)
        return len(needs) > 0


# =============================================================================
# Module-level singletons
# =============================================================================

_handler: AgenticQuestionHandler | None = None
_unified_handler: UnifiedAgenticHandler | None = None


def get_agentic_handler() -> AgenticQuestionHandler:
    """Get the shared legacy handler instance.

    Returns:
        The AgenticQuestionHandler singleton.

    DEPRECATED: Use get_unified_handler() for new code.
    """
    global _handler
    if _handler is None:
        _handler = AgenticQuestionHandler()
    return _handler


def get_unified_handler() -> UnifiedAgenticHandler:
    """Get the shared unified handler instance.

    Returns:
        The UnifiedAgenticHandler singleton.
    """
    global _unified_handler
    if _unified_handler is None:
        _unified_handler = UnifiedAgenticHandler()
    return _unified_handler


def reset_agentic_handler() -> None:
    """Reset all shared handler instances."""
    global _handler, _unified_handler
    _handler = None
    _unified_handler = None


# =============================================================================
# Exception Classes
# =============================================================================


class AgenticHandlerError(Exception):
    """Base exception for agentic handler errors."""

    pass


class AnalysisError(AgenticHandlerError):
    """Error during question analysis."""

    pass


class ExecutionError(AgenticHandlerError):
    """Error during capability execution."""

    pass


class SynthesisError(AgenticHandlerError):
    """Error during response synthesis."""

    pass
