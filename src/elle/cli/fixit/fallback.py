"""Rule-based fallback analysis for Fixit.

DEPRECATION NOTICE:
    The hardcoded patterns in this module are being migrated to the
    Incident Vault as learnable patterns. See:
    - elle.daemon.incidents.seeds for the new pattern definitions
    - elle.daemon.incidents.store for semantic search

    New error patterns should be added to seeds.py rather than here.
    The patterns here will eventually be removed once the Incident Vault
    semantic search is fully integrated.

Provides pattern-matching based error analysis when the LLM
is unavailable. Covers common error categories with
pre-defined suggestions.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

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
# IMPORTANT: More specific patterns MUST come BEFORE generic patterns!
# The first matching pattern wins, so order matters.
ERROR_PATTERNS: list[tuple[re.Pattern[str], ErrorCategory, str]] = [
    # ==========================================================================
    # SPECIFIC PATTERNS FIRST (before generic patterns that might mask them)
    # ==========================================================================
    # Database errors (must come before generic "authentication failed" and "Connection refused")
    # NOTE: Patterns are specific to database errors (PostgreSQL, MySQL)
    (re.compile(r"password authentication failed for user", re.I), "db_auth_failed", "Database authentication failed."),
    (re.compile(r"Access denied for user.*@", re.I), "db_auth_failed", "Database authentication failed."),
    (re.compile(r"FATAL:\s+password authentication failed", re.I), "db_auth_failed", "Database authentication failed."),
    (
        re.compile(r"Connection refused.*(5432|3306|27017)", re.I),
        "db_connection_refused",
        "Database server is not accepting connections.",
    ),
    (re.compile(r"database is locked", re.I), "db_locked", "Database file is locked by another process."),
    # Service/systemd errors (must come before generic "not found")
    # NOTE: dependency failed must come before "Job for .+\.service failed"
    (re.compile(r"dependency failed", re.I), "service_dep_failed", "Service dependency failed to start."),
    (re.compile(r"Failed to start.*\.service", re.I), "service_start_failed", "Systemd service failed to start."),
    (re.compile(r"Job for .+\.service failed", re.I), "service_start_failed", "Systemd service failed to start."),
    (re.compile(r"Address already in use", re.I), "address_in_use", "Network address/port is already bound."),
    (re.compile(r"Unit .+\.service.* not found", re.I), "unit_not_found", "Systemd unit file does not exist."),
    # Docker/container errors (must come before generic "not found")
    (re.compile(r"Cannot connect to the Docker daemon", re.I), "docker_not_running", "Docker daemon is not running."),
    (re.compile(r"No such container", re.I), "container_not_found", "Container does not exist."),
    (re.compile(r"manifest.+not found", re.I), "image_not_found", "Docker image not found in registry."),
    (
        re.compile(r"port is already allocated", re.I),
        "port_in_use",
        "Port is already in use by another container or process.",
    ),
    (re.compile(r"OCI runtime.*failed", re.I), "container_runtime_error", "Container runtime failed to start."),
    # Package manager errors (must come before generic patterns)
    (re.compile(r"E: Could not get lock", re.I), "apt_lock", "APT package manager is locked by another process."),
    (re.compile(r"dpkg was interrupted", re.I), "dpkg_interrupted", "Previous dpkg operation was interrupted."),
    (re.compile(r"held broken packages", re.I), "held_packages", "Held packages are preventing installation."),
    (re.compile(r"[Bb]roken packages", re.I), "broken_packages", "Package dependencies are broken."),
    (re.compile(r"Hash Sum mismatch", re.I), "apt_hash_mismatch", "Package file corruption detected."),
    (re.compile(r"NO_PUBKEY", re.I), "apt_key_missing", "Repository signing key is missing."),
    # Filesystem errors
    (re.compile(r"Read-only file system", re.I), "readonly_fs", "Filesystem is mounted read-only."),
    (re.compile(r"Device or resource busy", re.I), "device_busy", "Device is busy and cannot be unmounted/modified."),
    (re.compile(r"Stale file handle", re.I), "stale_nfs", "NFS mount has become stale."),
    (re.compile(r"Structure needs cleaning", re.I), "fs_corrupt", "Filesystem has corruption and needs fsck."),
    (re.compile(r"Input/output error", re.I), "io_error", "Disk I/O error, possible hardware failure."),
    # ==========================================================================
    # GENERIC PATTERNS (after specific patterns)
    # ==========================================================================
    # Command not found
    (re.compile(r"command not found", re.I), "command_not_found", "The command was not found in your PATH."),
    (
        re.compile(r"No such file or directory.*executable", re.I),
        "command_not_found",
        "The executable file does not exist.",
    ),
    # Permission denied
    (
        re.compile(r"permission denied", re.I),
        "permission_denied",
        "You don't have permission to perform this operation.",
    ),
    (re.compile(r"access denied", re.I), "permission_denied", "Access to the resource was denied."),
    (
        re.compile(r"Operation not permitted", re.I),
        "permission_denied",
        "The operation is not permitted for your user.",
    ),
    (
        re.compile(r"Authentication failed", re.I),
        "permission_denied",
        "Authentication failed - check your credentials.",
    ),
    # File not found
    (
        re.compile(r"No such file or directory", re.I),
        "file_not_found",
        "The specified file or directory does not exist.",
    ),
    (re.compile(r"cannot stat", re.I), "file_not_found", "Unable to access the file or directory."),
    (re.compile(r"does not exist", re.I), "file_not_found", "The specified path does not exist."),
    (re.compile(r"ENOENT", re.I), "file_not_found", "The file or directory was not found (ENOENT)."),
    # Syntax errors
    (re.compile(r"syntax error", re.I), "syntax_error", "There is a syntax error in the command."),
    (re.compile(r"unexpected token", re.I), "syntax_error", "Unexpected token in the command syntax."),
    (re.compile(r"parse error", re.I), "syntax_error", "The command could not be parsed."),
    # Argument errors
    (re.compile(r"invalid option", re.I), "argument_error", "An invalid option or flag was provided."),
    (re.compile(r"unrecognized option", re.I), "argument_error", "The command does not recognize this option."),
    (re.compile(r"missing argument", re.I), "argument_error", "A required argument is missing."),
    (
        re.compile(r"requires an argument", re.I),
        "argument_error",
        "An option requires an argument that was not provided.",
    ),
    (re.compile(r"too many arguments", re.I), "argument_error", "Too many arguments were provided."),
    # Dependency missing
    (
        re.compile(r"Unable to locate package", re.I),
        "dependency_missing",
        "The package could not be found in the repository.",
    ),
    (re.compile(r"Package .* is not available", re.I), "dependency_missing", "The requested package is not available."),
    (re.compile(r"unmet dependencies", re.I), "dependency_missing", "There are unmet package dependencies."),
    (re.compile(r"Depends:.*but it is not", re.I), "dependency_missing", "A required dependency is missing."),
    # Resource exhausted
    (re.compile(r"No space left on device", re.I), "resource_exhausted", "The disk is full."),
    (re.compile(r"Cannot allocate memory", re.I), "resource_exhausted", "Not enough memory available."),
    (re.compile(r"Too many open files", re.I), "resource_exhausted", "File descriptor limit reached."),
    (re.compile(r"quota exceeded", re.I), "resource_exhausted", "Disk quota has been exceeded."),
    # Network errors (generic - after specific db_connection_refused)
    (re.compile(r"Connection refused", re.I), "network_error", "The connection was refused by the remote host."),
    (re.compile(r"Connection timed out", re.I), "network_error", "The connection attempt timed out."),
    (re.compile(r"Network is unreachable", re.I), "network_error", "The network is not reachable."),
    (re.compile(r"Name or service not known", re.I), "network_error", "DNS resolution failed for the hostname."),
    (re.compile(r"Could not resolve host", re.I), "network_error", "Unable to resolve the hostname."),
    (re.compile(r"Temporary failure in name resolution", re.I), "network_error", "DNS lookup temporarily failed."),
    # Configuration errors
    (re.compile(r"Invalid configuration", re.I), "configuration_error", "The configuration is invalid."),
    (re.compile(r"configuration error", re.I), "configuration_error", "There is an error in the configuration."),
    (re.compile(r"Failed to parse", re.I), "configuration_error", "Failed to parse configuration file."),
    # Generic "not found" MUST be last - it matches too broadly
    (re.compile(r"not found", re.I), "command_not_found", "The command or executable was not found."),
]


# Suggested fixes for each error category
CATEGORY_FIXES: dict[ErrorCategory, list[dict[str, Any]]] = {
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
    # ==========================================================================
    # Package manager fixes
    # ==========================================================================
    "apt_lock": [
        {
            "command": "lsof /var/lib/dpkg/lock-frontend 2>/dev/null || echo 'No process holding lock'",
            "explanation": "Identify process holding the lock",
            "risk_level": "safe",
        },
        {
            "command": "ps aux | grep -E 'apt|dpkg'",
            "explanation": "Show running apt/dpkg processes",
            "risk_level": "safe",
        },
    ],
    "dpkg_interrupted": [
        {
            "command": "dpkg --configure -a",
            "explanation": "Complete interrupted package configuration",
            "risk_level": "moderate",
            "requires_privilege": True,
        },
    ],
    "broken_packages": [
        {
            "command": "apt --fix-broken install",
            "explanation": "Attempt to fix broken dependencies",
            "risk_level": "moderate",
            "requires_privilege": True,
        },
        {
            "command": "dpkg --audit",
            "explanation": "Show packages with problems",
            "risk_level": "safe",
        },
    ],
    "held_packages": [
        {
            "command": "apt-mark showhold",
            "explanation": "Show packages marked as held",
            "risk_level": "safe",
        },
        {
            "command": "apt list --upgradable",
            "explanation": "List packages with available upgrades",
            "risk_level": "safe",
        },
    ],
    "apt_hash_mismatch": [
        {
            "command": "apt clean",
            "explanation": "Clear the package cache",
            "risk_level": "safe",
            "requires_privilege": True,
        },
        {
            "command": "apt update",
            "explanation": "Refresh package lists",
            "risk_level": "safe",
            "requires_privilege": True,
        },
    ],
    "apt_key_missing": [
        {
            "command": "apt-key list",
            "explanation": "List trusted repository keys",
            "risk_level": "safe",
        },
        {
            "command": "cat /etc/apt/sources.list.d/",
            "explanation": "Check repository configuration",
            "risk_level": "safe",
        },
    ],
    # ==========================================================================
    # Docker fixes
    # ==========================================================================
    "docker_not_running": [
        {
            "command": "systemctl status docker",
            "explanation": "Check Docker daemon status",
            "risk_level": "safe",
        },
        {
            "command": "systemctl start docker",
            "explanation": "Start the Docker daemon",
            "risk_level": "safe",
            "requires_privilege": True,
        },
        {
            "command": "journalctl -u docker -n 50 --no-pager",
            "explanation": "View recent Docker logs",
            "risk_level": "safe",
        },
    ],
    "container_not_found": [
        {
            "command": "docker ps -a",
            "explanation": "List all containers including stopped",
            "risk_level": "safe",
        },
        {
            "command": "docker container ls -a --format 'table {{{{.Names}}}}\\t{{{{.Status}}}}'",
            "explanation": "Show container names and status",
            "risk_level": "safe",
        },
    ],
    "image_not_found": [
        {
            "command": "docker images",
            "explanation": "List locally available images",
            "risk_level": "safe",
        },
        {
            "command": "docker search {pkg}",
            "explanation": "Search Docker Hub for the image",
            "risk_level": "safe",
        },
    ],
    "port_in_use": [
        {
            "command": "ss -tlnp | grep {port}",
            "explanation": "Find what's using the port",
            "risk_level": "safe",
        },
        {
            "command": "docker ps --format 'table {{{{.Names}}}}\\t{{{{.Ports}}}}'",
            "explanation": "Show container port mappings",
            "risk_level": "safe",
        },
    ],
    "container_runtime_error": [
        {
            "command": "docker logs {container}",
            "explanation": "View container logs",
            "risk_level": "safe",
        },
        {
            "command": "docker inspect {container}",
            "explanation": "Show detailed container configuration",
            "risk_level": "safe",
        },
    ],
    # ==========================================================================
    # Filesystem fixes
    # ==========================================================================
    "readonly_fs": [
        {
            "command": "mount | grep ' / '",
            "explanation": "Check if root filesystem is read-only",
            "risk_level": "safe",
        },
        {
            "command": "dmesg | tail -50",
            "explanation": "Check kernel messages for filesystem errors",
            "risk_level": "safe",
        },
    ],
    "device_busy": [
        {
            "command": "lsof +D {path}",
            "explanation": "Find processes using the directory",
            "risk_level": "safe",
        },
        {
            "command": "fuser -vm {path}",
            "explanation": "Show processes accessing the path",
            "risk_level": "safe",
        },
    ],
    "stale_nfs": [
        {
            "command": "mount | grep nfs",
            "explanation": "List NFS mounts",
            "risk_level": "safe",
        },
        {
            "command": "showmount -e {host}",
            "explanation": "Check exports from NFS server",
            "risk_level": "safe",
        },
    ],
    "fs_corrupt": [
        {
            "command": "dmesg | grep -i 'error\\|corrupt\\|ext4'",
            "explanation": "Check kernel messages for filesystem errors",
            "risk_level": "safe",
        },
        {
            "command": "smartctl -H /dev/sda",
            "explanation": "Check disk SMART health",
            "risk_level": "safe",
        },
    ],
    "io_error": [
        {
            "command": "smartctl -H /dev/sda",
            "explanation": "Check disk SMART health status",
            "risk_level": "safe",
        },
        {
            "command": "dmesg | grep -i 'error\\|fail' | tail -20",
            "explanation": "Check kernel messages for hardware errors",
            "risk_level": "safe",
        },
    ],
    # ==========================================================================
    # Service/systemd fixes
    # ==========================================================================
    "service_start_failed": [
        {
            "command": "systemctl status {service}",
            "explanation": "Check detailed service status",
            "risk_level": "safe",
        },
        {
            "command": "journalctl -u {service} -n 50 --no-pager",
            "explanation": "View recent logs for the service",
            "risk_level": "safe",
        },
    ],
    "address_in_use": [
        {
            "command": "ss -tlnp | grep {port}",
            "explanation": "Find what's using the port",
            "risk_level": "safe",
        },
        {
            "command": "lsof -i :{port}",
            "explanation": "Show processes bound to the port",
            "risk_level": "safe",
        },
    ],
    "service_dep_failed": [
        {
            "command": "systemctl list-dependencies {service}",
            "explanation": "Show service dependency tree",
            "risk_level": "safe",
        },
        {
            "command": "systemctl --failed",
            "explanation": "List all failed services",
            "risk_level": "safe",
        },
    ],
    "unit_not_found": [
        {
            "command": "systemctl list-unit-files | grep {service}",
            "explanation": "Search for the unit file",
            "risk_level": "safe",
        },
        {
            "command": "ls -la /etc/systemd/system/",
            "explanation": "List system unit files",
            "risk_level": "safe",
        },
    ],
    # ==========================================================================
    # Database fixes
    # ==========================================================================
    "db_locked": [
        {
            "command": "lsof {path}",
            "explanation": "Find processes using the database file",
            "risk_level": "safe",
        },
        {
            "command": "fuser {path}",
            "explanation": "Show PIDs accessing the database",
            "risk_level": "safe",
        },
    ],
    "db_auth_failed": [
        {
            "command": "cat ~/.pgpass 2>/dev/null || echo 'No .pgpass file'",
            "explanation": "Check PostgreSQL password file",
            "risk_level": "safe",
        },
        {
            "command": "cat /etc/postgresql/*/main/pg_hba.conf | grep -v '^#'",
            "explanation": "Check PostgreSQL authentication config",
            "risk_level": "safe",
        },
    ],
    "db_connection_refused": [
        {
            "command": "systemctl status postgresql mysql mongod 2>/dev/null | head -20",
            "explanation": "Check database service status",
            "risk_level": "safe",
        },
        {
            "command": "ss -tlnp | grep -E '5432|3306|27017'",
            "explanation": "Check if database ports are listening",
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
    stderr = failure.stderr or ""

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
    pkg_match = re.search(r"package[:\s]+(\S+)", stderr, re.I)
    if pkg_match:
        pkg = pkg_match.group(1)

    # Extract service name from systemd errors
    service = "unknown"
    service_match = re.search(r"([\w\-@]+)\.service", stderr)
    if service_match:
        service = service_match.group(1)
    elif "systemctl" in failure.command:
        # Try to get service from systemctl command
        for i, part in enumerate(cmd_parts):
            if part in ("start", "stop", "restart", "status", "enable", "disable"):
                if i + 1 < len(cmd_parts):
                    service = cmd_parts[i + 1].replace(".service", "")
                    break

    # Extract port number from bind/address errors
    port = "80"
    port_match = re.search(r":(\d+)", stderr)
    if port_match:
        port = port_match.group(1)

    # Extract container name from docker errors
    container = "container"
    container_match = re.search(r"container\s+['\"]?(\w[\w\-]+)", stderr, re.I)
    if container_match:
        container = container_match.group(1)
    elif cmd_parts[0] == "docker" and len(cmd_parts) > 2:
        # Try to get container from docker command
        container = cmd_parts[-1]

    # Extract host for NFS errors
    host = "nfs-server"
    host_match = re.search(r"(\w[\w\-\.]+):/", stderr)
    if host_match:
        host = host_match.group(1)

    return {
        "cmd": cmd_name,
        "original": failure.command,
        "path": path,
        "name": name,
        "dir": dir_path,
        "pkg": pkg,
        "config": path if path.endswith(".conf") else "/etc/config",
        "service": service,
        "port": port,
        "container": container,
        "host": host,
    }


def build_fix_command(template: dict[str, Any], values: dict[str, str]) -> FixCommand:
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
        # Original categories
        "command_not_found": "Check if you need to install a package or if the command has a different name.",
        "permission_denied": "You may need elevated privileges. Check file ownership or request access.",
        "file_not_found": "Verify the path is correct. Use 'ls' or 'find' to locate the file.",
        "dependency_missing": "Try 'apt update' first, or search for the package with a different name.",
        "resource_exhausted": "Free up resources or increase limits. Check 'df', 'free', and 'ulimit'.",
        "network_error": "Check your network connection and DNS settings.",
        # Package manager
        "apt_lock": "Wait for other package operations to complete, or check for stale lock files.",
        "dpkg_interrupted": "Run 'sudo dpkg --configure -a' to complete interrupted configuration.",
        "broken_packages": "Try 'sudo apt --fix-broken install' or remove conflicting packages.",
        "held_packages": "Check held packages with 'apt-mark showhold' and unhold if needed.",
        "apt_hash_mismatch": "Clear the apt cache with 'sudo apt clean' and try again.",
        "apt_key_missing": "Add the missing GPG key for the repository.",
        # Docker
        "docker_not_running": "Start Docker with 'sudo systemctl start docker' or check installation.",
        "container_not_found": "List containers with 'docker ps -a' to find the correct name.",
        "image_not_found": "Pull the image first with 'docker pull' or check the image name.",
        "port_in_use": "Stop the conflicting service or use a different port mapping.",
        "container_runtime_error": "Check container logs and configuration for issues.",
        # Filesystem
        "readonly_fs": "The filesystem may have errors. Check dmesg and consider running fsck.",
        "device_busy": "Close applications using the device or wait for operations to complete.",
        "stale_nfs": "Remount the NFS share or check NFS server availability.",
        "fs_corrupt": "Unmount and run fsck on the affected filesystem. Backup data first.",
        "io_error": "Check disk health with smartctl. This may indicate hardware failure.",
        # Service/systemd
        "service_start_failed": "Check service logs with 'journalctl -u <service>' for details.",
        "address_in_use": "Find and stop the process using the port, or use a different port.",
        "service_dep_failed": "Start required dependencies first. Check with 'systemctl list-dependencies'.",
        "unit_not_found": "Verify the service name or check if the package is installed.",
        # Database
        "db_locked": "Close other database connections or wait for transactions to complete.",
        "db_auth_failed": "Verify username/password and check authentication configuration.",
        "db_connection_refused": "Ensure the database server is running and accepting connections.",
    }
    return alternatives.get(category)
