"""eBPF telemetry subsystem.

NOTE: eBPF telemetry is now handled by elled-telemetryd (C daemon).
This module provides capability checking and syscall analysis utilities.

For eBPF event capture, use elled-telemetryd instead of this module.
The C daemon provides:
- OOM kill tracing
- Process execution tracing
- Block I/O latency tracking
- TCP retransmission monitoring
- Capability denial tracing
- File permission denial tracing
- Mount operation monitoring
"""

from elle.daemon.telemetry.ebpf.capabilities import (
    CapabilityCheck,
    can_use_ebpf,
    check_capabilities,
    get_kernel_version_string,
    get_missing_capabilities,
)
from elle.daemon.telemetry.ebpf.models import (
    EBPF_EVENT_CATEGORIES,
    EBPF_EVENT_SEVERITIES,
    EBPFEventType,
    EBPFProgramStatus,
    EBPFRawEvent,
    EBPFWatcherStatus,
)

__all__ = [
    # Models
    "EBPFRawEvent",
    "EBPFProgramStatus",
    "EBPFWatcherStatus",
    "EBPFEventType",
    # Mappings
    "EBPF_EVENT_CATEGORIES",
    "EBPF_EVENT_SEVERITIES",
    # Capabilities
    "CapabilityCheck",
    "can_use_ebpf",
    "check_capabilities",
    "get_missing_capabilities",
    "get_kernel_version_string",
]
