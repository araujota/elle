"""Data models for syscall tracing.

Defines Pydantic models for syscall events, summaries, and traces
used for command explanation and auditing.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Syscall categories for classification
SyscallCategory = Literal[
    "file_read",
    "file_write",
    "file_delete",
    "file_create",
    "file_rename",
    "file_chmod",
    "net_connect",
    "net_bind",
    "net_send",
    "process_exec",
    "process_clone",
    "other",
]


class SyscallEvent(BaseModel):
    """Single syscall event captured during tracing.

    Represents a single system call made by a traced process.
    """

    model_config = ConfigDict(frozen=True)

    ts_ns: int = Field(description="Kernel timestamp in nanoseconds")
    syscall: str = Field(description="Syscall name (openat, connect, etc.)")
    category: SyscallCategory = Field(description="Category for aggregation")
    pid: int = Field(ge=0, description="Process ID")
    comm: str = Field(max_length=16, description="Process name")

    # File-related fields
    path: str | None = Field(default=None, description="File path if applicable")
    flags: int | None = Field(default=None, description="Open flags if applicable")
    mode: int | None = Field(default=None, description="File mode if applicable")

    # Network-related fields
    addr: str | None = Field(default=None, description="IP address if applicable")
    port: int | None = Field(default=None, ge=0, le=65535, description="Port if applicable")

    # Result
    ret: int | None = Field(default=None, description="Return value")


class NetworkConnection(BaseModel):
    """Network connection made during tracing."""

    model_config = ConfigDict(frozen=True)

    addr: str = Field(description="IP address or hostname")
    port: int = Field(ge=0, le=65535, description="Port number")
    family: Literal["ipv4", "ipv6", "unix"] = Field(default="ipv4")


class SyscallSummary(BaseModel):
    """Aggregated summary of syscalls for a traced command.

    Provides a high-level view of what a command actually did
    at the syscall level.
    """

    model_config = ConfigDict(frozen=True)

    command: str = Field(description="Command that was traced")
    pid: int = Field(ge=0, description="Main process ID")
    duration_ms: int = Field(ge=0, description="Total execution duration in ms")

    # File operations (deduplicated, sorted)
    files_read: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Files read (openat with O_RDONLY)",
    )
    files_written: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Files written to",
    )
    files_deleted: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Files deleted (unlinkat)",
    )
    files_created: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Files created (openat with O_CREAT)",
    )
    files_renamed: tuple[tuple[str, str], ...] = Field(
        default_factory=tuple,
        description="Files renamed (old_path, new_path)",
    )

    # Network operations
    connections_made: tuple[NetworkConnection, ...] = Field(
        default_factory=tuple,
        description="Network connections made",
    )

    # Process operations
    programs_executed: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Programs executed (execve)",
    )
    processes_spawned: int = Field(
        default=0,
        ge=0,
        description="Number of child processes (clone)",
    )

    # Statistics
    total_syscalls: int = Field(ge=0, description="Total syscalls traced")
    events_truncated: bool = Field(
        default=False,
        description="True if events were truncated due to limits",
    )

    # Timing
    start_time: datetime | None = Field(default=None)
    end_time: datetime | None = Field(default=None)


class SyscallTrace(BaseModel):
    """Complete trace result for a command.

    Contains the summary and optionally the human-readable explanation.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(
        default=False,
        description="Whether tracing was enabled for this command",
    )
    summary: SyscallSummary | None = Field(
        default=None,
        description="Aggregated syscall summary",
    )
    error: str | None = Field(
        default=None,
        description="Error message if tracing failed",
    )
    explanation: str | None = Field(
        default=None,
        description="Human-readable explanation of what the command did",
    )

    @property
    def has_trace(self) -> bool:
        """Check if trace data is available."""
        return self.summary is not None

    @property
    def has_file_operations(self) -> bool:
        """Check if any file operations were traced."""
        if not self.summary:
            return False
        return bool(
            self.summary.files_read
            or self.summary.files_written
            or self.summary.files_deleted
            or self.summary.files_created
            or self.summary.files_renamed
        )

    @property
    def has_network_operations(self) -> bool:
        """Check if any network operations were traced."""
        if not self.summary:
            return False
        return bool(self.summary.connections_made)

    @property
    def has_process_operations(self) -> bool:
        """Check if any process operations were traced."""
        if not self.summary:
            return False
        return bool(
            self.summary.programs_executed or self.summary.processes_spawned > 0
        )


# Constants for syscall tracing
MAX_TRACED_EVENTS = 10000  # Maximum events before truncation
MAX_PATH_LENGTH = 256  # Maximum path length to capture
MAX_FILES_PER_CATEGORY = 100  # Maximum files per category in summary
RING_BUFFER_SIZE_KB = 64  # Ring buffer size in KB


# Syscall to category mapping
SYSCALL_CATEGORIES: dict[str, SyscallCategory] = {
    # File read operations
    "openat": "file_read",  # Determined by flags at parse time
    "read": "file_read",
    "pread64": "file_read",
    "readv": "file_read",
    "preadv": "file_read",
    "readlink": "file_read",
    "readlinkat": "file_read",
    "getdents64": "file_read",
    "stat": "file_read",
    "statx": "file_read",
    "fstat": "file_read",
    "lstat": "file_read",
    "access": "file_read",
    "faccessat": "file_read",

    # File write operations
    "write": "file_write",
    "pwrite64": "file_write",
    "writev": "file_write",
    "pwritev": "file_write",
    "truncate": "file_write",
    "ftruncate": "file_write",

    # File delete operations
    "unlink": "file_delete",
    "unlinkat": "file_delete",
    "rmdir": "file_delete",

    # File create operations
    "creat": "file_create",
    "mkdir": "file_create",
    "mkdirat": "file_create",
    "mknod": "file_create",
    "mknodat": "file_create",
    "symlink": "file_create",
    "symlinkat": "file_create",
    "link": "file_create",
    "linkat": "file_create",

    # File rename operations
    "rename": "file_rename",
    "renameat": "file_rename",
    "renameat2": "file_rename",

    # File permission operations
    "chmod": "file_chmod",
    "fchmod": "file_chmod",
    "fchmodat": "file_chmod",
    "chown": "file_chmod",
    "fchown": "file_chmod",
    "fchownat": "file_chmod",
    "lchown": "file_chmod",

    # Network connect operations
    "connect": "net_connect",

    # Network bind operations
    "bind": "net_bind",

    # Network send operations
    "sendto": "net_send",
    "sendmsg": "net_send",
    "sendmmsg": "net_send",

    # Process execution
    "execve": "process_exec",
    "execveat": "process_exec",

    # Process creation
    "clone": "process_clone",
    "clone3": "process_clone",
    "fork": "process_clone",
    "vfork": "process_clone",
}
