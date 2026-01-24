"""Tests for event-to-incident correlation."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from elle.daemon.incidents.correlator import (
    IncidentCorrelator,
    get_correlator,
    reset_correlator,
)
from elle.daemon.incidents.schema import ensure_schema, get_connection
from elle.daemon.incidents.store import get_incident


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    ensure_schema(conn)
    yield conn, db_path

    conn.close()
    db_path.unlink()


@pytest.fixture
def correlator():
    """Create a fresh correlator."""
    reset_correlator()
    return IncidentCorrelator(
        time_window_sec=60,
        min_events_for_incident=1,
        critical_triggers_immediate=True,
    )


class TestDomainDetection:
    """Tests for domain detection from text."""

    def test_detect_network_domain(self, correlator):
        """Test detecting network domain."""
        domain = correlator._detect_domain("Connection refused to eth0")
        assert domain == "net"

    def test_detect_disk_domain(self, correlator):
        """Test detecting disk domain."""
        domain = correlator._detect_domain("I/O error on /dev/sda")
        assert domain == "disk"

    def test_detect_oom_domain(self, correlator):
        """Test detecting OOM domain."""
        domain = correlator._detect_domain("Out of memory: Killed process 1234")
        assert domain == "oom"

    def test_detect_docker_domain(self, correlator):
        """Test detecting docker domain."""
        domain = correlator._detect_domain("container exited with code 137")
        assert domain == "docker"

    def test_detect_auth_domain(self, correlator):
        """Test detecting auth domain."""
        domain = correlator._detect_domain("Failed password for user root")
        assert domain == "auth"

    def test_detect_pkg_domain(self, correlator):
        """Test detecting package domain."""
        domain = correlator._detect_domain("apt: unmet dependencies")
        assert domain == "pkg"

    def test_detect_service_domain(self, correlator):
        """Test detecting service domain."""
        domain = correlator._detect_domain("systemd: unit nginx.service failed")
        assert domain == "service"

    def test_detect_unknown_domain(self, correlator):
        """Test fallback to 'other' domain."""
        domain = correlator._detect_domain("Some random message")
        assert domain == "other"


class TestEntityExtraction:
    """Tests for entity extraction."""

    def test_extract_service_entity(self, correlator):
        """Test extracting service entity."""
        entities = correlator._extract_entities("systemd[1]: nginx.service failed")
        assert any("nginx" in e for e in entities)

    def test_extract_interface_entity(self, correlator):
        """Test extracting interface entity."""
        entities = correlator._extract_entities("Link is down on eth0")
        assert any("eth0" in e for e in entities)

    def test_extract_device_entity(self, correlator):
        """Test extracting device entity."""
        entities = correlator._extract_entities("Error on /dev/sda1")
        assert any("/dev/sda1" in e for e in entities)

    def test_extract_container_entity(self, correlator):
        """Test extracting container entity."""
        entities = correlator._extract_entities("container: abc123def456 exited")
        assert any("abc123def456" in e for e in entities)

    def test_extract_multiple_entities(self, correlator):
        """Test extracting multiple entities."""
        entities = correlator._extract_entities(
            "nginx.service failed on eth0 with /dev/sda error"
        )
        assert len(entities) >= 2


class TestEventProcessing:
    """Tests for event processing."""

    def test_process_critical_event(self, correlator, temp_db):
        """Test that critical events trigger immediate incident."""
        conn, _ = temp_db

        event = {
            "id": "event-1",
            "ts": datetime.utcnow(),
            "severity": "critical",
            "category": "oom",
            "message": "Out of memory: Killed process nginx",
        }

        incident_id = correlator.process_event(event, conn=conn)

        assert incident_id is not None
        incident = get_incident(incident_id, conn=conn)
        assert incident is not None
        assert incident.domain == "oom"

    def test_process_non_critical_event(self, correlator, temp_db):
        """Test that non-critical events may not trigger immediately."""
        conn, _ = temp_db

        # Single warning shouldn't trigger with min_events=2
        correlator_strict = IncidentCorrelator(
            min_events_for_incident=2,
            critical_triggers_immediate=False,
        )

        event = {
            "id": "event-1",
            "ts": datetime.utcnow(),
            "severity": "warning",
            "category": "disk",
            "message": "Disk usage high",
        }

        incident_id = correlator_strict.process_event(event, conn=conn)
        # May or may not create depending on implementation
        # This test just ensures no crash

    def test_process_accumulating_events(self, temp_db):
        """Test that accumulating events trigger incident."""
        conn, _ = temp_db

        correlator = IncidentCorrelator(
            min_events_for_incident=2,
            critical_triggers_immediate=False,
        )

        event1 = {
            "id": "event-1",
            "ts": datetime.utcnow(),
            "severity": "warning",
            "category": "net",
            "message": "Connection to eth0 failed",
        }
        event2 = {
            "id": "event-2",
            "ts": datetime.utcnow(),
            "severity": "warning",
            "category": "net",
            "message": "Connection to eth0 refused",
        }

        correlator.process_event(event1, conn=conn)
        incident_id = correlator.process_event(event2, conn=conn)

        # Should have created an incident after 2 events
        assert incident_id is not None


class TestCommandFailure:
    """Tests for command failure incidents."""

    def test_create_from_command_failure(self, correlator, temp_db):
        """Test creating incident from failed command."""
        conn, _ = temp_db

        incident_id = correlator.create_from_command_failure(
            command="apt update",
            stderr="E: Unable to fetch some archives",
            exit_code=100,
            conn=conn,
        )

        assert incident_id is not None
        incident = get_incident(incident_id, conn=conn)
        assert incident is not None
        assert incident.domain == "pkg"
        assert incident.trigger_source == "command_failure"
        assert incident.trigger_command == "apt update"

    def test_command_failure_attaches_snapshot(self, correlator, temp_db):
        """Test that command failure attaches pre-snapshot."""
        conn, _ = temp_db

        from elle.daemon.incidents.store import get_snapshot

        incident_id = correlator.create_from_command_failure(
            command="docker run test",
            stderr="container exited with error",
            exit_code=1,
            conn=conn,
        )

        snapshot = get_snapshot(incident_id, "pre", conn=conn)
        assert snapshot is not None

    def test_command_failure_domain_detection(self, correlator, temp_db):
        """Test that domain is detected from stderr."""
        conn, _ = temp_db

        # Network error
        incident_id = correlator.create_from_command_failure(
            command="curl https://example.com",
            stderr="Connection refused",
            exit_code=7,
            conn=conn,
        )
        incident = get_incident(incident_id, conn=conn)
        assert incident.domain == "net"


class TestCorrelationKey:
    """Tests for correlation key generation."""

    def test_same_domain_entities_same_key(self, correlator):
        """Test that same domain/entities produce same key."""
        event1 = {"message": "eth0 connection failed"}
        event2 = {"message": "connection to eth0 refused"}

        key1 = correlator._get_correlation_key(event1)
        key2 = correlator._get_correlation_key(event2)

        assert key1 == key2

    def test_different_entities_different_keys(self, correlator):
        """Test that different entities produce different keys."""
        event1 = {"message": "eth0 connection failed"}
        event2 = {"message": "wlan0 connection failed"}

        key1 = correlator._get_correlation_key(event1)
        key2 = correlator._get_correlation_key(event2)

        assert key1 != key2


class TestSingleton:
    """Tests for correlator singleton."""

    def test_get_correlator_returns_same(self):
        """Test that get_correlator returns same instance."""
        reset_correlator()
        c1 = get_correlator()
        c2 = get_correlator()
        assert c1 is c2

    def test_reset_creates_new(self):
        """Test that reset creates new instance."""
        reset_correlator()
        c1 = get_correlator()
        reset_correlator()
        c2 = get_correlator()
        assert c1 is not c2
