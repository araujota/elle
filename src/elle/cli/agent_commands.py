"""Agent loop introspection commands.

Provides commands for inspecting agent loop executions:
- `agent last [N]`       - Show last N agent executions
- `agent inspect <id>`   - Detailed view of execution
- `agent stages`         - Explain the loop stages
- `agent hypotheses <id>` - Show hypotheses for execution

This module surfaces the agent's reasoning process, enabling users
to understand how ELLE arrived at its answers and actions.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Command Handlers
# =============================================================================


async def handle_agent_command(args: str) -> str:
    """Handle /agent or 'agent' commands.

    Usage:
        agent                   - Show last 5 executions (default)
        agent last [N]          - Show last N agent executions
        agent inspect <id>      - Detailed view of execution
        agent stages            - Explain the loop stages
        agent hypotheses <id>   - Show hypotheses for execution
        agent tools <id>        - Show tool calls for execution
        agent help              - Show this help

    Args:
        args: Command arguments.

    Returns:
        Response string.
    """
    args = args.strip()

    if not args or args == "last":
        return await _handle_last("5")

    parts = args.split(None, 1)
    subcommand = parts[0].lower()
    subargs = parts[1] if len(parts) > 1 else ""

    handlers: dict[str, Any] = {
        "last": _handle_last,
        "inspect": _handle_inspect,
        "stages": lambda _: _handle_stages(),
        "hypotheses": _handle_hypotheses,
        "tools": _handle_tools,
        "help": lambda _: _handle_help(),
    }

    handler = handlers.get(subcommand)
    if handler:
        result = handler(subargs)
        if asyncio.iscoroutine(result):
            return str(await result)
        return str(result)

    # If subcommand looks like an ID, treat as inspect
    if len(subcommand) >= 8:
        return await _handle_inspect(subcommand)

    return f"Unknown agent subcommand: {subcommand}\n\n{_handle_help()}"


def _handle_help() -> str:
    """Get help text for agent commands."""
    return """
agent - Agent loop introspection commands

Usage:
  agent last [N]          Show last N agent executions (default: 5)
  agent inspect <id>      Detailed view of execution
  agent stages            Explain the loop stages
  agent hypotheses <id>   Show hypotheses for execution
  agent tools <id>        Show tool calls for execution
  agent help              Show this help

The agent loop processes questions and tasks through multiple stages:
  OBSERVE -> CLASSIFY -> RETRIEVE -> HYPOTHESIZE -> SELECT -> ACT -> VERIFY -> RECORD

