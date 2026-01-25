"""Normalizer for converting raw events to TelemetryEvents.

Handles:
- Priority mapping (journald priority -> severity)
- Category detection (regex patterns)
- Entity extraction (service:X, interface:Y)
- Fingerprint generation for deduplication
"""

import re
from datetime import UTC, datetime
from typing import Any

from elle.daemon.telemetry.models import TelemetryEvent, TelemetrySeverity


# =============================================================================
# Priority Mapping
# =============================================================================

# journald priority to severity
PRIORITY_MAP: dict[int, TelemetrySeverity] = {
    0: "critical",  # emerg
    1: "critical",  # alert
    2: "critical",  # crit
    3: "error",     # err
    4: "warning",   # warning
    5: "notice",    # notice
    6: "info",      # info
    7: "debug",     # debug
}


def priority_to_severity(priority: int | str | None) -> TelemetrySeverity:
    """Convert journald priority to TelemetryEvent severity.

    Args:
        priority: journald priority (0-7) or string.

    Returns:
        Corresponding severity level.
    """
    if priority is None:
        return "info"

    try:
        p = int(priority)
        return PRIORITY_MAP.get(p, "info")
    except (ValueError, TypeError):
        return "info"


# =============================================================================
# Category Detection
# =============================================================================

# Compiled regex patterns for category detection
CATEGORY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # OOM events (highest priority - check first)
    (re.compile(r"Out of memory|oom[-_]?kill|invoked oom-killer|Killed process", re.I), "oom"),
    (re.compile(r"cannot allocate memory|memory cgroup out of memory", re.I), "oom"),

    # Disk/storage events
    (re.compile(r"I/O error|buffer I/O error|SCSI error|read error|write error", re.I), "disk"),
    (re.compile(r"SMART|smartd|sector|bad block|pending sector", re.I), "smart"),
    (re.compile(r"No space left|disk full|quota exceeded|ENOSPC", re.I), "disk"),
    (re.compile(r"EXT4-fs error|XFS error|BTRFS error|filesystem error", re.I), "fs"),

    # Network events
    (re.compile(r"link (up|down)|carrier (lost|acquired)", re.I), "net"),
    (re.compile(r"connection (refused|timed out|reset)", re.I), "net"),
    (re.compile(r"NetworkManager|network unreachable|no route to host", re.I), "net"),
    (re.compile(r"DHCP|DNS|timeout waiting for|network is down", re.I), "net"),
    (re.compile(r"(eth|enp|ens|wlan|wlp)\d+.*error", re.I), "net"),

    # Authentication events
    (re.compile(r"authentication failure|pam_unix.*failed", re.I), "auth"),
    (re.compile(r"invalid user|failed password|Failed publickey", re.I), "auth"),
    (re.compile(r"unauthorized|permission denied|access denied", re.I), "auth"),
    (re.compile(r"sshd.*Failed|sudo:.*incorrect password", re.I), "auth"),

    # Service events
    (re.compile(r"(Started|Stopped|Starting|Stopping).*\.service", re.I), "service"),
    (re.compile(r"Failed to start|entered failed state|failed with result", re.I), "service"),
    (re.compile(r"systemd\[\d+\]:", re.I), "service"),
    (re.compile(r"Unit .* entered failed state", re.I), "service"),

    # Thermal events
    (re.compile(r"thermal|temperature|throttl|overheat", re.I), "thermal"),
    (re.compile(r"CPU\d+ package.*above threshold", re.I), "thermal"),

    # Package management
    (re.compile(r"apt|apt-get|dpkg|snap|flatpak", re.I), "pkg"),
    (re.compile(r"package|dependency|unmet dependencies", re.I), "pkg"),
    (re.compile(r"Unable to fetch|repository.*failed", re.I), "pkg"),

    # Docker/container events
    (re.compile(r"docker|container|containerd|podman|runc", re.I), "docker"),
    (re.compile(r"container (started|stopped|died|killed)", re.I), "docker"),
    (re.compile(r"Container .* (crashloop|oom|died)", re.I), "docker"),
    (re.compile(r"crashlooping|restart.*times", re.I), "docker"),

    # File monitoring events (auth-sensitive files)
    (re.compile(r"authorized_keys|sshd_config|ssh key", re.I), "auth"),
    (re.compile(r"/etc/passwd|/etc/shadow|/etc/sudoers", re.I), "auth"),
    (re.compile(r"File modified:.*/(authorized_keys|sshd_config|sudoers)", re.I), "auth"),

    # Port monitoring events
    (re.compile(r"New listener on port|unauthorized.*port", re.I), "net"),
    (re.compile(r"process.*listening on.*port", re.I), "net"),
    (re.compile(r"port:\d+/(tcp|udp)", re.I), "net"),

    # WireGuard events
    (re.compile(r"wireguard|wg\d+|peer.*handshake.*stale", re.I), "net"),
    (re.compile(r"wg-quick|wg show|handshake.*minutes ago", re.I), "net"),

    # Mount/filesystem events
    (re.compile(r"mount|umount|fstab|mounted|unmounted", re.I), "fs"),
]


