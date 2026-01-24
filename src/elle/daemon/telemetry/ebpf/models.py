"""Data models for eBPF telemetry.

Defines Pydantic models for eBPF raw events, program status,
and overall watcher status.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EBPFRawEvent(BaseModel):
    """Raw event from eBPF ring buffer.

    This represents the structured data extracted from the kernel
    before normalization into TelemetryEvent.
    """

    model_config = ConfigDict(frozen=True)

    # Kernel timestamp (nanoseconds since boot)
    ts_ns: int = Field(description="Kernel timestamp in nanoseconds")

    # CPU where event occurred
    cpu: int = Field(ge=0, description="CPU core where event occurred")

    # Event type identifier
    event_type: str = Field(
        description="Event type (oom_kill, block_rq_complete, net_drop, etc.)"
    )

    # Process context (may be None for kernel-only events)
    pid: int | None = Field(default=None, description="Process ID")
    tgid: int | None = Field(default=None, description="Thread group ID")
    uid: int | None = Field(default=None, description="User ID")
    comm: str | None = Field(
        default=None,
        max_length=16,
        description="Process name (16 chars max in kernel)",
    )

    # Event-specific payload
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific data payload",
    )


class EBPFProgramStatus(BaseModel):
    """Status of a loaded eBPF program.

    Tracks whether a program is loaded, attached, and its
    event/error counters.
    """

    model_config = ConfigDict(frozen=True)

    # Program identification
    name: str = Field(description="Program name (oom, block_io, etc.)")
    category: str = Field(description="Event category this program produces")

    # Load state
    loaded: bool = Field(default=False, description="Whether program is loaded")
    attached: bool = Field(default=False, description="Whether program is attached")

    # Hook information
    hook_type: str = Field(
        default="",
        description="Hook type (tracepoint, kprobe, kretprobe)",
    )
    hook_name: str = Field(
        default="",
        description="Hook name (e.g., 'oom:mark_victim')",
    )

    # Counters
    events_count: int = Field(ge=0, default=0, description="Total events produced")
    errors_count: int = Field(ge=0, default=0, description="Total errors encountered")

    # Last activity
    last_event: datetime | None = Field(
        default=None,
        description="Timestamp of last event",
    )


class EBPFWatcherStatus(BaseModel):
    """Overall eBPF subsystem status.

    Provides a summary of the eBPF watcher's health,
    capabilities, and all loaded programs.
    """

    model_config = ConfigDict(frozen=True)

    # Overall status
    enabled: bool = Field(default=False, description="Whether eBPF is enabled")
    capabilities_ok: bool = Field(
        default=False,
        description="Whether required capabilities are available",
    )
    missing_capabilities: tuple[str, ...] = Field(
        default_factory=tuple,
        description="List of missing capabilities",
    )

    # Program status
    programs: tuple[EBPFProgramStatus, ...] = Field(
        default_factory=tuple,
        description="Status of all loaded programs",
    )

    # Aggregate counters
    total_events: int = Field(ge=0, default=0, description="Total events from all programs")
    uptime_sec: int = Field(ge=0, default=0, description="Watcher uptime in seconds")

    # Backend information
    backend: str = Field(default="bcc", description="eBPF backend (bcc or libbpf)")
    kernel_version: str = Field(default="", description="Kernel version")


# Event type constants for consistency
class EBPFEventType:
    """Constants for eBPF event types."""

    # OOM events
    OOM_KILL = "oom_kill"
    OOM_SCORE_UPDATE = "oom_score_update"

    # Block I/O events
    BLOCK_RQ_COMPLETE = "block_rq_complete"
    BLOCK_RQ_ERROR = "block_rq_error"

    # Network events
    NET_DROP = "net_drop"
    NET_RETRANSMIT = "net_retransmit"

    # Process events
    PROCESS_EXIT = "process_exit"
    SIGNAL_DELIVER = "signal_deliver"

    # Thermal events
    THERMAL_ZONE_TRIP = "thermal_zone_trip"
    CPU_FREQUENCY = "cpu_frequency"


# Category mapping for eBPF events
EBPF_EVENT_CATEGORIES: dict[str, str] = {
    EBPFEventType.OOM_KILL: "oom",
    EBPFEventType.OOM_SCORE_UPDATE: "oom",
    EBPFEventType.BLOCK_RQ_COMPLETE: "disk",
    EBPFEventType.BLOCK_RQ_ERROR: "disk",
    EBPFEventType.NET_DROP: "net",
    EBPFEventType.NET_RETRANSMIT: "net",
    EBPFEventType.PROCESS_EXIT: "service",
    EBPFEventType.SIGNAL_DELIVER: "service",
    EBPFEventType.THERMAL_ZONE_TRIP: "thermal",
    EBPFEventType.CPU_FREQUENCY: "thermal",
}


# Severity mapping for eBPF events
EBPF_EVENT_SEVERITIES: dict[str, str] = {
    EBPFEventType.OOM_KILL: "critical",
    EBPFEventType.OOM_SCORE_UPDATE: "info",
    EBPFEventType.BLOCK_RQ_COMPLETE: "info",
    EBPFEventType.BLOCK_RQ_ERROR: "error",
    EBPFEventType.NET_DROP: "warning",
    EBPFEventType.NET_RETRANSMIT: "warning",
    EBPFEventType.PROCESS_EXIT: "info",  # Elevated based on signal
    EBPFEventType.SIGNAL_DELIVER: "warning",
    EBPFEventType.THERMAL_ZONE_TRIP: "warning",
    EBPFEventType.CPU_FREQUENCY: "info",
}
