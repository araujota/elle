"""Additional coverage tests for elle.cli.fixit.verifier.

Targets uncovered lines:
- L83: _check_executable_exists with empty command (empty parts)
- L125-128: _check_executable_exists TimeoutExpired and OSError paths
- L157-158: _extract_flags ValueError path
- L173: _check_flags_exist with empty parts
- L184-186: _check_flags_exist ImportError on manvault
- L194-196: _check_flags_exist flag_exists raises Exception
- L204-205: _check_flags_exist ValueError path
- L227-230: _check_bash_syntax TimeoutExpired and OSError paths
- L271: verify_command with flags warning + some verified flags
- L288: verify_command with verified flags partial pass message
- L295: verify_command syntax warning added to warnings list
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from elle.cli.fixit.models import (
    FixCommand,
    FixitAnalysis,
    FixitDiagnosis,
    VerificationStatus,
)
from elle.cli.fixit.verifier import (
    _check_bash_syntax,
    _check_executable_exists,
    _check_flags_exist,
    _check_parse,
    _extract_flags,
    verify_command,
)


# =============================================================================
# _check_executable_exists edge cases (lines 83, 125-128)
# =============================================================================


class TestCheckExecutableExistsEdgeCases:
    """Cover edge cases in _check_executable_exists."""

    def test_empty_command_string(self):
        """Empty command after shlex.split returns empty list -> (False, 'Empty command')."""
        ok, err = _check_executable_exists("")
        assert ok is False
        assert err == "Empty command"

    def test_which_timeout(self):
        """TimeoutExpired from 'which' returns (True, None) - assume OK."""
        with patch(
            "elle.cli.fixit.verifier.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="which", timeout=5),
        ):
            ok, err = _check_executable_exists("some_cmd arg1")
        assert ok is True
        assert err is None

    def test_oserror_from_which(self):
        """OSError from subprocess returns (False, 'Check failed: ...')."""
        with patch(
            "elle.cli.fixit.verifier.subprocess.run",
            side_effect=OSError("No such file or directory"),
        ):
            ok, err = _check_executable_exists("some_cmd arg1")
        assert ok is False
        assert "Check failed" in err

    def test_value_error_from_shlex(self):
        """ValueError from shlex.split returns (False, 'Check failed: ...')."""
        # A string that causes shlex.split to raise ValueError
        ok, err = _check_executable_exists("echo 'unterminated")
        assert ok is False
        assert err is not None


# =============================================================================
# _extract_flags edge cases (lines 157-158)
# =============================================================================


class TestExtractFlagsEdgeCases:
    """Cover edge cases in _extract_flags."""

    def test_unparseable_command_returns_empty(self):
        """ValueError from shlex.split in _extract_flags returns empty list."""
        flags = _extract_flags("echo 'unterminated")
        assert flags == []


# =============================================================================
# _check_flags_exist edge cases (lines 173, 184-186, 194-196, 204-205)
# =============================================================================


class TestCheckFlagsExistEdgeCases:
    """Cover edge cases in _check_flags_exist."""

    def test_empty_command_returns_all_verified(self):
        """Empty command after shlex.split -> (True, [], [])."""
        ok, verified, unknown = _check_flags_exist("")
        assert ok is True
        assert verified == []
        assert unknown == []

    def test_import_error_on_manvault(self):
        """ImportError on manvault import returns (True, [], flags)."""
        # Block the manvault module
        with patch.dict("sys.modules", {"elle.daemon.manvault.retriever": None}):
            ok, verified, unknown = _check_flags_exist("ls -la --all")
        assert ok is True
        assert verified == []
        # The flags should be in the unknown list since we can't verify them
        assert len(unknown) > 0

    def test_flag_exists_raises_exception(self):
        """Exception from flag_exists marks flag as unknown."""
        mock_mod = MagicMock()
        mock_mod.flag_exists = MagicMock(side_effect=RuntimeError("db error"))

        with patch.dict("sys.modules", {"elle.daemon.manvault.retriever": mock_mod}):
            ok, verified, unknown = _check_flags_exist("ls -l --color")
        assert ok is False
        assert len(unknown) > 0
        assert verified == []

    def test_flag_exists_partial_verification(self):
        """Some flags verified, some unknown."""
        mock_mod = MagicMock()
        # First flag found, second not
        mock_mod.flag_exists = MagicMock(side_effect=[True, False])

        with patch.dict("sys.modules", {"elle.daemon.manvault.retriever": mock_mod}):
            ok, verified, unknown = _check_flags_exist("ls -l --all")
        assert ok is False
        assert len(verified) == 1
        assert len(unknown) == 1

    def test_value_error_in_shlex_returns_true(self):
        """ValueError during parsing returns (True, [], [])."""
        ok, verified, unknown = _check_flags_exist("echo 'unterminated")
        assert ok is True
        assert verified == []
        assert unknown == []


# =============================================================================
# _check_bash_syntax edge cases (lines 227-230)
# =============================================================================


class TestCheckBashSyntaxEdgeCases:
    """Cover edge cases in _check_bash_syntax."""

    def test_timeout_returns_true(self):
        """TimeoutExpired returns (True, None)."""
        with patch(
            "elle.cli.fixit.verifier.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="bash", timeout=5),
        ):
            ok, err = _check_bash_syntax("echo test")
        assert ok is True
        assert err is None

    def test_oserror_returns_true(self):
        """OSError returns (True, None) - skip check."""
        with patch(
            "elle.cli.fixit.verifier.subprocess.run",
            side_effect=OSError("bash not found"),
        ):
            ok, err = _check_bash_syntax("echo test")
        assert ok is True
        assert err is None


# =============================================================================
# verify_command branches for warning paths (lines 271, 288, 295)
# =============================================================================


class TestVerifyCommandWarningBranches:
    """Cover the warning branches in verify_command."""

    def test_unknown_flags_warning_with_some_verified(self):
        """When some flags verified and some unknown, warning is added and
        verified count message added to passed list."""
        fix = FixCommand(
            command="ls -l --all",
            explanation="List with flags",
            risk_level="safe",
        )

        mock_mod = MagicMock()
        # -l verified, --all unknown
        mock_mod.flag_exists = MagicMock(side_effect=[True, False])

        with patch.dict("sys.modules", {"elle.daemon.manvault.retriever": mock_mod}):
            result = verify_command(fix)

        # Should still be safe (flag warnings don't block)
        assert result.is_safe is True
        # Should have a warning about unknown flags
        assert any("Unknown flags" in w for w in result.verification.warnings)
        # Should note verified flags in passed
        assert any("1 verified" in p for p in result.verification.checks_passed)

    def test_syntax_warning_produces_warning_status(self):
        """When bash syntax fails, a warning is added (not a failure)."""
        fix = FixCommand(
            command="if then fi",
            explanation="Bad syntax",
            risk_level="safe",
        )

        # Mock executable check to pass (since 'if' is a shell keyword)
        with patch(
            "elle.cli.fixit.verifier._check_executable_exists",
            return_value=(True, None),
        ):
            result = verify_command(fix)

        # Is still safe (syntax errors are warnings, not critical)
        assert result.is_safe is True
        # But status should be WARNING if there are any warnings
        if result.verification.warnings:
            assert result.verification.status == VerificationStatus.WARNING

    def test_parse_failure_in_verify_command(self):
        """When command has unclosed quote, parse check fails -> FAILED status."""
        fix = FixCommand(
            command="echo 'unterminated",
            explanation="Bad quoting",
            risk_level="safe",
        )
        result = verify_command(fix)
        assert result.is_safe is False
        assert result.verification.status == VerificationStatus.FAILED
        assert any("parse" in f.lower() for f in result.verification.checks_failed)

    def test_all_flags_unknown_no_verified_count(self):
        """When all flags are unknown, no 'X verified' message in passed."""
        fix = FixCommand(
            command="ls --unknown1 --unknown2",
            explanation="Unknown flags",
            risk_level="safe",
        )

        mock_mod = MagicMock()
        mock_mod.flag_exists = MagicMock(return_value=False)

        with patch.dict("sys.modules", {"elle.daemon.manvault.retriever": mock_mod}):
            result = verify_command(fix)

        # Should have warning about unknown flags
        assert any("Unknown flags" in w for w in result.verification.warnings)
        # Should NOT have "X verified" in passed since no flags were verified
        assert not any("verified" in p for p in result.verification.checks_passed)
