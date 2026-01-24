"""Tests for fixit models."""

from pathlib import Path

import pytest

from elle.cli.fixit.models import (
    CommandFailure,
    FixCommand,
    FixitAction,
    FixitAnalysis,
    FixitContext,
    FixitDiagnosis,
    FixitOutcome,
    FixitResult,
    ManSnippetContext,
    PriorArtContext,
    VerificationResult,
    VerificationStatus,
    VerifiedFixCommand,
)


class TestCommandFailure:
    """Tests for CommandFailure model."""

    def test_basic_creation(self):
        """Test creating a basic CommandFailure."""
        failure = CommandFailure(
            command="cat /etc/shadow",
            exit_code=1,
            stderr="cat: /etc/shadow: Permission denied",
        )

        assert failure.command == "cat /etc/shadow"
        assert failure.exit_code == 1
        assert "Permission denied" in failure.stderr
        assert failure.stdout == ""

    def test_with_all_fields(self):
        """Test CommandFailure with all fields."""
        failure = CommandFailure(
            command="ls /nonexistent",
            exit_code=2,
            stdout="",
            stderr="ls: cannot access '/nonexistent': No such file or directory",
            cwd=Path("/home/user"),
        )

        assert failure.exit_code == 2
        assert failure.cwd == Path("/home/user")

    def test_immutable(self):
        """Test that CommandFailure is immutable."""
        failure = CommandFailure(command="test", exit_code=1)
        with pytest.raises(Exception):  # ValidationError for frozen model
            failure.command = "modified"


class TestFixitDiagnosis:
    """Tests for FixitDiagnosis model."""

    def test_basic_creation(self):
        """Test creating a basic diagnosis."""
        diag = FixitDiagnosis(
            error_category="permission_denied",
            summary="Permission denied accessing /etc/shadow",
            root_cause="The file is owned by root and not world-readable",
            confidence=0.85,
        )

        assert diag.error_category == "permission_denied"
        assert diag.confidence == 0.85

    def test_confidence_bounds(self):
        """Test confidence must be between 0 and 1."""
        # Valid
        FixitDiagnosis(
            error_category="other",
            summary="test",
            root_cause="test",
            confidence=0.0,
        )
        FixitDiagnosis(
            error_category="other",
            summary="test",
            root_cause="test",
            confidence=1.0,
        )

        # Invalid
        with pytest.raises(Exception):
            FixitDiagnosis(
                error_category="other",
                summary="test",
                root_cause="test",
                confidence=1.5,
            )

    def test_valid_error_categories(self):
        """Test valid error categories."""
        categories = [
            "command_not_found",
            "permission_denied",
            "file_not_found",
            "syntax_error",
            "argument_error",
            "dependency_missing",
            "resource_exhausted",
            "network_error",
            "configuration_error",
            "other",
        ]

        for cat in categories:
            diag = FixitDiagnosis(
                error_category=cat,
                summary="test",
                root_cause="test",
                confidence=0.5,
            )
            assert diag.error_category == cat


class TestFixCommand:
    """Tests for FixCommand model."""

    def test_basic_creation(self):
        """Test creating a basic fix command."""
        fix = FixCommand(
            command="ls -la /etc/shadow",
            explanation="Check the permissions on the shadow file",
            risk_level="safe",
        )

        assert fix.command == "ls -la /etc/shadow"
        assert fix.risk_level == "safe"
        assert fix.requires_privilege is False
        assert fix.verification is None

    def test_with_privilege(self):
        """Test fix command requiring privilege."""
        fix = FixCommand(
            command="apt install nginx",
            explanation="Install nginx web server",
            risk_level="moderate",
            requires_privilege=True,
            verification="nginx -v",
        )

        assert fix.requires_privilege is True
        assert fix.verification == "nginx -v"

    def test_risk_levels(self):
        """Test valid risk levels."""
        for level in ["safe", "moderate", "high"]:
            fix = FixCommand(
                command="test",
                explanation="test",
                risk_level=level,
            )
            assert fix.risk_level == level


class TestFixitAnalysis:
    """Tests for FixitAnalysis model."""

    def test_basic_creation(self):
        """Test creating a basic analysis."""
        diag = FixitDiagnosis(
            error_category="permission_denied",
            summary="Permission denied",
            root_cause="Need root access",
            confidence=0.8,
        )
        analysis = FixitAnalysis(diagnosis=diag)

        assert analysis.diagnosis == diag
        assert analysis.suggestions == ()
        assert analysis.alternative_approach is None
        assert analysis.learn_more == ()

    def test_with_suggestions(self):
        """Test analysis with suggestions."""
        diag = FixitDiagnosis(
            error_category="command_not_found",
            summary="Command not found",
            root_cause="Package not installed",
            confidence=0.9,
        )
        fix1 = FixCommand(
            command="apt install foo",
            explanation="Install the package",
            risk_level="safe",
        )
        fix2 = FixCommand(
            command="which foo",
            explanation="Check if installed",
            risk_level="safe",
        )

        analysis = FixitAnalysis(
            diagnosis=diag,
            suggestions=(fix1, fix2),
            alternative_approach="Build from source",
            learn_more=("man apt", "man dpkg"),
        )

        assert len(analysis.suggestions) == 2
        assert analysis.alternative_approach == "Build from source"
        assert len(analysis.learn_more) == 2


