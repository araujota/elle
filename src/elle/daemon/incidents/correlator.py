"""Event-to-incident correlation.

Groups related telemetry events into incidents based on:
- Category/domain
- Time window
- Shared entities

Implements basic incident detection heuristics.
"""

import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from elle.daemon.incidents.models import IncidentDomain
from elle.daemon.incidents.snapshot import collect_snapshot, extract_fingerprint
from elle.daemon.incidents.store import (
    attach_snapshot,
    create_incident_draft,
    link_events,
    update_incident,
)

# Domain detection patterns
DOMAIN_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # Network
    ("net", re.compile(r"(network|eth\d|wlan\d|enp\d|ens\d|NetworkManager|DNS|DHCP|connection refused|timeout)", re.I), "net"),
    ("net", re.compile(r"(link is (down|up)|carrier lost|no route)", re.I), "net"),

    # Disk
    ("disk", re.compile(r"(disk|nvme|sda|sdb|I/O error|read error|write error|SMART|filesystem)", re.I), "disk"),
    ("disk", re.compile(r"(No space left|quota exceeded|disk full)", re.I), "disk"),

    # OOM
    ("oom", re.compile(r"(Out of memory|oom[-_]?kill|Killed process|invoked oom-killer)", re.I), "oom"),
    ("oom", re.compile(r"(memory pressure|cannot allocate memory)", re.I), "oom"),

    # Docker
    ("docker", re.compile(r"(docker|container|containerd|podman)", re.I), "docker"),

    # Auth
    ("auth", re.compile(r"(authentication|auth|login|password|pam_unix|sshd|sudo|permission denied)", re.I), "auth"),
    ("auth", re.compile(r"(failed password|invalid user|unauthorized)", re.I), "auth"),

    # Package
    ("pkg", re.compile(r"(apt|apt-get|dpkg|snap|package|dependency|unmet dependencies|Unable to fetch|repository)", re.I), "pkg"),

    # Filesystem
    ("fs", re.compile(r"(mount|unmount|fstab|ext4|xfs|btrfs|inode)", re.I), "fs"),

    # Service
    ("service", re.compile(r"(systemd|service|unit|failed to start|entered failed state)", re.I), "service"),
]

# Entity extraction patterns
ENTITY_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Service patterns: "nginx.service", "service nginx", "unit nginx.service"
    ("service", re.compile(r"([a-zA-Z0-9_-]+)\.service", re.I)),
    ("service", re.compile(r"(?:service|unit)\s+([a-zA-Z0-9_-]+)", re.I)),
    ("interface", re.compile(r"(eth\d+|wlan\d+|enp\d+s\d+|ens\d+|wlp\d+s\d+)", re.I)),
    ("device", re.compile(r"(/dev/(?:sd[a-z]\d*|nvme\d+n\d+(?:p\d+)?|loop\d+))", re.I)),
    ("container", re.compile(r"container[:\s]+([a-f0-9]{12}|[a-zA-Z0-9_-]+)", re.I)),
    ("mount", re.compile(r"(?:mount point|mounted on)\s+([/\w]+)", re.I)),
]


