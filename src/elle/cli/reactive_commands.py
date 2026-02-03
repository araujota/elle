"""REPL commands for Reactive Functions.

Provides /react commands for managing reactive functions:
- /react create "prompt" - Create from natural language
- /react list - List all functions
- /react show <name> - Show function details
- /react enable/disable <name> - Toggle function
- /react delete <name> - Delete function
- /react history <name> - Show execution history
- /react test <name> - Dry-run function
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from elle.common.session import Session

logger = logging.getLogger(__name__)


# =============================================================================
# Colors (matching terminal renderer)
# =============================================================================


class Colors:
    """ANSI color codes for terminal output."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    BOLD_RED = "\033[1;31m"
    BOLD_GREEN = "\033[1;32m"
    BOLD_YELLOW = "\033[1;33m"


# =============================================================================
# Command Handlers
# =============================================================================


def handle_reactive_command(user_input: str, session: Session) -> tuple[str, bool]:
    """Handle /react commands.

    Args:
        user_input: The full user input string.
        session: Current session state.

    Returns:
        Tuple of (output_string, success).
    """
    # Parse the subcommand
    parts = user_input.strip().split(maxsplit=2)

    # /react alone - show help
    if len(parts) == 1:
        return _react_help(), True

    subcommand = parts[1].lower()

    # Route to handler
    HandlerType = Callable[[list[str], "Session"], tuple[str, bool]]
    handlers: dict[str, HandlerType] = {
        "help": lambda p, s: (_react_help(), True),
        "list": lambda p, s: _react_list(s),
        "ls": lambda p, s: _react_list(s),
        "show": lambda p, s: _react_show(p[2] if len(p) > 2 else None, s),
        "create": lambda p, s: _react_create(p[2] if len(p) > 2 else None, s),
        "new": lambda p, s: _react_create(p[2] if len(p) > 2 else None, s),
        "enable": lambda p, s: _react_enable(p[2] if len(p) > 2 else None, s),
        "disable": lambda p, s: _react_disable(p[2] if len(p) > 2 else None, s),
        "delete": lambda p, s: _react_delete(p[2] if len(p) > 2 else None, s),
        "rm": lambda p, s: _react_delete(p[2] if len(p) > 2 else None, s),
        "history": lambda p, s: _react_history(p[2] if len(p) > 2 else None, s),
        "test": lambda p, s: _react_test(p[2] if len(p) > 2 else None, s),
        "dry-run": lambda p, s: _react_test(p[2] if len(p) > 2 else None, s),
    }

    handler = handlers.get(subcommand)
    if handler:
        return handler(parts, session)

    return (
        f"{Colors.RED}Unknown subcommand: {subcommand}{Colors.RESET}\nUse '/react help' for available commands.",
        False,
    )


def is_reactive_command(text: str) -> bool:
    """Check if text is a reactive function command."""
    patterns = [
        r"^/react(\s|$)",
        r"^reactive(\s|$)",
    ]
    lower = text.lower().strip()
    return any(re.match(p, lower) for p in patterns)


# =============================================================================
# Subcommand Handlers
# =============================================================================


def _react_help() -> str:
    """Generate help text for /react commands."""
    return f"""{Colors.BOLD}Reactive Functions - Event-Driven Automation{Colors.RESET}

{Colors.BOLD}Commands:{Colors.RESET}
  /react list              List all reactive functions
  /react show <name>       Show function details
  /react create "<prompt>" Create function from natural language
  /react enable <name>     Enable a function
  /react disable <name>    Disable a function
  /react delete <name>     Delete a function
  /react history <name>    Show execution history
  /react test <name>       Dry-run a function

{Colors.BOLD}Examples:{Colors.RESET}
  /react create "when disk > 90% clean docker and notify me"
  /react list
  /react show docker-cleanup
  /react disable docker-cleanup
  /react history docker-cleanup

{Colors.DIM}Reactive functions trigger on system events or schedules
and execute ELLE capabilities automatically.{Colors.RESET}"""


def _react_list(session: Session) -> tuple[str, bool]:
    """List all reactive functions."""
    try:
        from elle.reactive.store import list_functions

        functions = list_functions(limit=50)

        if not functions:
            return (
                f"\n  {Colors.DIM}No reactive functions defined.{Colors.RESET}\n"
                f"\n  Use '/react create \"<prompt>\"' to create one.\n",
                True,
            )

        lines = [f"\n{Colors.BOLD}Reactive Functions{Colors.RESET}", ""]

        for func in functions:
            # Status indicator
            if func.enabled:
                status = f"{Colors.GREEN}enabled{Colors.RESET}"
            else:
                status = f"{Colors.DIM}disabled{Colors.RESET}"

            # Trigger type
            trigger_type = func.trigger.type
            if trigger_type == "event" and func.trigger.event:
                trigger_info = f"on {func.trigger.event.category or 'event'}"
            elif trigger_type == "schedule" and func.trigger.schedule:
                trigger_info = f"cron: {func.trigger.schedule.cron}"
            else:
                trigger_info = trigger_type

            lines.append(f"  {Colors.CYAN}{func.name}{Colors.RESET}  [{status}] {trigger_info}")

            # Description if present
            if func.description:
                desc = func.description[:60]
                if len(func.description) > 60:
                    desc += "..."
                lines.append(f"    {Colors.DIM}{desc}{Colors.RESET}")

        lines.append("")
        lines.append(f"{Colors.DIM}Use '/react show <name>' for details{Colors.RESET}")

        return "\n".join(lines), True

    except Exception as e:
        logger.exception("Failed to list reactive functions")
        return f"{Colors.RED}Error listing functions: {e}{Colors.RESET}", False


def _react_show(name: str | None, session: Session) -> tuple[str, bool]:
    """Show details of a reactive function."""
    if not name:
        return f"{Colors.RED}Usage: /react show <name>{Colors.RESET}", False

    try:
        from elle.reactive.store import get_function_by_name

        func = get_function_by_name(name)

        if not func:
            return f"{Colors.RED}Function not found: {name}{Colors.RESET}", False

        lines = [
            f"\n{Colors.BOLD}Reactive Function: {func.name}{Colors.RESET}",
            "",
        ]

        # Status
        if func.enabled:
            lines.append(f"Status:      {Colors.GREEN}enabled{Colors.RESET}")
        else:
            lines.append(f"Status:      {Colors.YELLOW}disabled{Colors.RESET}")

        # Description
        if func.description:
            lines.append(f"Description: {func.description}")

        # Created
        lines.append(f"Created:     {func.created_at.strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"Updated:     {func.updated_at.strftime('%Y-%m-%d %H:%M')}")

        # Trigger
        lines.append("")
        lines.append(f"{Colors.BOLD}Trigger:{Colors.RESET}")
        lines.append(f"  Type: {func.trigger.type}")

        if func.trigger.event:
            et = func.trigger.event
            if et.source:
                lines.append(f"  Source: {et.source}")
            if et.category:
                lines.append(f"  Category: {et.category}")
            if et.severity:
                lines.append(f"  Severity: {et.severity}+")
            if et.match:
                lines.append(f"  Match: {et.match}")

        if func.trigger.schedule:
            st = func.trigger.schedule
            lines.append(f"  Cron: {st.cron}")
            lines.append(f"  Timezone: {st.timezone}")

        # Condition
        if func.condition:
            lines.append("")
            lines.append(f"{Colors.BOLD}Condition:{Colors.RESET}")
            import json

            expr_str = json.dumps(func.condition.expression, indent=2)
            for line in expr_str.split("\n"):
                lines.append(f"  {line}")

        # Actions
        lines.append("")
        lines.append(f"{Colors.BOLD}Actions:{Colors.RESET}")
        for i, action in enumerate(func.actions, 1):
            lines.append(f"  {i}. {action.capability}")
            if action.input:
                import json

                input_str = json.dumps(action.input)
                if len(input_str) > 60:
                    input_str = input_str[:60] + "..."
                lines.append(f"     Input: {input_str}")
            if action.on_failure != "stop":
                lines.append(f"     On failure: {action.on_failure}")

        # Policy
        lines.append("")
        lines.append(f"{Colors.BOLD}Policy:{Colors.RESET}")
        lines.append(f"  Max frequency: {func.policy.max_frequency}")
        lines.append(f"  Max daily: {func.policy.max_daily_executions}")
        if func.policy.require_confirmation:
            lines.append("  Requires confirmation: yes")
        if func.policy.allowed_hours:
            lines.append(f"  Allowed hours: {func.policy.allowed_hours[0]}-{func.policy.allowed_hours[1]}")
        if func.policy.escalate_on_failure:
            lines.append("  Escalate on failure: yes")

        # Tags
        if func.tags:
            lines.append("")
            lines.append(f"{Colors.BOLD}Tags:{Colors.RESET} {', '.join(func.tags)}")

        # Source prompt
        if func.source_prompt:
            lines.append("")
            lines.append(f"{Colors.BOLD}Created from:{Colors.RESET}")
            lines.append(f'  "{func.source_prompt}"')

        lines.append("")

        return "\n".join(lines), True

    except Exception as e:
        logger.exception("Failed to show reactive function")
        return f"{Colors.RED}Error showing function: {e}{Colors.RESET}", False


def _react_create(prompt: str | None, session: Session) -> tuple[str, bool]:
    """Create a reactive function from natural language."""
    if not prompt:
        return (
            f'{Colors.RED}Usage: /react create "<description>"{Colors.RESET}\n'
            f"\n"
            f'Example: /react create "when disk usage > 90% clean docker images"',
            False,
        )

    # Remove quotes if present
    prompt = prompt.strip().strip("\"'")

    try:
        from elle.reactive.compiler import get_compiler
        from elle.reactive.store import create_function, get_function_by_name

        compiler = get_compiler()

        # Compile the prompt
        print(f"\n{Colors.DIM}Compiling reactive function...{Colors.RESET}")

        # Run async compile in sync context
        func = asyncio.get_event_loop().run_until_complete(compiler.compile(prompt))

        # Check if name already exists
        existing = get_function_by_name(func.name)
        if existing:
            # Generate unique name
            base_name = func.name
            counter = 2
            while get_function_by_name(f"{base_name}-{counter}"):
                counter += 1
            # Create new function with unique name
            func = func.model_copy(update={"name": f"{base_name}-{counter}"})

        # Show preview
        lines = [
            "",
            f"{Colors.BOLD}Generated Function:{Colors.RESET}",
            "",
            f"  Name: {Colors.CYAN}{func.name}{Colors.RESET}",
            f"  Description: {func.description}",
            "",
            f"  {Colors.BOLD}Trigger:{Colors.RESET} {func.trigger.type}",
        ]

        if func.trigger.event:
            et = func.trigger.event
            trigger_parts = []
            if et.category:
                trigger_parts.append(f"category={et.category}")
            if et.source:
                trigger_parts.append(f"source={et.source}")
            if trigger_parts:
                lines.append(f"    {', '.join(trigger_parts)}")

        if func.condition:
            import json

            lines.append(f"  {Colors.BOLD}Condition:{Colors.RESET}")
            lines.append(f"    {json.dumps(func.condition.expression)}")

        lines.append(f"  {Colors.BOLD}Actions:{Colors.RESET}")
        for action in func.actions:
            lines.append(f"    - {action.capability}")

        lines.append("")

        # Get suggestions
        suggestions = compiler.suggest_improvements(func)
        if suggestions:
            lines.append(f"{Colors.YELLOW}Suggestions:{Colors.RESET}")
            for s in suggestions:
                lines.append(f"  - {s}")
            lines.append("")

        print("\n".join(lines))

        # Prompt for confirmation
        try:
            response = input(f"{Colors.BOLD}Save this function? [y/N/e(dit)]: {Colors.RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return f"\n{Colors.DIM}Cancelled.{Colors.RESET}", False

        if response in ("y", "yes"):
            # Save the function
            create_function(func)

            # Record to incident vault for provenance
            try:
                from elle.cli.agentic.incident_recorder import record_arm_action

                record_arm_action(
                    arm_name="reactive_functions",
                    action="create_function",
                    target=func.name,
                    success=True,
                    details={
                        "function_name": func.name,
                        "description": func.description,
                        "trigger_type": func.trigger.type,
                        "action_count": len(func.actions),
                        "capabilities": [a.capability for a in func.actions],
                    },
                )
            except Exception as e:
                logger.debug(f"Failed to record function creation: {e}")

            return (
                f"\n{Colors.GREEN}Created reactive function: {func.name}{Colors.RESET}\n"
                f"\n{Colors.DIM}Use '/react show {func.name}' to view details{Colors.RESET}",
                True,
            )
        elif response in ("e", "edit"):
            # Save the function, then inform user they can recreate it if needed
            create_function(func)

            # Record to incident vault for provenance
            try:
                from elle.cli.agentic.incident_recorder import record_arm_action

                record_arm_action(
                    arm_name="reactive_functions",
                    action="create_function",
                    target=func.name,
                    success=True,
                    details={
                        "function_name": func.name,
                        "description": func.description,
                        "trigger_type": func.trigger.type,
                        "action_count": len(func.actions),
                    },
                )
            except Exception as e:
                logger.debug(f"Failed to record function creation: {e}")

            return (
                f"\n{Colors.GREEN}Created reactive function: {func.name}{Colors.RESET}\n"
                f"\n{Colors.DIM}To modify, delete with '/react delete {func.name}' "
                f"and recreate.{Colors.RESET}",
                True,
            )
        else:
            return f"\n{Colors.DIM}Cancelled.{Colors.RESET}", False

    except Exception as e:
        logger.exception("Failed to create reactive function")
        return f"{Colors.RED}Error creating function: {e}{Colors.RESET}", False


def _react_enable(name: str | None, session: Session) -> tuple[str, bool]:
    """Enable a reactive function."""
    if not name:
        return f"{Colors.RED}Usage: /react enable <name>{Colors.RESET}", False

    try:
        from elle.reactive.store import get_function_by_name, update_function

        func = get_function_by_name(name)
        if not func:
            return f"{Colors.RED}Function not found: {name}{Colors.RESET}", False

        if func.enabled:
            return f"{Colors.YELLOW}Function already enabled: {name}{Colors.RESET}", True

        update_function(func.id, enabled=True)

        try:
            from elle.cli.agentic.incident_recorder import record_arm_action

            record_arm_action(
                arm_name="reactive_functions",
                action="enable_function",
                target=name,
                success=True,
                details={"function_id": func.id},
            )
        except Exception:
            logger.debug("Failed to record enable action", exc_info=True)

        return f"{Colors.GREEN}Enabled reactive function: {name}{Colors.RESET}", True

    except Exception as e:
        logger.exception("Failed to enable reactive function")
        return f"{Colors.RED}Error enabling function: {e}{Colors.RESET}", False


def _react_disable(name: str | None, session: Session) -> tuple[str, bool]:
    """Disable a reactive function."""
    if not name:
        return f"{Colors.RED}Usage: /react disable <name>{Colors.RESET}", False

    try:
        from elle.reactive.store import get_function_by_name, update_function

        func = get_function_by_name(name)
        if not func:
            return f"{Colors.RED}Function not found: {name}{Colors.RESET}", False

        if not func.enabled:
            return f"{Colors.YELLOW}Function already disabled: {name}{Colors.RESET}", True

        update_function(func.id, enabled=False)

        try:
            from elle.cli.agentic.incident_recorder import record_arm_action

            record_arm_action(
                arm_name="reactive_functions",
                action="disable_function",
                target=name,
                success=True,
                details={"function_id": func.id},
            )
        except Exception:
            logger.debug("Failed to record disable action", exc_info=True)

        return f"{Colors.YELLOW}Disabled reactive function: {name}{Colors.RESET}", True

    except Exception as e:
        logger.exception("Failed to disable reactive function")
        return f"{Colors.RED}Error disabling function: {e}{Colors.RESET}", False


def _react_delete(name: str | None, session: Session) -> tuple[str, bool]:
    """Delete a reactive function."""
    if not name:
        return f"{Colors.RED}Usage: /react delete <name>{Colors.RESET}", False

    try:
        from elle.reactive.store import delete_function, get_function_by_name

        func = get_function_by_name(name)
        if not func:
            return f"{Colors.RED}Function not found: {name}{Colors.RESET}", False

        # Confirm deletion
        try:
            response = input(f"{Colors.YELLOW}Delete reactive function '{name}'? [y/N]: {Colors.RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return f"{Colors.DIM}Cancelled.{Colors.RESET}", False

        if response not in ("y", "yes"):
            return f"{Colors.DIM}Cancelled.{Colors.RESET}", False

        delete_function(func.id)

        # Record to incident vault for audit
        try:
            from elle.cli.agentic.incident_recorder import record_arm_action

            record_arm_action(
                arm_name="reactive_functions",
                action="delete_function",
                target=name,
                success=True,
                details={
                    "function_id": func.id,
                    "function_name": name,
                    "description": func.description,
                },
            )
        except Exception as e:
            logger.debug(f"Failed to record function deletion: {e}")

        return f"{Colors.GREEN}Deleted reactive function: {name}{Colors.RESET}", True

    except Exception as e:
        logger.exception("Failed to delete reactive function")
        return f"{Colors.RED}Error deleting function: {e}{Colors.RESET}", False


def _react_history(name: str | None, session: Session) -> tuple[str, bool]:
    """Show execution history for a reactive function."""
    if not name:
        return f"{Colors.RED}Usage: /react history <name>{Colors.RESET}", False

    try:
        from elle.reactive.store import get_execution_history, get_function_by_name

        func = get_function_by_name(name)
        if not func:
            return f"{Colors.RED}Function not found: {name}{Colors.RESET}", False

        history = get_execution_history(func.id, limit=20)

        if not history:
            return (
                f"\n  {Colors.DIM}No executions recorded for: {name}{Colors.RESET}\n",
                True,
            )

        lines = [
            f"\n{Colors.BOLD}Execution History: {name}{Colors.RESET}",
            "",
        ]

        for record in history:
            # Timestamp
            ts = record.triggered_at.strftime("%Y-%m-%d %H:%M:%S")

            # Status
            if record.success:
                status = f"{Colors.GREEN}success{Colors.RESET}"
            else:
                status = f"{Colors.RED}failed{Colors.RESET}"

            lines.append(f"  {ts}  [{status}]  {record.execution_time_ms}ms")

            # Condition result
            if not record.condition_result:
                explanation = record.condition_explanation
                lines.append(f"    {Colors.DIM}Condition not met: {explanation}{Colors.RESET}")

            # Actions
            if record.actions_executed:
                lines.append(f"    Actions: {', '.join(record.actions_executed)}")

            # Error
            if record.error:
                lines.append(f"    {Colors.RED}Error: {record.error[:60]}{Colors.RESET}")

            lines.append("")

        return "\n".join(lines), True

    except Exception as e:
        logger.exception("Failed to get execution history")
        return f"{Colors.RED}Error getting history: {e}{Colors.RESET}", False


def _react_test(name: str | None, session: Session) -> tuple[str, bool]:
    """Dry-run a reactive function."""
    if not name:
        return f"{Colors.RED}Usage: /react test <name>{Colors.RESET}", False

    try:
        from elle.reactive.engine import get_engine
        from elle.reactive.store import get_function_by_name

        func = get_function_by_name(name)
        if not func:
            return f"{Colors.RED}Function not found: {name}{Colors.RESET}", False

        engine = get_engine()

        # Run dry-run
        result, explanation, context = asyncio.get_event_loop().run_until_complete(engine.dry_run(func))

        lines = [
            f"\n{Colors.BOLD}Dry-run: {name}{Colors.RESET}",
            "",
        ]

        # Condition result
        if result:
            lines.append(f"Condition: {Colors.GREEN}PASS{Colors.RESET}")
        else:
            lines.append(f"Condition: {Colors.YELLOW}FAIL{Colors.RESET}")
        lines.append(f"  {explanation}")

        # Context
        lines.append("")
        lines.append(f"{Colors.BOLD}Context:{Colors.RESET}")

        if context.get("event"):
            lines.append("  Event: present")
        else:
            lines.append(f"  Event: {Colors.DIM}(none - would be provided by trigger){Colors.RESET}")

        if context.get("state"):
            import json

            lines.append(f"  State: {json.dumps(context['state'])}")

        if context.get("system"):
            lines.append(f"  System: {context['system'].get('timestamp', 'available')}")

        # Actions that would execute
        if result:
            lines.append("")
            lines.append(f"{Colors.BOLD}Would execute:{Colors.RESET}")
            for action in func.actions:
                lines.append(f"  - {action.capability}")
        else:
            lines.append("")
            lines.append(f"{Colors.DIM}No actions would execute (condition not met){Colors.RESET}")

        lines.append("")

        return "\n".join(lines), True

    except Exception as e:
        logger.exception("Failed to test reactive function")
        return f"{Colors.RED}Error testing function: {e}{Colors.RESET}", False
