"""CLI commands for reboot persistence and recovery.

Provides navigation commands for managing reboot operations:
- reboot status: Show pending/recent reboot intents
- reboot cancel: Cancel pending reboot or rollback
- reboot confirm: Manually confirm boot success
- reboot rollback: Force rollback to previous config
- reboot history: View full reboot history
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

from elle.cli.terminal.renderer import Colors
from elle.common.session import Session

logger = logging.getLogger(__name__)


def handle_reboot_command(
    user_input: str,
    session: Session,
) -> tuple[str, bool]:
    """Handle reboot navigation commands.

    Args:
        user_input: The reboot command.
        session: Current session state.

    Returns:
        Tuple of (output_string, success).
    """
    parts = user_input.strip().split()

    # Parse subcommand
    subcommand = "status" if len(parts) < 2 else parts[1].lower()

    match subcommand:
        case "status":
            return render_reboot_status(), True
        case "history":
            limit = 20
            if len(parts) > 2 and parts[2].isdigit():
                limit = int(parts[2])
            return render_reboot_history(limit), True
        case "cancel":
            return handle_cancel(), True
        case "confirm":
            return handle_confirm(), True
        case "rollback":
            return handle_rollback(), True
        case _:
            # Check if it's an intent ID
            if _looks_like_uuid(subcommand):
                return render_intent_detail(subcommand), True
            return _reboot_help(), True


def _looks_like_uuid(text: str) -> bool:
    """Check if text looks like a UUID (full or partial)."""
    uuid_pattern = r"^[a-f0-9-]{8,36}$"
    return bool(re.match(uuid_pattern, text.lower()))


def _reboot_help() -> str:
    """Generate reboot command help text."""
    return f"""{Colors.BOLD}Reboot Recovery Commands{Colors.RESET}

{Colors.BOLD}Commands:{Colors.RESET}
  reboot status      Show current reboot status
  reboot history     View reboot history
  reboot cancel      Cancel pending reboot/rollback
  reboot confirm     Manually confirm boot success
  reboot rollback    Force rollback to previous config
  reboot <id>        Show details for a specific intent

{Colors.BOLD}Safety Features:{Colors.RESET}
  - GRUB one-shot boot for automatic rollback on failure
  - Maximum 3 verification retries
  - 60-second intervention window before auto-rollback
  - All state persisted to SQLite

