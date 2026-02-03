"""Safe subprocess execution for ELLE.

Provides a secure wrapper around subprocess execution with:
- Timeouts (default 30s, configurable)
- Output capture (stdout/stderr)
- Streaming output option
- Exit code handling
- No implicit sudo
- Policy-based denylist for dangerous commands

This module is the foundation for shell passthrough and provides
the "last failed command" substrate for the fixit feature.

Note: Command denial is now handled by the Policy Engine.
The DenyReason enum is retained for backwards compatibility.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable
from enum import Enum, auto
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class RunMode(Enum):
    """How to handle subprocess output."""

    CAPTURE = auto()  # Capture all output, return when complete
    STREAM = auto()  # Stream output to callback/stdout as it arrives


class DenyReason(Enum):
    """Reason a command was denied."""

    DESTRUCTIVE_RM = auto()
    FILESYSTEM_FORMAT = auto()
    RAW_DISK_WRITE = auto()
    FORK_BOMB = auto()
    RECURSIVE_PERMISSION = auto()
    PIPE_TO_SHELL = auto()
    SUDO_ATTEMPT = auto()
    SYSTEM_SHUTDOWN = auto()
    HISTORY_CLEAR = auto()


class CommandDeniedError(Exception):
    """Raised when a command is blocked by the denylist."""

    def __init__(self, command: str, reason: DenyReason, explanation: str) -> None:
        self.command = command
        self.reason = reason
        self.explanation = explanation
        super().__init__(f"Command denied ({reason.name}): {explanation}")


class SubprocessResult(BaseModel):
    """Result of a subprocess execution.

    Captures all information needed for debugging and fixit.
    """

    model_config = ConfigDict(frozen=True)

    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    denied: bool = False
    deny_reason: DenyReason | None = None
    deny_explanation: str | None = None

    @property
    def success(self) -> bool:
        """Check if command succeeded (exit code 0, not denied/timed out)."""
        return self.exit_code == 0 and not self.timed_out and not self.denied

    @property
    def failed(self) -> bool:
        """Check if command failed."""
        return not self.success


# =============================================================================
# Denylist patterns
# =============================================================================

# Patterns that match dangerous rm commands
_RM_DANGEROUS_PATTERNS = [
    r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?(-[a-zA-Z]*r[a-zA-Z]*\s+)?/\s*$",  # rm -rf /
    r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+)?(-[a-zA-Z]*f[a-zA-Z]*\s+)?/\s*$",  # rm -fr /
    r"rm\s+.*--no-preserve-root",  # rm with --no-preserve-root
    r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?(-[a-zA-Z]*r[a-zA-Z]*\s+)?/\*",  # rm -rf /*
    r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+)?(-[a-zA-Z]*f[a-zA-Z]*\s+)?/\*",  # rm -fr /*
    r"rm\s+.*\s+~\s*$",  # rm ~ (home directory)
    r"rm\s+.*\s+/home\s*$",  # rm /home
    r"rm\s+.*\s+/etc\s*$",  # rm /etc
    r"rm\s+.*\s+/usr\s*$",  # rm /usr
    r"rm\s+.*\s+/var\s*$",  # rm /var
    r"rm\s+.*\s+/boot\s*$",  # rm /boot
]

# Fork bomb patterns
_FORK_BOMB_PATTERNS = [
    r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",  # :(){:|:&};: (with optional spaces)
    r"\.\/\s*&\s*\.\/\s*&",  # ./& ./&
    r"fork\s*while.*fork",  # perl/python fork bombs
    r"while\s*true.*do.*&.*done",  # bash while true fork
]

# Commands that format filesystems
_FORMAT_COMMANDS = [
    r"^mkfs\b",
    r"^mkswap\b",
    r"^mke2fs\b",
    r"^mkdosfs\b",
    r"^mkntfs\b",
]

# dd with dangerous output targets
_DD_DANGEROUS_PATTERNS = [
    r"dd\s+.*of=/dev/[a-z]+\b",  # dd to block device
    r"dd\s+.*of=/dev/sd[a-z]",  # dd to disk
    r"dd\s+.*of=/dev/nvme",  # dd to nvme
    r"dd\s+.*of=/dev/vd[a-z]",  # dd to virtual disk
]

# Recursive permission changes on system dirs
_RECURSIVE_PERM_PATTERNS = [
    r"chmod\s+(-[a-zA-Z]*R[a-zA-Z]*\s+).*\s+/\s*$",
    r"chmod\s+(-[a-zA-Z]*R[a-zA-Z]*\s+).*\s+/etc",
    r"chmod\s+(-[a-zA-Z]*R[a-zA-Z]*\s+).*\s+/usr",
    r"chmod\s+(-[a-zA-Z]*R[a-zA-Z]*\s+).*\s+/var",
    r"chmod\s+.*\s+777\s+/",
    r"chown\s+(-[a-zA-Z]*R[a-zA-Z]*\s+).*\s+/\s*$",
    r"chown\s+(-[a-zA-Z]*R[a-zA-Z]*\s+).*\s+/etc",
    r"chown\s+(-[a-zA-Z]*R[a-zA-Z]*\s+).*\s+/usr",
]

# Piping to shell (common attack vector)
_PIPE_TO_SHELL_PATTERNS = [
    r"curl\s+.*\|\s*(ba)?sh",
    r"wget\s+.*\|\s*(ba)?sh",
    r"curl\s+.*\|\s*sudo",
    r"wget\s+.*\|\s*sudo",
    r"\|\s*bash\s+-c",
    r"\|\s*sh\s+-c",
]

# Shutdown/reboot commands
_SHUTDOWN_PATTERNS = [
    r"^shutdown\b",
    r"^reboot\b",
    r"^poweroff\b",
    r"^halt\b",
    r"^init\s+[0156]",
    r"^systemctl\s+(reboot|poweroff|halt)",
]

# History manipulation (covers tracks)
_HISTORY_CLEAR_PATTERNS = [
    r"history\s+-c",
    r">\s*~/\.bash_history",
    r"rm\s+.*\.bash_history",
    r"rm\s+.*\.zsh_history",
    r"export\s+HISTSIZE=0",
    r"unset\s+HISTFILE",
]


def _compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    """Compile regex patterns for efficient matching."""
    return [re.compile(p, re.IGNORECASE) for p in patterns]


# Pre-compiled pattern lists
_COMPILED_RM = _compile_patterns(_RM_DANGEROUS_PATTERNS)
_COMPILED_FORK = _compile_patterns(_FORK_BOMB_PATTERNS)
_COMPILED_FORMAT = _compile_patterns(_FORMAT_COMMANDS)
_COMPILED_DD = _compile_patterns(_DD_DANGEROUS_PATTERNS)
_COMPILED_PERM = _compile_patterns(_RECURSIVE_PERM_PATTERNS)
_COMPILED_PIPE = _compile_patterns(_PIPE_TO_SHELL_PATTERNS)
_COMPILED_SHUTDOWN = _compile_patterns(_SHUTDOWN_PATTERNS)
_COMPILED_HISTORY = _compile_patterns(_HISTORY_CLEAR_PATTERNS)


def check_denylist(command: str) -> tuple[bool, DenyReason | None, str | None]:
    """Check if a command matches the denylist via the Policy Engine.

    This function delegates to the Policy Engine for evaluation.
    The DenyReason is inferred from matched rule IDs for backwards
    compatibility with existing code that depends on specific reasons.

    Args:
        command: The command string to check.

    Returns:
        Tuple of (is_denied, reason, explanation).
    """
    try:
        from elle.policy import PolicyEffect, evaluate_command

        result = evaluate_command(command, audit=True)

        if result.effect == PolicyEffect.DENY:
            # Map matched rules to DenyReason for backwards compatibility
            reason = _map_rules_to_deny_reason(result.matched_rules)
            return (True, reason, result.message)

        return (False, None, None)

    except ImportError:
        # Fallback to legacy check if policy module not available
        logger.warning("Policy module not available, using legacy denylist")
        return _check_denylist_legacy(command)
    except Exception as e:
        # On any policy evaluation error, fall back to legacy
        logger.warning(f"Policy evaluation failed: {e}, using legacy denylist")
        return _check_denylist_legacy(command)


def _map_rules_to_deny_reason(matched_rules: tuple[str, ...]) -> DenyReason:
    """Map policy rule IDs to DenyReason enum.

    Args:
        matched_rules: Tuple of matched rule IDs.

    Returns:
        DenyReason based on matched rules.
    """
    if not matched_rules:
        return DenyReason.DESTRUCTIVE_RM  # Default

    # Check the first matched rule (highest priority)
    rule_id = matched_rules[0]

    rule_mapping = {
        "deny-sudo": DenyReason.SUDO_ATTEMPT,
        "deny-destructive-rm": DenyReason.DESTRUCTIVE_RM,
        "deny-fork-bomb": DenyReason.FORK_BOMB,
        "deny-filesystem-format": DenyReason.FILESYSTEM_FORMAT,
        "deny-raw-disk-write": DenyReason.RAW_DISK_WRITE,
        "deny-recursive-perm": DenyReason.RECURSIVE_PERMISSION,
        "deny-pipe-to-shell": DenyReason.PIPE_TO_SHELL,
        "deny-shutdown": DenyReason.SYSTEM_SHUTDOWN,
        "deny-history-clear": DenyReason.HISTORY_CLEAR,
    }

    # Check for rule prefix matches
    for prefix, reason in rule_mapping.items():
        if rule_id.startswith(prefix):
            return reason

    return DenyReason.DESTRUCTIVE_RM  # Default fallback


def _check_denylist_legacy(command: str) -> tuple[bool, DenyReason | None, str | None]:
    """Legacy denylist check using hardcoded patterns.

    Used as fallback when policy engine is unavailable.

    Args:
        command: The command string to check.

    Returns:
        Tuple of (is_denied, reason, explanation).
    """
    # Normalize command for checking
    cmd_normalized = command.strip()
    # M18: Normalize escaped spaces (e.g. "rm\ -rf" -> "rm -rf")
    cmd_normalized = cmd_normalized.replace("\\ ", " ")
    # Collapse whitespace to defeat spacing evasion (e.g. "rm  -rf /")
    cmd_normalized = re.sub(r"\s+", " ", cmd_normalized)
    # Strip inline comments that could hide payloads
    cmd_normalized = re.sub(r"\s+#[^'\"]*$", "", cmd_normalized)

    # Detect command substitution / variable expansion evasion
    if re.search(r"\$\(|\$\{|`[^`]+`", cmd_normalized):
        return (
            True,
            DenyReason.PIPE_TO_SHELL,
            "Command contains shell expansion/substitution which could bypass safety checks.",
        )

    # Check for sudo at start
    if cmd_normalized.startswith("sudo "):
        return (
            True,
            DenyReason.SUDO_ATTEMPT,
            "ELLE does not execute commands with sudo. Privileged operations go through Polkit authorization.",
        )

    # Check each pattern category
    checks: list[tuple[list[re.Pattern[str]], DenyReason, str]] = [
        (
            _COMPILED_RM,
            DenyReason.DESTRUCTIVE_RM,
            "This rm command could destroy critical system files or your entire filesystem.",
        ),
        (
            _COMPILED_FORK,
            DenyReason.FORK_BOMB,
            "This appears to be a fork bomb that would crash your system.",
        ),
        (
            _COMPILED_FORMAT,
            DenyReason.FILESYSTEM_FORMAT,
            "Filesystem formatting commands require explicit confirmation outside ELLE.",
        ),
        (
            _COMPILED_DD,
            DenyReason.RAW_DISK_WRITE,
            "Writing directly to block devices can destroy data. Use specific tools instead.",
        ),
        (
            _COMPILED_PERM,
            DenyReason.RECURSIVE_PERMISSION,
            "Recursive permission changes on system directories can break your system.",
        ),
        (
            _COMPILED_PIPE,
            DenyReason.PIPE_TO_SHELL,
            "Piping remote content to a shell is a security risk. Download and inspect first.",
        ),
        (
            _COMPILED_SHUTDOWN,
            DenyReason.SYSTEM_SHUTDOWN,
            "System shutdown/reboot commands should be run directly, not through ELLE.",
        ),
        (
            _COMPILED_HISTORY,
            DenyReason.HISTORY_CLEAR,
            "Clearing shell history is often used to cover tracks after malicious activity.",
        ),
    ]

    for patterns, reason, explanation in checks:
        for pattern in patterns:
            if pattern.search(cmd_normalized):
                return (True, reason, explanation)

    return (False, None, None)


# =============================================================================
# Subprocess execution
# =============================================================================

# Default timeout in seconds
DEFAULT_TIMEOUT = 30.0

# Maximum output size to capture (10MB)
MAX_OUTPUT_SIZE = 10 * 1024 * 1024


def run(
    command: str,
    *,
    cwd: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    mode: RunMode = RunMode.CAPTURE,
    stream_callback: Callable[[str], None] | None = None,
    check_denylist_flag: bool = True,
    env: dict[str, str] | None = None,
) -> SubprocessResult:
    """Execute a shell command safely.

    Args:
        command: The command to execute (will be run through shell).
        cwd: Working directory for the command.
        timeout: Maximum execution time in seconds.
        mode: CAPTURE to collect output, STREAM to output in real-time.
        stream_callback: Function to call with output lines (for STREAM mode).
        check_denylist_flag: Whether to check against the denylist.
        env: Additional environment variables.

    Returns:
        SubprocessResult with exit code, stdout, stderr, and status.

    Raises:
        CommandDeniedError: If the command matches the denylist.
    """
    # Check denylist first
    if check_denylist_flag:
        denied, reason, explanation = check_denylist(command)
        if denied and reason and explanation:
            raise CommandDeniedError(command, reason, explanation)
    else:
        logger.warning("Denylist bypass requested for: %s", command[:100])

    # Determine working directory
    working_dir = cwd or Path.cwd()

    # Set up environment
    run_env = None
    if env:
        import os

        run_env = {**os.environ, **env}

    try:
        if mode == RunMode.STREAM:
            return _run_streaming(
                command,
                working_dir,
                timeout,
                stream_callback,
                run_env,
            )
        else:
            return _run_capture(command, working_dir, timeout, run_env)

    except subprocess.TimeoutExpired:
        return SubprocessResult(
            command=command,
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout} seconds",
            timed_out=True,
        )


def _run_capture(
    command: str,
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None,
) -> SubprocessResult:
    """Run command and capture all output."""
    result = subprocess.run(
        command,
        shell=True,  # nosec B602 - commands validated through denylist before execution
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )

    # M17: Strip null bytes from output
    stdout = result.stdout.replace("\x00", "")
    stderr = result.stderr.replace("\x00", "")

    # Truncate output to 1MB
    _MAX_CAPTURE_SIZE = 1 * 1024 * 1024  # 1MB
    if len(stdout) > _MAX_CAPTURE_SIZE:
        stdout = stdout[:_MAX_CAPTURE_SIZE] + "\n... (output truncated)"
    if len(stderr) > _MAX_CAPTURE_SIZE:
        stderr = stderr[:_MAX_CAPTURE_SIZE] + "\n... (output truncated)"

    return SubprocessResult(
        command=command,
        exit_code=result.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _run_streaming(
    command: str,
    cwd: Path,
    timeout: float,
    callback: Callable[[str], None] | None,
    env: dict[str, str] | None,
) -> SubprocessResult:
    """Run command and stream output in real-time."""
    import select

    # Default callback prints to stdout
    if callback is None:

        def default_callback(line: str) -> None:
            print(line, end="", flush=True)

        callback = default_callback

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    process = subprocess.Popen(
        command,
        shell=True,  # nosec B602 - commands validated through denylist before execution
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    try:
        # Use select for non-blocking reads on Unix
        import time

        start_time = time.time()

        while process.poll() is None:
            # Check timeout
            if time.time() - start_time > timeout:
                process.kill()
                process.wait()
                return SubprocessResult(
                    command=command,
                    exit_code=-1,
                    stdout="".join(stdout_lines),
                    stderr="".join(stderr_lines) + f"\nCommand timed out after {timeout} seconds",
                    timed_out=True,
                )

            # Check for readable output
            streams_to_watch = [s for s in [process.stdout, process.stderr] if s is not None]
            readable, _, _ = select.select(
                streams_to_watch,
                [],
                [],
                0.1,  # 100ms poll interval
            )

            for stream in readable:
                if stream is None:
                    continue
                line = stream.readline()
                if line:
                    if stream == process.stdout:
                        stdout_lines.append(line)
                        callback(line)
                    else:
                        stderr_lines.append(line)
                        # Also stream stderr through callback
                        callback(line)

        # Read any remaining output
        remaining_stdout, remaining_stderr = process.communicate(timeout=1.0)
        if remaining_stdout:
            stdout_lines.append(remaining_stdout)
            callback(remaining_stdout)
        if remaining_stderr:
            stderr_lines.append(remaining_stderr)
            callback(remaining_stderr)

    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return SubprocessResult(
            command=command,
            exit_code=-1,
            stdout="".join(stdout_lines),
            stderr="".join(stderr_lines) + f"\nCommand timed out after {timeout} seconds",
            timed_out=True,
        )

    return SubprocessResult(
        command=command,
        exit_code=process.returncode or 0,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
    )


def run_safe(command: str, **kwargs: Any) -> SubprocessResult:
    """Execute a command, returning a denied result instead of raising.

    This is a convenience wrapper around run() that catches CommandDeniedError
    and returns it as a SubprocessResult.

    Args:
        command: The command to execute.
        **kwargs: Additional arguments passed to run().

    Returns:
        SubprocessResult (may have denied=True if command was blocked).
    """
    try:
        return run(command, **kwargs)
    except CommandDeniedError as e:
        return SubprocessResult(
            command=command,
            exit_code=-1,
            stdout="",
            stderr=e.explanation,
            denied=True,
            deny_reason=e.reason,
            deny_explanation=e.explanation,
        )


# Patterns that are unsafe when appearing in LLM-generated commands
_LLM_UNSAFE_PATTERN = re.compile(r"[;|`$]")
_LLM_KNOWN_SAFE = re.compile(
    r"^(cat|head|tail|grep|ls|stat|file|wc|df|du|free|uptime|"
    r"uname|hostname|id|whoami|date|ps|top|lsof|ss|ip|journalctl|"
    r"systemctl\s+(status|is-active|is-enabled|show)|dpkg\s+-[lLsS]|"
    r"apt\s+list|pip\s+list)\b"
)


def run_llm_command(command: str, **kwargs: Any) -> SubprocessResult:
    """Run a command generated by the LLM with stricter validation.

    In addition to the standard denylist, this rejects commands with
    shell meta-characters (pipes, semicolons, backticks, variable
    expansion) unless the command matches a known-safe pattern.

    Args:
        command: The command to execute.
        **kwargs: Additional arguments passed to run().

    Returns:
        SubprocessResult (may have denied=True if command was blocked).
    """
    if _LLM_UNSAFE_PATTERN.search(command) and not _LLM_KNOWN_SAFE.match(command):
        return SubprocessResult(
            command=command,
            exit_code=-1,
            stdout="",
            stderr="Command contains unsafe characters for LLM-generated execution",
            denied=True,
        )
    return run_safe(command, **kwargs)