def detect_category(message: str) -> str:
    """Detect category from message content.

    Args:
        message: Log message text.

    Returns:
        Detected category or 'other'.
    """
    for pattern, category in CATEGORY_PATTERNS:
        if pattern.search(message):
            return category
    return "other"


# =============================================================================
# Entity Extraction
# =============================================================================

# Entity extraction patterns
ENTITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Service units
    ("service", re.compile(r"([a-zA-Z0-9_@-]+)\.service", re.I)),
    ("service", re.compile(r"(?:unit|service)\s+([a-zA-Z0-9_@-]+)", re.I)),

    # Network interfaces
    ("interface", re.compile(r"\b(eth\d+|enp\d+s\d+|ens\d+|wlan\d+|wlp\d+s\d+)\b", re.I)),

    # Block devices
    ("disk", re.compile(r"(/dev/(?:sd[a-z]\d*|nvme\d+n\d+(?:p\d+)?|loop\d+))", re.I)),
    ("disk", re.compile(r"\b(sd[a-z]|nvme\d+n\d+)\b", re.I)),

    # Docker containers
    ("container", re.compile(r"container[=:\s]+([a-f0-9]{12}|[a-f0-9]{64})", re.I)),
    ("container", re.compile(r"container\s+([a-zA-Z0-9_-]+)\s+\(", re.I)),

    # Mount points
    ("mount", re.compile(r"(?:mount point|mounted on|umount)\s+([/\w]+)", re.I)),

    # Users (for auth events)
    ("user", re.compile(r"user[=:\s]+([a-zA-Z0-9_-]+)", re.I)),
    ("user", re.compile(r"invalid user\s+([a-zA-Z0-9_-]+)", re.I)),

    # PIDs (for process events)
    ("pid", re.compile(r"(?:pid|process)\s*[=:\s]*(\d+)", re.I)),

    # Ports (for network events)
    ("port", re.compile(r"port[:\s]+(\d+)/(tcp|udp)", re.I)),
    ("port", re.compile(r"port\s+(\d+)", re.I)),
    ("port", re.compile(r"listening on.*:(\d+)", re.I)),

    # Files (for file monitoring events)
    ("file", re.compile(r"File (?:modified|created|deleted):\s*(.+?)(?:\s|$)", re.I)),
    ("file", re.compile(r"authorized_keys|sshd_config|sudoers", re.I)),

    # WireGuard interfaces
    ("wg_interface", re.compile(r"\b(wg\d+)\b", re.I)),
]