{Colors.DIM}Reboots are managed through 'reboot intents' that persist
across boots, enabling verification and automatic recovery.{Colors.RESET}"""


def render_reboot_status() -> str:
    """Render current reboot status.

    Returns:
        Formatted status string.
    """
    try:
        from elle.daemon.reboot import (
            get_active_intent,
            get_reboot_status,
        )

        status = get_reboot_status()
        active = get_active_intent()

        lines = [f"{Colors.BOLD}Reboot Status{Colors.RESET}", ""]

        # Active intent (if any)
        if active:
            status_color = _status_color(active.status)
            lines.extend(
                [
                    f"{Colors.YELLOW}Active Reboot Intent:{Colors.RESET}",
                    f"  ID:      {active.id[:12]}...",
                    f"  Goal:    {active.goal}",
                    f"  Status:  {status_color}{active.status.upper()}{Colors.RESET}",
                    f"  Reason:  {active.reason}",
                    f"  Started: {_format_datetime(active.created_at)}",
                    "",
                ]
            )

            if active.status == "verifying":
                lines.extend(
                    [
                        f"  {Colors.CYAN}Verification in progress...{Colors.RESET}",
                        f"  Attempts: {active.verification_attempts}/3",
                        "",
                    ]
                )
            elif active.status == "failed":
                lines.extend(
                    [
                        f"  {Colors.RED}Verification failed!{Colors.RESET}",
                        f"  {Colors.YELLOW}Auto-rollback pending - use 'reboot cancel' to intervene{Colors.RESET}",
                        "",
                    ]
                )
        else:
            lines.append(f"{Colors.GREEN}No active reboot operations.{Colors.RESET}")
            lines.append("")

        # Summary counts
        lines.extend(
            [
                f"{Colors.BOLD}Summary:{Colors.RESET}",
                f"  Total intents: {status.total_intents}",
                f"  Completed:     {status.completed_count}",
                f"  Failed:        {status.failed_count}",
                f"  Rolled back:   {status.rolled_back_count}",
                "",
            ]
        )

        # Recent intents
        if status.recent_intents:
            lines.append(f"{Colors.BOLD}Recent (last 7 days):{Colors.RESET}")
            for intent in status.recent_intents[:5]:
                status_color = _status_color(intent.status)
                outcome_str = _outcome_str(intent.outcome)
                lines.append(
                    f"  [{status_color}{intent.status.upper():11}{Colors.RESET}] "
                    f"{intent.goal[:40]}{'...' if len(intent.goal) > 40 else ''} "
                    f"{Colors.DIM}{outcome_str}{Colors.RESET}"
                )

        return "\n".join(lines)

    except Exception as e:
        logger.exception("Failed to get reboot status")
        return f"{Colors.RED}Error getting reboot status: {e}{Colors.RESET}"


def render_reboot_history(limit: int = 20) -> str:
    """Render reboot history.

    Args:
        limit: Maximum number of intents to show.

    Returns:
        Formatted history string.
    """
    try:
        from elle.daemon.reboot import list_intents

        intents = list_intents(limit=limit)

        if not intents:
            return f"\n  {Colors.DIM}No reboot history.{Colors.RESET}\n"

        lines = [f"{Colors.BOLD}Reboot History{Colors.RESET}", ""]

        for intent in intents:
            status_color = _status_color(intent.status)
            outcome_str = _outcome_str(intent.outcome)

            lines.append(f"  {Colors.CYAN}{intent.id[:12]}{Colors.RESET} {_format_datetime(intent.created_at)}")
            lines.append(f"    [{status_color}{intent.status.upper()}{Colors.RESET}] {intent.goal}")
            lines.append(
                f"    Reason: {intent.reason} | Outcome: {outcome_str} | Attempts: {intent.verification_attempts}"
            )
            if intent.outcome_detail:
                detail = intent.outcome_detail[:60]
                if len(intent.outcome_detail) > 60:
                    detail += "..."
                lines.append(f"    {Colors.DIM}{detail}{Colors.RESET}")
            lines.append("")

        lines.append(f"{Colors.DIM}Use 'reboot <id>' for full details{Colors.RESET}")

        return "\n".join(lines)

    except Exception as e:
        logger.exception("Failed to get reboot history")
        return f"{Colors.RED}Error getting reboot history: {e}{Colors.RESET}"


def render_intent_detail(intent_id: str) -> str:
    """Render detailed view of a reboot intent.

    Args:
        intent_id: Full or partial intent ID.

    Returns:
        Formatted detail string.
    """
    try:
        from elle.daemon.reboot import get_intent, list_intents

        # Try exact match first
        intent = get_intent(intent_id)

        # Try partial match
        if intent is None:
            all_intents = list_intents(limit=100)
            matches = [i for i in all_intents if i.id.startswith(intent_id)]
            if len(matches) == 1:
                intent = matches[0]
            elif len(matches) > 1:
                return f"{Colors.YELLOW}Multiple intents match '{intent_id}':{Colors.RESET}\n" + "\n".join(
                    f"  - {i.id[:12]} {i.goal}" for i in matches[:5]
                )

        if intent is None:
            return f"{Colors.RED}Intent not found: {intent_id}{Colors.RESET}"

        status_color = _status_color(intent.status)
        outcome_str = _outcome_str(intent.outcome)

        lines = [
            f"{Colors.BOLD}Reboot Intent Detail{Colors.RESET}",
            "",
            f"  ID:          {intent.id}",
            f"  Goal:        {intent.goal}",
            f"  Status:      {status_color}{intent.status.upper()}{Colors.RESET}",
            f"  Outcome:     {outcome_str}",
            f"  Reason:      {intent.reason}",
            "",
            f"  Created:     {_format_datetime(intent.created_at)}",
            f"  Updated:     {_format_datetime(intent.updated_at)}",
            "",
        ]

        # Task description
        if intent.task_description:
            lines.append(f"{Colors.BOLD}Task Description:{Colors.RESET}")
            lines.append(f"  {intent.task_description}")
            lines.append("")

        # Reason detail
        if intent.reason_detail:
            lines.append(f"{Colors.BOLD}Reason Detail:{Colors.RESET}")
            lines.append(f"  {intent.reason_detail}")
            lines.append("")

        # Boot IDs
        if intent.boot_id or intent.new_boot_id:
            lines.append(f"{Colors.BOLD}Boot IDs:{Colors.RESET}")
            if intent.boot_id:
                lines.append(f"  Before: {intent.boot_id[:12]}...")
            if intent.new_boot_id:
                lines.append(f"  After:  {intent.new_boot_id[:12]}...")
            lines.append("")

        # GRUB config
        if intent.grub_entry:
            lines.append(f"{Colors.BOLD}GRUB Configuration:{Colors.RESET}")
            lines.append(f"  Entry:    {intent.grub_entry}")
            if intent.grub_default_saved:
                lines.append(f"  Default:  {intent.grub_default_saved}")
            lines.append("")

        # Verification
        lines.append(f"{Colors.BOLD}Verification:{Colors.RESET}")
        lines.append(f"  Attempts: {intent.verification_attempts}/3")
        if intent.last_verification_at:
            lines.append(f"  Last run: {_format_datetime(intent.last_verification_at)}")

        if intent.verifications:
            lines.append("")
            lines.append(f"  {Colors.BOLD}Checks:{Colors.RESET}")
            for v in intent.verifications:
                if v.passed is None:
                    check_status = f"{Colors.DIM}pending{Colors.RESET}"
                elif v.passed:
                    check_status = f"{Colors.GREEN}PASS{Colors.RESET}"
                else:
                    check_status = f"{Colors.RED}FAIL{Colors.RESET}"

                req_str = "*" if v.required else ""
                lines.append(f"    [{check_status}] {v.check_type}: {v.check_command}{req_str}")
        lines.append("")

        # Outcome detail
        if intent.outcome_detail:
            lines.append(f"{Colors.BOLD}Outcome Detail:{Colors.RESET}")
            lines.append(f"  {intent.outcome_detail}")
            lines.append("")

        # Incident link
        if intent.incident_id:
            lines.append(f"{Colors.DIM}Linked incident: {intent.incident_id[:12]}{Colors.RESET}")

        return "\n".join(lines)

    except Exception as e:
        logger.exception("Failed to get intent detail")
        return f"{Colors.RED}Error loading intent: {e}{Colors.RESET}"


def _record_reboot_action(
    action: str,
    target: str,
    success: bool,
    details: dict[str, Any] | None = None,
) -> None:
    """Record a reboot management action to the incident vault."""
    try:
        from elle.cli.agentic.incident_recorder import record_arm_action

        record_arm_action(
            arm_name="reboot_management",
            action=action,
            target=target,
            success=success,
            details=details,
        )
    except Exception:
        logger.debug("Failed to record reboot action", exc_info=True)


def handle_cancel() -> str:
    """Handle cancel command.

    Cancels pending reboot or pending rollback.

    Returns:
        Result message.
    """
    try:
        from elle.daemon.reboot import get_active_intent, get_manager

        active = get_active_intent()
        if not active:
            return f"{Colors.YELLOW}No active reboot to cancel.{Colors.RESET}"

        manager = get_manager()

        # Try to cancel pending rollback first
        if active.status == "failed":
            cancelled = asyncio.run(manager.cancel_pending_rollback())
            if cancelled:
                return (
                    f"{Colors.GREEN}Pending rollback cancelled.{Colors.RESET}\n"
                    f"{Colors.DIM}The system will not automatically revert. "
                    f"Use 'reboot confirm' to mark as successful or "
                    f"'reboot rollback' to manually revert.{Colors.RESET}"
                )

        # Try to cancel pending reboot
        if active.status == "pending":
            result = asyncio.run(manager.cancel_pending_reboot(active.id))
            if result:
                _record_reboot_action("cancel_reboot", active.id, True, {"goal": active.goal})
                return f"{Colors.GREEN}Pending reboot cancelled: {active.goal}{Colors.RESET}"

        return (
            f"{Colors.YELLOW}Cannot cancel reboot in status: {active.status}{Colors.RESET}\n"
            f"{Colors.DIM}Only 'pending' reboots or 'failed' (pending rollback) "
            f"can be cancelled.{Colors.RESET}"
        )

    except Exception as e:
        logger.exception("Failed to cancel reboot")
        return f"{Colors.RED}Error cancelling reboot: {e}{Colors.RESET}"


def handle_confirm() -> str:
    """Handle confirm command.

    Manually confirms boot success despite verification failure.

    Returns:
        Result message.
    """
    try:
        from elle.daemon.reboot import get_active_intent, get_manager

        active = get_active_intent()
        if not active:
            return f"{Colors.YELLOW}No active reboot to confirm.{Colors.RESET}"

        if active.status not in ("failed", "verifying"):
            return (
                f"{Colors.YELLOW}Cannot confirm reboot in status: {active.status}{Colors.RESET}\n"
                f"{Colors.DIM}Use 'reboot confirm' after verification fails "
                f"if you know the system is working.{Colors.RESET}"
            )

        manager = get_manager()
        result = asyncio.run(manager.force_confirm_boot(active.id))

        if result and result.status == "completed":
            _record_reboot_action("confirm_boot", active.id, True, {"goal": active.goal})
            return (
                f"{Colors.GREEN}Boot confirmed successfully!{Colors.RESET}\n"
                f"{Colors.DIM}Reboot marked as completed: {active.goal}{Colors.RESET}"
            )
        else:
            _record_reboot_action("confirm_boot", active.id, False)
            return f"{Colors.RED}Failed to confirm boot.{Colors.RESET}"

    except Exception as e:
        logger.exception("Failed to confirm boot")
        return f"{Colors.RED}Error confirming boot: {e}{Colors.RESET}"


def handle_rollback() -> str:
    """Handle rollback command.

    Forces immediate rollback to previous configuration.

    Returns:
        Result message.
    """
    try:
        from elle.daemon.reboot import get_active_intent

        active = get_active_intent()
        if not active:
            return (
                f"{Colors.YELLOW}No active reboot to rollback.{Colors.RESET}\n"
                f"{Colors.DIM}Rollback is only available after a reboot operation.{Colors.RESET}"
            )

        # Confirm with user
        _record_reboot_action("rollback_initiated", active.id, True, {"goal": active.goal})
        lines = [
            f"{Colors.BOLD_YELLOW}WARNING: This will reboot the system!{Colors.RESET}",
            "",
            f"Rollback target: {active.grub_default_saved or '(default)'}",
            "",
            f"{Colors.DIM}The system will reboot to the previous configuration.{Colors.RESET}",
            "",
            f"Run this command again to confirm: {Colors.BOLD}reboot rollback --confirm{Colors.RESET}",
        ]

        return "\n".join(lines)

    except Exception as e:
        logger.exception("Failed to initiate rollback")
        return f"{Colors.RED}Error initiating rollback: {e}{Colors.RESET}"


def render_recent_reboots_summary() -> str | None:
    """Render a brief summary of recent reboot operations for REPL startup.

    Returns:
        Summary string if there are recent operations, None otherwise.
    """
    try:
        from elle.daemon.reboot import get_active_intent, list_recent_intents

        active = get_active_intent()
        recent = list_recent_intents(days=7, limit=3)

        if not active and not recent:
            return None

        lines = []

        # Active reboot warning
        if active:
            status_color = _status_color(active.status)
            lines.extend(
                [
                    f"{Colors.YELLOW}Active reboot operation:{Colors.RESET}",
                    f"  [{status_color}{active.status.upper()}{Colors.RESET}] {active.goal}",
                ]
            )
            if active.status == "failed":
                lines.append(f"  {Colors.RED}Verification failed - use 'reboot status' for details{Colors.RESET}")
            lines.append("")

        # Recent completed/failed
        notable = [
            i for i in recent if i.outcome in ("failed", "rolled_back") and i.id != (active.id if active else "")
        ]
        if notable:
            lines.append(f"{Colors.DIM}Recent reboot issues:{Colors.RESET}")
            for intent in notable[:2]:
                outcome_str = _outcome_str(intent.outcome)
                lines.append(f"  {outcome_str} {intent.goal[:40]} ({_format_datetime(intent.created_at)})")
            lines.append(f"{Colors.DIM}Use 'reboot history' for details.{Colors.RESET}")
            lines.append("")

        return "\n".join(lines) if lines else None

    except Exception:
        return None


# =============================================================================
# Helper Functions
# =============================================================================


def _status_color(status: str) -> str:
    """Get color code for status."""
    colors = {
        "pending": Colors.YELLOW,
        "rebooting": Colors.CYAN,
        "verifying": Colors.CYAN,
        "completed": Colors.GREEN,
        "failed": Colors.RED,
        "rolled_back": Colors.RED,
        "cancelled": Colors.DIM,
    }
    return colors.get(status, Colors.RESET)


def _outcome_str(outcome: str) -> str:
    """Get formatted outcome string with color."""
    outcomes = {
        "improved": f"{Colors.GREEN}improved{Colors.RESET}",
        "failed": f"{Colors.RED}failed{Colors.RESET}",
        "rolled_back": f"{Colors.RED}rolled_back{Colors.RESET}",
        "unknown": f"{Colors.DIM}unknown{Colors.RESET}",
    }
    return outcomes.get(outcome, outcome)


def _format_datetime(dt: datetime | None) -> str:
    """Format datetime for display."""
    if dt is None:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M")
