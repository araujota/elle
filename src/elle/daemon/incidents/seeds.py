"""Incident pattern seeds for the Incident Vault.

Provides template incidents that can be seeded into the vault to enable
semantic search for common error patterns. This replaces the hardcoded
regex patterns in cli/fixit/fallback.py.

These patterns are loaded once when the daemon starts, providing a
learnable baseline that can be augmented by actual user incidents.

Usage:
    from elle.daemon.incidents.seeds import seed_incident_patterns

    # Seed the vault with common patterns
    await seed_incident_patterns(incident_store)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    pass

# IncidentStore was refactored to standalone functions
IncidentStore = Any

logger = logging.getLogger(__name__)


class IncidentPatternSeed(BaseModel):
    """A template incident pattern for seeding the vault."""

    model_config = ConfigDict(frozen=True)

    fingerprint: str = Field(description="Unique pattern identifier")
    title: str = Field(description="Human-readable title")
    domain: str = Field(description="Incident domain (net, disk, auth, pkg, etc.)")
    severity: str = Field(description="Severity level")
    symptoms: tuple[str, ...] = Field(description="Error messages/patterns to match")
    diagnosis: str = Field(description="What this error means")
    suggested_fixes: tuple[str, ...] = Field(description="Suggested fix commands/actions")
    capability_refs: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Related capabilities that can fix this",
    )


# Common error patterns organized by domain
SEED_PATTERNS: tuple[IncidentPatternSeed, ...] = (
    # ==========================================================================
    # Database Errors
    # ==========================================================================
    IncidentPatternSeed(
        fingerprint="db_auth_failed",
        title="Database authentication failed",
        domain="db",
        severity="error",
        symptoms=(
            "password authentication failed for user",
            "Access denied for user",
            "FATAL: password authentication failed",
            "authentication failed for database",
        ),
        diagnosis=(
            "The database server rejected the authentication credentials. "
            "This is typically caused by an incorrect password, wrong username, "
            "or the user not having access to the specified database."
        ),
        suggested_fixes=(
            "Check the database password in environment variables or config files",
            "Verify the database user exists: SELECT usename FROM pg_catalog.pg_user;",
            "Reset the database password if needed",
            "Check pg_hba.conf for authentication method settings",
        ),
        capability_refs=("config.edit",),
    ),
    IncidentPatternSeed(
        fingerprint="db_connection_refused",
        title="Database connection refused",
        domain="db",
        severity="error",
        symptoms=(
            "Connection refused",
            "could not connect to server",
            "connection to server at",
            "Is the server running",
            "accepting TCP/IP connections on port",
        ),
        diagnosis=(
            "The database server is not accepting connections. Either the service "
            "is not running, it's not configured to accept network connections, "
            "or a firewall is blocking the connection."
        ),
        suggested_fixes=(
            "Check if the database service is running: systemctl status postgresql",
            "Verify the database is listening on the expected port: ss -tlnp | grep 5432",
            "Check listen_addresses in postgresql.conf",
            "Verify firewall allows the database port: ufw status",
        ),
        capability_refs=("service.status", "service.restart"),
    ),
    # ==========================================================================
    # Service/Systemd Errors
    # ==========================================================================
    IncidentPatternSeed(
        fingerprint="service_start_failed",
        title="Systemd service failed to start",
        domain="service",
        severity="error",
        symptoms=(
            "Failed to start",
            "Job for .service failed",
            "Unit .service entered failed state",
            "systemd service failed",
        ),
        diagnosis=(
            "A systemd service failed to start. The most common causes are "
            "configuration errors, missing dependencies, permission issues, "
            "or port conflicts."
        ),
        suggested_fixes=(
            "Check service logs: journalctl -xeu <service>",
            "Check service status: systemctl status <service>",
            "Verify configuration syntax",
            "Check if required ports are available: ss -tlnp",
        ),
        capability_refs=("service.restart", "service.status"),
    ),
    IncidentPatternSeed(
        fingerprint="address_in_use",
        title="Network address already in use",
        domain="net",
        severity="error",
        symptoms=(
            "Address already in use",
            "bind: Address already in use",
            "port is already allocated",
            "EADDRINUSE",
        ),
        diagnosis=(
            "Another process is already bound to the requested network port. "
            "The service cannot start until the port is freed."
        ),
        suggested_fixes=(
            "Find process using the port: ss -tlnp | grep <port>",
            "Stop the conflicting service",
            "Change the service to use a different port",
            "Kill the process if it's stuck: kill <pid>",
        ),
        capability_refs=("network.listeners",),
    ),
    # ==========================================================================
    # Docker Errors
    # ==========================================================================
    IncidentPatternSeed(
        fingerprint="docker_not_running",
        title="Docker daemon not running",
        domain="docker",
        severity="error",
        symptoms=(
            "Cannot connect to the Docker daemon",
            "Is the docker daemon running?",
            "docker.sock: connect: no such file",
        ),
        diagnosis=(
            "The Docker daemon is not running or not accessible. This prevents any Docker operations from working."
        ),
        suggested_fixes=(
            "Start Docker: systemctl start docker",
            "Check Docker status: systemctl status docker",
            "Check Docker logs: journalctl -xeu docker",
            "Verify user is in docker group: groups $USER",
        ),
        capability_refs=("service.start", "service.status"),
    ),
    IncidentPatternSeed(
        fingerprint="image_not_found",
        title="Docker image not found",
        domain="docker",
        severity="warning",
        symptoms=(
            "manifest unknown",
            "manifest not found",
            "repository does not exist",
            "pull access denied",
            "image not found",
        ),
        diagnosis=(
            "The requested Docker image does not exist in the registry, "
            "has been deleted, or you don't have access to it."
        ),
        suggested_fixes=(
            "Check the image name and tag for typos",
            "Verify the image exists: docker search <image>",
            "Login to registry if private: docker login",
            "Pull the correct image tag",
        ),
        capability_refs=("docker.list",),
    ),
    # ==========================================================================
    # Package Manager Errors
    # ==========================================================================
    IncidentPatternSeed(
        fingerprint="apt_lock",
        title="APT package manager locked",
        domain="pkg",
        severity="warning",
        symptoms=(
            "Could not get lock",
            "dpkg frontend lock",
            "Unable to acquire the dpkg frontend lock",
            "Another process is using the package management system",
        ),
        diagnosis=(
            "Another package management process (apt, dpkg, Software Center) "
            "is currently running. Only one package operation can run at a time."
        ),
        suggested_fixes=(
            "Wait for the other process to finish",
            "Find the blocking process: ps aux | grep -E 'apt|dpkg'",
            "If stuck, carefully remove lock: sudo rm /var/lib/dpkg/lock-frontend",
            "After removing lock, fix packages: sudo dpkg --configure -a",
        ),
        capability_refs=("package.update",),
    ),
    IncidentPatternSeed(
        fingerprint="broken_packages",
        title="Broken package dependencies",
        domain="pkg",
        severity="error",
        symptoms=(
            "held broken packages",
            "Broken packages",
            "unmet dependencies",
            "depends on .* but it is not installable",
        ),
        diagnosis=(
            "The package system has dependency conflicts that prevent "
            "installation of new packages. This often happens after "
            "interrupted updates or incompatible third-party packages."
        ),
        suggested_fixes=(
            "Fix broken packages: sudo apt --fix-broken install",
            "Update package lists: sudo apt update",
            "Try force-installing: sudo apt install -f",
            "Remove problematic package if identified",
        ),
        capability_refs=("package.update",),
    ),
    # ==========================================================================
    # Permission Errors
    # ==========================================================================
    IncidentPatternSeed(
        fingerprint="permission_denied",
        title="Permission denied",
        domain="auth",
        severity="warning",
        symptoms=(
            "Permission denied",
            "access denied",
            "Operation not permitted",
            "EACCES",
            "you cannot perform this operation",
        ),
        diagnosis=(
            "The operation requires higher privileges or the user lacks "
            "permission to access the resource. This may require sudo or "
            "group membership changes."
        ),
        suggested_fixes=(
            "Try with sudo: sudo <command>",
            "Check file permissions: ls -la <path>",
            "Add user to required group: sudo usermod -aG <group> $USER",
            "Check SELinux/AppArmor: sestatus or aa-status",
        ),
        capability_refs=(),
    ),
    # ==========================================================================
    # Filesystem Errors
    # ==========================================================================
    IncidentPatternSeed(
        fingerprint="no_space_left",
        title="No space left on device",
        domain="disk",
        severity="critical",
        symptoms=(
            "No space left on device",
            "ENOSPC",
            "Disk quota exceeded",
            "write failed",
        ),
        diagnosis=(
            "The filesystem is full or quota is exceeded. No new files can "
            "be created until space is freed or quota increased."
        ),
        suggested_fixes=(
            "Check disk usage: df -h",
            "Find large files: du -h --max-depth=1 / | sort -hr | head -20",
            "Clear package cache: sudo apt clean",
            "Clear old logs: sudo journalctl --vacuum-time=3d",
            "Remove old Docker images: docker system prune",
        ),
        capability_refs=("docker.prune",),
    ),
    IncidentPatternSeed(
        fingerprint="readonly_fs",
        title="Read-only filesystem",
        domain="disk",
        severity="error",
        symptoms=(
            "Read-only file system",
            "EROFS",
            "cannot create directory",
            "cannot write",
        ),
        diagnosis=(
            "The filesystem is mounted read-only, either due to disk errors, "
            "intentional configuration, or emergency remount."
        ),
        suggested_fixes=(
            "Check mount options: mount | grep <device>",
            "Remount as read-write: sudo mount -o remount,rw /",
            "Check for filesystem errors: sudo fsck <device>",
            "Check dmesg for disk errors: dmesg | grep -i error",
        ),
        capability_refs=(),
    ),
    # ==========================================================================
    # Network Errors
    # ==========================================================================
    IncidentPatternSeed(
        fingerprint="connection_refused",
        title="Connection refused",
        domain="net",
        severity="warning",
        symptoms=(
            "Connection refused",
            "connect: Connection refused",
            "ECONNREFUSED",
        ),
        diagnosis=("No service is listening on the target address/port, or a firewall is blocking the connection."),
        suggested_fixes=(
            "Check if service is running: systemctl status <service>",
            "Check if port is listening: ss -tlnp | grep <port>",
            "Check firewall: ufw status or iptables -L",
            "Verify the correct host/port is being used",
        ),
        capability_refs=("service.status", "network.listeners"),
    ),
    IncidentPatternSeed(
        fingerprint="dns_resolution_failed",
        title="DNS resolution failed",
        domain="net",
        severity="warning",
        symptoms=(
            "could not resolve host",
            "Name or service not known",
            "getaddrinfo failed",
            "DNS resolution failed",
            "Temporary failure in name resolution",
        ),
        diagnosis=(
            "DNS resolution is failing. This could be due to network "
            "connectivity issues, DNS server problems, or misconfigured "
            "/etc/resolv.conf."
        ),
        suggested_fixes=(
            "Check DNS settings: cat /etc/resolv.conf",
            "Test DNS: nslookup google.com",
            "Try alternative DNS: dig @8.8.8.8 <hostname>",
            "Check network connectivity: ping 8.8.8.8",
        ),
        capability_refs=(),
    ),
    # ==========================================================================
    # Command Errors
    # ==========================================================================
    IncidentPatternSeed(
        fingerprint="command_not_found",
        title="Command not found",
        domain="shell",
        severity="info",
        symptoms=(
            "command not found",
            "not found",
            "No such file or directory",
        ),
        diagnosis=("The command is not installed or not in the system PATH."),
        suggested_fixes=(
            "Install the package: sudo apt install <package>",
            "Use full path if installed elsewhere",
            "Check if installed: which <command> or type <command>",
            "Update PATH if needed",
        ),
        capability_refs=("package.install",),
    ),
)


async def seed_incident_patterns(incident_store: IncidentStore) -> int:
    """Seed the incident vault with common error patterns.

    Args:
        incident_store: IncidentStore instance.

    Returns:
        Number of patterns seeded.
    """
    seeded = 0

    for pattern in SEED_PATTERNS:
        try:
            # Check if pattern already exists (by fingerprint)
            existing = incident_store.get_by_fingerprint(pattern.fingerprint)
            if existing:
                logger.debug(f"Pattern {pattern.fingerprint} already exists")
                continue

            # Create seed incident
            incident_store.create_seed_incident(
                fingerprint=pattern.fingerprint,
                title=pattern.title,
                domain=pattern.domain,
                severity=pattern.severity,
                symptoms=pattern.symptoms,
                diagnosis=pattern.diagnosis,
                suggested_fixes=pattern.suggested_fixes,
            )
            seeded += 1
            logger.debug(f"Seeded pattern: {pattern.fingerprint}")

        except Exception as e:
            logger.warning(f"Failed to seed pattern {pattern.fingerprint}: {e}")

    if seeded > 0:
        logger.info(f"Seeded {seeded} incident patterns")

    return seeded


def get_pattern_by_fingerprint(fingerprint: str) -> IncidentPatternSeed | None:
    """Get a seed pattern by fingerprint.

    Args:
        fingerprint: Pattern fingerprint.

    Returns:
        IncidentPatternSeed or None.
    """
    for pattern in SEED_PATTERNS:
        if pattern.fingerprint == fingerprint:
            return pattern
    return None


def get_patterns_by_domain(domain: str) -> list[IncidentPatternSeed]:
    """Get all seed patterns for a domain.

    Args:
        domain: Domain to filter by.

    Returns:
        List of matching patterns.
    """
    return [p for p in SEED_PATTERNS if p.domain == domain]


def search_patterns(error_text: str) -> list[IncidentPatternSeed]:
    """Search for matching patterns based on error text.

    This is a fallback when the incident store semantic search
    doesn't find matches.

    Args:
        error_text: Error message to match.

    Returns:
        List of matching patterns, sorted by relevance.
    """
    matches = []
    error_lower = error_text.lower()

    for pattern in SEED_PATTERNS:
        for symptom in pattern.symptoms:
            if symptom.lower() in error_lower:
                matches.append(pattern)
                break

    return matches
