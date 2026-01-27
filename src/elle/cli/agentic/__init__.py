"""ELLE Unified Agentic Execution System.

A unified system that handles both questions AND tasks through capability execution.
When a user asks "why is nginx failing and can you restart it?", ELLE will:

1. Analyze the intent to extract information needs AND action requests
2. Plan capability calls with dependencies
3. Execute capabilities in parallel groups
4. Evaluate if the goal was achieved
5. Loop and replan if needed
6. Synthesize a response with full provenance

Usage (NEW - Unified Handler):
    from elle.cli.agentic import UnifiedAgenticHandler, get_unified_handler

    handler = get_unified_handler()
    response = await handler.handle("why is nginx failing and can you restart it?")
    print(response.answer)
    print(response.actions_taken)

Usage (LEGACY - Question Handler):
    from elle.cli.agentic import AgenticQuestionHandler, get_agentic_handler

    handler = get_agentic_handler()
    response = await handler.handle("what is the status of nginx?")
    print(response.answer)

Components (NEW):
    - IntentAnalyzer: Extracts unified intent (info needs + actions)
    - CapabilityPlanner: Maps intent to execution plan with dependencies
    - PlanExecutor: Executes plan through real CapabilityExecutor
    - GoalEvaluator: Checks if goal was achieved
    - UnifiedAgenticHandler: Main orchestrator for unified system

Components (LEGACY - still supported):
    - InformationNeedAnalyzer: Determines what information is needed
    - CapabilitySelector: Maps needs to capability calls
    - GatherExecutor: Executes read-only capabilities
    - SufficiencyEvaluator: Checks if evidence is sufficient
    - ResponseSynthesizer: Generates natural language response
    - AgenticQuestionHandler: Question-only orchestrator
"""

# =============================================================================
# NEW Unified System Imports
# =============================================================================

# =============================================================================
# LEGACY Imports (backwards compatibility)
# =============================================================================
from elle.cli.agentic.analyzer import (
    InformationNeedAnalyzer,
    get_analyzer,
    reset_analyzer,
)
from elle.cli.agentic.evaluator import (
    GoalEvaluator,
    SufficiencyEvaluator,
    get_evaluator,
    get_goal_evaluator,
    reset_evaluator,
)
from elle.cli.agentic.executor import (
    GatherExecutor,
    get_executor,
    reset_executor,
)
from elle.cli.agentic.handler import (
    AgenticHandlerError,
    AgenticQuestionHandler,
    AnalysisError,
    ExecutionError,
    SynthesisError,
    UnifiedAgenticHandler,
    get_agentic_handler,
    get_unified_handler,
    reset_agentic_handler,
)
from elle.cli.agentic.intent_analyzer import (
    IntentAnalyzer,
    get_intent_analyzer,
    reset_intent_analyzer,
)
from elle.cli.agentic.models import (
    # NEW models
    ActionRequest,
    ActionType,
    AgenticIntent,
    # LEGACY models (still supported)
    AgenticResponse,
    CapabilityCall,
    EvaluationResult,
    EvaluationStatus,
    ExecutionEvidence,
    ExecutionPlan,
    ExecutionResult,
    GatheredEvidence,
    GatherPlan,
    GatherResult,
    InformationCategory,
    InformationNeed,
    ParallelGroup,
)
from elle.cli.agentic.plan_executor import (
    PlanExecutor,
    get_plan_executor,
    reset_plan_executor,
)
from elle.cli.agentic.planner import (
    CapabilityPlanner,
    get_capability_planner,
    reset_capability_planner,
)
from elle.cli.agentic.selector import (
    CapabilitySelector,
    get_selector,
    reset_selector,
)
from elle.cli.agentic.synthesizer import (
    ResponseSynthesizer,
    get_synthesizer,
    reset_synthesizer,
)

__all__ = [
    # ==========================================================================
    # NEW Unified System
    # ==========================================================================
    # Models (NEW)
    "ActionRequest",
    "ActionType",
    "AgenticIntent",
    "EvaluationResult",
    "EvaluationStatus",
    "ExecutionEvidence",
    "ExecutionPlan",
    "ExecutionResult",
    "ParallelGroup",
    # Components (NEW)
    "IntentAnalyzer",
    "CapabilityPlanner",
    "PlanExecutor",
    "GoalEvaluator",
    "UnifiedAgenticHandler",
    # Factory functions (NEW)
    "get_intent_analyzer",
    "get_capability_planner",
    "get_plan_executor",
    "get_goal_evaluator",
    "get_unified_handler",
    # Reset functions (NEW)
    "reset_intent_analyzer",
    "reset_capability_planner",
    "reset_plan_executor",
    # ==========================================================================
    # LEGACY (backwards compatible)
    # ==========================================================================
    # Models (LEGACY)
    "InformationCategory",
    "InformationNeed",
    "CapabilityCall",
    "GatheredEvidence",
    "GatherPlan",
    "GatherResult",
    "AgenticResponse",
    # Components (LEGACY)
    "InformationNeedAnalyzer",
    "CapabilitySelector",
    "GatherExecutor",
    "SufficiencyEvaluator",
    "ResponseSynthesizer",
    "AgenticQuestionHandler",
    # Factory functions (LEGACY)
    "get_analyzer",
    "get_selector",
    "get_executor",
    "get_evaluator",
    "get_synthesizer",
    "get_agentic_handler",
    # Reset functions (LEGACY)
    "reset_analyzer",
    "reset_selector",
    "reset_executor",
    "reset_evaluator",
    "reset_synthesizer",
    "reset_agentic_handler",
    # ==========================================================================
    # Exceptions
    # ==========================================================================
    "AgenticHandlerError",
    "AnalysisError",
    "ExecutionError",
    "SynthesisError",
]
