"""Fixit - LLM-driven command failure analysis and repair.

The fixit module provides intelligent error analysis when commands fail:
1. Builds context from session state, Man Vault, and Incident Vault
2. Analyzes failures using LLM
3. Verifies suggested fixes for safety
4. Presents results via interactive UI or text renderer
5. Records outcomes to Incident Vault for learning

Usage (REPL mode):
    After a command fails, type 'fix' to get analysis and suggestions.

Usage (one-shot mode):
    elle fix

Usage (programmatic):
    from elle.cli.fixit import get_fixit_service

    service = get_fixit_service()
    result = service.run_full_pipeline(session)

    # For interactive mode:
    from elle.cli.fixit import run_interactive_fixit
    final_result = run_interactive_fixit(result, session)

    # For text output:
    from elle.cli.fixit import render_fixit_result
    print(render_fixit_result(result))
"""

# Models
# Interactive UI
from elle.cli.fixit.interactive import (
    FixitInteractive,
    run_interactive_fixit,
)
from elle.cli.fixit.models import (
    CommandFailure,
    ErrorCategory,
    FixCommand,
    FixitAction,
    FixitAnalysis,
    FixitContext,
    FixitDiagnosis,
    FixitOutcome,
    FixitResult,
    ManSnippetContext,
    PriorArtContext,
    RiskLevel,
    SuccessfulAction,
    VerificationResult,
    VerificationStatus,
    VerifiedFixCommand,
)

# Rendering
from elle.cli.fixit.renderer import (
    render_action_result,
    render_compact_result,
    render_fixit_result,
    render_outcome_prompt,
)

# Service
from elle.cli.fixit.service import (
    FixitService,
    get_fixit_service,
    reset_fixit_service,
)

# Verification
from elle.cli.fixit.verifier import (
    filter_safe_suggestions,
    verify_analysis,
    verify_command,
)

__all__ = [
    # Models
    "CommandFailure",
    "ErrorCategory",
    "FixCommand",
    "FixitAction",
    "FixitAnalysis",
    "FixitContext",
    "FixitDiagnosis",
    "FixitOutcome",
    "FixitResult",
    "ManSnippetContext",
    "PriorArtContext",
    "RiskLevel",
    "SuccessfulAction",
    "VerificationResult",
    "VerificationStatus",
    "VerifiedFixCommand",
    # Service
    "FixitService",
    "get_fixit_service",
    "reset_fixit_service",
    # Rendering
    "render_action_result",
    "render_compact_result",
    "render_fixit_result",
    "render_outcome_prompt",
    # Interactive
    "FixitInteractive",
    "run_interactive_fixit",
    # Verification
    "filter_safe_suggestions",
    "verify_analysis",
    "verify_command",
]
