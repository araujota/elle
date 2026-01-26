"""Command verification pipeline for Fixit.

Verifies suggested fix commands before presenting them:
1. Denylist check - Block dangerous commands
2. Parse check - Verify shell syntax
3. Executable check - Verify command exists
4. Flag check - Verify flags via Man Vault
5. Syntax check - bash -n validation

Commands failing critical checks are filtered out.
Commands with unknown flags get warnings.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from typing import TYPE_CHECKING

from elle.cli.fixit.models import (
    FixCommand,
    VerificationResult,
    VerificationStatus,
    VerifiedFixCommand,
)
from elle.cli.subprocess_runner import check_denylist

if TYPE_CHECKING:
    from elle.cli.fixit.models import FixitAnalysis

logger = logging.getLogger(__name__)


# =============================================================================
# Verification Checks
# =============================================================================


def _check_denylist_safe(command: str) -> tuple[bool, str | None]:
    """Check if command is on the denylist.

    Args:
        command: Command string to check.

    Returns:
        Tuple of (is_safe, error_message).
    """
    denied, reason, explanation = check_denylist(command)
    if denied:
        return False, f"Blocked: {explanation}"
    return True, None


def _check_parse(command: str) -> tuple[bool, str | None]:
    """Check if command can be parsed by shlex.

    Args:
        command: Command string to check.

    Returns:
        Tuple of (is_valid, error_message).
    """
    try:
        shlex.split(command)
        return True, None
    except ValueError as e:
        return False, f"Parse error: {e}"


def _check_executable_exists(command: str) -> tuple[bool, str | None]:
    """Check if the executable exists in PATH.

    Args:
        command: Command string (extracts first word as executable).

    Returns:
        Tuple of (exists, error_message).
    """
    try:
        parts = shlex.split(command)
        if not parts:
            return False, "Empty command"

        executable = parts[0]

        # Skip shell builtins that won't be found by which
        shell_builtins = {
            "cd",
            "export",
            "source",
            ".",
            "alias",
            "unalias",
            "echo",
            "printf",
            "read",
            "type",
            "set",
            "unset",
            "local",
            "return",
            "exit",
            "exec",
            "eval",
            "true",
            "false",
            "test",
            "[",
            "[[",
        }
        if executable in shell_builtins:
            return True, None

        # Check with 'which' or 'command -v'
        result = subprocess.run(
            ["which", executable],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return True, None
        return False, f"Command not found: {executable}"

    except subprocess.TimeoutExpired:
        return True, None  # Assume OK if which times out
    except (ValueError, OSError) as e:
        return False, f"Check failed: {e}"


def _extract_flags(command: str) -> list[str]:
    """Extract flags from a command.

    Args:
        command: Command string.

    Returns:
        List of flags (e.g., ["-l", "--all", "-rf"]).
    """
    try:
        parts = shlex.split(command)
        flags = []
        for part in parts[1:]:  # Skip executable
            if part.startswith("-"):
                # Handle combined short flags like -rf
                if part.startswith("--"):
                    # Long flag
                    flag = part.split("=")[0]  # Handle --flag=value
                    flags.append(flag)
                elif len(part) > 2:
                    # Combined short flags like -rf -> -r, -f
                    for char in part[1:]:
                        flags.append(f"-{char}")
                else:
                    flags.append(part)
        return flags
    except ValueError:
        return []


def _check_flags_exist(command: str) -> tuple[bool, list[str], list[str]]:
    """Check if flags exist in Man Vault documentation.

    Args:
        command: Command string.

    Returns:
        Tuple of (all_verified, verified_flags, unknown_flags).
    """
    try:
        parts = shlex.split(command)
        if not parts:
            return True, [], []

        executable = parts[0]
        flags = _extract_flags(command)

        if not flags:
            return True, [], []

        # Try to import manvault
        try:
            from elle.daemon.manvault.retriever import flag_exists
        except ImportError:
            # Man Vault not available, can't verify
            return True, [], flags

        verified = []
        unknown = []

        for flag in flags:
            try:
                if flag_exists(executable, flag):
                    verified.append(flag)
                else:
                    unknown.append(flag)
            except Exception:
                # Can't verify, mark as unknown
                unknown.append(flag)

        all_verified = len(unknown) == 0
        return all_verified, verified, unknown

    except ValueError:
        return True, [], []


def _check_bash_syntax(command: str) -> tuple[bool, str | None]:
    """Check if command has valid bash syntax using bash -n.

    Args:
        command: Command string.

    Returns:
        Tuple of (is_valid, error_message).
    """
    try:
        result = subprocess.run(
            ["bash", "-n", "-c", command],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return True, None
        return False, f"Syntax error: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return True, None  # Assume OK if check times out
    except OSError:
        return True, None  # Can't run bash, skip check


# =============================================================================
# Verification Pipeline
# =============================================================================


def verify_command(fix: FixCommand) -> VerifiedFixCommand:
    """Run full verification pipeline on a fix command.

    Checks:
    1. Denylist - critical (blocks command)
    2. Parse - critical (blocks command)
    3. Executable exists - critical (blocks command)
    4. Flag verification - warning (doesn't block)
    5. Bash syntax - warning (doesn't block)

    Args:
        fix: The fix command to verify.

    Returns:
        VerifiedFixCommand with verification results.
    """
    command = fix.command
    passed = []
    failed = []
    warnings = []

    # 1. Denylist check (critical)
    denylist_ok, denylist_err = _check_denylist_safe(command)
    if denylist_ok:
        passed.append("denylist")
    else:
        failed.append(f"denylist: {denylist_err}")

    # 2. Parse check (critical)
    parse_ok, parse_err = _check_parse(command)
    if parse_ok:
        passed.append("parse")
    else:
        failed.append(f"parse: {parse_err}")

    # 3. Executable check (critical)
    exec_ok, exec_err = _check_executable_exists(command)
    if exec_ok:
        passed.append("executable")
    else:
        failed.append(f"executable: {exec_err}")

    # 4. Flag check (warning only)
    flags_ok, verified_flags, unknown_flags = _check_flags_exist(command)
    if flags_ok:
        passed.append("flags")
    else:
        # Not a failure, just a warning
        warnings.append(f"Unknown flags (may be valid): {', '.join(unknown_flags)}")
        if verified_flags:
            passed.append(f"flags ({len(verified_flags)} verified)")

    # 5. Bash syntax check (warning only)
    syntax_ok, syntax_err = _check_bash_syntax(command)
    if syntax_ok:
        passed.append("syntax")
    else:
        warnings.append(f"Bash warning: {syntax_err}")

    # Determine overall status
    if failed:
        status = VerificationStatus.FAILED
    elif warnings:
        status = VerificationStatus.WARNING
    else:
        status = VerificationStatus.PASSED

    # Command is safe if no critical checks failed
    is_safe = len(failed) == 0

    verification = VerificationResult(
        command=command,
        status=status,
        checks_passed=tuple(passed),
        checks_failed=tuple(failed),
        warnings=tuple(warnings),
    )

    return VerifiedFixCommand(
        fix=fix,
        verification=verification,
        is_safe=is_safe,
    )


def verify_analysis(
    analysis: FixitAnalysis,
) -> tuple[tuple[VerifiedFixCommand, ...], tuple[str, ...]]:
    """Verify all suggestions in an analysis.

    Args:
        analysis: The fixit analysis with suggestions.

    Returns:
        Tuple of (verified_suggestions, errors).
        verified_suggestions includes all commands (safe and unsafe).
        errors contains messages for commands that failed verification.
    """
    verified = []
    errors = []

    for fix in analysis.suggestions:
        result = verify_command(fix)
        verified.append(result)

        if not result.is_safe:
            # Collect error messages
            for err in result.verification.checks_failed:
                errors.append(f"{fix.command}: {err}")

    return tuple(verified), tuple(errors)


def filter_safe_suggestions(
    verified: tuple[VerifiedFixCommand, ...],
) -> tuple[VerifiedFixCommand, ...]:
    """Filter to only safe suggestions.

    Args:
        verified: All verified suggestions.

    Returns:
        Only suggestions that passed critical checks.
    """
    return tuple(v for v in verified if v.is_safe)
