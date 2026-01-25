"""Generate human-readable explanations from syscall summaries.

Transforms SyscallSummary data into natural language explanations
that describe what a command actually did.
"""

import logging

from elle.daemon.telemetry.ebpf.syscall_models import (
    NetworkConnection,
    SyscallSummary,
    SyscallTrace,
)

logger = logging.getLogger(__name__)


# Maximum items to show in each category before truncating
MAX_DISPLAY_ITEMS = 10


def _format_path_list(
    paths: tuple[str, ...],
    max_items: int = MAX_DISPLAY_ITEMS,
) -> list[str]:
    """Format a list of paths for display.

    Args:
        paths: Tuple of paths to format.
        max_items: Maximum items to show.

    Returns:
        List of formatted path strings.
    """
    lines = []
    for path in paths[:max_items]:
        lines.append(f"  {path}")

    if len(paths) > max_items:
        remaining = len(paths) - max_items
        lines.append(f"  ... and {remaining} more")

    return lines


def _format_connections(
    connections: tuple[NetworkConnection, ...],
    max_items: int = MAX_DISPLAY_ITEMS,
) -> list[str]:
    """Format network connections for display.

    Args:
        connections: Tuple of connections to format.
        max_items: Maximum items to show.

    Returns:
        List of formatted connection strings.
    """
    lines = []
    for conn in connections[:max_items]:
        lines.append(f"  {conn.addr}:{conn.port} ({conn.family})")

    if len(connections) > max_items:
        remaining = len(connections) - max_items
        lines.append(f"  ... and {remaining} more")

    return lines


def _format_rename_list(
    renames: tuple[tuple[str, str], ...],
    max_items: int = MAX_DISPLAY_ITEMS,
) -> list[str]:
    """Format rename operations for display.

    Args:
        renames: Tuple of (old_path, new_path) tuples.
        max_items: Maximum items to show.

    Returns:
        List of formatted rename strings.
    """
    lines = []
    for old_path, new_path in renames[:max_items]:
        lines.append(f"  {old_path} -> {new_path}")

    if len(renames) > max_items:
        remaining = len(renames) - max_items
        lines.append(f"  ... and {remaining} more")

    return lines


def explain_summary(summary: SyscallSummary) -> str:
    """Generate a human-readable explanation from a syscall summary.

    Args:
        summary: The syscall summary to explain.

    Returns:
        Human-readable explanation string.
    """
    lines = []

    # Header
    lines.append(f"Command: {summary.command}")
    lines.append(f"Duration: {summary.duration_ms}ms, {summary.total_syscalls} syscalls")

    if summary.events_truncated:
        lines.append("(Note: Some events were truncated due to volume)")

    lines.append("")

    # File operations
    has_file_ops = (
        summary.files_read
        or summary.files_written
        or summary.files_created
        or summary.files_deleted
        or summary.files_renamed
    )

    if has_file_ops:
        lines.append("File Operations:")

        if summary.files_created:
            lines.append(f"  Created {len(summary.files_created)} file(s):")
            lines.extend(_format_path_list(summary.files_created))

        if summary.files_written:
            lines.append(f"  Wrote to {len(summary.files_written)} file(s):")
            lines.extend(_format_path_list(summary.files_written))

        if summary.files_deleted:
            lines.append(f"  Deleted {len(summary.files_deleted)} file(s):")
            lines.extend(_format_path_list(summary.files_deleted))

        if summary.files_renamed:
            lines.append(f"  Renamed {len(summary.files_renamed)} file(s):")
            lines.extend(_format_rename_list(summary.files_renamed))

        if summary.files_read:
            lines.append(f"  Read {len(summary.files_read)} file(s):")
            lines.extend(_format_path_list(summary.files_read))

        lines.append("")

    # Network operations
    if summary.connections_made:
        lines.append("Network Operations:")
        lines.append(f"  Made {len(summary.connections_made)} connection(s):")
        lines.extend(_format_connections(summary.connections_made))
        lines.append("")

    # Process operations
    has_process_ops = summary.programs_executed or summary.processes_spawned > 0

    if has_process_ops:
        lines.append("Process Operations:")

        if summary.programs_executed:
            lines.append(f"  Executed {len(summary.programs_executed)} program(s):")
            lines.extend(_format_path_list(summary.programs_executed))

        if summary.processes_spawned > 0:
            lines.append(f"  Spawned {summary.processes_spawned} child process(es)")

        lines.append("")

    # Summary if nothing notable happened
    if not has_file_ops and not summary.connections_made and not has_process_ops:
        lines.append("No notable file, network, or process operations detected.")
        lines.append("(The command may have only performed internal computations)")

    return "\n".join(lines)