Use 'agent stages' for detailed information about each stage.
Use 'agent last' to see recent executions and their IDs.
""".strip()


async def _handle_last(args: str) -> str:
    """Handle 'agent last' command."""
    from elle.cli.agentic.execution import get_execution_summaries

    limit = 5
    if args:
        try:
            limit = int(args)
        except ValueError:
            return f"Invalid number: {args}"

    try:
        summaries = get_execution_summaries(limit)

        if not summaries:
            return "No agent executions recorded.\n\nUse the agentic loop to process questions or tasks."

        lines = [f"LAST {len(summaries)} AGENT EXECUTIONS:", ""]

        for summary in summaries:
            execution_id = summary["execution_id"][:8]
            user_input = _truncate(summary["user_input"], 50)
            intent = summary["classified_intent"]
            status = "[OK]" if summary["success"] else "[FAIL]"
            incident = summary["incident_id"][:8] + "..." if summary["incident_id"] else "-"
            tools = summary["total_tool_calls"]
            caps = ", ".join(summary["capabilities_executed"][:2]) or "-"
            if len(summary["capabilities_executed"]) > 2:
                caps += f" +{len(summary['capabilities_executed']) - 2}"

            started = _format_time(summary["started_at"])

            lines.append(f"{status} {execution_id}  {started}")
            lines.append(f"    Input: {user_input}")
            lines.append(f"    Intent: {intent} | Tools: {tools} | Caps: {caps}")
            if summary["incident_id"]:
                lines.append(f"    Incident: {incident}")
            lines.append("")

        lines.append("Use 'agent inspect <id>' for full details.")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error getting executions: {e}")
        return f"Error getting executions: {e}"


async def _handle_inspect(args: str) -> str:
    """Handle 'agent inspect' command."""
    from elle.cli.agentic.execution import get_execution_store

    execution_id = args.strip()
    if not execution_id:
        return "Usage: agent inspect <execution_id>\n\nUse 'agent last' to see execution IDs."

    try:
        # Try partial match
        store = get_execution_store()
        execution = store.get(execution_id)

        # If not found, try prefix match
        if not execution:
            summaries = store.get_summary(100)
            for summary in summaries:
                if summary["execution_id"].startswith(execution_id):
                    execution = store.get(summary["execution_id"])
                    break

        if not execution:
            return f"Execution not found: {execution_id}"

        return _render_execution_detail(execution)

    except Exception as e:
        logger.error(f"Error inspecting execution: {e}")
        return f"Error inspecting execution: {e}"


def _handle_stages() -> str:
    """Handle 'agent stages' command."""
    from elle.cli.agentic.stages import explain_stages

    return explain_stages()


async def _handle_hypotheses(args: str) -> str:
    """Handle 'agent hypotheses' command."""
    from elle.cli.agentic.execution import get_execution_store

    execution_id = args.strip()
    if not execution_id:
        return "Usage: agent hypotheses <execution_id>"

    try:
        store = get_execution_store()
        execution = store.get(execution_id)

        # Try prefix match
        if not execution:
            summaries = store.get_summary(100)
            for summary in summaries:
                if summary["execution_id"].startswith(execution_id):
                    execution = store.get(summary["execution_id"])
                    break

        if not execution:
            return f"Execution not found: {execution_id}"

        if not execution.hypotheses:
            return f"No hypotheses recorded for execution {execution_id[:8]}...\n\nThis may be a simple query that didn't require hypothesis generation."

        lines = [f"HYPOTHESES FOR {execution.execution_id[:8]}...", ""]

        for h in execution.hypotheses:
            if h.chosen:
                status = "[CHOSEN]"
            elif h.rejected:
                status = "[REJECTED]"
            else:
                status = "[CONSIDERED]"

            confidence = f"{h.confidence:.0%}"
            lines.append(f"{status} {h.hypothesis_id[:8]}... ({confidence})")
            lines.append(f"    {h.text}")
            lines.append(f"    Source: {h.source}")

            if h.supporting_evidence:
                lines.append("    Evidence:")
                for ev in h.supporting_evidence[:3]:
                    lines.append(f"      - {_truncate(ev, 60)}")

            if h.capability_candidates:
                lines.append(f"    Candidates: {', '.join(h.capability_candidates)}")

            if h.rejected_reason:
                lines.append(f"    Rejection: {h.rejected_reason}")

            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error getting hypotheses: {e}")
        return f"Error getting hypotheses: {e}"


async def _handle_tools(args: str) -> str:
    """Handle 'agent tools' command."""
    from elle.cli.agentic.execution import get_execution_store

    execution_id = args.strip()
    if not execution_id:
        return "Usage: agent tools <execution_id>"

    try:
        store = get_execution_store()
        execution = store.get(execution_id)

        # Try prefix match
        if not execution:
            summaries = store.get_summary(100)
            for summary in summaries:
                if summary["execution_id"].startswith(execution_id):
                    execution = store.get(summary["execution_id"])
                    break

        if not execution:
            return f"Execution not found: {execution_id}"

        if not execution.tool_calls:
            return f"No tool calls recorded for execution {execution_id[:8]}..."

        lines = [f"TOOL CALLS FOR {execution.execution_id[:8]}...", ""]

        for i, tc in enumerate(execution.tool_calls, 1):
            status = "[OK]" if tc.success else "[FAIL]"
            duration = f"{tc.duration_ms}ms" if tc.duration_ms else ""

            lines.append(f"{i}. {status} {tc.tool} {duration}")
            lines.append(f"    Reason: {tc.reason}")
            lines.append(f"    Result: {_truncate(tc.result_summary, 80)}")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error getting tool calls: {e}")
        return f"Error getting tool calls: {e}"


# =============================================================================
# Rendering
# =============================================================================


def _render_execution_detail(execution: Any) -> str:
    """Render detailed view of an execution."""
    lines = []

    # Header
    lines.append(f"EXECUTION: {execution.execution_id}")
    lines.append("=" * 60)
    lines.append("")

    # Timing
    started = execution.started_at.strftime("%Y-%m-%d %H:%M:%S")
    duration = f"{execution.total_duration_ms}ms"
    status = "SUCCESS" if execution.success else "FAILED"
    lines.append(f"Started: {started}")
    lines.append(f"Duration: {duration}")
    lines.append(f"Status: {status}")
    lines.append(f"Iterations: {execution.iterations}")
    lines.append("")

    # Input
    lines.append("INPUT:")
    lines.append(f"  {execution.user_input}")
    lines.append("")
    lines.append(f"Classified as: {execution.classified_intent} ({execution.classification_confidence:.0%})")
    lines.append("")

    # Stage trace
    if execution.stage_transitions:
        lines.append("STAGE TRACE:")
        for trans in execution.stage_transitions:
            duration_str = f" ({trans.duration_ms}ms)" if trans.duration_ms else ""
            lines.append(f"  {trans.from_stage.value} -> {trans.to_stage.value}{duration_str}")
            if trans.reason:
                lines.append(f"    {trans.reason}")
        lines.append("")

    # Hypotheses (summary)
    if execution.hypotheses:
        lines.append(f"HYPOTHESES: {len(execution.hypotheses)} considered")
        chosen = execution.chosen_hypothesis
        if chosen:
            lines.append(f"  Chosen: {chosen.text[:60]}...")
        lines.append("  Use 'agent hypotheses <id>' for details.")
        lines.append("")

    # Capabilities
    if execution.capabilities_evaluated:
        lines.append(f"CAPABILITIES EVALUATED: {len(execution.capabilities_evaluated)}")
        for cap in execution.capabilities_evaluated[:5]:
            status = "[USED]" if cap.chosen else "[SKIP]"
            lines.append(f"  {status} {cap.capability_name} (risk: {cap.risk_level})")
        if len(execution.capabilities_evaluated) > 5:
            lines.append(f"  ... and {len(execution.capabilities_evaluated) - 5} more")
        lines.append("")

    # Tool calls (summary)
    if execution.tool_calls:
        lines.append(f"TOOL CALLS: {len(execution.tool_calls)}")
        for tc in execution.tool_calls[:5]:
            status = "[OK]" if tc.success else "[FAIL]"
            lines.append(f"  {status} {tc.tool}")
        if len(execution.tool_calls) > 5:
            lines.append(f"  ... and {len(execution.tool_calls) - 5} more")
        lines.append("  Use 'agent tools <id>' for details.")
        lines.append("")

    # Incident linkage
    if execution.incident_id:
        lines.append(f"INCIDENT: {execution.incident_id}")
        lines.append("")

    # Response
    if execution.response:
        lines.append("RESPONSE:")
        response_lines = execution.response.split("\n")
        for line in response_lines[:10]:
            lines.append(f"  {line}")
        if len(response_lines) > 10:
            lines.append(f"  ... ({len(response_lines) - 10} more lines)")
        lines.append("")

    # Error
    if execution.error:
        lines.append("ERROR:")
        lines.append(f"  {execution.error}")
        lines.append("")

    # Token usage
    if execution.total_tokens:
        lines.append(f"TOKENS: {execution.total_tokens:,} total")
        if execution.prompt_tokens:
            lines.append(f"  Prompt: {execution.prompt_tokens:,}")
        if execution.completion_tokens:
            lines.append(f"  Completion: {execution.completion_tokens:,}")

    return "\n".join(lines)


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis."""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _format_time(iso_time: str) -> str:
    """Format ISO time as relative or absolute."""
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now(timezone.utc)
        delta = now - dt

        if delta.days == 0:
            if delta.seconds < 60:
                return "just now"
            if delta.seconds < 3600:
                return f"{delta.seconds // 60}m ago"
            return f"{delta.seconds // 3600}h ago"
        if delta.days == 1:
            return "yesterday"
        if delta.days < 7:
            return f"{delta.days}d ago"
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return iso_time[:10] if iso_time else "unknown"


# =============================================================================
# Synchronous wrapper
# =============================================================================


def handle_agent_command_sync(args: str) -> str:
    """Synchronous version of handle_agent_command."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(handle_agent_command(args))
