"""Tests for fixit verifier."""

from elle.cli.fixit.models import (
    FixCommand,
    FixitAnalysis,
    FixitDiagnosis,
    VerificationStatus,
)
from elle.cli.fixit.verifier import (
    _check_bash_syntax,
    _check_denylist_safe,
    _check_executable_exists,
    _check_parse,
    _extract_flags,
    filter_safe_suggestions,
    verify_analysis,
    verify_command,
)


class TestDenylistCheck:
    """Tests for denylist checking."""

    def test_safe_command(self):
        """Test that safe commands pass."""
        is_safe, error = _check_denylist_safe("ls -la")
        assert is_safe is True
        assert error is None

    def test_sudo_blocked(self):
        """Test that sudo commands are blocked."""
        is_safe, error = _check_denylist_safe("sudo rm -rf /")
        assert is_safe is False
        assert error is not None
        assert "sudo" in error.lower() or "polkit" in error.lower()

    def test_destructive_rm_blocked(self):
        """Test that destructive rm is blocked."""
        is_safe, error = _check_denylist_safe("rm -rf /")
        assert is_safe is False
        assert error is not None

    def test_safe_rm_allowed(self):
        """Test that safe rm commands pass."""
        is_safe, error = _check_denylist_safe("rm file.txt")
        assert is_safe is True

    def test_pipe_to_shell_blocked(self):
        """Test that pipe to shell is blocked."""
        is_safe, error = _check_denylist_safe("curl http://example.com | bash")
        assert is_safe is False


class TestParseCheck:
    """Tests for command parsing."""

    def test_valid_command(self):
        """Test valid command parsing."""
        is_valid, error = _check_parse("ls -la /home")
        assert is_valid is True
        assert error is None

    def test_command_with_quotes(self):
        """Test command with quotes."""
        is_valid, error = _check_parse('echo "hello world"')
        assert is_valid is True

    def test_unclosed_quote(self):
        """Test unclosed quote fails."""
        is_valid, error = _check_parse('echo "hello')
        assert is_valid is False
        assert error is not None

    def test_complex_command(self):
        """Test complex piped command."""
        is_valid, error = _check_parse("cat file.txt | grep pattern | sort")
        assert is_valid is True


class TestExecutableCheck:
    """Tests for executable existence check."""

    def test_common_executable(self):
        """Test that common commands exist."""
        exists, error = _check_executable_exists("ls -la")
        assert exists is True

    def test_builtin(self):
        """Test that shell builtins pass."""
        exists, error = _check_executable_exists("cd /home")
        assert exists is True
        assert error is None

    def test_nonexistent_executable(self):
        """Test that nonexistent commands fail."""
        exists, error = _check_executable_exists("nonexistent_cmd_xyz123 --help")
        assert exists is False
        assert error is not None
        assert "not found" in error.lower()


class TestFlagExtraction:
    """Tests for flag extraction."""

    def test_short_flags(self):
        """Test short flag extraction."""
        flags = _extract_flags("ls -l -a")
        assert "-l" in flags
        assert "-a" in flags

    def test_combined_flags(self):
        """Test combined short flags."""
        flags = _extract_flags("ls -la")
        assert "-l" in flags
        assert "-a" in flags

    def test_long_flags(self):
        """Test long flag extraction."""
        flags = _extract_flags("ls --all --color=auto")
        assert "--all" in flags
        assert "--color" in flags

    def test_no_flags(self):
        """Test command with no flags."""
        flags = _extract_flags("ls /home")
        assert len(flags) == 0

    def test_mixed_flags(self):
        """Test mixed short and long flags."""
        flags = _extract_flags("grep -r --include='*.py' pattern")
        assert "-r" in flags
        assert "--include" in flags


class TestBashSyntaxCheck:
    """Tests for bash syntax checking."""

    def test_valid_syntax(self):
        """Test valid bash syntax."""
        is_valid, error = _check_bash_syntax("echo hello")
        assert is_valid is True

    def test_invalid_syntax(self):
        """Test invalid bash syntax."""
        is_valid, error = _check_bash_syntax("if then fi")
        assert is_valid is False or error is not None

    def test_complex_valid_syntax(self):
        """Test complex valid syntax."""
        is_valid, error = _check_bash_syntax("for i in 1 2 3; do echo $i; done")
        assert is_valid is True