def explain_trace(trace: SyscallTrace) -> str:
    """Generate a human-readable explanation from a syscall trace.

    Args:
        trace: The syscall trace to explain.

    Returns:
        Human-readable explanation string.
    """
    if not trace.enabled:
        return "Syscall tracing was not enabled for this command."

    if trace.error:
        return f"Syscall tracing failed: {trace.error}"

    if trace.summary is None:
        return "No syscall data was captured."

    # If we already have an explanation, use it
    if trace.explanation:
        return trace.explanation

    return explain_summary(trace.summary)


def create_brief_summary(summary: SyscallSummary) -> str:
    """Create a brief one-line summary of syscall activity.

    Args:
        summary: The syscall summary.

    Returns:
        Brief summary string (e.g., "Created 1 file, read 5 files").
    """
    parts = []

    if summary.files_created:
        count = len(summary.files_created)
        parts.append(f"created {count} file{'s' if count > 1 else ''}")

    if summary.files_written:
        count = len(summary.files_written)
        parts.append(f"wrote {count} file{'s' if count > 1 else ''}")

    if summary.files_deleted:
        count = len(summary.files_deleted)
        parts.append(f"deleted {count} file{'s' if count > 1 else ''}")

    if summary.files_renamed:
        count = len(summary.files_renamed)
        parts.append(f"renamed {count} file{'s' if count > 1 else ''}")

    if summary.files_read:
        count = len(summary.files_read)
        parts.append(f"read {count} file{'s' if count > 1 else ''}")

    if summary.connections_made:
        count = len(summary.connections_made)
        parts.append(f"made {count} connection{'s' if count > 1 else ''}")

    if summary.programs_executed:
        count = len(summary.programs_executed)
        parts.append(f"executed {count} program{'s' if count > 1 else ''}")

    if summary.processes_spawned > 0:
        count = summary.processes_spawned
        parts.append(f"spawned {count} process{'es' if count > 1 else ''}")

    if not parts:
        return f"{summary.total_syscalls} syscalls (no notable I/O)"

    return ", ".join(parts)


def create_trace_with_explanation(summary: SyscallSummary) -> SyscallTrace:
    """Create a SyscallTrace with explanation already populated.

    Args:
        summary: The syscall summary.

    Returns:
        SyscallTrace with explanation filled in.
    """
    explanation = explain_summary(summary)

    return SyscallTrace(
        enabled=True,
        summary=summary,
        error=None,
        explanation=explanation,
    )


def format_trace_for_display(trace: SyscallTrace, use_colors: bool = True) -> str:
    """Format a syscall trace for terminal display.

    Args:
        trace: The trace to format.
        use_colors: Whether to use ANSI colors.

    Returns:
        Formatted string for display.
    """
    if use_colors:
        # Import colors from renderer
        try:
            from elle.cli.terminal.renderer import Colors
        except ImportError:
            use_colors = False

    lines = []

    if not trace.enabled:
        msg = "Syscall tracing was not enabled."
        if use_colors:
            lines.append(f"{Colors.DIM}{msg}{Colors.RESET}")
        else:
            lines.append(msg)
        return "\n".join(lines)

    if trace.error:
        msg = f"Tracing error: {trace.error}"
        if use_colors:
            lines.append(f"{Colors.RED}{msg}{Colors.RESET}")
        else:
            lines.append(msg)
        return "\n".join(lines)

    if trace.summary is None:
        msg = "No trace data captured."
        if use_colors:
            lines.append(f"{Colors.DIM}{msg}{Colors.RESET}")
        else:
            lines.append(msg)
        return "\n".join(lines)

    summary = trace.summary

    # Header
    if use_colors:
        lines.append(f"{Colors.BOLD}Syscall Trace: {summary.command}{Colors.RESET}")
    else:
        lines.append(f"Syscall Trace: {summary.command}")

    lines.append(f"Duration: {summary.duration_ms}ms | Syscalls: {summary.total_syscalls}")

    if summary.events_truncated:
        if use_colors:
            lines.append(f"{Colors.YELLOW}(Truncated - high syscall volume){Colors.RESET}")
        else:
            lines.append("(Truncated - high syscall volume)")

    lines.append("")

    # Use the explanation
    if trace.explanation:
        lines.append(trace.explanation)
    else:
        lines.append(explain_summary(summary))

    return "\n".join(lines)
