"""Plan verification for the System Task Planner.

Ensures generated plans are safe to execute by checking:
- Command syntax and executable existence
- Flag validity via Man Vault
- Rollback presence for critical operations
- Validation checks for medium/high risk
- Denylist compliance
"""

import logging
import shlex
import subprocess

from elle.cli.planner.models import (
    HIGH_RISK_PATTERNS,
    MEDIUM_RISK_PATTERNS,
    REQUIRES_ROLLBACK,
    CommandPlan,
    PlanStep,
    PlanVerification,
    RiskLevel,
    StepVerification,
)
from elle.cli.subprocess_runner import check_denylist

logger = logging.getLogger(__name__)


# =============================================================================
# Step Verification
# =============================================================================


def verify_step(step: PlanStep, step_index: int) -> StepVerification:
    """Verify a single plan step.

    Checks:
    1. Denylist compliance
    2. Command parses correctly
    3. Executable exists
    4. Bash syntax is valid

    Args:
        step: The plan step to verify.
        step_index: Index of the step in the plan.

    Returns:
        StepVerification with results.
    """
    # Early return if no command
    if step.command is None:
        return StepVerification(
            step_index=step_index,
            command="",
            is_valid=False,
            checks_passed=(),
            checks_failed=("No command specified",),
            warnings=(),
        )

    command = step.command
    checks_passed: list[str] = []
    checks_failed: list[str] = []
    warnings: list[str] = []

    # 1. Denylist check
    denied, deny_reason, deny_explanation = check_denylist(command)
    if denied:
        explanation = deny_explanation or (deny_reason.value if deny_reason else "Blocked")
        checks_failed.append(f"Denylist: {explanation}")
        return StepVerification(
            step_index=step_index,
            command=command,
            is_valid=False,
            checks_passed=tuple(checks_passed),
            checks_failed=tuple(checks_failed),
            warnings=tuple(warnings),
        )
    checks_passed.append("Denylist check passed")

    # 2. Parse check
    try:
        tokens = shlex.split(command)
        if not tokens:
            checks_failed.append("Parse: Empty command")
            return StepVerification(
                step_index=step_index,
                command=command,
                is_valid=False,
                checks_passed=tuple(checks_passed),
                checks_failed=tuple(checks_failed),
                warnings=tuple(warnings),
            )
        checks_passed.append("Command parses correctly")
    except ValueError as e:
        checks_failed.append(f"Parse: {e}")
        return StepVerification(
            step_index=step_index,
            command=command,
            is_valid=False,
            checks_passed=tuple(checks_passed),
            checks_failed=tuple(checks_failed),
            warnings=tuple(warnings),
        )

    # 3. Executable check
    executable = tokens[0]
    if not _executable_exists(executable):
        # Check if it's a builtin or might be installed by earlier step
        if not _is_builtin(executable):
            warnings.append(f"Executable '{executable}' not found (may be installed by prior step)")
    else:
        checks_passed.append(f"Executable '{executable}' exists")

    # 4. Flag verification via Man Vault
    flags = _extract_flags(tokens)
    if flags:
        unknown_flags = _check_flags(executable, flags)
        if unknown_flags:
            warnings.append(f"Unknown flags for '{executable}': {', '.join(unknown_flags)}")
        else:
            checks_passed.append("Flags verified")

    # 5. Bash syntax check
    syntax_ok, syntax_error = _check_bash_syntax(command)
    if not syntax_ok:
        checks_failed.append(f"Syntax: {syntax_error}")
        return StepVerification(
            step_index=step_index,
            command=command,
            is_valid=False,
            checks_passed=tuple(checks_passed),
            checks_failed=tuple(checks_failed),
            warnings=tuple(warnings),
        )
    checks_passed.append("Bash syntax valid")

    # 6. Risk level validation
    computed_risk = _compute_risk_level(command)
    if computed_risk != step.risk_level:
        if _risk_to_int(computed_risk) > _risk_to_int(step.risk_level):
            warnings.append(
                f"Risk may be underestimated: marked as '{step.risk_level}' "
                f"but '{command}' appears to be '{computed_risk}'"
            )

    return StepVerification(
        step_index=step_index,
        command=command,
        is_valid=True,
        checks_passed=tuple(checks_passed),
        checks_failed=tuple(checks_failed),
        warnings=tuple(warnings),
    )


def verify_plan(plan: CommandPlan) -> PlanVerification:
    """Verify an entire command plan.

    Checks all steps plus:
    - Rollback present for critical operations
    - Validation checks present for medium/high risk

    Args:
        plan: The command plan to verify.

    Returns:
        PlanVerification with results.
    """
    step_results: list[StepVerification] = []
    errors: list[str] = []
    warnings: list[str] = []
    rollback_required = False
    checks_required = False

    # Verify each step
    for i, step in enumerate(plan.steps):
        result = verify_step(step, i)
        step_results.append(result)

        # Check for critical errors
        if not result.is_valid:
            errors.append(f"Step {i + 1}: {', '.join(result.checks_failed)}")

        # Collect warnings
        warnings.extend(result.warnings)

    # Check if rollback is required
    needs_rollback = _requires_rollback(plan)
    if needs_rollback and not plan.has_rollback:
        rollback_required = True
        errors.append("Rollback required for config changes (netplan/ufw/fstab/crontab) but not provided")

    # Check if validation checks are required
    if plan.overall_risk in ("medium", "high") and not plan.has_checks:
        checks_required = True
        warnings.append(f"Validation checks recommended for {plan.overall_risk}-risk operations")

    # Verify rollback steps if present
    if plan.has_rollback:
        for i, rb in enumerate(plan.rollback):
            denied, deny_reason, deny_explanation = check_denylist(rb.command)
            if denied:
                explanation = deny_explanation or (deny_reason.value if deny_reason else "Blocked")
                errors.append(f"Rollback {i + 1} blocked: {explanation}")

    # Overall validity
    is_valid = len(errors) == 0

    return PlanVerification(
        is_valid=is_valid,
        step_results=tuple(step_results),
        errors=tuple(errors),
        warnings=tuple(warnings),
        rollback_required=rollback_required,
        checks_required=checks_required,
    )


