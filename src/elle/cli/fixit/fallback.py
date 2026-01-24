"""Rule-based fallback analysis for Fixit.

Provides pattern-matching based error analysis when the LLM
is unavailable. Covers common error categories with
pre-defined suggestions.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from elle.cli.fixit.models import (
    ErrorCategory,
    FixCommand,
    FixitAnalysis,
    FixitDiagnosis,
)

if TYPE_CHECKING:
    from elle.cli.fixit.models import FixitContext


# =============================================================================
# Pattern Definitions
# =============================================================================

# Patterns that match common errors
ERROR_PATTERNS: list[tuple[re.Pattern, ErrorCategory, str]] = [
    # Command not found
    (re.compile(r"command not found", re.I), "command_not_found",
     "The command was not found in your PATH."),
    (re.compile(r"not found", re.I), "command_not_found",
     "The command or executable was not found."),
    (re.compile(r"No such file or directory.*executable", re.I), "command_not_found",
     "The executable file does not exist."),

    # Permission denied
    (re.compile(r"permission denied", re.I), "permission_denied",
     "You don't have permission to perform this operation."),
    (re.compile(r"access denied", re.I), "permission_denied",
     "Access to the resource was denied."),
    (re.compile(r"Operation not permitted", re.I), "permission_denied",
     "The operation is not permitted for your user."),
    (re.compile(r"Authentication failed", re.I), "permission_denied",
     "Authentication failed - check your credentials."),

    # File not found
    (re.compile(r"No such file or directory", re.I), "file_not_found",
     "The specified file or directory does not exist."),
    (re.compile(r"cannot stat", re.I), "file_not_found",
     "Unable to access the file or directory."),
    (re.compile(r"does not exist", re.I), "file_not_found",
     "The specified path does not exist."),
    (re.compile(r"ENOENT", re.I), "file_not_found",
     "The file or directory was not found (ENOENT)."),

    # Syntax errors
    (re.compile(r"syntax error", re.I), "syntax_error",
     "There is a syntax error in the command."),
    (re.compile(r"unexpected token", re.I), "syntax_error",
     "Unexpected token in the command syntax."),
    (re.compile(r"parse error", re.I), "syntax_error",
     "The command could not be parsed."),

    # Argument errors
    (re.compile(r"invalid option", re.I), "argument_error",
     "An invalid option or flag was provided."),
    (re.compile(r"unrecognized option", re.I), "argument_error",
     "The command does not recognize this option."),
    (re.compile(r"missing argument", re.I), "argument_error",
     "A required argument is missing."),
    (re.compile(r"requires an argument", re.I), "argument_error",
     "An option requires an argument that was not provided."),
    (re.compile(r"too many arguments", re.I), "argument_error",
     "Too many arguments were provided."),

    # Dependency missing
    (re.compile(r"Unable to locate package", re.I), "dependency_missing",
     "The package could not be found in the repository."),
    (re.compile(r"Package .* is not available", re.I), "dependency_missing",
     "The requested package is not available."),
    (re.compile(r"unmet dependencies", re.I), "dependency_missing",
     "There are unmet package dependencies."),
    (re.compile(r"Depends:.*but it is not", re.I), "dependency_missing",
     "A required dependency is missing."),

    # Resource exhausted
    (re.compile(r"No space left on device", re.I), "resource_exhausted",
     "The disk is full."),
    (re.compile(r"Cannot allocate memory", re.I), "resource_exhausted",
     "Not enough memory available."),
    (re.compile(r"Too many open files", re.I), "resource_exhausted",
     "File descriptor limit reached."),
    (re.compile(r"quota exceeded", re.I), "resource_exhausted",
     "Disk quota has been exceeded."),

    # Network errors
    (re.compile(r"Connection refused", re.I), "network_error",
     "The connection was refused by the remote host."),
    (re.compile(r"Connection timed out", re.I), "network_error",
     "The connection attempt timed out."),
    (re.compile(r"Network is unreachable", re.I), "network_error",
     "The network is not reachable."),
    (re.compile(r"Name or service not known", re.I), "network_error",
     "DNS resolution failed for the hostname."),
    (re.compile(r"Could not resolve host", re.I), "network_error",
     "Unable to resolve the hostname."),
    (re.compile(r"Temporary failure in name resolution", re.I), "network_error",
     "DNS lookup temporarily failed."),

    # Configuration errors
    (re.compile(r"Invalid configuration", re.I), "configuration_error",
     "The configuration is invalid."),
    (re.compile(r"configuration error", re.I), "configuration_error",
     "There is an error in the configuration."),
    (re.compile(r"Failed to parse", re.I), "configuration_error",
     "Failed to parse configuration file."),
]


# Suggested fixes for each error category
CATEGORY_FIXES: dict[ErrorCategory, list[dict]] = {
    "command_not_found": [
        {
            "command": "which {cmd}",
            "explanation": "Check if the command exists in PATH",
            "risk_level": "safe",
        },
        {
            "command": "apt search {cmd}",
            "explanation": "Search for a package containing this command",
            "risk_level": "safe",
        },
        {
            "command": "type {cmd}",
            "explanation": "Check what type of command this is",
            "risk_level": "safe",
        },
    ],
    "permission_denied": [
        {
            "command": "ls -la {path}",
            "explanation": "Check permissions on the file or directory",
            "risk_level": "safe",
        },
        {
            "command": "stat {path}",
            "explanation": "View detailed file information",
            "risk_level": "safe",
        },
        {
            "command": "groups",
            "explanation": "Check your group memberships",
            "risk_level": "safe",
        },
    ],
    "file_not_found": [
        {
            "command": "ls -la {dir}",
            "explanation": "List contents of the parent directory",
            "risk_level": "safe",
        },
        {
            "command": "find . -name '{name}' 2>/dev/null",
            "explanation": "Search for the file in current directory",
            "risk_level": "safe",
        },
        {
            "command": "locate {name}",
            "explanation": "Search for the file system-wide (if updatedb is run)",
            "risk_level": "safe",
        },
    ],
    "syntax_error": [
        {
            "command": "bash -n -c '{original}'",
            "explanation": "Check bash syntax without executing",
            "risk_level": "safe",
        },
        {
            "command": "man {cmd}",
            "explanation": "Read the manual for correct syntax",
            "risk_level": "safe",
        },
    ],
    "argument_error": [
        {
            "command": "{cmd} --help",
            "explanation": "Show help for the command",
            "risk_level": "safe",
        },
        {
            "command": "man {cmd}",
            "explanation": "Read the full manual page",
            "risk_level": "safe",
        },
    ],
    "dependency_missing": [
        {
            "command": "apt update",
            "explanation": "Update package lists",
            "risk_level": "safe",
        },
        {
            "command": "apt --fix-broken install",
            "explanation": "Fix broken dependencies",
            "risk_level": "moderate",
            "requires_privilege": True,
        },
        {
            "command": "apt search {pkg}",
            "explanation": "Search for related packages",
            "risk_level": "safe",
        },
    ],
    "resource_exhausted": [
        {
            "command": "df -h",
            "explanation": "Check disk space usage",
            "risk_level": "safe",
        },
        {
            "command": "free -h",
            "explanation": "Check memory usage",
            "risk_level": "safe",
        },
        {
            "command": "du -sh * | sort -h | tail -20",
            "explanation": "Find largest files/directories",
            "risk_level": "safe",
        },
    ],
    "network_error": [
        {
            "command": "ping -c 3 8.8.8.8",
            "explanation": "Test basic network connectivity",
            "risk_level": "safe",
        },
        {
            "command": "ip addr show",
            "explanation": "Show network interface status",
            "risk_level": "safe",
        },
        {
            "command": "cat /etc/resolv.conf",
            "explanation": "Check DNS configuration",
            "risk_level": "safe",
        },
        {
            "command": "systemctl status systemd-resolved",
            "explanation": "Check DNS resolver service",
            "risk_level": "safe",
        },
    ],
    "configuration_error": [
        {
            "command": "cat {config}",
            "explanation": "View the configuration file",
            "risk_level": "safe",
        },
        {
            "command": "{cmd} --version",
            "explanation": "Check the version for compatibility",
            "risk_level": "safe",
        },
    ],
    "other": [
        {
            "command": "{cmd} --help",
            "explanation": "Show help for the command",
            "risk_level": "safe",
        },
        {
            "command": "man {cmd}",
            "explanation": "Read the manual page",
            "risk_level": "safe",
        },
    ],
}


# =============================================================================
# Fallback Analysis
# =============================================================================


def detect_error_category(stderr: str) -> tuple[ErrorCategory, str]:
    """Detect error category from stderr output.

    Args:
        stderr: The error output to analyze.

    Returns:
        Tuple of (category, explanation).
    """
    for pattern, category, explanation in ERROR_PATTERNS:
        if pattern.search(stderr):
            return category, explanation

    return "other", "An unrecognized error occurred."


def extract_context_values(context: FixitContext) -> dict[str, str]:
    """Extract values for template substitution.

    Args:
        context: Fixit context.

    Returns:
        Dict of template values.
    """
    failure = context.failure
    cmd_parts = failure.command.split()
    cmd_name = cmd_parts[0] if cmd_parts else "command"

    # Try to extract path from command or error
    path = "."
    for part in cmd_parts[1:]:
        if part.startswith("/") or part.startswith("~") or part.startswith("."):
            path = part
            break

    # Try to extract filename
    name = "*"
    if path != ".":
        name = path.split("/")[-1]

    # Try to extract directory
    dir_path = "."
    if "/" in path:
        dir_path = "/".join(path.split("/")[:-1]) or "/"

    # Try to extract package name from error
    pkg = cmd_parts[1] if len(cmd_parts) > 1 else "package"
    pkg_match = re.search(r"package[:\s]+(\S+)", failure.stderr, re.I)
    if pkg_match:
        pkg = pkg_match.group(1)

    return {
        "cmd": cmd_name,
        "original": failure.command,
        "path": path,
        "name": name,
        "dir": dir_path,
        "pkg": pkg,
        "config": path if path.endswith(".conf") else "/etc/config",
    }


def build_fix_command(template: dict, values: dict[str, str]) -> FixCommand:
    """Build a FixCommand from a template.

    Args:
        template: Fix command template.
        values: Substitution values.

    Returns:
        FixCommand with substituted values.
    """
    command = template["command"]
    for key, value in values.items():
        command = command.replace(f"{{{key}}}", value)

    explanation = template["explanation"]

    return FixCommand(
        command=command,
        explanation=explanation,
        risk_level=template.get("risk_level", "moderate"),
        requires_privilege=template.get("requires_privilege", False),
        verification=None,
    )


def analyze_with_fallback(context: FixitContext) -> FixitAnalysis:
    """Analyze command failure using rule-based patterns.

    Args:
        context: The fixit context.

    Returns:
        FixitAnalysis with diagnosis and suggestions.
    """
    failure = context.failure
    stderr = failure.stderr

    # Detect error category
    category, explanation = detect_error_category(stderr)

    # Get command name for summary
    cmd_parts = failure.command.split()
    cmd_name = cmd_parts[0] if cmd_parts else "command"

    # Build diagnosis
    diagnosis = FixitDiagnosis(
        error_category=category,
        summary=f"'{cmd_name}' failed: {explanation}",
        root_cause=explanation,
        confidence=0.6,  # Lower confidence for rule-based
    )

    # Get fix templates for this category
    templates = CATEGORY_FIXES.get(category, CATEGORY_FIXES["other"])

    # Extract context values
    values = extract_context_values(context)

    # Build suggestions
    suggestions = []
    for template in templates[:3]:  # Max 3 suggestions
        fix = build_fix_command(template, values)
        # Skip if command is identical to failed command
        if fix.command != failure.command:
            suggestions.append(fix)

    # Determine learn_more based on category
    learn_more = [f"man {cmd_name}"]
    if category == "permission_denied":
        learn_more.append("man chmod")
        learn_more.append("man chown")
    elif category == "network_error":
        learn_more.append("man ip")
        learn_more.append("man ss")
    elif category == "dependency_missing":
        learn_more.append("man apt")

    return FixitAnalysis(
        diagnosis=diagnosis,
        suggestions=tuple(suggestions),
        alternative_approach=_get_alternative(category),
        learn_more=tuple(learn_more),
    )


def _get_alternative(category: ErrorCategory) -> str | None:
    """Get alternative approach suggestion.

    Args:
        category: Error category.

    Returns:
        Alternative approach string or None.
    """
    alternatives = {
        "command_not_found": "Check if you need to install a package or if the command has a different name.",
        "permission_denied": "You may need elevated privileges. Check file ownership or request access.",
        "file_not_found": "Verify the path is correct. Use 'ls' or 'find' to locate the file.",
        "dependency_missing": "Try 'apt update' first, or search for the package with a different name.",
        "resource_exhausted": "Free up resources or increase limits. Check 'df', 'free', and 'ulimit'.",
        "network_error": "Check your network connection and DNS settings.",
    }
    return alternatives.get(category)
