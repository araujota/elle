"""Tests for fixit service."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.fixit.models import (
    CommandFailure,
    FixitContext,
    FixitOutcome,
)
from elle.cli.fixit.service import (
    FixitService,
    get_fixit_service,
    reset_fixit_service,
)
from elle.common.session import Session


@pytest.fixture
def session_with_failure():
    """Create a session with a failed command."""
    return Session(
        cwd=Path("/home/user"),
        last_cmd="cat /etc/shadow",
        last_stdout="",
        last_stderr="cat: /etc/shadow: Permission denied",
        last_exit=1,
        history=("ls", "cd /home", "cat /etc/shadow"),
    )


@pytest.fixture
def session_success():
    """Create a session with a successful command."""
    return Session(
        cwd=Path("/home/user"),
        last_cmd="ls -la",
        last_exit=0,
    )


@pytest.fixture
def session_no_command():
    """Create a session with no previous command."""
    return Session(cwd=Path("/home/user"))


class TestFixitServiceInit:
    """Tests for service initialization."""

    def test_default_init(self):
        """Test default initialization."""
        service = FixitService()
        assert service._use_man_vault is True
        assert service._use_incident_vault is True
        assert service._use_llm is True

    def test_custom_init(self):
        """Test custom initialization."""
        service = FixitService(
            use_man_vault=False,
            use_incident_vault=False,
            use_llm=False,
        )
        assert service._use_man_vault is False
        assert service._use_incident_vault is False
        assert service._use_llm is False

    def test_singleton(self):
        """Test singleton pattern."""
        reset_fixit_service()
        s1 = get_fixit_service()
        s2 = get_fixit_service()
        assert s1 is s2
        reset_fixit_service()


class TestBuildContext:
    """Tests for context building."""

    def test_build_basic_context(self, session_with_failure):
        """Test building basic context."""
        service = FixitService(
            use_man_vault=False,
            use_incident_vault=False,
        )

        context = service.build_context(session_with_failure)

        assert isinstance(context, FixitContext)
        assert context.failure.command == "cat /etc/shadow"
        assert context.failure.exit_code == 1
        assert "Permission denied" in context.failure.stderr
        assert len(context.history) == 3

    def test_context_failure_fields(self, session_with_failure):
        """Test that failure fields are populated."""
        service = FixitService(
            use_man_vault=False,
            use_incident_vault=False,
        )

        context = service.build_context(session_with_failure)

        assert isinstance(context.failure, CommandFailure)
        assert context.failure.cwd == Path("/home/user")

    def test_empty_vaults(self, session_with_failure):
        """Test context with disabled vaults."""
        service = FixitService(
            use_man_vault=False,
            use_incident_vault=False,
        )

        context = service.build_context(session_with_failure)

        assert context.man_snippets == ()
        assert context.prior_art == ()


class TestRunFullPipeline:
    """Tests for the full pipeline."""

    def test_pipeline_with_fallback(self, session_with_failure):
        """Test pipeline uses fallback when LLM unavailable."""
        service = FixitService(
            use_man_vault=False,
            use_incident_vault=False,
            use_llm=False,  # Force fallback
        )

        result = service.run_full_pipeline(session_with_failure)

        # Should use fallback
        assert result.used_fallback is True
        assert result.analysis is not None
        # Fallback should detect permission denied
        assert result.analysis.diagnosis.error_category == "permission_denied"

    def test_pipeline_verifies_suggestions(self, session_with_failure):
        """Test pipeline verifies suggestions."""
        service = FixitService(
            use_man_vault=False,
            use_incident_vault=False,
            use_llm=False,
        )

        result = service.run_full_pipeline(session_with_failure)

        # Should have verified suggestions
        assert result.analysis is not None
        # Suggestions should be verified
        for verified in result.verified_suggestions:
            assert verified.verification is not None

    def test_pipeline_outcome_default(self, session_with_failure):
        """Test default outcome is SKIPPED."""
        service = FixitService(
            use_man_vault=False,
            use_incident_vault=False,
            use_llm=False,
        )

        result = service.run_full_pipeline(session_with_failure)

        assert result.outcome == FixitOutcome.SKIPPED

    @patch("elle.cli.fixit.service.FixitService._search_man_vault")
    def test_pipeline_with_man_vault(self, mock_man_vault, session_with_failure):
        """Test pipeline includes man vault results."""
        from elle.cli.fixit.models import ManSnippetContext

        mock_man_vault.return_value = (
            ManSnippetContext(
                name="cat",
                section="1",
                snippet="Concatenate files",
            ),
        )

        service = FixitService(
            use_man_vault=True,
            use_incident_vault=False,
            use_llm=False,
        )

        result = service.run_full_pipeline(session_with_failure)

        assert len(result.context.man_snippets) == 1
        assert result.context.man_snippets[0].name == "cat"


class TestFallbackAnalysis:
    """Tests for fallback analysis via service."""

    def test_command_not_found(self):
        """Test command not found detection."""
        session = Session(
            cwd=Path("/home/user"),
            last_cmd="foobar123",
            last_stderr="foobar123: command not found",
            last_exit=127,
        )

        service = FixitService(
            use_man_vault=False,
            use_incident_vault=False,
            use_llm=False,
        )

        result = service.run_full_pipeline(session)

        assert result.analysis is not None
        assert result.analysis.diagnosis.error_category == "command_not_found"

    def test_file_not_found(self):
        """Test file not found detection."""
        session = Session(
            cwd=Path("/home/user"),
            last_cmd="cat /nonexistent",
            last_stderr="cat: /nonexistent: No such file or directory",
            last_exit=1,
        )

        service = FixitService(
            use_man_vault=False,
            use_incident_vault=False,
            use_llm=False,
        )

        result = service.run_full_pipeline(session)

        assert result.analysis is not None
        assert result.analysis.diagnosis.error_category == "file_not_found"

    def test_network_error(self):
        """Test network error detection."""
        session = Session(
            cwd=Path("/home/user"),
            last_cmd="curl http://192.0.2.1",
            last_stderr="curl: (7) Failed to connect: Connection refused",
            last_exit=7,
        )

        service = FixitService(
            use_man_vault=False,
            use_incident_vault=False,
            use_llm=False,
        )

        result = service.run_full_pipeline(session)

        assert result.analysis is not None
        assert result.analysis.diagnosis.error_category == "network_error"


class TestExecuteFix:
    """Tests for executing fixes."""

    @patch("elle.cli.subprocess_runner.run_safe")
    def test_execute_successful_fix(self, mock_run, session_with_failure):
        """Test executing a successful fix."""
        from elle.cli.subprocess_runner import SubprocessResult

        mock_run.return_value = SubprocessResult(
            command="ls -la",
            exit_code=0,
            stdout="total 0\n",
            stderr="",
        )

        service = FixitService(
            use_man_vault=False,
            use_incident_vault=False,
            use_llm=False,
        )

        result = service.run_full_pipeline(session_with_failure)
        new_result, action = service.execute_fix(result, "ls -la", session_with_failure)

        assert action.success is True
        assert action.exit_code == 0
        assert len(new_result.actions) == 1

    @patch("elle.cli.subprocess_runner.run_safe")
    def test_execute_failed_fix(self, mock_run, session_with_failure):
        """Test executing a failed fix.

        Note: With capability primacy, fixes route through capabilities when possible.
        This test verifies failure is properly reported regardless of execution path.
        """
        from elle.cli.subprocess_runner import SubprocessResult

        mock_run.return_value = SubprocessResult(
            command="apt install foo",
            exit_code=1,
            stdout="",
            stderr="E: Unable to locate package foo",
        )

        service = FixitService(
            use_man_vault=False,
            use_incident_vault=False,
            use_llm=False,
        )

        result = service.run_full_pipeline(session_with_failure)
        new_result, action = service.execute_fix(
            result, "apt install foo", session_with_failure
        )

        # Core assertion: failed fixes are properly reported
        assert action.success is False
        assert action.exit_code == 1
        # stderr contains some error (either APT error or capability error)
        assert action.stderr is not None and len(action.stderr) > 0


class TestIncidentIntegration:
    """Tests for incident vault integration."""

    @patch("elle.daemon.incidents.correlator.get_correlator")
    def test_creates_incident(self, mock_get_correlator, session_with_failure):
        """Test that incident is created."""
        mock_correlator = MagicMock()
        mock_correlator.create_from_command_failure.return_value = "test-incident-123"
        mock_get_correlator.return_value = mock_correlator

        service = FixitService(
            use_man_vault=False,
            use_incident_vault=True,
            use_llm=False,
        )

        result = service.run_full_pipeline(session_with_failure)

        # Should have incident ID
        assert result.incident_id == "test-incident-123"
        mock_correlator.create_from_command_failure.assert_called_once()

    def test_no_incident_when_disabled(self, session_with_failure):
        """Test no incident when vault disabled."""
        service = FixitService(
            use_man_vault=False,
            use_incident_vault=False,
            use_llm=False,
        )

        result = service.run_full_pipeline(session_with_failure)

        assert result.incident_id is None
