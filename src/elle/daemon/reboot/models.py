"""Pydantic models for the Reboot Persistence and Recovery System.

Defines data structures for reboot intents, verification checks,
and GRUB state management.

SAFETY-CRITICAL: This module handles persistent reboot operations
with automatic verification and GRUB-based rollback.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Reboot reason categories
RebootReason = Literal[
    "kernel_update",  # Kernel or module update requiring reboot
    "driver_update",  # Driver update (GPU, network, etc.)
    "config_change",  # System config change requiring reboot
    "service_restart",  # Service that requires full reboot
    "grub_update",  # GRUB configuration change
    "user_requested",  # User explicitly requested reboot
]

# Reboot intent lifecycle states
RebootIntentStatus = Literal[
    "pending",  # Intent created, reboot not yet started
    "rebooting",  # Reboot initiated, waiting for boot
    "verifying",  # Post-boot verification in progress
    "completed",  # Verification passed, boot confirmed
    "failed",  # Verification failed, manual intervention needed
    "rolled_back",  # Automatic rollback occurred
    "cancelled",  # User cancelled the reboot
]

# Outcome after reboot
RebootOutcome = Literal[
    "improved",  # System healthy after reboot
    "failed",  # Verification checks failed
    "rolled_back",  # Automatic rollback to previous config
    "unknown",  # Outcome not yet determined
]

# Verification check types
VerificationCheckType = Literal[
    "command",  # Run a command and check exit code
    "service_active",  # Check if a systemd service is active
    "file_exists",  # Check if a file exists
    "port_listening",  # Check if a port is listening
    "filesystem_writable",  # Check if a path is writable
    "mount_exists",  # Check if a mount point is mounted
    "kernel_module",  # Check if a kernel module is loaded
    "dmesg_no_errors",  # Check dmesg for error patterns
    "journal_no_errors",  # Check journald for error patterns
    "disk_space",  # Check minimum free disk space
    "network_interface",  # Check network interface is up
]


class PendingVerification(BaseModel):
    """A single verification check to run post-reboot.

    Defines what check to run and how to evaluate success.
    """

    model_config = ConfigDict(frozen=True)

    id: int | None = Field(default=None, description="Database ID")
    reboot_intent_id: str | None = Field(
        default=None,
        description="Parent reboot intent UUID",
    )
    step_index: int = Field(ge=0, description="Order in verification sequence")

    # Check definition
    check_type: VerificationCheckType = Field(
        description="Type of verification check",
    )
    check_command: str = Field(
        description="Command or service name to check",
    )
    expected_exit_code: int = Field(
        default=0,
        description="Expected exit code for command checks",
    )
    expected_output_contains: str | None = Field(
        default=None,
        description="String that stdout must contain (optional)",
    )
    required: bool = Field(
        default=True,
        description="If True, failure triggers rollback consideration",
    )

    # Results (filled post-boot)
    executed_at: datetime | None = Field(
        default=None,
        description="When this check was executed",
    )
    exit_code: int | None = Field(
        default=None,
        description="Actual exit code from check",
    )
    stdout: str | None = Field(
        default=None,
        description="Stdout from check (truncated)",
    )
    stderr: str | None = Field(
        default=None,
        description="Stderr from check (truncated)",
    )
    passed: bool | None = Field(
        default=None,
        description="Whether this check passed",
    )

    @property
    def is_executed(self) -> bool:
        """Check if verification has been executed."""
        return self.executed_at is not None


class GRUBState(BaseModel):
    """GRUB boot configuration state.

    Captures GRUB default entry and available entries for
    rollback management.
    """

    model_config = ConfigDict(frozen=True)

    # Current default entry
    default_entry: str = Field(
        description="Current GRUB default entry (index or saved)",
    )

    # Available entries
    entries: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Available GRUB menu entries",
    )

    # One-shot boot entry (if set)
    oneshot_entry: str | None = Field(
        default=None,
        description="GRUB one-shot boot entry (grub-reboot)",
    )

    # Saved default (GRUB_SAVEDEFAULT=true)
    saved_default: str | None = Field(
        default=None,
        description="Saved default from grubenv",
    )


class RebootIntent(BaseModel):
    """Complete reboot intent - the core artifact.

    Captures the full lifecycle of a reboot operation:
    - What we're trying to achieve (goal)
    - Why we need to reboot (reason)
    - System state before reboot (pre_snapshot)
    - GRUB configuration for rollback
    - Verification checks to run post-boot
    - Results and outcome

    Used for:
    - Reboot state persistence across boots
    - Automatic verification and rollback
    - Audit trail of reboot operations
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    id: str = Field(description="UUID for this reboot intent")
    incident_id: str | None = Field(
        default=None,
        description="Linked incident ID (if triggered by incident)",
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Intent buffer (what we're trying to achieve)
    goal: str = Field(description="High-level goal of this reboot")
    task_description: str = Field(
        description="Detailed description of the task requiring reboot",
    )

    # Reboot reason
    reason: RebootReason = Field(description="Why reboot is needed")
    reason_detail: str | None = Field(
        default=None,
        description="Additional detail about the reason",
    )

    # Session trace (for context continuity)
    session_history: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Commands executed in session before reboot",
    )
    plan_json: dict[str, Any] = Field(
        default_factory=dict,
        description="The plan being executed (if applicable)",
    )

    # State machine
    status: RebootIntentStatus = Field(default="pending")

    # Pre-reboot state
    pre_snapshot_json: dict[str, Any] = Field(
        default_factory=dict,
        description="System snapshot before reboot",
    )
    boot_id: str | None = Field(
        default=None,
        description="systemd boot ID before reboot",
    )

    # GRUB configuration
    grub_entry: str | None = Field(
        default=None,
        description="GRUB entry to boot into",
    )
    grub_default_saved: str | None = Field(
        default=None,
        description="Original GRUB default (for rollback)",
    )
    grub_state: GRUBState | None = Field(
        default=None,
        description="Complete GRUB state before reboot",
    )

    # Post-reboot results
    post_snapshot_json: dict[str, Any] = Field(
        default_factory=dict,
        description="System snapshot after reboot",
    )
    new_boot_id: str | None = Field(
        default=None,
        description="systemd boot ID after reboot",
    )
    verification_attempts: int = Field(
        default=0,
        ge=0,
        description="Number of verification attempts",
    )
    last_verification_at: datetime | None = Field(
        default=None,
        description="When verification was last attempted",
    )

    # Outcome
    outcome: RebootOutcome = Field(default="unknown")
    outcome_detail: str | None = Field(
        default=None,
        description="Detailed outcome explanation",
    )

    # Verifications (populated from separate table)
    verifications: tuple[PendingVerification, ...] = Field(
        default_factory=tuple,
        description="Verification checks for this intent",
    )


class RebootIntentSummary(BaseModel):
    """Summary view of a reboot intent for listings."""

    model_config = ConfigDict(frozen=True)

    id: str
    created_at: datetime
    goal: str
    reason: RebootReason
    status: RebootIntentStatus
    outcome: RebootOutcome
    verification_attempts: int


class RebootStatus(BaseModel):
    """Status report for the Reboot module."""

    model_config = ConfigDict(frozen=True)

    # Counts by status
    total_intents: int = Field(ge=0, default=0)
    pending_count: int = Field(ge=0, default=0)
    rebooting_count: int = Field(ge=0, default=0)
    verifying_count: int = Field(ge=0, default=0)
    completed_count: int = Field(ge=0, default=0)
    failed_count: int = Field(ge=0, default=0)
    rolled_back_count: int = Field(ge=0, default=0)

    # Counts by outcome
    by_outcome: dict[str, int] = Field(default_factory=dict)

    # Active operations
    active_intent: RebootIntentSummary | None = Field(
        default=None,
        description="Currently active reboot intent",
    )

    # Recent operations
    recent_intents: tuple[RebootIntentSummary, ...] = Field(
        default_factory=tuple,
        description="Recent reboot intents (last 7 days)",
    )

    # Database info
    db_size_bytes: int = Field(ge=0, default=0)
    oldest_intent: datetime | None = Field(default=None)
    newest_intent: datetime | None = Field(default=None)


# =============================================================================
# Safety Constants
# =============================================================================


# Maximum verification attempts before declaring failure
MAX_VERIFICATION_ATTEMPTS = 3

# Delays between verification attempts (seconds)
VERIFICATION_DELAYS = (0, 30, 120)

# Per-check timeout
VERIFICATION_TIMEOUT_SEC = 30

# User intervention window before auto-rollback
AUTO_ROLLBACK_DELAY_SEC = 60

# Time to wait for network after boot
NETWORK_CHECK_TIMEOUT_SEC = 60

# Critical services that must be running
CRITICAL_SERVICES = (
    "NetworkManager",
    "systemd-resolved",
    "ssh",
    "sshd",
)

# Maximum age of a "rebooting" intent before assuming boot failed (hours)
STALE_REBOOT_THRESHOLD_HOURS = 24

# Diagnostic collection settings
JOURNAL_LINES_TO_CAPTURE = 200
DMESG_LINES_TO_CAPTURE = 100

# Common error patterns to scan for in logs
DMESG_ERROR_PATTERNS = (
    r"error",
    r"failed",
    r"panic",
    r"oops",
    r"BUG:",
    r"WARNING:",
    r"unable to",
    r"cannot",
    r"I/O error",
    r"read-only",
    r"EXT4-fs error",
    r"XFS error",
    r"BTRFS error",
    r"Out of memory",
    r"oom-killer",
    r"segfault",
    r"general protection",
    r"Call Trace",
)

JOURNAL_ERROR_PATTERNS = (
    r"Failed to start",
    r"failed with exit",
    r"Failed to mount",
    r"Dependency failed",
    r"cannot open shared object",
    r"No such file or directory",
    r"Permission denied",
    r"Read-only file system",
    r"No space left on device",
    r"Input/output error",
    r"Timeout was reached",
    r"Connection refused",
    r"Failed to activate",
)

# Mount points that are critical for system operation
CRITICAL_MOUNT_POINTS = (
    "/",
    "/var",
    "/tmp",
    "/home",
)

# Minimum disk space requirements (path, min_mb)
DISK_SPACE_REQUIREMENTS = (
    ("/", 500),  # 500MB on root
    ("/var", 200),  # 200MB on /var
    ("/boot", 50),  # 50MB on /boot
)


class FailureDiagnostics(BaseModel):
    """Comprehensive diagnostics collected after a failed reboot.

    This model captures all relevant information needed for the LLM
    to understand what went wrong and propose a fix.
    """

    model_config = ConfigDict(frozen=True)

    # What we tried to do
    intent_goal: str = Field(description="The goal of the failed reboot")
    intent_reason: RebootReason = Field(description="Why reboot was needed")
    intent_reason_detail: str | None = Field(default=None)

    # What changes were made before reboot
    commands_before_reboot: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Commands executed in session before reboot",
    )
    config_changes: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Config files that were modified",
    )

    # Verification results
    failed_checks: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Verification checks that failed",
    )
    passed_checks: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Verification checks that passed",
    )

    # System state
    kernel_version: str | None = Field(default=None)
    previous_kernel: str | None = Field(default=None)
    boot_mode: str | None = Field(default=None, description="BIOS or UEFI")
    uptime_seconds: int | None = Field(default=None)

    # Log extracts (critical for LLM diagnosis)
    dmesg_errors: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Error lines from dmesg",
    )
    journal_errors: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Error lines from journald since boot",
    )
    failed_services: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Services in failed state",
    )

    # Filesystem state
    read_only_mounts: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Mount points that are read-only unexpectedly",
    )
    missing_mounts: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Expected mount points that are missing",
    )
    disk_space_issues: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Paths with low disk space",
    )

    # Network state
    interfaces_down: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Network interfaces that are down",
    )
    network_errors: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Network-related errors",
    )

    # Driver/module state
    missing_modules: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Kernel modules that should be loaded but aren't",
    )
    module_errors: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Errors related to kernel modules",
    )

    # GRUB state for rollback context
    current_grub_entry: str | None = Field(default=None)
    previous_grub_entry: str | None = Field(default=None)
    grub_entries_available: tuple[str, ...] = Field(default_factory=tuple)

    # Suggested focus areas for LLM
    likely_causes: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Likely root causes based on diagnostics",
    )
    suggested_investigation: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Suggested commands to run for more info",
    )

    def to_llm_context(self) -> str:
        """Format diagnostics as context for LLM prompt injection.

        Returns:
            Formatted string suitable for LLM prompt.
        """
        sections = []

        sections.append("## REBOOT FAILURE DIAGNOSTICS\n")
        sections.append(f"**Goal:** {self.intent_goal}")
        sections.append(f"**Reason:** {self.intent_reason}")
        if self.intent_reason_detail:
            sections.append(f"**Detail:** {self.intent_reason_detail}")

        if self.commands_before_reboot:
            sections.append("\n### Commands Executed Before Reboot")
            for cmd in self.commands_before_reboot[-10:]:  # Last 10
                sections.append(f"  $ {cmd}")

        if self.failed_checks:
            sections.append("\n### Failed Verification Checks")
            for check in self.failed_checks:
                sections.append(f"  - [{check.get('check_type')}] {check.get('check_command')}")
                stderr_val = check.get("stderr")
                if stderr_val:
                    sections.append(f"    stderr: {str(stderr_val)[:200]}")

        if self.dmesg_errors:
            sections.append("\n### Kernel Errors (dmesg)")
            for line in self.dmesg_errors[:15]:  # First 15
                sections.append(f"  {line}")

        if self.journal_errors:
            sections.append("\n### System Journal Errors")
            for line in self.journal_errors[:15]:
                sections.append(f"  {line}")

        if self.failed_services:
            sections.append("\n### Failed Services")
            for svc in self.failed_services:
                sections.append(f"  - {svc}")

        if self.read_only_mounts:
            sections.append("\n### Read-Only Mounts (PROBLEM)")
            for mount in self.read_only_mounts:
                sections.append(f"  - {mount}")

        if self.missing_mounts:
            sections.append("\n### Missing Mounts (PROBLEM)")
            for mount in self.missing_mounts:
                sections.append(f"  - {mount}")

        if self.disk_space_issues:
            sections.append("\n### Disk Space Issues")
            for issue in self.disk_space_issues:
                sections.append(f"  - {issue}")

        if self.interfaces_down:
            sections.append("\n### Network Interfaces Down")
            for iface in self.interfaces_down:
                sections.append(f"  - {iface}")

        if self.missing_modules:
            sections.append("\n### Missing Kernel Modules")
            for mod in self.missing_modules:
                sections.append(f"  - {mod}")

        if self.likely_causes:
            sections.append("\n### Likely Root Causes")
            for cause in self.likely_causes:
                sections.append(f"  - {cause}")

        if self.suggested_investigation:
            sections.append("\n### Suggested Investigation Commands")
            for cmd in self.suggested_investigation:
                sections.append(f"  $ {cmd}")

        sections.append("\n### System State")
        sections.append(f"  Kernel: {self.kernel_version or 'unknown'}")
        if self.previous_kernel:
            sections.append(f"  Previous kernel: {self.previous_kernel}")
        sections.append(f"  Boot mode: {self.boot_mode or 'unknown'}")
        if self.uptime_seconds:
            sections.append(f"  Uptime: {self.uptime_seconds}s")

        return "\n".join(sections)
