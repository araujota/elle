"""Extended normalizer for eBPF events.

Provides specialized normalization for eBPF raw events, extracting
richer entity and context information from kernel-level data.
"""

import re
from datetime import UTC, datetime
from typing import Any

from elle.daemon.telemetry.models import TelemetryEvent


def is_ebpf_event(raw: dict[str, Any]) -> bool:
    """Check if raw event is from eBPF source.

    Args:
        raw: Raw event dict.

    Returns:
        True if this is an eBPF event.
    """
    return raw.get("_SOURCE") == "ebpf"


def normalize_ebpf_event(raw: dict[str, Any]) -> TelemetryEvent | None:
    """Normalize an eBPF raw event to TelemetryEvent.

    This is called by the main normalizer when it detects an eBPF event.

    Args:
        raw: Raw eBPF event dict.

    Returns:
        Normalized TelemetryEvent or None if invalid.
    """
    # Get message
    message = raw.get("MESSAGE", "")
    if not message:
        return None

    # Parse timestamp from kernel time (nanoseconds since boot)
    ts = _parse_ebpf_timestamp(raw)

    # Get severity from priority
    from elle.daemon.telemetry.normalizer import priority_to_severity

    priority = raw.get("PRIORITY", "6")
    severity = priority_to_severity(priority)

    # Get category from eBPF-specific field or detect from message
    category = raw.get("_EBPF_CATEGORY", "other")
    if category == "other":
        from elle.daemon.telemetry.normalizer import detect_category

        category = detect_category(message)

    # Extract entity with eBPF-specific logic
    entity = _extract_ebpf_entity(raw, category)

    # Generate fingerprint
    from elle.daemon.telemetry.normalizer import generate_fingerprint

    fingerprint = generate_fingerprint(category, entity, message)

    return TelemetryEvent(
        ts=ts,
        source="ebpf",  # type: ignore
        severity=severity,
        category=category,
        message=message,
        entity=entity,
        fingerprint=fingerprint,
        raw=raw,
    )


def _parse_ebpf_timestamp(raw: dict[str, Any]) -> datetime:
    """Parse timestamp from eBPF event.

    eBPF timestamps are kernel monotonic time in nanoseconds.
    We convert to wall clock time using current time.

    Args:
        raw: Raw event dict.

    Returns:
        Datetime timestamp.
    """
    # For now, just use current time
    # In a more sophisticated implementation, we could track
    # the offset between kernel monotonic time and wall clock
    return datetime.now(UTC)


def _extract_ebpf_entity(raw: dict[str, Any], category: str) -> str | None:
    """Extract entity from eBPF event.

    Uses eBPF-specific fields for richer entity extraction.

    Args:
        raw: Raw event dict.
        category: Event category.

    Returns:
        Entity string or None.
    """
    _event_type = raw.get("_EBPF_EVENT_TYPE", "")  # Available for future type-specific handling

    # OOM events - entity is the killed process
    if category == "oom":
        comm = raw.get("_COMM")
        if comm:
            return f"process:{comm}"

    # Disk events - entity is the device
    if category == "disk":
        # Try major:minor
        major = raw.get("_EBPF_MAJOR")
        minor = raw.get("_EBPF_MINOR")
        if major is not None and minor is not None:
            return f"disk:{major}:{minor}"
        # Try device name
        dev = raw.get("_EBPF_DEV")
        if dev:
            return f"disk:{dev}"

    # Network events - entity is the interface
    if category == "net":
        ifname = raw.get("_EBPF_IFNAME")
        if ifname:
            return f"interface:{ifname}"
        ifindex = raw.get("_EBPF_IFINDEX")
        if ifindex:
            return f"interface:ifindex_{ifindex}"

    # Service/process events - entity is the process
    if category == "service":
        comm = raw.get("_COMM")
        if comm:
            return f"process:{comm}"
        pid = raw.get("_PID")
        if pid:
            return f"pid:{pid}"

    # Thermal events - entity is the zone
    if category == "thermal":
        zone = raw.get("_EBPF_ZONE")
        if zone is not None:
            return f"thermal_zone:{zone}"
        cpu = raw.get("_EBPF_CPU")
        if cpu is not None:
            return f"cpu:{cpu}"

    return None


def get_ebpf_severity_override(raw: dict[str, Any]) -> str | None:
    """Get severity override based on eBPF event specifics.

    Some events should have elevated severity based on their
    specific data (e.g., signal number for crashes).

    Args:
        raw: Raw event dict.

    Returns:
        Severity string or None to use default.
    """
    event_type = raw.get("_EBPF_EVENT_TYPE", "")

    # Process exits - elevate if killed by signal
    if event_type == "process_exit":
        signal = raw.get("_EBPF_SIGNAL", 0)
        if signal in (6, 9, 11, 15):  # SIGABRT, SIGKILL, SIGSEGV, SIGTERM
            return "error"

    # Block I/O - elevate for certain error codes
    if event_type == "block_rq_error":
        error = abs(raw.get("_EBPF_ERROR", 0))
        if error in (5, 28):  # EIO, ENOSPC
            return "critical"

    return None


# Entity patterns for eBPF-specific messages
EBPF_ENTITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Process names from eBPF
    ("process", re.compile(r"process (\w+) \(PID", re.I)),
    # Device names from block I/O
    ("disk", re.compile(r"device (\w+)", re.I)),
    # Interface names from network
    ("interface", re.compile(r"on (eth\d+|enp\d+s\d+|wlan\d+)", re.I)),
    # Thermal zones
    ("thermal_zone", re.compile(r"zone (\d+)", re.I)),
]


def extract_entity_from_message(message: str) -> str | None:
    """Extract entity from eBPF message text.

    Fallback when raw fields don't contain entity info.

    Args:
        message: Message text.

    Returns:
        Entity string or None.
    """
    for entity_type, pattern in EBPF_ENTITY_PATTERNS:
        match = pattern.search(message)
        if match:
            return f"{entity_type}:{match.group(1)}"
    return None