class TestVerificationResult:
    """Tests for VerificationResult model."""

    def test_passed(self):
        """Test passed verification."""
        result = VerificationResult(
            command="ls -la",
            status=VerificationStatus.PASSED,
            checks_passed=("denylist", "parse", "executable"),
        )

        assert result.status == VerificationStatus.PASSED
        assert len(result.checks_passed) == 3
        assert len(result.checks_failed) == 0

    def test_failed(self):
        """Test failed verification."""
        result = VerificationResult(
            command="rm -rf /",
            status=VerificationStatus.FAILED,
            checks_failed=("denylist: Destructive rm command",),
        )

        assert result.status == VerificationStatus.FAILED
        assert len(result.checks_failed) == 1

    def test_warning(self):
        """Test verification with warnings."""
        result = VerificationResult(
            command="ls --unknown-flag",
            status=VerificationStatus.WARNING,
            checks_passed=("denylist", "parse", "executable"),
            warnings=("Unknown flag: --unknown-flag",),
        )

        assert result.status == VerificationStatus.WARNING
        assert len(result.warnings) == 1


class TestFixitResult:
    """Tests for FixitResult model."""

    def test_basic_creation(self):
        """Test creating a basic result."""
        failure = CommandFailure(command="test", exit_code=1)
        context = FixitContext(failure=failure)
        result = FixitResult(context=context)

        assert result.context == context
        assert result.analysis is None
        assert result.outcome == FixitOutcome.SKIPPED
        assert result.incident_id is None

    def test_with_analysis(self):
        """Test result with analysis helper."""
        failure = CommandFailure(command="test", exit_code=1)
        context = FixitContext(failure=failure)
        result = FixitResult(context=context)

        diag = FixitDiagnosis(
            error_category="other",
            summary="test",
            root_cause="test",
            confidence=0.5,
        )
        analysis = FixitAnalysis(diagnosis=diag)

        new_result = result.with_analysis(analysis)

        assert new_result.analysis == analysis
        assert result.analysis is None  # Original unchanged

    def test_with_outcome(self):
        """Test result with outcome helper."""
        failure = CommandFailure(command="test", exit_code=1)
        context = FixitContext(failure=failure)
        result = FixitResult(context=context)

        new_result = result.with_outcome(FixitOutcome.IMPROVED)

        assert new_result.outcome == FixitOutcome.IMPROVED
        assert result.outcome == FixitOutcome.SKIPPED  # Original unchanged

    def test_with_action(self):
        """Test result with action helper."""
        failure = CommandFailure(command="test", exit_code=1)
        context = FixitContext(failure=failure)
        result = FixitResult(context=context)

        action = FixitAction(
            command="ls -la",
            exit_code=0,
            success=True,
        )

        new_result = result.with_action(action)

        assert len(new_result.actions) == 1
        assert new_result.actions[0] == action
        assert len(result.actions) == 0  # Original unchanged

    def test_has_suggestions(self):
        """Test has_suggestions property."""
        failure = CommandFailure(command="test", exit_code=1)
        context = FixitContext(failure=failure)
        result = FixitResult(context=context)

        assert result.has_suggestions is False

        # Add verified suggestion
        fix = FixCommand(command="ls", explanation="test", risk_level="safe")
        verification = VerificationResult(
            command="ls",
            status=VerificationStatus.PASSED,
        )
        verified = VerifiedFixCommand(fix=fix, verification=verification, is_safe=True)

        result_with_suggestions = result.with_verified_suggestions((verified,), ())
        assert result_with_suggestions.has_suggestions is True

    def test_safe_suggestions(self):
        """Test safe_suggestions property."""
        failure = CommandFailure(command="test", exit_code=1)
        context = FixitContext(failure=failure)

        # Create safe and unsafe suggestions
        fix_safe = FixCommand(command="ls", explanation="safe", risk_level="safe")
        fix_unsafe = FixCommand(command="rm", explanation="unsafe", risk_level="high")

        ver_safe = VerificationResult(
            command="ls",
            status=VerificationStatus.PASSED,
        )
        ver_unsafe = VerificationResult(
            command="rm",
            status=VerificationStatus.FAILED,
            checks_failed=("denylist",),
        )

        verified_safe = VerifiedFixCommand(fix=fix_safe, verification=ver_safe, is_safe=True)
        verified_unsafe = VerifiedFixCommand(fix=fix_unsafe, verification=ver_unsafe, is_safe=False)

        result = FixitResult(
            context=context,
            verified_suggestions=(verified_safe, verified_unsafe),
        )

        safe = result.safe_suggestions
        assert len(safe) == 1
        assert safe[0].fix.command == "ls"


class TestFixitOutcome:
    """Tests for FixitOutcome enum."""

    def test_all_outcomes(self):
        """Test all outcome values exist."""
        assert FixitOutcome.IMPROVED.value == "improved"
        assert FixitOutcome.PARTIAL.value == "partial"
        assert FixitOutcome.NO_CHANGE.value == "no_change"
        assert FixitOutcome.WORSE.value == "worse"
        assert FixitOutcome.SKIPPED.value == "skipped"
        assert FixitOutcome.ERROR.value == "error"


class TestManSnippetContext:
    """Tests for ManSnippetContext model."""

    def test_creation(self):
        """Test creating a man snippet context."""
        snippet = ManSnippetContext(
            name="ls",
            section="1",
            snippet="List directory contents",
            match_section="DESCRIPTION",
        )

        assert snippet.name == "ls"
        assert snippet.section == "1"
        assert "List" in snippet.snippet


class TestPriorArtContext:
    """Tests for PriorArtContext model."""

    def test_creation(self):
        """Test creating a prior art context."""
        art = PriorArtContext(
            incident_id="abc-123",
            title="Permission denied on shadow file",
            outcome="improved",
            summary="User tried to read shadow file",
            root_cause="File is owned by root",
        )

        assert art.incident_id == "abc-123"
        assert art.outcome == "improved"
