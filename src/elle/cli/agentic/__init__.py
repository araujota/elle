"""ELLE Agentic Question Answering System.

Transforms ELLE from suggesting commands to actually running them to answer questions.
When a user asks "what is the status of nginx?", ELLE will:

1. Analyze the question to identify what information is needed
2. Select appropriate read-only capabilities to gather that information
3. Execute capabilities and collect evidence
4. Evaluate if gathered information is sufficient
5. Synthesize evidence into a natural language response

Usage:
    from elle.cli.agentic import AgenticQuestionHandler, get_agentic_handler

    handler = get_agentic_handler()
    response = await handler.handle("what is the status of nginx?", session)
    print(response.answer)

Components:
    - InformationNeedAnalyzer: Determines what information is needed
    - CapabilitySelector: Maps needs to capability calls
    - GatherExecutor: Executes read-only capabilities
    - SufficiencyEvaluator: Checks if evidence is sufficient
    - ResponseSynthesizer: Generates natural language response
    - AgenticQuestionHandler: Main orchestrator
"""

from elle.cli.agentic.analyzer import (
    InformationNeedAnalyzer,
    get_analyzer,
    reset_analyzer,
)
from elle.cli.agentic.evaluator import (
    SufficiencyEvaluator,
    get_evaluator,
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
    get_agentic_handler,
    reset_agentic_handler,
)
from elle.cli.agentic.models import (
    AgenticResponse,
    CapabilityCall,
    GatheredEvidence,
    GatherPlan,
    GatherResult,
    InformationNeed,
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
    # Models
    "InformationNeed",
    "CapabilityCall",
    "GatheredEvidence",
    "GatherPlan",
    "GatherResult",
    "AgenticResponse",
    # Components
    "InformationNeedAnalyzer",
    "CapabilitySelector",
    "GatherExecutor",
    "SufficiencyEvaluator",
    "ResponseSynthesizer",
    "AgenticQuestionHandler",
    # Factory functions
    "get_analyzer",
    "get_selector",
    "get_executor",
    "get_evaluator",
    "get_synthesizer",
    "get_agentic_handler",
    # Reset functions
    "reset_analyzer",
    "reset_selector",
    "reset_executor",
    "reset_evaluator",
    "reset_synthesizer",
    "reset_agentic_handler",
    # Exceptions
    "AgenticHandlerError",
    "AnalysisError",
    "ExecutionError",
    "SynthesisError",
]
