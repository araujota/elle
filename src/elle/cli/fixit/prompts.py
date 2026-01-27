"""Prompt templates for the Fixit command.

Contains the base system prompt and fixit-specific prompt template
for LLM-based error analysis.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from elle.cli.fixit.models import FixitContext

# =============================================================================
# Base System Prompt
# =============================================================================

BASE_SYSTEM_PROMPT = """\
You are ELLE, a local Linux system assistant for Ubuntu 24.04 LTS.

CORE PRINCIPLES:
- You run LOCALLY via Ollama - no cloud, no external calls
- You NEVER suggest sudo directly - privileged operations go through Polkit
- You explain before acting - users understand what will happen
- You verify before executing - check flags exist, commands parse
- You prefer reversible actions - suggest undo/rollback when possible

OUTPUT FORMAT:
Always respond with valid JSON matching the requested schema.
No markdown, no explanations outside the JSON structure.
"""

# =============================================================================
# Fixit Prompt Template
# =============================================================================

FIXIT_PROMPT_TEMPLATE = """\
A command has failed. Analyze the error and suggest fixes.

FAILED COMMAND: {command}
EXIT CODE: {exit_code}

STDERR:
{stderr}

STDOUT:
{stdout}

WORKING DIRECTORY: {cwd}

{man_context}

{prior_art_context}

ANALYSIS REQUIREMENTS:
1. Identify the error category from: command_not_found, permission_denied, \
file_not_found, syntax_error, argument_error, dependency_missing, \
resource_exhausted, network_error, configuration_error, other
2. Explain WHY the command failed in plain language
3. Suggest 1-3 fix commands, ordered by likelihood of success
4. For each fix: explain what it does, assess risk (safe/moderate/high), \
note if privileges needed
5. Provide a verification command to confirm the fix worked

CONSTRAINTS:
- Never suggest: rm -rf, sudo without explanation, curl|bash
- Prefer: specific flags over globs, reversible over destructive
- If the error suggests a package is missing, suggest installing it
- If permission denied, explain what permissions are needed

Respond with JSON matching this schema:
{schema}
"""

# =============================================================================
# JSON Schema for LLM Response
# =============================================================================

FIXIT_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["diagnosis", "suggestions"],
    "properties": {
        "diagnosis": {
            "type": "object",
            "required": ["error_category", "summary", "root_cause", "confidence"],
            "properties": {
                "error_category": {
                    "type": "string",
                    "enum": [
                        "command_not_found",
                        "permission_denied",
                        "file_not_found",
                        "syntax_error",
                        "argument_error",
                        "dependency_missing",
                        "resource_exhausted",
                        "network_error",
                        "configuration_error",
                        "other",
                    ],
                },
                "summary": {"type": "string"},
                "root_cause": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        },
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["command", "explanation", "risk_level"],
                "properties": {
                    "command": {"type": "string"},
                    "explanation": {"type": "string"},
                    "risk_level": {"type": "string", "enum": ["safe", "moderate", "high"]},
                    "requires_privilege": {"type": "boolean"},
                    "verification": {"type": "string"},
                },
            },
        },
        "alternative_approach": {"type": "string"},
        "learn_more": {"type": "array", "items": {"type": "string"}},
    },
}


# =============================================================================
# Prompt Builder
# =============================================================================


def _format_man_context(context: FixitContext) -> str:
    """Format Man Vault snippets for the prompt.

    Args:
        context: Fixit context with man snippets.

    Returns:
        Formatted string for prompt injection.
    """
    if not context.man_snippets:
        return "DOCUMENTATION CONTEXT:\nNo relevant documentation found."

    lines = ["DOCUMENTATION CONTEXT:"]
    for _i, snippet in enumerate(context.man_snippets[:3], 1):
        lines.append(f"\n--- {snippet.name}({snippet.section}) ---")
        if snippet.match_section:
            lines.append(f"Section: {snippet.match_section}")
        # Truncate snippet to reasonable length
        text = snippet.snippet[:800]
        if len(snippet.snippet) > 800:
            text += "..."
        lines.append(text)

    return "\n".join(lines)


def _format_prior_art_context(context: FixitContext) -> str:
    """Format prior incidents for the prompt.

    Includes successful actions so the LLM can recommend
    proven fixes for similar failures.

    Args:
        context: Fixit context with prior art.

    Returns:
        Formatted string for prompt injection.
    """
    if not context.prior_art:
        return "SIMILAR PAST INCIDENTS:\nNo similar past incidents found."

    lines = ["SIMILAR PAST INCIDENTS:"]
    lines.append("(Ranked by relevance × outcome quality × recency)")
    lines.append("")

    for i, art in enumerate(context.prior_art[:3], 1):
        # Header with relevance info
        outcome_emoji = {"improved": "+", "partial": "~", "no_change": "-", "worse": "!"}.get(art.outcome, "?")
        lines.append(f"--- Incident #{i}: {art.title} [{outcome_emoji}{art.outcome}] ---")

        # Match quality
        if art.score > 0:
            lines.append(f"Relevance: {art.score:.2f} (precondition match: {art.precondition_match:.0%})")
        if art.days_ago > 0:
            lines.append(f"Age: {art.days_ago} days ago")

        # Original trigger for comparison
        if art.trigger_command:
            lines.append(f"Original failed command: {art.trigger_command}")

        # Summary and root cause
        if art.summary:
            lines.append(f"Summary: {art.summary[:300]}")
        if art.root_cause:
            lines.append(f"Root Cause: {art.root_cause}")

        # CRITICAL: Successful actions that fixed it
        if art.successful_actions:
            lines.append("Successful fix commands:")
            for action in art.successful_actions[:3]:
                lines.append(f"  $ {action.command}")

        # Decision/approach taken
        if art.decision and "diagnosis" in art.decision:
            diag = art.decision["diagnosis"]
            if isinstance(diag, dict) and "error_category" in diag:
                lines.append(f"Diagnosed as: {diag['error_category']}")

        lines.append("")

    lines.append("IMPORTANT: If the current failure matches a prior incident with outcome='improved',")
    lines.append("strongly consider recommending the same successful fix commands.")
    return "\n".join(lines)


def build_fixit_prompt(context: FixitContext) -> str:
    """Build the complete fixit prompt for the LLM.

    Combines the template with context information.

    Args:
        context: Complete fixit context with failure details,
            man snippets, and prior art.

    Returns:
        Formatted prompt string ready for LLM.
    """
    failure = context.failure

    # Truncate outputs for prompt
    stderr = failure.stderr[:2000] if failure.stderr else "(empty)"
    stdout = failure.stdout[:1000] if failure.stdout else "(empty)"

    return FIXIT_PROMPT_TEMPLATE.format(
        command=failure.command,
        exit_code=failure.exit_code,
        stderr=stderr,
        stdout=stdout,
        cwd=str(failure.cwd),
        man_context=_format_man_context(context),
        prior_art_context=_format_prior_art_context(context),
        schema=json.dumps(FIXIT_RESPONSE_SCHEMA, indent=2),
    )


def get_schema_hint() -> dict[str, Any]:
    """Get the schema hint for generate_json().

    Returns a simplified schema dict for the LLM's schema hint parameter.
    """
    return {
        "diagnosis": {
            "error_category": "string",
            "summary": "string",
            "root_cause": "string",
            "confidence": "number",
        },
        "suggestions": [
            {
                "command": "string",
                "explanation": "string",
                "risk_level": "safe|moderate|high",
                "requires_privilege": "boolean",
                "verification": "string",
            }
        ],
        "alternative_approach": "string|null",
        "learn_more": ["string"],
    }
