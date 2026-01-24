"""Tests for session state management."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from elle.common.session import create_session


class TestSession:
    """Tests for the Session dataclass."""

    def test_create_session_defaults(self) -> None:
        """Session should have sensible defaults."""
        session = create_session()

        assert session.cwd == Path.cwd()
        assert session.last_cmd is None
        assert session.last_stdout is None
        assert session.last_stderr is None
        assert session.last_exit is None
        assert session.history == ()

    def test_create_session_with_cwd(self, tmp_path: Path) -> None:
        """Session should accept custom cwd."""
        session = create_session(cwd=tmp_path)
        assert session.cwd == tmp_path

    def test_session_is_immutable(self) -> None:
        """Session should be immutable (frozen Pydantic model)."""
        session = create_session()

        with pytest.raises(ValidationError):
            session.cwd = Path("/tmp")  # type: ignore[misc]

    def test_with_command_result(self) -> None:
        """with_command_result should return new session with updated state."""
        session = create_session()

        new_session = session.with_command_result(
            cmd="ls -la",
            stdout="file1\nfile2",
            stderr=None,
            exit_code=0,
        )

        # Original unchanged
        assert session.last_cmd is None
        assert session.history == ()

        # New session has updates
        assert new_session.last_cmd == "ls -la"
        assert new_session.last_stdout == "file1\nfile2"
        assert new_session.last_stderr is None
        assert new_session.last_exit == 0
        assert new_session.history == ("ls -la",)

    def test_with_cwd(self, tmp_path: Path) -> None:
        """with_cwd should return new session with updated cwd."""
        session = create_session()
        new_session = session.with_cwd(tmp_path)

        assert session.cwd == Path.cwd()
        assert new_session.cwd == tmp_path

    def test_last_failed_true(self) -> None:
        """last_failed should be True for non-zero exit."""
        session = create_session().with_command_result(
            cmd="false",
            exit_code=1,
        )
        assert session.last_failed is True

    def test_last_failed_false(self) -> None:
        """last_failed should be False for zero exit."""
        session = create_session().with_command_result(
            cmd="true",
            exit_code=0,
        )
        assert session.last_failed is False

    def test_last_failed_none(self) -> None:
        """last_failed should be False when no command run."""
        session = create_session()
        assert session.last_failed is False

    def test_has_history(self) -> None:
        """has_history should reflect command history presence."""
        session = create_session()
        assert session.has_history is False

        session = session.with_command_result(cmd="ls", exit_code=0)
        assert session.has_history is True

    def test_history_accumulates(self) -> None:
        """History should accumulate across commands."""
        session = create_session()
        session = session.with_command_result(cmd="cmd1", exit_code=0)
        session = session.with_command_result(cmd="cmd2", exit_code=0)
        session = session.with_command_result(cmd="cmd3", exit_code=0)

        assert session.history == ("cmd1", "cmd2", "cmd3")
