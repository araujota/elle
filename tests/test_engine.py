"""Tests for the ELLE engine."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from elle.cli.engine import Engine, EngineAction, EngineResult, get_engine
from elle.cli.terminal.classifier import IntentClassifier
from elle.common.session import create_session


@pytest.fixture
def mock_classifier() -> IntentClassifier:
    """Create a classifier for testing."""
    return IntentClassifier(log_path=Path("/tmp/elle-test-logs"))


class TestEngine:
    """Tests for the Engine class."""

    @pytest.fixture
    def engine(self, mock_classifier: IntentClassifier) -> Engine:
        """Create a fresh engine instance with mocked classifier."""
        return Engine(classifier=mock_classifier)

    @pytest.fixture
    def session(self):
        """Create a fresh session."""
        return create_session()

    def test_empty_input(self, engine: Engine, session) -> None:
        """Empty input should return empty result and continue."""
        result = engine.process("", session)

        assert result.output == ""
        assert result.action == EngineAction.CONTINUE
        assert result.success is True

    def test_whitespace_input(self, engine: Engine, session) -> None:
        """Whitespace-only input should be treated as empty."""
        result = engine.process("   \t  ", session)

        assert result.output == ""
        assert result.action == EngineAction.CONTINUE

    def test_exit_command(self, engine: Engine, session) -> None:
        """'exit' should signal EXIT action."""
        result = engine.process("exit", session)

        assert result.action == EngineAction.EXIT
        assert "Goodbye" in result.output

    def test_quit_command(self, engine: Engine, session) -> None:
        """'quit' should signal EXIT action."""
        result = engine.process("quit", session)

        assert result.action == EngineAction.EXIT

    def test_help_command(self, engine: Engine, session) -> None:
        """'help' should return help text."""
        result = engine.process("help", session)

        assert result.action == EngineAction.CONTINUE
        assert "ELLE Terminal" in result.output
        assert "help" in result.output
        assert "exit" in result.output

    def test_clear_command(self, engine: Engine, session) -> None:
        """'clear' should signal CLEAR action."""
        result = engine.process("clear", session)

        assert result.action == EngineAction.CLEAR

    def test_status_command(self, engine: Engine, session) -> None:
        """'status' should return a result (daemon or fallback)."""
        result = engine.process("status", session)

        # Status command returns daemon status or fallback session status
        assert result.action == EngineAction.CONTINUE
        assert result.success is True
        assert len(result.output) > 0

    def test_history_empty(self, engine: Engine, session) -> None:
        """'history' with no commands should indicate empty."""
        result = engine.process("history", session)

        assert "No commands" in result.output

    def test_history_with_commands(self, engine: Engine, session) -> None:
        """'history' should list previous commands."""
        # Add some history
        session = session.with_command_result(cmd="ls", exit_code=0)
        session = session.with_command_result(cmd="pwd", exit_code=0)

        result = engine.process("history", session)

        assert "ls" in result.output
        assert "pwd" in result.output

    def test_meta_commands_case_insensitive(self, engine: Engine, session) -> None:
        """Meta commands should be case insensitive."""
        result = engine.process("HELP", session)
        assert "ELLE Terminal" in result.output

        result = engine.process("Exit", session)
        assert result.action == EngineAction.EXIT

    def test_get_engine_singleton(self) -> None:
        """get_engine should return the same instance."""
        engine1 = get_engine()
        engine2 = get_engine()

        assert engine1 is engine2


class TestEngineShellCommands:
    """Tests for shell command execution through the engine."""

    @pytest.fixture
    def engine(self, mock_classifier: IntentClassifier) -> Engine:
        """Create a fresh engine instance with mocked classifier."""
        return Engine(classifier=mock_classifier)

    @pytest.fixture
    def session(self):
        """Create a fresh session."""
        return create_session()

    def test_executes_simple_command(self, engine: Engine, session) -> None:
        """Should execute simple shell commands."""
        result = engine.process("echo hello", session, stream_output=False)

        assert result.success is True
        assert "hello" in result.output
        assert result.session.last_cmd == "echo hello"
        assert result.session.last_exit == 0

    def test_captures_stdout(self, engine: Engine, session) -> None:
        """Should capture command stdout."""
        result = engine.process("echo 'test output'", session, stream_output=False)

        assert "test output" in result.output
        assert "test output" in result.session.last_stdout

    def test_captures_stderr(self, engine: Engine, session) -> None:
        """Should capture command stderr."""
        result = engine.process("echo 'error' >&2", session, stream_output=False)

        assert "error" in result.session.last_stderr

    def test_captures_exit_code(self, engine: Engine, session) -> None:
        """Should capture non-zero exit codes."""
        result = engine.process("exit 42", session, stream_output=False)

        assert result.success is False
        assert result.session.last_exit == 42

    def test_updates_session_history(self, engine: Engine, session) -> None:
        """Should add commands to session history."""
        result = engine.process("echo first", session, stream_output=False)
        result = engine.process("echo second", result.session, stream_output=False)

        assert len(result.session.history) == 2
        assert "echo first" in result.session.history
        assert "echo second" in result.session.history

    def test_blocks_sudo(self, engine: Engine, session) -> None:
        """Should block sudo commands."""
        result = engine.process("sudo ls", session, stream_output=False)

        assert result.success is False
        # Classifier flags dangerous commands and requires clarification
        assert (
            "blocked" in result.output.lower()
            or "denied" in result.output.lower()
            or "not sure" in result.output.lower()
            or "elle does not" in result.output.lower()
        )

    def test_blocks_dangerous_rm(self, engine: Engine, session) -> None:
        """Should block dangerous rm commands."""
        result = engine.process("rm -rf /", session, stream_output=False)

        assert result.success is False
        # Classifier flags dangerous commands and requires clarification
        assert (
            "blocked" in result.output.lower()
            or "denied" in result.output.lower()
            or "not sure" in result.output.lower()
            or "dangerous" in result.output.lower()
        )

    def test_failed_command_hint(self, engine: Engine, session) -> None:
        """Failed commands should show fix hint."""
        result = engine.process("ls /nonexistent_path_12345", session, stream_output=False)

        assert result.success is False
        assert "fix" in result.output.lower()


class TestEngineFix:
    """Tests for the 'fix' command."""

    @pytest.fixture
    def engine(self, mock_classifier: IntentClassifier) -> Engine:
        """Create a fresh engine instance with mocked classifier."""
        return Engine(classifier=mock_classifier)

    @pytest.fixture
    def session(self):
        """Create a fresh session."""
        return create_session()

    def test_fix_no_previous_command(self, engine: Engine, session) -> None:
        """'fix' with no previous command should show error."""
        result = engine.process("fix", session)

        assert result.success is False
        assert "No previous command" in result.output

    def test_fix_after_success(self, engine: Engine, session) -> None:
        """'fix' after successful command should indicate nothing to fix."""
        # Run a successful command first
        session = session.with_command_result(cmd="echo hello", exit_code=0, stdout="hello")

        result = engine.process("fix", session)

        assert "succeeded" in result.output.lower() or "nothing to fix" in result.output.lower()

    def test_fix_after_failure(self, engine: Engine, session) -> None:
        """'fix' after failed command should show error context and diagnosis."""
        # Simulate a failed command
        session = session.with_command_result(
            cmd="ls /nonexistent",
            exit_code=2,
            stdout="",
            stderr="ls: cannot access '/nonexistent': No such file or directory",
        )

        result = engine.process("fix", session)

        assert "ls /nonexistent" in result.output
        # New fixit output includes diagnosis
        assert "Fixit Analysis" in result.output or "No verified fixes" in result.output

    def test_fix_aliases(self, engine: Engine, session) -> None:
        """'fix it' and 'fixit' should work like 'fix'."""
        result1 = engine.process("fix it", session)
        result2 = engine.process("fixit", session)

        # Both should handle the no-command case the same way
        assert "No previous command" in result1.output
        assert "No previous command" in result2.output


class TestEngineResult:
    """Tests for EngineResult."""

    def test_default_values(self) -> None:
        """EngineResult should have sensible defaults."""
        session = create_session()
        result = EngineResult(output="test", session=session)

        assert result.action == EngineAction.CONTINUE
        assert result.success is True

    def test_immutable(self) -> None:
        """EngineResult should be immutable (frozen Pydantic model)."""
        session = create_session()
        result = EngineResult(output="test", session=session)

        with pytest.raises(ValidationError):
            result.output = "changed"  # type: ignore[misc]
