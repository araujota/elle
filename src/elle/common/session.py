"""Session state management for ELLE.

The Session object captures all state needed to process commands:
- Current working directory
- Last command executed
- Last command's stdout/stderr
- Last command's exit code
- Command history
- Last syscall trace (for command explanation)

Sessions are immutable - operations return new session instances.
This enables both REPL (persistent session) and one-shot (fresh session) modes
to use the same engine.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from elle.daemon.telemetry.ebpf.syscall_models import SyscallTrace


class Session(BaseModel):
    """Immutable session state.

    All engine operations take a Session and return a new Session,
    enabling functional composition and easy testing.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    cwd: Path = Field(default_factory=Path.cwd)
    last_cmd: str | None = None
    last_stdout: str | None = None
    last_stderr: str | None = None
    last_exit: int | None = None
    history: tuple[str, ...] = ()
    last_syscall_trace: SyscallTrace | None = Field(
        default=None,
        description="Syscall trace from last traced command",
    )

    def with_command_result(
        self,
        cmd: str,
        stdout: str | None = None,
        stderr: str | None = None,
        exit_code: int | None = None,
        syscall_trace: SyscallTrace | None = None,
    ) -> Session:
        """Create a new session with updated command result.

        Args:
            cmd: The command that was executed.
            stdout: Command's stdout output.
            stderr: Command's stderr output.
            exit_code: Command's exit code.
            syscall_trace: Syscall trace if tracing was enabled.

        Returns:
            New Session with updated state.
        """
        return Session(
            cwd=self.cwd,
            last_cmd=cmd,
            last_stdout=stdout,
            last_stderr=stderr,
            last_exit=exit_code,
            history=(*self.history, cmd),
            last_syscall_trace=syscall_trace,
        )

    def with_syscall_trace(
        self,
        trace: SyscallTrace,
    ) -> Session:
        """Create a new session with updated syscall trace.

        Args:
            trace: The syscall trace from the last traced command.

        Returns:
            New Session with updated trace.
        """
        return Session(
            cwd=self.cwd,
            last_cmd=self.last_cmd,
            last_stdout=self.last_stdout,
            last_stderr=self.last_stderr,
            last_exit=self.last_exit,
            history=self.history,
            last_syscall_trace=trace,
        )

    def with_cwd(self, new_cwd: Path) -> Session:
        """Create a new session with updated working directory.

        Args:
            new_cwd: The new working directory.

        Returns:
            New Session with updated cwd.
        """
        return Session(
            cwd=new_cwd,
            last_cmd=self.last_cmd,
            last_stdout=self.last_stdout,
            last_stderr=self.last_stderr,
            last_exit=self.last_exit,
            history=self.history,
            last_syscall_trace=self.last_syscall_trace,
        )

    @property
    def last_failed(self) -> bool:
        """Check if the last command failed (non-zero exit)."""
        return self.last_exit is not None and self.last_exit != 0

    @property
    def has_history(self) -> bool:
        """Check if any commands have been executed."""
        return len(self.history) > 0

    @property
    def has_trace(self) -> bool:
        """Check if a syscall trace is available for the last command."""
        return self.last_syscall_trace is not None and self.last_syscall_trace.has_trace


def create_session(cwd: Path | None = None) -> Session:
    """Create a fresh session.

    Args:
        cwd: Starting working directory. Defaults to current directory.

    Returns:
        A new Session instance.
    """
    if cwd is not None:
        return Session(cwd=cwd)
    return Session()
