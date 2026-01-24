"""Agentic RAG engine for grounded reasoning.

Inputs:
- User intent
- System facts
- Telemetry (if relevant)
- Man Vault snippets

Outputs CommandPlan for tasks/fixes.

Verification Loop (Mandatory):
1. Generate plan
2. Verify locally:
   - Flags exist
   - Commands parse
   - High-risk ops detected
3. If invalid -> re-prompt model with verifier errors
4. Return verified plan only
"""

from elle.cli.terminal.classifier import ClassificationResult
from elle.common.models import CommandPlan


def generate_plan(
    classification: ClassificationResult,
    system_facts: dict,
    telemetry: list | None = None,
    manvault_snippets: list | None = None,
) -> CommandPlan:
    """Generate a verified command plan.

    Args:
        classification: The classified user intent.
        system_facts: Current system state facts.
        telemetry: Relevant telemetry events.
        manvault_snippets: Relevant documentation snippets.

    Returns:
        A verified CommandPlan.
    """
    raise NotImplementedError


def verify_plan(plan: CommandPlan) -> tuple[bool, list[str]]:
    """Verify a generated plan before presenting to user.

    Checks:
    - Command syntax validity
    - Flag existence
    - High-risk operation detection

    Args:
        plan: The plan to verify.

    Returns:
        Tuple of (is_valid, list of error messages).
    """
    raise NotImplementedError


def generate_explanation(
    classification: ClassificationResult,
    system_facts: dict,
    telemetry: list | None = None,
    manvault_snippets: list | None = None,
) -> str:
    """Generate a grounded explanation for a system question.

    Args:
        classification: The classified user intent.
        system_facts: Current system state facts.
        telemetry: Relevant telemetry events.
        manvault_snippets: Relevant documentation snippets.

    Returns:
        Formatted explanation string.
    """
    raise NotImplementedError