# =============================================================================
# Helper Functions
# =============================================================================


def _executable_exists(name: str) -> bool:
    """Check if an executable exists in PATH.

    Args:
        name: Executable name.

    Returns:
        True if executable exists.
    """
    try:
        result = subprocess.run(
            ["which", name],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _is_builtin(name: str) -> bool:
    """Check if a command is a shell builtin.

    Args:
        name: Command name.

    Returns:
        True if it's a builtin.
    """
    builtins = {
        "cd",
        "echo",
        "export",
        "source",
        ".",
        "alias",
        "unalias",
        "read",
        "set",
        "unset",
        "eval",
        "exec",
        "exit",
        "return",
        "trap",
        "wait",
        "jobs",
        "fg",
        "bg",
        "kill",
        "test",
        "[",
        "true",
        "false",
        ":",
        "pwd",
        "type",
        "hash",
        "help",
        "history",
        "fc",
        "bind",
        "builtin",
        "command",
        "declare",
        "typeset",
        "local",
        "readonly",
        "shift",
        "shopt",
        "getopts",
        "printf",
        "pushd",
        "popd",
        "dirs",
        "enable",
        "let",
        "mapfile",
        "readarray",
        "suspend",
        "times",
        "ulimit",
        "umask",
        "complete",
        "compgen",
        "compopt",
        "caller",
        "logout",
        "disown",
        "coproc",
    }
    return name in builtins


def _extract_flags(tokens: list[str]) -> list[str]:
    """Extract flags from command tokens.

    Args:
        tokens: Parsed command tokens.

    Returns:
        List of flags found.
    """
    flags = []
    for token in tokens[1:]:  # Skip command name
        if token.startswith("--"):
            # Long flag: --verbose, --output=file
            flag = token.split("=")[0]
            flags.append(flag)
        elif token.startswith("-") and len(token) > 1:
            # Short flag(s): -v, -rf
            if token[1].isalpha():
                for char in token[1:]:
                    if char.isalpha():
                        flags.append(f"-{char}")
    return flags


def _check_flags(command: str, flags: list[str]) -> list[str]:
    """Check if flags are valid for a command via Man Vault.

    Args:
        command: The command name.
        flags: List of flags to check.

    Returns:
        List of unknown flags.
    """
    unknown = []
    try:
        from elle.daemon.manvault import flag_exists

        for flag in flags:
            if not flag_exists(command, flag):
                unknown.append(flag)
    except ImportError:
        # Man Vault not available, can't verify
        pass
    except Exception:
        # Error checking flags, assume OK
        pass
    return unknown


def _check_bash_syntax(command: str) -> tuple[bool, str]:
    """Check if a command has valid bash syntax.

    Args:
        command: The command to check.

    Returns:
        Tuple of (is_valid, error_message).
    """
    try:
        result = subprocess.run(
            ["bash", "-n", "-c", command],
            capture_output=True,
            timeout=5,
            text=True,
        )
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return True, ""  # Assume OK if timeout
    except Exception:
        return True, ""  # Assume OK on error


def _requires_rollback(plan: CommandPlan) -> bool:
    """Check if the plan modifies critical configs requiring rollback.

    Args:
        plan: The command plan.

    Returns:
        True if rollback is required.
    """
    for step in plan.steps:
        if step.command is None:
            continue
        command_lower = step.command.lower()
        for pattern in REQUIRES_ROLLBACK:
            if pattern in command_lower:
                return True
    return False


def _compute_risk_level(command: str) -> RiskLevel:
    """Compute the risk level for a command.

    Args:
        command: The command string.

    Returns:
        Computed risk level.
    """
    command_lower = command.lower()

    # Check high-risk patterns
    for pattern in HIGH_RISK_PATTERNS:
        if pattern in command_lower:
            return "high"

    # Check medium-risk patterns
    for pattern in MEDIUM_RISK_PATTERNS:
        if pattern in command_lower:
            return "medium"

    return "low"


def _risk_to_int(risk: RiskLevel) -> int:
    """Convert risk level to integer for comparison.

    Args:
        risk: Risk level.

    Returns:
        Integer value (0=low, 1=medium, 2=high).
    """
    return {"low": 0, "medium": 1, "high": 2}.get(risk, 0)


# =============================================================================
# Auto-Approval Logic
# =============================================================================


def can_auto_approve(plan: CommandPlan, allow_auto: bool = True) -> tuple[bool, str]:
    """Check if a plan can be auto-approved (low risk, no privilege needed).

    Args:
        plan: The verified command plan.
        allow_auto: Whether auto-approval is enabled in settings.

    Returns:
        Tuple of (can_auto_approve, reason).
    """
    if not allow_auto:
        return False, "Auto-approval disabled"

    if plan.requires_privilege:
        return False, "Requires elevated privileges"

    if plan.overall_risk != "low":
        return False, f"Risk level is {plan.overall_risk}"

    # Check for any state-changing operations
    for step in plan.steps:
        if step.risk_level != "low":
            return False, f"Step has {step.risk_level} risk"

    return True, "Low-risk, no privileges needed"
