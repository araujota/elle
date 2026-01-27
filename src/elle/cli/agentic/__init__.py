"""ELLE Unified Agentic Execution System.

A unified system that handles both questions AND tasks through capability execution.
When a user asks "why is nginx failing and can you restart it?", ELLE will:

1. Create an AgenticInput from the user message
2. Run unified retrieval (incidents, man vault, capabilities)
3. Reason and execute via tool-calling loop
4. Verify outcomes for mutating operations
5. Record complete audit trail
6. Return response with provenance

Usage (RECOMMENDED - Agentic Loop):
    from elle.cli.agentic import (
        AgenticLoop,
        run_agentic_loop,
        run_for_incident,
        run_for_failed_command,
    )

    # User question/task
    result = await run_agentic_loop("why is nginx failing?", stream_callback=print)
    print(result.response)
    print(f"Used {result.tool_call_count} tool calls")

    # Daemon incident
    result = await run_for_incident(incident_id)

    # Failed command (fixit)
    result = await run_for_failed_command("nginx -t", exit_code=1, stderr="...")

Usage (Unified Input):
    from elle.cli.agentic import AgenticInput, InputSource

    # Create input from various sources
    input = AgenticInput.from_user_message("restart nginx")
    input = AgenticInput.from_incident(incident_id)
    input = AgenticInput.from_failed_command("cmd", 1, stderr="...")

    result = await loop.run_with_input(input)

Usage (Unified Handler - LEGACY):
    from elle.cli.agentic import UnifiedAgenticHandler, get_unified_handler

    handler = get_unified_handler()
    response = await handler.handle("why is nginx failing and can you restart it?")
    print(response.answer)
    print(response.actions_taken)

Components (Core):
    - AgenticInput: Unified input model for all trigger types
    - AgenticLoop: Core ReAct-style tool-calling loop with KV cache
    - UnifiedRetrievalPipeline: Parallel multi-source retrieval
    - AuditRecorder: Complete audit trail recording
    - ToolRegistry: Registry of available tools for the loop

Components (Retrieval):
    - RetrievalContext: Combined context from all sources
    - IncidentMatch: Matched past incidents
    - ManSnippet: Documentation snippets
    - CapabilityMatch: Semantic capability matches

Components (LEGACY - still supported):
    - IntentAnalyzer: Extracts unified intent
    - CapabilityPlanner: Maps intent to execution plan
    - PlanExecutor: Executes plan through CapabilityExecutor
    - UnifiedAgenticHandler: Main orchestrator
"""

# =============================================================================
# NEW Unified Input System
# =============================================================================
from elle.cli.agentic.unified_input import (
    AgenticInput,
    FailedCommand,
    InputSource,
    SystemFingerprint,
)

# =============================================================================
# NEW ReAct Loop
# =============================================================================
from elle.cli.agentic.loop import (
    AgenticLoop,
    AgenticLoopResult,
    ToolCallRecord,
    get_agentic_loop,
    is_agentic_loop_enabled,
    reset_agentic_loop,
    run_agentic_loop,
    run_for_failed_command,
    run_for_incident,
)

# =============================================================================
# NEW Tools
# =============================================================================
from elle.cli.agentic.tools import (
    ConfirmCallback,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    get_tool_registry,
    reset_tool_registry,
)

# =============================================================================
# NEW Retrieval Pipeline
# =============================================================================
from elle.cli.agentic.retrieval_pipeline import (
    CapabilityMatch,
    IncidentMatch,
    ManSnippet,
    RetrievalContext,
    UnifiedRetrievalPipeline,
    get_retrieval_pipeline,
    reset_retrieval_pipeline,
    retrieve_context,
)

# =============================================================================
# NEW Audit System
# =============================================================================
from elle.cli.agentic.audit import (
    AuditRecord,
    AuditRecorder,
    Provenance,
    ToolCallRecord as AuditToolCallRecord,
    VerificationResult,
    get_audit_recorder,
    reset_audit_recorder,
)

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
    # NEW Unified Input System
    # ==========================================================================
    "AgenticInput",
    "InputSource",
    "FailedCommand",
    "SystemFingerprint",
    # ==========================================================================
    # NEW ReAct Loop System
    # ==========================================================================
    # Core loop
    "AgenticLoop",
    "AgenticLoopResult",
    "ToolCallRecord",
    "run_agentic_loop",
    "run_for_incident",
    "run_for_failed_command",
    "get_agentic_loop",
    "reset_agentic_loop",
    "is_agentic_loop_enabled",
    # Tools
    "ToolRegistry",
    "ToolSpec",
    "ToolResult",
    "ConfirmCallback",
    "get_tool_registry",
    "reset_tool_registry",
    # ==========================================================================
    # NEW Retrieval Pipeline
    # ==========================================================================
    "RetrievalContext",
    "UnifiedRetrievalPipeline",
    "IncidentMatch",
    "ManSnippet",
    "CapabilityMatch",
    "get_retrieval_pipeline",
    "reset_retrieval_pipeline",
    "retrieve_context",
    # ==========================================================================
    # NEW Audit System
    # ==========================================================================
    "AuditRecord",
    "AuditRecorder",
    "AuditToolCallRecord",
    "Provenance",
    "VerificationResult",
    "get_audit_recorder",
    "reset_audit_recorder",
    # ==========================================================================
    # Unified Handler System
    # ==========================================================================
    # Models
    "ActionRequest",
    "ActionType",
    "AgenticIntent",
    "EvaluationResult",
    "EvaluationStatus",
    "ExecutionEvidence",
    "ExecutionPlan",
    "ExecutionResult",
    "ParallelGroup",
    # Components
    "IntentAnalyzer",
    "CapabilityPlanner",
    "PlanExecutor",
    "GoalEvaluator",
    "UnifiedAgenticHandler",
    # Factory functions
    "get_intent_analyzer",
    "get_capability_planner",
    "get_plan_executor",
    "get_goal_evaluator",
    "get_unified_handler",
    # Reset functions
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
