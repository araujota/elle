"""eBPF telemetry subsystem.

Provides low-latency kernel event capture using eBPF with BCC backend.

This module includes:
- EBPFWatcher: Main async watcher that polls ring buffers
- BCC programs: OOM, Block I/O, Network, Process, Thermal
- Capability checking for CAP_BPF/CAP_PERFMON
- Extended normalizer for eBPF events
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
from elle.daemon.telemetry.ebpf.normalizer import (
    is_ebpf_event,
    normalize_ebpf_event,
)
from elle.daemon.telemetry.ebpf.watcher import (
    EBPFWatcher,
    watch_ebpf,
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
    # Watcher
    "EBPFWatcher",
    "watch_ebpf",
    # Normalizer
    "is_ebpf_event",
    "normalize_ebpf_event",
]
