"""Prompt templates for the System Task Planner.

Contains the base system prompt and task planner prompt template
for LLM-based command plan generation.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from elle.cli.planner.models import PlanContext

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
- You assess risk honestly - warn about potential issues

OUTPUT FORMAT:
Always respond with valid JSON matching the requested schema.
No markdown, no explanations outside the JSON structure.
"""

# =============================================================================
# Task Planner Prompt Template
# =============================================================================

PLANNER_PROMPT_TEMPLATE = """\
Generate a safe, verified command plan for the following task.

TASK REQUEST: {request}

WORKING DIRECTORY: {cwd}

{man_context}

{prior_context}

PLANNING REQUIREMENTS:
1. Break down the task into discrete, atomic steps
2. For each step:
   - Provide the exact command to run
   - Explain what it does in plain language
   - Assess risk level: low (read-only/info), medium (installs/config), high (destructive)
   - Note if privileges are required
3. Provide validation checks to verify success
4. Provide rollback commands for any state changes
5. List risks and warnings

CONSTRAINTS:
- Never suggest: rm -rf /, sudo (use requires_privilege flag instead)
- Prefer: specific flags over globs, reversible over destructive
- CRITICAL: For netplan, ufw, fstab, crontab changes - rollback is REQUIRED
- For medium/high risk operations - validation checks are REQUIRED
- Ground commands in the provided man page documentation
- Use deterministic validation commands (not grep output that may vary)

RISK LEVELS:
- low: Read-only operations, information gathering, no state changes
  Examples: ls, cat, grep, systemctl status, ufw status
- medium: Package installs, service restarts, config changes with easy rollback
  Examples: apt install, systemctl restart, ufw allow, netplan apply
- high: Destructive operations, hard to reverse, affects system stability
  Examples: rm, format, partition changes, kernel modifications

Respond with JSON matching this schema:
{schema}
"""

# =============================================================================
# JSON Schema for LLM Response
# =============================================================================

PLANNER_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["title", "explanation", "steps"],
    "properties": {
        "title": {
            "type": "string",
            "description": "Short title for the plan (e.g., 'Open firewall ports')",
        },
        "explanation": {
            "type": "string",
            "description": "What this plan accomplishes",
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["command", "explanation", "risk_level"],
                "properties": {
                    "command": {"type": "string"},
                    "explanation": {"type": "string"},
                    "risk_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "requires_privilege": {"type": "boolean", "default": False},
                    "can_fail": {"type": "boolean", "default": False},
                },
            },
        },
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["command", "description"],
                "properties": {
                    "command": {"type": "string"},
                    "description": {"type": "string"},
                    "expected": {"type": "string"},
                },
            },
        },
        "rollback": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["command", "explanation"],
                "properties": {
                    "command": {"type": "string"},
                    "explanation": {"type": "string"},
                    "requires_privilege": {"type": "boolean", "default": False},
                },
            },
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
        },
        "grounded_in": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Man pages referenced (e.g., 'ufw(8)', 'netplan(5)')",
        },
    },
}


# =============================================================================
# Context Formatters
# =============================================================================


def _format_man_context(context: PlanContext) -> str:
    """Format Man Vault documentation for the prompt.

    Args:
        context: Plan context with man docs.

    Returns:
        Formatted string for prompt injection.
    """
    if not context.man_docs:
        return "DOCUMENTATION CONTEXT:\nNo relevant documentation found."

    lines = ["DOCUMENTATION CONTEXT:"]
    for _i, doc in enumerate(context.man_docs[:4], 1):
        lines.append(f"\n--- {doc.name}({doc.section}) ---")
        # Truncate snippet to reasonable length
        text = doc.snippet[:1000]
        if len(doc.snippet) > 1000:
            text += "..."
        lines.append(text)

    return "\n".join(lines)


def _format_prior_context(context: PlanContext) -> str:
    """Format prior plans for the prompt.

    Args:
        context: Plan context with prior art.

    Returns:
        Formatted string for prompt injection.
    """
    if not context.prior_plans:
        return "SIMILAR PAST TASKS:\nNo similar past tasks found."

    lines = ["SIMILAR PAST TASKS:"]
    lines.append("(Learn from these past successful executions)")
    lines.append("")

    for i, prior in enumerate(context.prior_plans[:3], 1):
        outcome_emoji = {
            "success": "+",
            "partial": "~",
            "failed": "-",
            "rolled_back": "!",
        }.get(prior.outcome, "?")

        lines.append(f"--- Task #{i}: {prior.title} [{outcome_emoji}{prior.outcome}] ---")

        if prior.plan_summary:
            lines.append(f"Summary: {prior.plan_summary}")

        if prior.commands_executed:
            lines.append("Commands that worked:")
            for cmd in prior.commands_executed[:5]:
                lines.append(f"  $ {cmd}")

        if prior.rollback_used:
            lines.append("Note: Rollback was used")

        lines.append("")

    lines.append("IMPORTANT: If the current task matches a prior successful execution,")
    lines.append("consider using the same proven command sequence.")
    return "\n".join(lines)


def build_planner_prompt(context: PlanContext) -> str:
    """Build the complete planner prompt for the LLM.

    Combines the template with context information.

    Args:
        context: Complete plan context with request, man docs, and prior art.

    Returns:
        Formatted prompt string ready for LLM.
    """
    return PLANNER_PROMPT_TEMPLATE.format(
        request=context.request.request,
        cwd=str(context.request.cwd),
        man_context=_format_man_context(context),
        prior_context=_format_prior_context(context),
        schema=json.dumps(PLANNER_RESPONSE_SCHEMA, indent=2),
    )


def get_schema_hint() -> dict[str, Any]:
    """Get the schema hint for generate_json().

    Returns a simplified schema dict for the LLM's schema hint parameter.
    """
    return {
        "title": "string",
        "explanation": "string",
        "steps": [
            {
                "command": "string",
                "explanation": "string",
                "risk_level": "low|medium|high",
                "requires_privilege": "boolean",
                "can_fail": "boolean",
            }
        ],
        "checks": [
            {
                "command": "string",
                "description": "string",
                "expected": "string|null",
            }
        ],
        "rollback": [
            {
                "command": "string",
                "explanation": "string",
                "requires_privilege": "boolean",
            }
        ],
        "risks": ["string"],
        "grounded_in": ["string"],
    }
