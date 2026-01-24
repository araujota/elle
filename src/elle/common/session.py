"""Session state management for ELLE.

The Session object captures all state needed to process commands:
- Current working directory
- Last command executed
- Last command's stdout/stderr
- Last command's exit code
- Command history

Sessions are immutable - operations return new session instances.
This enables both REPL (persistent session) and one-shot (fresh session) modes
to use the same engine.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Session(BaseModel):
    """Immutable session state.

    All engine operations take a Session and return a new Session,
    enabling functional composition and easy testing.
    """

    model_config = ConfigDict(frozen=True)

    cwd: Path = Field(default_factory=Path.cwd)
    last_cmd: str | None = None
    last_stdout: str | None = None
    last_stderr: str | None = None
    last_exit: int | None = None
    history: tuple[str, ...] = ()

    def with_command_result(
        self,
        cmd: str,
        stdout: str | None = None,
        stderr: str | None = None,
        exit_code: int | None = None,
    ) -> "Session":
        """Create a new session with updated command result.

        Args:
            cmd: The command that was executed.
            stdout: Command's stdout output.
            stderr: Command's stderr output.
            exit_code: Command's exit code.

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
        )

    def with_cwd(self, new_cwd: Path) -> "Session":
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
        )

    @property
    def last_failed(self) -> bool:
        """Check if the last command failed (non-zero exit)."""
        return self.last_exit is not None and self.last_exit != 0

    @property
    def has_history(self) -> bool:
        """Check if any commands have been executed."""
        return len(self.history) > 0


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
