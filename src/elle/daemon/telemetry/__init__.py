"""Telemetry ingestion from journal, kernel, and periodic probes.

Provides:
- JournalWatcher: Async journal event streaming
- KernelWatcher: Async kernel message streaming
- ProbeRunner: Periodic system health probes
- Normalizer: Raw event to TelemetryEvent conversion
- TelemetryQueue: Bounded async queue with backpressure
"""

from elle.daemon.telemetry.journal import JournalWatcher, watch_journal
from elle.daemon.telemetry.kernel import KernelWatcher, watch_kernel
from elle.daemon.telemetry.models import DaemonStatus, ProbeResult, QueueStats, TelemetryEvent
from elle.daemon.telemetry.normalizer import Normalizer, normalize_event, normalize_events
from elle.daemon.telemetry.package_probe import PackageProbe
from elle.daemon.telemetry.probes import (
    BaseProbe,
    DiskProbe,
    MemoryProbe,
    NetworkProbe,
    ProbeRunner,
    SmartProbe,
    ThermalProbe,
    run_probes,
)
from elle.daemon.telemetry.queue import TelemetryQueue, create_queues

__all__ = [
    # Watchers
    "JournalWatcher",
    "KernelWatcher",
    "watch_journal",
    "watch_kernel",
    # Probes
    "BaseProbe",
    "MemoryProbe",
    "DiskProbe",
    "NetworkProbe",
    "ThermalProbe",
    "SmartProbe",
    "ProbeRunner",
    "run_probes",
    # Normalizer
    "Normalizer",
    "normalize_event",
    "normalize_events",
    # Queue
    "TelemetryQueue",
    "create_queues",
    # Models
    "TelemetryEvent",
    "ProbeResult",
    "QueueStats",
    "DaemonStatus",
    # Package Probe
    "PackageProbe",
]