def extract_entity(message: str, category: str, raw: dict[str, Any]) -> str | None:
    """Extract the primary entity from message and raw data.

    Args:
        message: Log message text.
        category: Detected category.
        raw: Raw event data.

    Returns:
        Entity string (type:name) or None.
    """
    # Try to extract from raw data first (more reliable)
    if category == "service":
        # Check systemd unit from raw
        unit = raw.get("_SYSTEMD_UNIT") or raw.get("UNIT")
        if unit:
            # Remove .service suffix for cleaner display
            name = unit.removesuffix(".service")
            return f"service:{name}"

    # Fall back to pattern matching on message
    for entity_type, pattern in ENTITY_PATTERNS:
        match = pattern.search(message)
        if match:
            name = match.group(1)
            # Only return entity types relevant to the category
            if category in ("service",) and entity_type == "service":
                return f"service:{name.removesuffix('.service')}"
            if category in ("net",) and entity_type == "interface":
                return f"interface:{name}"
            if category in ("disk", "smart", "fs") and entity_type == "disk":
                return f"disk:{name}"
            if category == "docker" and entity_type == "container":
                return f"container:{name[:12]}"
            if category == "auth" and entity_type == "user":
                return f"user:{name}"
            if category == "auth" and entity_type == "file":
                return f"file:{name}"
            if category == "net" and entity_type == "port":
                return f"port:{name}"
            if category == "net" and entity_type == "wg_interface":
                return f"interface:{name}"
            # Generic case - return first match
            if entity_type not in ("pid",):  # Skip PIDs as they're too transient
                return f"{entity_type}:{name}"

    return None


# =============================================================================
# Fingerprint Generation
# =============================================================================


def generate_fingerprint(category: str, entity: str | None, message: str) -> str:
    """Generate a fingerprint for event deduplication.

    The fingerprint is based on normalized message content,
    category, and entity to group similar events.

    Args:
        category: Event category.
        entity: Affected entity.
        message: Event message.

    Returns:
        SHA256 hash truncated to 16 chars.
    """
    return TelemetryEvent.compute_fingerprint(category, entity, message)


# =============================================================================
# Timestamp Parsing
# =============================================================================


def parse_timestamp(raw: dict[str, Any]) -> datetime:
    """Parse timestamp from raw journal data.

    Args:
        raw: Raw journal entry data.

    Returns:
        Parsed datetime.
    """
    # Try __REALTIME_TIMESTAMP (microseconds since epoch)
    realtime = raw.get("__REALTIME_TIMESTAMP")
    if realtime:
        try:
            ts_us = int(realtime)
            return datetime.fromtimestamp(ts_us / 1_000_000, UTC)
        except (ValueError, TypeError, OverflowError):
            pass

    # Try _SOURCE_REALTIME_TIMESTAMP
    source_realtime = raw.get("_SOURCE_REALTIME_TIMESTAMP")
    if source_realtime:
        try:
            ts_us = int(source_realtime)
            return datetime.fromtimestamp(ts_us / 1_000_000, UTC)
        except (ValueError, TypeError, OverflowError):
            pass

    # Fall back to current time
    return datetime.now(UTC)


# =============================================================================
# Main Normalizer
# =============================================================================