class IncidentCorrelator:
    """Correlates events into incidents."""

    def __init__(
        self,
        time_window_sec: int = 600,  # 10 minutes
        min_events_for_incident: int = 1,
        critical_triggers_immediate: bool = True,
    ):
        """Initialize the correlator.

        Args:
            time_window_sec: Time window for grouping events.
            min_events_for_incident: Minimum events to trigger incident.
            critical_triggers_immediate: If True, critical events trigger immediately.
        """
        self.time_window_sec = time_window_sec
        self.min_events_for_incident = min_events_for_incident
        self.critical_triggers_immediate = critical_triggers_immediate

        # Event buffer for correlation
        self._event_buffer: list[dict[str, Any]] = []

        # Active incident tracking
        self._active_incidents: dict[str, str] = {}  # key -> incident_id

    def process_event(
        self,
        event: dict[str, Any],
        conn: sqlite3.Connection | None = None,
    ) -> str | None:
        """Process a telemetry event and potentially create/update an incident.

        Args:
            event: Telemetry event dict with ts, severity, category, message, etc.
            conn: SQLite connection.

        Returns:
            Incident ID if an incident was created/updated, None otherwise.
        """
        # Add to buffer
        self._event_buffer.append(event)

        # Clean old events from buffer
        self._clean_buffer()

        # Check for immediate trigger (critical severity)
        if self.critical_triggers_immediate and event.get("severity") == "critical":
            return self._create_incident_from_event(event, conn)

        # Check for pattern-based incident
        incident_id = self._check_for_incident_pattern(event, conn)

        return incident_id

    def create_from_command_failure(
        self,
        command: str,
        stderr: str,
        exit_code: int,
        conn: sqlite3.Connection | None = None,
    ) -> str:
        """Create an incident from a failed command.

        Args:
            command: The command that failed.
            stderr: Stderr output.
            exit_code: Exit code.
            conn: SQLite connection.

        Returns:
            Incident ID.
        """
        # Detect domain from stderr
        domain = self._detect_domain(stderr)

        # Extract entities
        entities = self._extract_entities(stderr)

        # Create title
        cmd_short = command.split()[0] if command else "command"
        title = f"Command failed: {cmd_short} (exit {exit_code})"

        # Collect snapshot
        snapshot = collect_snapshot()
        fingerprint = extract_fingerprint(
            snapshot,
            entities=entities,
        )

        # Create incident
        incident = create_incident_draft(
            title=title,
            domain=domain,
            severity="error",
            trigger_source="command_failure",
            trigger_command=command,
            conn=conn,
        )

        # Attach snapshot
        attach_snapshot(
            incident.incident_id,
            "pre",
            snapshot,
            conn=conn,
        )

        # Update with details
        update_incident(
            incident.incident_id,
            summary=f"Command '{command}' failed with exit code {exit_code}.",
            symptoms=[
                f"Exit code: {exit_code}",
                f"Stderr: {stderr[:200]}..." if len(stderr) > 200 else f"Stderr: {stderr}",
            ],
            log_snippets=[stderr[:500]] if stderr else [],
            fingerprint=fingerprint,
            conn=conn,
        )

        return incident.incident_id

    def _clean_buffer(self) -> None:
        """Remove old events from the buffer."""
        cutoff = datetime.utcnow() - timedelta(seconds=self.time_window_sec * 2)
        self._event_buffer = [
            e for e in self._event_buffer
            if self._get_event_time(e) > cutoff
        ]

    def _get_event_time(self, event: dict[str, Any]) -> datetime:
        """Get timestamp from event."""
        ts = event.get("ts")
        if isinstance(ts, datetime):
            return ts
        if isinstance(ts, str):
            return datetime.fromisoformat(ts)
        return datetime.utcnow()

    def _detect_domain(self, text: str) -> IncidentDomain:
        """Detect incident domain from text."""
        for domain, pattern, _ in DOMAIN_PATTERNS:
            if pattern.search(text):
                return domain  # type: ignore
        return "other"

    def _extract_entities(self, text: str) -> list[str]:
        """Extract entity names from text."""
        entities = []
        for entity_type, pattern in ENTITY_PATTERNS:
            matches = pattern.findall(text)
            for match in matches:
                entity = f"{entity_type}:{match}" if entity_type else match
                if entity not in entities:
                    entities.append(entity)
        return entities

    def _get_correlation_key(self, event: dict[str, Any]) -> str:
        """Generate a correlation key for grouping events."""
        domain = self._detect_domain(event.get("message", ""))
        entities = self._extract_entities(event.get("message", ""))
        entity_key = "_".join(sorted(entities[:3])) if entities else "none"
        return f"{domain}:{entity_key}"

    def _check_for_incident_pattern(
        self,
        event: dict[str, Any],
        conn: sqlite3.Connection | None,
    ) -> str | None:
        """Check if events form a pattern warranting an incident."""
        key = self._get_correlation_key(event)
        cutoff = datetime.utcnow() - timedelta(seconds=self.time_window_sec)

        # Count related events in window
        related = [
            e for e in self._event_buffer
            if self._get_correlation_key(e) == key
            and self._get_event_time(e) > cutoff
        ]

        # Check threshold
        if len(related) >= self.min_events_for_incident:
            # Check if we already have an active incident for this key
            if key in self._active_incidents:
                incident_id = self._active_incidents[key]
                # Link new events
                event_ids = [str(e.get("id", "")) for e in related if e.get("id")]
                if event_ids:
                    link_events(incident_id, event_ids, conn)
                return incident_id

            # Create new incident
            incident_id = self._create_incident_from_events(related, conn)
            if incident_id:
                self._active_incidents[key] = incident_id
            return incident_id

        return None

    def _create_incident_from_event(
        self,
        event: dict[str, Any],
        conn: sqlite3.Connection | None,
    ) -> str:
        """Create an incident from a single event."""
        return self._create_incident_from_events([event], conn)

    def _create_incident_from_events(
        self,
        events: list[dict[str, Any]],
        conn: sqlite3.Connection | None,
    ) -> str:
        """Create an incident from a group of events."""
        if not events:
            return ""

        # Aggregate information
        messages = [e.get("message", "") for e in events]
        combined_text = " ".join(messages)

        domain = self._detect_domain(combined_text)
        entities = self._extract_entities(combined_text)

        # Determine severity (use highest)
        severities = [e.get("severity", "info") for e in events]
        severity_order = ["info", "warning", "error", "critical"]
        max_severity = max(severities, key=lambda s: severity_order.index(s) if s in severity_order else 0)

        # Generate title
        if entities:
            title = f"{domain.upper()}: Issue with {entities[0].split(':')[-1]}"
        else:
            title = f"{domain.upper()}: Detected anomaly"

        # Collect snapshot
        snapshot = collect_snapshot()
        fingerprint = extract_fingerprint(
            snapshot,
            entities=entities,
        )

        # Create incident
        incident = create_incident_draft(
            title=title,
            domain=domain,
            severity=max_severity,
            trigger_source="telemetry",
            conn=conn,
        )

        # Attach snapshot
        attach_snapshot(
            incident.incident_id,
            "pre",
            snapshot,
            conn=conn,
        )

        # Update with details
        symptoms = list(set(messages[:5]))  # Deduplicated symptoms
        log_snippets = messages[:3]

        update_incident(
            incident.incident_id,
            symptoms=symptoms,
            log_snippets=log_snippets,
            fingerprint=fingerprint,
            conn=conn,
        )

        # Link events
        event_ids = [str(e.get("id", "")) for e in events if e.get("id")]
        if event_ids:
            link_events(incident.incident_id, event_ids, conn)

        return incident.incident_id

    def clear_active_incident(self, key: str) -> None:
        """Clear an active incident tracking key."""
        if key in self._active_incidents:
            del self._active_incidents[key]


# Module-level correlator instance
_correlator: IncidentCorrelator | None = None


def get_correlator() -> IncidentCorrelator:
    """Get the shared incident correlator."""
    global _correlator
    if _correlator is None:
        _correlator = IncidentCorrelator()
    return _correlator


def reset_correlator() -> None:
    """Reset the correlator (for testing)."""
    global _correlator
    _correlator = None
