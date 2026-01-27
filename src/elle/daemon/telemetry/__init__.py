"""Telemetry ingestion from elled-telemetryd.

This module provides the interface to receive pre-normalized events from
the C telemetry daemon (elled-telemetryd).

All telemetry collection, normalization, and deduplication is handled
by the C daemon. This Python code receives events via Unix socket.

Provides:
- TelemetrydWatcher: Reads pre-normalized events from C daemon
- TelemetryQueue: Bounded async queue with backpressure
"""

from elle.daemon.telemetry.models import DaemonStatus, ProbeResult, QueueStats, TelemetryEvent
from elle.daemon.telemetry.queue import TelemetryQueue, create_queues
from elle.daemon.telemetry.telemetryd_watcher import TelemetrydWatcher, is_telemetryd_available

__all__ = [
    # Telemetryd watcher
    "TelemetrydWatcher",
    "is_telemetryd_available",
    # Queue
    "TelemetryQueue",
    "create_queues",
    # Models
    "TelemetryEvent",
    "ProbeResult",
    "QueueStats",
    "DaemonStatus",
]