class Normalizer:
    """Normalizes raw journal/kernel events into TelemetryEvents.

    Handles category detection, entity extraction, fingerprinting,
    and deduplication within a time window.
    """

    def __init__(self, dedupe_window_sec: int = 60):
        """Initialize the normalizer.

        Args:
            dedupe_window_sec: Window for fingerprint deduplication.
        """
        self._dedupe_window = dedupe_window_sec
        self._recent_fingerprints: dict[str, datetime] = {}
        self._stats = {
            "processed": 0,
            "deduplicated": 0,
            "by_category": {},
        }

    def normalize(self, raw: dict[str, Any]) -> TelemetryEvent | None:
        """Normalize a raw event into a TelemetryEvent.

        Args:
            raw: Raw journal/kernel/ebpf entry data.

        Returns:
            TelemetryEvent or None if deduplicated.
        """
        self._stats["processed"] += 1

        # Check for eBPF events - use specialized normalizer
        if raw.get("_SOURCE") == "ebpf":
            return self._normalize_ebpf(raw)

        # Determine source
        source = raw.get("_SOURCE", "journal")
        if raw.get("_TRANSPORT") == "kernel":
            source = "kernel"

        # Get message
        message = raw.get("MESSAGE", "")
        if not message:
            return None

        # Parse timestamp
        ts = parse_timestamp(raw)

        # Determine severity
        priority = raw.get("PRIORITY")
        severity = priority_to_severity(priority)

        # Detect category
        category = detect_category(message)

        # Extract entity
        entity = extract_entity(message, category, raw)

        # Generate fingerprint
        fingerprint = generate_fingerprint(category, entity, message)

        # Check deduplication
        if self._is_duplicate(fingerprint, ts):
            self._stats["deduplicated"] += 1
            return None

        # Track by category
        self._stats["by_category"][category] = (
            self._stats["by_category"].get(category, 0) + 1
        )

        return TelemetryEvent(
            ts=ts,
            source=source,  # type: ignore
            severity=severity,
            category=category,
            message=message,
            entity=entity,
            fingerprint=fingerprint,
            raw=raw,
        )

    def normalize_batch(
        self,
        raw_events: list[dict[str, Any]],
    ) -> list[TelemetryEvent]:
        """Normalize a batch of raw events.

        Args:
            raw_events: List of raw event data.

        Returns:
            List of TelemetryEvents (excluding duplicates).
        """
        events = []
        for raw in raw_events:
            event = self.normalize(raw)
            if event:
                events.append(event)
        return events

    def _is_duplicate(self, fingerprint: str, ts: datetime) -> bool:
        """Check if fingerprint is a duplicate within the window.

        Args:
            fingerprint: Event fingerprint.
            ts: Event timestamp.

        Returns:
            True if duplicate, False otherwise.
        """
        # Clean old fingerprints
        self._clean_fingerprints(ts)

        # Check if seen recently
        if fingerprint in self._recent_fingerprints:
            return True

        # Record new fingerprint
        self._recent_fingerprints[fingerprint] = ts
        return False

    def _clean_fingerprints(self, now: datetime) -> None:
        """Remove fingerprints outside the deduplication window.

        Args:
            now: Current timestamp.
        """
        from datetime import timedelta

        cutoff = now - timedelta(seconds=self._dedupe_window)
        expired = [
            fp for fp, ts in self._recent_fingerprints.items()
            if ts < cutoff
        ]
        for fp in expired:
            del self._recent_fingerprints[fp]

    def _normalize_ebpf(self, raw: dict[str, Any]) -> TelemetryEvent | None:
        """Normalize an eBPF event.

        Delegates to the specialized eBPF normalizer.

        Args:
            raw: Raw eBPF event data.

        Returns:
            TelemetryEvent or None if deduplicated.
        """
        from elle.daemon.telemetry.ebpf.normalizer import normalize_ebpf_event

        event = normalize_ebpf_event(raw)
        if not event:
            return None

        # Check deduplication
        if self._is_duplicate(event.fingerprint or "", event.ts):
            self._stats["deduplicated"] += 1
            return None

        # Track by category
        self._stats["by_category"][event.category] = (
            self._stats["by_category"].get(event.category, 0) + 1
        )

        return event

    def get_stats(self) -> dict[str, Any]:
        """Get normalizer statistics.

        Returns:
            Stats dictionary.
        """
        return self._stats.copy()

    def reset_stats(self) -> None:
        """Reset statistics counters."""
        self._stats = {
            "processed": 0,
            "deduplicated": 0,
            "by_category": {},
        }


# Module-level normalizer instance
_normalizer: Normalizer | None = None


def get_normalizer() -> Normalizer:
    """Get the shared normalizer instance."""
    global _normalizer
    if _normalizer is None:
        _normalizer = Normalizer()
    return _normalizer


def reset_normalizer() -> None:
    """Reset the normalizer (for testing)."""
    global _normalizer
    _normalizer = None


# Convenience functions
def normalize_event(raw: dict[str, Any]) -> TelemetryEvent | None:
    """Normalize a single raw event using shared normalizer."""
    return get_normalizer().normalize(raw)


def normalize_events(raw_events: list[dict[str, Any]]) -> list[TelemetryEvent]:
    """Normalize a batch of raw events using shared normalizer."""
    return get_normalizer().normalize_batch(raw_events)
