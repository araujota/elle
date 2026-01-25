"""Syscall event aggregation into human-readable summaries.

Collects syscall events from tracing and aggregates them into
a SyscallSummary that can be used for command explanation.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

from elle.daemon.telemetry.ebpf.syscall_models import (
    MAX_FILES_PER_CATEGORY,
    MAX_TRACED_EVENTS,
    NetworkConnection,
    SyscallEvent,
    SyscallSummary,
)

logger = logging.getLogger(__name__)


# Paths to exclude from summaries (noise reduction)
EXCLUDED_PATH_PREFIXES = (
    "/proc/",
    "/sys/",
    "/dev/null",
    "/dev/urandom",
    "/dev/random",
    "/usr/lib/locale/",
    "/usr/share/locale/",
    "/etc/ld.so.cache",
    "/lib/x86_64-linux-gnu/",
    "/lib64/ld-linux",
    "/usr/lib/x86_64-linux-gnu/",
)

# Paths that are always excluded (internal files)
EXCLUDED_PATHS = frozenset({
    "/dev/null",
    "/dev/zero",
    "/dev/urandom",
    "/dev/random",
})


def _should_include_path(path: str) -> bool:
    """Check if a path should be included in the summary.

    Args:
        path: File path to check.

    Returns:
        True if path should be included.
    """
    if not path:
        return False

    if path in EXCLUDED_PATHS:
        return False

    for prefix in EXCLUDED_PATH_PREFIXES:
        if path.startswith(prefix):
            return False

    return True


def _normalize_path(path: str, cwd: Path | None = None) -> str:
    """Normalize a path for consistent display.

    Args:
        path: Path to normalize.
        cwd: Current working directory for relative path resolution.

    Returns:
        Normalized path string.
    """
    if not path:
        return path

    try:
        # Resolve to absolute path
        p = Path(path)
        if not p.is_absolute() and cwd:
            p = cwd / p

        # Return as string (don't resolve symlinks to keep user intent)
        return str(p)
    except Exception:
        return path


class SyscallSummarizer:
    """Aggregates syscall events into a summary.

    Collects events as they come in and produces a final
    summary when requested.
    """

    def __init__(
        self,
        command: str,
        pid: int,
        cwd: Path | None = None,
        max_events: int = MAX_TRACED_EVENTS,
    ) -> None:
        """Initialize the summarizer.

        Args:
            command: Command being traced.
            pid: Main process ID.
            cwd: Current working directory.
            max_events: Maximum events to collect before truncating.
        """
        self._command = command
        self._pid = pid
        self._cwd = cwd
        self._max_events = max_events

        # Event collection
        self._events: list[SyscallEvent] = []
        self._truncated = False

        # Aggregated data (sets for deduplication)
        self._files_read: set[str] = set()
        self._files_written: set[str] = set()
        self._files_deleted: set[str] = set()
        self._files_created: set[str] = set()
        self._files_renamed: list[tuple[str, str]] = []
        self._connections: list[NetworkConnection] = []
        self._programs_executed: set[str] = set()
        self._processes_spawned = 0

        # Timing
        self._start_time: datetime | None = None
        self._end_time: datetime | None = None
        self._start_ns: int | None = None
        self._end_ns: int | None = None

    def add_event(self, event: SyscallEvent) -> bool:
        """Add a syscall event to the summary.

        Args:
            event: Syscall event to add.

        Returns:
            True if event was added, False if truncated.
        """
        # Track timing
        if self._start_ns is None:
            self._start_ns = event.ts_ns
            self._start_time = datetime.now(UTC)
        self._end_ns = event.ts_ns
        self._end_time = datetime.now(UTC)

        # Check truncation limit
        if len(self._events) >= self._max_events:
            self._truncated = True
            return False

        self._events.append(event)

        # Aggregate based on category
        self._aggregate_event(event)

        return True

    def _aggregate_event(self, event: SyscallEvent) -> None:
        """Aggregate a single event into summary data.

        Args:
            event: Event to aggregate.
        """
        path = event.path
        if path:
            path = _normalize_path(path, self._cwd)

        match event.category:
            case "file_read":
                if path and _should_include_path(path):
                    self._files_read.add(path)

            case "file_write":
                if path and _should_include_path(path):
                    self._files_written.add(path)

            case "file_create":
                if path and _should_include_path(path):
                    self._files_created.add(path)

            case "file_delete":
                if path and _should_include_path(path):
                    self._files_deleted.add(path)

            case "file_rename":
                if path and event.path:
                    # For rename, we need to look at the raw event data
                    # The path2 would be in the original event
                    # For now, just track the old path
                    if _should_include_path(path):
                        # Note: path2 handling would need event extension
                        self._files_renamed.append((path, path))

            case "net_connect":
                if event.addr and event.port:
                    conn = NetworkConnection(
                        addr=event.addr,
                        port=event.port,
                        family="ipv4",
                    )
                    if conn not in self._connections:
                        self._connections.append(conn)

            case "process_exec":
                if path and _should_include_path(path):
                    self._programs_executed.add(path)

            case "process_clone":
                self._processes_spawned += 1

    def get_summary(self) -> SyscallSummary:
        """Get the aggregated summary.

        Returns:
            SyscallSummary with all collected data.
        """
        # Calculate duration
        duration_ms = 0
        if self._start_ns is not None and self._end_ns is not None:
            duration_ms = (self._end_ns - self._start_ns) // 1_000_000

        # Sort and limit collections
        files_read = tuple(sorted(self._files_read)[:MAX_FILES_PER_CATEGORY])
        files_written = tuple(sorted(self._files_written)[:MAX_FILES_PER_CATEGORY])
        files_deleted = tuple(sorted(self._files_deleted)[:MAX_FILES_PER_CATEGORY])
        files_created = tuple(sorted(self._files_created)[:MAX_FILES_PER_CATEGORY])
        files_renamed = tuple(self._files_renamed[:MAX_FILES_PER_CATEGORY])
        programs_executed = tuple(sorted(self._programs_executed)[:MAX_FILES_PER_CATEGORY])

        return SyscallSummary(
            command=self._command,
            pid=self._pid,
            duration_ms=duration_ms,
            files_read=files_read,
            files_written=files_written,
            files_deleted=files_deleted,
            files_created=files_created,
            files_renamed=files_renamed,
            connections_made=tuple(self._connections[:MAX_FILES_PER_CATEGORY]),
            programs_executed=programs_executed,
            processes_spawned=self._processes_spawned,
            total_syscalls=len(self._events),
            events_truncated=self._truncated,
            start_time=self._start_time,
            end_time=self._end_time,
        )

    def reset(self) -> None:
        """Reset the summarizer for a new trace."""
        self._events.clear()
        self._truncated = False
        self._files_read.clear()
        self._files_written.clear()
        self._files_deleted.clear()
        self._files_created.clear()
        self._files_renamed.clear()
        self._connections.clear()
        self._programs_executed.clear()
        self._processes_spawned = 0
        self._start_time = None
        self._end_time = None
        self._start_ns = None
        self._end_ns = None


def create_summarizer(
    command: str,
    pid: int,
    cwd: Path | None = None,
) -> SyscallSummarizer:
    """Create a new syscall summarizer.

    Args:
        command: Command being traced.
        pid: Main process ID.
        cwd: Current working directory.

    Returns:
        Configured SyscallSummarizer instance.
    """
    return SyscallSummarizer(command=command, pid=pid, cwd=cwd)


def summarize_events(
    events: list[SyscallEvent],
    command: str,
    pid: int,
    cwd: Path | None = None,
) -> SyscallSummary:
    """Summarize a list of syscall events.

    Convenience function for summarizing events after collection.

    Args:
        events: List of syscall events.
        command: Command that was traced.
        pid: Main process ID.
        cwd: Current working directory.

    Returns:
        SyscallSummary for the events.
    """
    summarizer = create_summarizer(command=command, pid=pid, cwd=cwd)

    for event in events:
        summarizer.add_event(event)

    return summarizer.get_summary()
