"""Data models for telemetry ingestion.

Extends the base TelemetryEvent with daemon-specific fields
and defines additional models for probe results and queue items.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# Extended severity levels (journald priorities)
TelemetrySeverity = Literal["debug", "info", "notice", "warning", "error", "critical"]

# Telemetry source types
TelemetrySource = Literal["journal", "kernel", "probe", "ebpf", "docker", "inotify"]

# Category types for event classification
TelemetryCategory = Literal[
    "oom",  # Out-of-memory events
    "disk",  # Disk/storage issues
    "net",  # Network issues
    "auth",  # Authentication/permission
    "service",  # Systemd service events
    "thermal",  # Temperature warnings
    "smart",  # SMART disk health
    "fs",  # Filesystem events
    "pkg",  # Package management
    "docker",  # Container events
    "other",  # Uncategorized
]


class TelemetryEvent(BaseModel):
    """A normalized telemetry event from any source.

    Extends the base TelemetryEvent with:
    - Unique event_id for deduplication
    - Entity extraction (service:nginx, interface:eth0, etc.)
    - Fingerprint for event grouping
    - Extended severity levels
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    event_id: str = Field(
        default_factory=lambda: uuid4().hex[:12],
        description="Unique event identifier",
    )

    # Timestamp
    ts: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Event timestamp",
    )

    # Source classification
    source: TelemetrySource = Field(description="Event source type")
    severity: TelemetrySeverity = Field(
        default="info",
        description="Event severity level",
    )
    category: str = Field(
        default="other",
        description="Event category for grouping",
    )

    # Content
    message: str = Field(description="Human-readable event message")

    # Entity tracking
    entity: str | None = Field(
        default=None,
        description="Affected entity (service:X, interface:Y, disk:Z)",
    )

    # Deduplication
    fingerprint: str | None = Field(
        default=None,
        description="SHA256 hash for event deduplication",
    )

    # Raw data
    raw: dict[str, Any] = Field(
        default_factory=dict,
        description="Original raw event data",
    )

    @classmethod
    def compute_fingerprint(cls, category: str, entity: str | None, message: str) -> str:
        """Compute a fingerprint for deduplication.

        Args:
            category: Event category.
            entity: Affected entity (optional).
            message: Event message.

        Returns:
            SHA256 hash truncated to 16 chars.
        """
        # Normalize message by removing timestamps and dynamic content
        normalized = message.lower()
        # Remove common dynamic parts
        for pattern in [
            r"\d{4}-\d{2}-\d{2}",  # Dates
            r"\d{2}:\d{2}:\d{2}",  # Times
            r"\b\d+\b",  # Numbers
        ]:
            import re

            normalized = re.sub(pattern, "", normalized)

        content = f"{category}:{entity or ''}:{normalized}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


class ProbeResult(BaseModel):
    """Result from a periodic system probe.

    Probes run on intervals and check system health metrics.
    Results may or may not generate TelemetryEvents depending
    on whether thresholds are exceeded.
    """

    model_config = ConfigDict(frozen=True)

    # Probe identification
    probe_name: str = Field(description="Name of the probe")
    ts: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Probe execution timestamp",
    )

    # Result
    success: bool = Field(
        default=True,
        description="Whether probe executed successfully",
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Probe-specific result data",
    )
    error: str | None = Field(
        default=None,
        description="Error message if probe failed",
    )

    # Event generation
    events: tuple[TelemetryEvent, ...] = Field(
        default_factory=tuple,
        description="Events generated from probe results",
    )


# Raw journal entry fields (documented for reference)
# These are the important journald JSON fields:
# - MESSAGE: Log message
# - __REALTIME_TIMESTAMP: Timestamp in microseconds since epoch
# - PRIORITY: Syslog priority (0-7)
# - SYSLOG_IDENTIFIER: Syslog identifier
# - _SYSTEMD_UNIT: Systemd unit name
# - _COMM: Command name
# - _EXE: Executable path
# - _BOOT_ID: Boot UUID
# - _MACHINE_ID: Machine UUID
# - _TRANSPORT: Transport type (journal, stdout, kernel)
#
# Raw events are processed as dict[str, Any] rather than Pydantic models
# to avoid issues with underscore field names


class QueueStats(BaseModel):
    """Statistics for a telemetry queue."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Queue name")
    size: int = Field(ge=0, description="Current queue size")
    max_size: int = Field(ge=0, description="Maximum queue size")
    dropped: int = Field(ge=0, default=0, description="Total dropped items")
    processed: int = Field(ge=0, default=0, description="Total processed items")

    @property
    def utilization(self) -> float:
        """Queue utilization percentage."""
        if self.max_size == 0:
            return 0.0
        return self.size / self.max_size


class DaemonStatus(BaseModel):
    """Overall daemon status for health checks."""

    model_config = ConfigDict(frozen=True)

    # Runtime info
    started_at: datetime = Field(description="Daemon start time")
    uptime_sec: int = Field(ge=0, description="Daemon uptime in seconds")
    pid: int = Field(description="Daemon process ID")

    # Component status
    journal_active: bool = Field(default=False)
    kernel_active: bool = Field(default=False)
    probes_active: bool = Field(default=False)
    api_active: bool = Field(default=False)

    # Queue stats
    raw_queue: QueueStats = Field(description="Raw event queue stats")
    event_queue: QueueStats = Field(description="Normalized event queue stats")

    # Counters
    events_total: int = Field(ge=0, default=0, description="Total events processed")
    events_1h: int = Field(ge=0, default=0, description="Events in last hour")
    incidents_total: int = Field(ge=0, default=0, description="Total incidents created")
    incidents_open: int = Field(ge=0, default=0, description="Currently open incidents")

    # Health
    healthy: bool = Field(default=True, description="Overall health status")
    errors: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Current error conditions",
    )