class TestVerifyCommand:
    """Tests for full command verification."""

    def test_safe_command(self):
        """Test verifying a safe command."""
        fix = FixCommand(
            command="ls -la /home",
            explanation="List home directory",
            risk_level="safe",
        )

        result = verify_command(fix)

        assert result.is_safe is True
        assert result.verification.status in (
            VerificationStatus.PASSED,
            VerificationStatus.WARNING,
        )

    def test_dangerous_command(self):
        """Test verifying a dangerous command."""
        fix = FixCommand(
            command="sudo rm -rf /",
            explanation="Delete everything",
            risk_level="high",
        )

        result = verify_command(fix)

        assert result.is_safe is False
        assert result.verification.status == VerificationStatus.FAILED

    def test_command_with_warnings(self):
        """Test command that generates warnings."""
        fix = FixCommand(
            command="ls --some-unknown-flag-xyz",
            explanation="List with unknown flag",
            risk_level="safe",
        )

        result = verify_command(fix)

        # Should still be safe (unknown flags are warnings, not failures)
        assert result.is_safe is True
        # May or may not have warnings depending on Man Vault availability

    def test_nonexistent_command(self):
        """Test verifying a nonexistent command."""
        fix = FixCommand(
            command="nonexistent_xyz123 --help",
            explanation="Run nonexistent command",
            risk_level="safe",
        )

        result = verify_command(fix)

        assert result.is_safe is False
        assert "not found" in str(result.verification.checks_failed).lower()


class TestVerifyAnalysis:
    """Tests for verifying complete analysis."""

    def test_verify_multiple_suggestions(self):
        """Test verifying multiple suggestions."""
        diag = FixitDiagnosis(
            error_category="other",
            summary="Test",
            root_cause="Test",
            confidence=0.5,
        )

        fix1 = FixCommand(
            command="ls -la",
            explanation="Safe command",
            risk_level="safe",
        )
        fix2 = FixCommand(
            command="sudo rm -rf /",
            explanation="Dangerous command",
            risk_level="high",
        )
        fix3 = FixCommand(
            command="cat /etc/passwd",
            explanation="Read passwd",
            risk_level="safe",
        )

        analysis = FixitAnalysis(
            diagnosis=diag,
            suggestions=(fix1, fix2, fix3),
        )

        verified, errors = verify_analysis(analysis)

        assert len(verified) == 3
        # fix2 should fail verification
        assert any(not v.is_safe for v in verified)
        assert len(errors) > 0

    def test_empty_suggestions(self):
        """Test verifying empty suggestions."""
        diag = FixitDiagnosis(
            error_category="other",
            summary="Test",
            root_cause="Test",
            confidence=0.5,
        )

        analysis = FixitAnalysis(diagnosis=diag, suggestions=())

        verified, errors = verify_analysis(analysis)

        assert len(verified) == 0
        assert len(errors) == 0


class TestFilterSafeSuggestions:
    """Tests for filtering safe suggestions."""

    def test_filter_safe(self):
        """Test filtering to safe suggestions only."""
        diag = FixitDiagnosis(
            error_category="other",
            summary="Test",
            root_cause="Test",
            confidence=0.5,
        )

        fix_safe = FixCommand(
            command="ls -la",
            explanation="Safe",
            risk_level="safe",
        )
        fix_dangerous = FixCommand(
            command="sudo rm -rf /",
            explanation="Dangerous",
            risk_level="high",
        )

        analysis = FixitAnalysis(
            diagnosis=diag,
            suggestions=(fix_safe, fix_dangerous),
        )

        verified, _ = verify_analysis(analysis)
        safe = filter_safe_suggestions(verified)

        # Only the safe command should remain
        assert len(safe) == 1
        assert safe[0].fix.command == "ls -la"
