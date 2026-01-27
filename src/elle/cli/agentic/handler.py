"""AgenticQuestionHandler - Main orchestrator for agentic question answering.

Coordinates the full flow from question to answer:
1. Analyze what information is needed
2. Select capabilities to gather that information
3. Execute capabilities and collect evidence
4. Evaluate if evidence is sufficient
5. Synthesize evidence into a natural language response
"""

from __future__ import annotations

import logging

from elle.cli.agentic.analyzer import InformationNeedAnalyzer, get_analyzer
from elle.cli.agentic.evaluator import SufficiencyEvaluator, get_evaluator
from elle.cli.agentic.executor import GatherExecutor, get_executor
from elle.cli.agentic.models import AgenticResponse, GatherResult
from elle.cli.agentic.selector import CapabilitySelector, get_selector
from elle.cli.agentic.synthesizer import ResponseSynthesizer, get_synthesizer

logger = logging.getLogger(__name__)


# =============================================================================
# AgenticQuestionHandler
# =============================================================================


class AgenticQuestionHandler:
    """Handles system questions agentically.

    Orchestrates the analysis → selection → execution → synthesis pipeline
    to answer questions by actually gathering system information.
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
                answer="I couldn't determine what information you're asking about. "
                "Could you rephrase your question?",
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
# Module-level singleton
# =============================================================================

_handler: AgenticQuestionHandler | None = None


def get_agentic_handler() -> AgenticQuestionHandler:
    """Get the shared handler instance.

    Returns:
        The AgenticQuestionHandler singleton.
    """
    global _handler
    if _handler is None:
        _handler = AgenticQuestionHandler()
    return _handler


def reset_agentic_handler() -> None:
    """Reset the shared handler instance."""
    global _handler
    _handler = None


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
