"""Tests for event-to-incident correlation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from elle.daemon.incidents.correlator import (
    IncidentCorrelator,
    get_correlator,
    reset_correlator,
)
from elle.daemon.incidents.store import get_incident


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
        entities = correlator._extract_entities("nginx.service failed on eth0 with /dev/sda error")
        assert len(entities) >= 2


class TestEventProcessing:
    """Tests for event processing."""

    def test_process_critical_event(self, correlator, incidents_conn):
        """Test that critical events trigger immediate incident."""
        conn = incidents_conn

        event = {
            "id": "event-1",
            "ts": datetime.now(timezone.utc),
            "severity": "critical",
            "category": "oom",
            "message": "Out of memory: Killed process nginx",
        }

        incident_id = correlator.process_event(event, conn=conn)

        assert incident_id is not None
        incident = get_incident(incident_id, conn=conn)
        assert incident is not None
        assert incident.domain == "oom"

    def test_process_non_critical_event(self, correlator, incidents_conn):
        """Test that non-critical events may not trigger immediately."""
        conn = incidents_conn

        # Single warning shouldn't trigger with min_events=2
        correlator_strict = IncidentCorrelator(
            min_events_for_incident=2,
            critical_triggers_immediate=False,
        )

        event = {
            "id": "event-1",
            "ts": datetime.now(timezone.utc),
            "severity": "warning",
            "category": "disk",
            "message": "Disk usage high",
        }

        correlator_strict.process_event(event, conn=conn)
        # May or may not create depending on implementation
        # This test just ensures no crash

    def test_process_accumulating_events(self, incidents_conn):
        """Test that accumulating events trigger incident."""
        conn = incidents_conn

        correlator = IncidentCorrelator(
            min_events_for_incident=2,
            critical_triggers_immediate=False,
        )

        event1 = {
            "id": "event-1",
            "ts": datetime.now(timezone.utc),
            "severity": "warning",
            "category": "net",
            "message": "Connection to eth0 failed",
        }
        event2 = {
            "id": "event-2",
            "ts": datetime.now(timezone.utc),
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

    def test_create_from_command_failure(self, correlator, incidents_conn):
        """Test creating incident from failed command."""
        conn = incidents_conn

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

    def test_command_failure_attaches_snapshot(self, correlator, incidents_conn):
        """Test that command failure attaches pre-snapshot."""
        conn = incidents_conn

        from elle.daemon.incidents.store import get_snapshot

        incident_id = correlator.create_from_command_failure(
            command="docker run test",
            stderr="container exited with error",
            exit_code=1,
            conn=conn,
        )

        snapshot = get_snapshot(incident_id, "pre", conn=conn)
        assert snapshot is not None

    def test_command_failure_domain_detection(self, correlator, incidents_conn):
        """Test that domain is detected from stderr."""
        conn = incidents_conn

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


# =============================================================================
# Additional coverage tests (mocked, no DB required)
# =============================================================================

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from elle.daemon.incidents.correlator import (
    initialize_with_agentic_handler,
)


class TestGetEventTime:
    """Tests for _get_event_time helper."""

    @pytest.fixture
    def corr(self):
        return IncidentCorrelator()

    def test_datetime_passthrough(self, corr):
        """datetime value is returned as-is."""
        now = datetime.now(timezone.utc)
        event = {"ts": now}
        assert corr._get_event_time(event) == now

    def test_string_iso_format(self, corr):
        """ISO format string is parsed to datetime."""
        ts_str = "2025-01-15T10:30:00"
        event = {"ts": ts_str}
        result = corr._get_event_time(event)
        assert isinstance(result, datetime)
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15

    def test_missing_ts_returns_now(self, corr):
        """Missing ts returns current time."""
        event = {}
        result = corr._get_event_time(event)
        assert isinstance(result, datetime)

    def test_none_ts_returns_now(self, corr):
        """None ts returns current time."""
        event = {"ts": None}
        result = corr._get_event_time(event)
        assert isinstance(result, datetime)

    def test_numeric_ts_returns_now(self, corr):
        """Non-datetime, non-string ts returns current time."""
        event = {"ts": 12345}
        result = corr._get_event_time(event)
        assert isinstance(result, datetime)


class TestCleanBuffer:
    """Tests for _clean_buffer."""

    def test_removes_old_events(self):
        """Events older than 2x time_window are removed."""
        corr = IncidentCorrelator(time_window_sec=60)
        old_time = datetime.now(timezone.utc) - timedelta(seconds=200)
        recent_time = datetime.now(timezone.utc)
        corr._event_buffer = [
            {"ts": old_time, "message": "old"},
            {"ts": recent_time, "message": "recent"},
        ]
        corr._clean_buffer()
        assert len(corr._event_buffer) == 1
        assert corr._event_buffer[0]["message"] == "recent"

    def test_keeps_recent_events(self):
        """Events within 2x time_window are kept."""
        corr = IncidentCorrelator(time_window_sec=300)
        now = datetime.now(timezone.utc)
        corr._event_buffer = [
            {"ts": now - timedelta(seconds=100), "message": "a"},
            {"ts": now - timedelta(seconds=200), "message": "b"},
            {"ts": now, "message": "c"},
        ]
        corr._clean_buffer()
        assert len(corr._event_buffer) == 3


class TestDomainDetectionExtended:
    """Additional domain detection tests for patterns not in existing tests."""

    @pytest.fixture
    def corr(self):
        return IncidentCorrelator()

    def test_detect_net_link_down(self, corr):
        """Network: link is down pattern."""
        assert corr._detect_domain("link is down on enp0s3") == "net"

    def test_detect_net_carrier_lost(self, corr):
        """Network: carrier lost pattern."""
        assert corr._detect_domain("carrier lost on wlan0") == "net"

    def test_detect_net_no_route(self, corr):
        """Network: no route pattern."""
        assert corr._detect_domain("no route to host") == "net"

    def test_detect_disk_no_space(self, corr):
        """Disk: No space left pattern."""
        assert corr._detect_domain("No space left on device") == "disk"

    def test_detect_disk_quota_exceeded(self, corr):
        """Disk: quota exceeded pattern."""
        assert corr._detect_domain("disk quota exceeded") == "disk"

    def test_detect_oom_memory_pressure(self, corr):
        """OOM: memory pressure pattern."""
        assert corr._detect_domain("cannot allocate memory") == "oom"

    def test_detect_oom_killer(self, corr):
        """OOM: invoked oom-killer pattern."""
        assert corr._detect_domain("invoked oom-killer") == "oom"

    def test_detect_auth_invalid_user(self, corr):
        """Auth: invalid user pattern."""
        assert corr._detect_domain("invalid user admin from 192.168.1.1") == "auth"

    def test_detect_auth_unauthorized(self, corr):
        """Auth: unauthorized pattern."""
        assert corr._detect_domain("unauthorized access attempt") == "auth"

    def test_detect_fs_mount(self, corr):
        """Filesystem: mount pattern (no disk keywords)."""
        assert corr._detect_domain("mount point busy at /mnt/data") == "fs"

    def test_detect_fs_ext4(self, corr):
        """Filesystem: ext4 pattern (note: 'filesystem' matches disk first)."""
        # "ext4" alone does not match disk, but "ext4 filesystem error" hits disk due to "filesystem"
        assert corr._detect_domain("ext4 checksum error detected") == "fs"

    def test_detect_fs_btrfs(self, corr):
        """Filesystem: btrfs pattern."""
        assert corr._detect_domain("btrfs corruption detected") == "fs"

    def test_detect_fs_inode(self, corr):
        """Filesystem: inode pattern."""
        assert corr._detect_domain("inode table full") == "fs"


class TestEntityExtractionExtended:
    """Additional entity extraction tests."""

    @pytest.fixture
    def corr(self):
        return IncidentCorrelator()

    def test_extract_nvme_device(self, corr):
        """Extract NVMe device entity."""
        entities = corr._extract_entities("Error on /dev/nvme0n1p2")
        assert any("/dev/nvme0n1p2" in e for e in entities)

    def test_extract_mount_point(self, corr):
        """Extract mount point entity."""
        entities = corr._extract_entities("mounted on /var/lib")
        assert any("/var/lib" in e for e in entities)

    def test_extract_service_with_unit_keyword(self, corr):
        """Extract service from 'unit nginx' pattern."""
        entities = corr._extract_entities("unit nginx entered failed state")
        assert any("nginx" in e for e in entities)

    def test_extract_no_duplicates(self, corr):
        """No duplicate entities in result."""
        entities = corr._extract_entities("nginx.service failed, nginx.service restarted")
        # Count occurrences of nginx
        nginx_entities = [e for e in entities if "nginx" in e]
        # Should not have duplicates for same entity
        assert len(nginx_entities) == len(set(nginx_entities))

    def test_extract_empty_string(self, corr):
        """Empty string produces no entities."""
        entities = corr._extract_entities("")
        assert entities == []

    def test_extract_wlp_interface(self, corr):
        """Extract wlp interface entity."""
        entities = corr._extract_entities("link down on wlp2s0")
        assert any("wlp2s0" in e for e in entities)


class TestCorrelationKeyExtended:
    """Additional correlation key tests."""

    @pytest.fixture
    def corr(self):
        return IncidentCorrelator()

    def test_empty_message_produces_key(self, corr):
        """Empty message still produces a valid correlation key."""
        event = {"message": ""}
        key = corr._get_correlation_key(event)
        assert ":" in key  # Format is domain:entity_key

    def test_missing_message_produces_key(self, corr):
        """Missing message key still produces a valid correlation key."""
        event = {}
        key = corr._get_correlation_key(event)
        assert key == "other:none"

    def test_entity_key_is_sorted(self, corr):
        """Entities in the key are sorted for consistency."""
        event1 = {"message": "eth0 link down and /dev/sda error"}
        event2 = {"message": "/dev/sda error and eth0 link down"}
        key1 = corr._get_correlation_key(event1)
        key2 = corr._get_correlation_key(event2)
        assert key1 == key2

    def test_entity_key_limits_to_three(self, corr):
        """Key uses at most 3 entities."""
        # Create a message with many entities
        event = {"message": "eth0 eth1 wlan0 enp0s3 link issues"}
        key = corr._get_correlation_key(event)
        entity_part = key.split(":")[1]
        entity_count = len(entity_part.split("_"))
        assert entity_count <= 3


class TestCreateIncidentFromEventsMocked:
    """Tests for _create_incident_from_events with mocked store functions."""

    @pytest.fixture
    def corr(self):
        return IncidentCorrelator()

    @patch("elle.daemon.incidents.correlator.link_events")
    @patch("elle.daemon.incidents.correlator.update_incident")
    @patch("elle.daemon.incidents.correlator.attach_snapshot")
    @patch("elle.daemon.incidents.correlator.create_incident_draft")
    @patch("elle.daemon.incidents.correlator.extract_fingerprint", return_value="fp-123")
    @patch("elle.daemon.incidents.correlator.collect_snapshot")
    def test_empty_events_returns_empty_string(
        self, mock_snap, mock_fp, mock_create, mock_attach, mock_update, mock_link
    ):
        """Empty event list returns empty string."""
        corr = IncidentCorrelator()
        result = corr._create_incident_from_events([])
        assert result == ""
        mock_create.assert_not_called()

    @patch("elle.daemon.incidents.correlator.link_events")
    @patch("elle.daemon.incidents.correlator.update_incident")
    @patch("elle.daemon.incidents.correlator.attach_snapshot")
    @patch("elle.daemon.incidents.correlator.create_incident_draft")
    @patch("elle.daemon.incidents.correlator.extract_fingerprint", return_value="fp-123")
    @patch("elle.daemon.incidents.correlator.collect_snapshot")
    def test_single_event_creates_incident(self, mock_snap, mock_fp, mock_create, mock_attach, mock_update, mock_link):
        """Single event creates an incident."""
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-001"
        mock_create.return_value = mock_incident

        corr = IncidentCorrelator()
        events = [{"message": "Out of memory: Killed process 1234", "severity": "critical", "id": "e1"}]
        result = corr._create_incident_from_events(events)
        assert result == "inc-001"
        mock_create.assert_called_once()
        mock_attach.assert_called_once()
        mock_update.assert_called_once()
        mock_link.assert_called_once()

    @patch("elle.daemon.incidents.correlator.link_events")
    @patch("elle.daemon.incidents.correlator.update_incident")
    @patch("elle.daemon.incidents.correlator.attach_snapshot")
    @patch("elle.daemon.incidents.correlator.create_incident_draft")
    @patch("elle.daemon.incidents.correlator.extract_fingerprint", return_value="fp-123")
    @patch("elle.daemon.incidents.correlator.collect_snapshot")
    def test_no_entities_uses_anomaly_title(self, mock_snap, mock_fp, mock_create, mock_attach, mock_update, mock_link):
        """Events with no entities produce 'Detected anomaly' title."""
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-002"
        mock_create.return_value = mock_incident

        corr = IncidentCorrelator()
        events = [{"message": "Something random happened", "severity": "warning"}]
        corr._create_incident_from_events(events)
        call_kwargs = mock_create.call_args
        assert "anomaly" in call_kwargs.kwargs.get(
            "title", call_kwargs[1].get("title", "")
        ).lower() or "anomaly" in str(call_kwargs)

    @patch("elle.daemon.incidents.correlator.link_events")
    @patch("elle.daemon.incidents.correlator.update_incident")
    @patch("elle.daemon.incidents.correlator.attach_snapshot")
    @patch("elle.daemon.incidents.correlator.create_incident_draft")
    @patch("elle.daemon.incidents.correlator.extract_fingerprint", return_value="fp-123")
    @patch("elle.daemon.incidents.correlator.collect_snapshot")
    def test_max_severity_selected(self, mock_snap, mock_fp, mock_create, mock_attach, mock_update, mock_link):
        """Highest severity among events is used for the incident."""
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-003"
        mock_create.return_value = mock_incident

        corr = IncidentCorrelator()
        events = [
            {"message": "Info message", "severity": "info"},
            {"message": "Critical: OOM kill", "severity": "critical"},
            {"message": "Warning disk", "severity": "warning"},
        ]
        corr._create_incident_from_events(events)
        call_kwargs = mock_create.call_args
        assert "critical" in str(call_kwargs)

    @patch("elle.daemon.incidents.correlator.link_events")
    @patch("elle.daemon.incidents.correlator.update_incident")
    @patch("elle.daemon.incidents.correlator.attach_snapshot")
    @patch("elle.daemon.incidents.correlator.create_incident_draft")
    @patch("elle.daemon.incidents.correlator.extract_fingerprint", return_value="fp-123")
    @patch("elle.daemon.incidents.correlator.collect_snapshot")
    def test_events_without_ids_skip_link(self, mock_snap, mock_fp, mock_create, mock_attach, mock_update, mock_link):
        """Events without id field do not call link_events."""
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-004"
        mock_create.return_value = mock_incident

        corr = IncidentCorrelator()
        events = [{"message": "No id event", "severity": "error"}]
        corr._create_incident_from_events(events)
        mock_link.assert_not_called()


class TestNotifyIncidentCreated:
    """Tests for _notify_incident_created."""

    def test_no_callback_is_noop(self):
        """No callback means nothing happens."""
        corr = IncidentCorrelator(on_incident_created=None)
        # Should not raise
        corr._notify_incident_created("inc-001")

    def test_sync_callback_called(self):
        """Sync callback is called with incident_id."""
        callback = MagicMock(return_value=None)
        corr = IncidentCorrelator(on_incident_created=callback)
        corr._notify_incident_created("inc-001")
        callback.assert_called_once_with("inc-001")

    def test_callback_exception_does_not_propagate(self):
        """Callback exceptions are caught and do not propagate."""
        callback = MagicMock(side_effect=RuntimeError("callback failed"))
        corr = IncidentCorrelator(on_incident_created=callback)
        # Should not raise
        corr._notify_incident_created("inc-001")

    def test_async_callback_with_running_loop(self):
        """Async callback is scheduled on the running loop."""
        import asyncio

        async_cb = AsyncMock()
        corr = IncidentCorrelator(on_incident_created=async_cb)

        async def _run():
            corr._notify_incident_created("inc-001")
            # Allow the scheduled task to execute
            await asyncio.sleep(0.01)

        asyncio.run(_run())
        async_cb.assert_called_once_with("inc-001")


class TestClearActiveIncident:
    """Tests for clear_active_incident."""

    def test_clear_existing_key(self):
        """Clearing an existing key removes it."""
        corr = IncidentCorrelator()
        corr._active_incidents["test:key"] = "inc-001"
        corr.clear_active_incident("test:key")
        assert "test:key" not in corr._active_incidents

    def test_clear_nonexistent_key(self):
        """Clearing a nonexistent key is a no-op."""
        corr = IncidentCorrelator()
        corr.clear_active_incident("nonexistent")
        # Should not raise


class TestCheckForIncidentPattern:
    """Tests for _check_for_incident_pattern with mocked store."""

    @patch("elle.daemon.incidents.correlator.link_events")
    @patch("elle.daemon.incidents.correlator.update_incident")
    @patch("elle.daemon.incidents.correlator.attach_snapshot")
    @patch("elle.daemon.incidents.correlator.create_incident_draft")
    @patch("elle.daemon.incidents.correlator.extract_fingerprint", return_value="fp-123")
    @patch("elle.daemon.incidents.correlator.collect_snapshot")
    def test_returns_existing_active_incident(
        self, mock_snap, mock_fp, mock_create, mock_attach, mock_update, mock_link
    ):
        """Returns existing active incident for same correlation key."""
        corr = IncidentCorrelator(min_events_for_incident=1)
        event = {"message": "eth0 connection lost", "id": "e1", "ts": datetime.now(timezone.utc)}
        corr._event_buffer.append(event)

        # Set up active incident for the correlation key
        key = corr._get_correlation_key(event)
        corr._active_incidents[key] = "inc-existing"

        result = corr._check_for_incident_pattern(event)
        assert result == "inc-existing"
        mock_link.assert_called_once()

    @patch("elle.daemon.incidents.correlator.link_events")
    @patch("elle.daemon.incidents.correlator.update_incident")
    @patch("elle.daemon.incidents.correlator.attach_snapshot")
    @patch("elle.daemon.incidents.correlator.create_incident_draft")
    @patch("elle.daemon.incidents.correlator.extract_fingerprint", return_value="fp-123")
    @patch("elle.daemon.incidents.correlator.collect_snapshot")
    def test_creates_new_incident_for_new_key(
        self, mock_snap, mock_fp, mock_create, mock_attach, mock_update, mock_link
    ):
        """Creates new incident when no active incident for key."""
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-new"
        mock_create.return_value = mock_incident

        corr = IncidentCorrelator(min_events_for_incident=1)
        event = {"message": "eth0 connection lost", "id": "e1", "ts": datetime.now(timezone.utc)}
        corr._event_buffer.append(event)

        result = corr._check_for_incident_pattern(event)
        assert result == "inc-new"
        key = corr._get_correlation_key(event)
        assert corr._active_incidents[key] == "inc-new"

    def test_below_threshold_returns_none(self):
        """Returns None when event count is below threshold."""
        corr = IncidentCorrelator(min_events_for_incident=10)
        event = {"message": "eth0 issue", "ts": datetime.now(timezone.utc)}
        corr._event_buffer.append(event)

        result = corr._check_for_incident_pattern(event)
        assert result is None


class TestProcessEventMocked:
    """Tests for process_event with mocked store functions."""

    @patch("elle.daemon.incidents.correlator.link_events")
    @patch("elle.daemon.incidents.correlator.update_incident")
    @patch("elle.daemon.incidents.correlator.attach_snapshot")
    @patch("elle.daemon.incidents.correlator.create_incident_draft")
    @patch("elle.daemon.incidents.correlator.extract_fingerprint", return_value="fp-123")
    @patch("elle.daemon.incidents.correlator.collect_snapshot")
    def test_critical_event_triggers_immediate(
        self, mock_snap, mock_fp, mock_create, mock_attach, mock_update, mock_link
    ):
        """Critical event creates immediate incident."""
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-crit"
        mock_create.return_value = mock_incident

        corr = IncidentCorrelator(critical_triggers_immediate=True, min_events_for_incident=100)
        event = {"severity": "critical", "message": "OOM kill", "ts": datetime.now(timezone.utc)}
        result = corr.process_event(event)
        assert result == "inc-crit"

    def test_non_critical_added_to_buffer(self):
        """Non-critical events are added to buffer."""
        corr = IncidentCorrelator(min_events_for_incident=100)
        event = {"severity": "info", "message": "Normal log", "ts": datetime.now(timezone.utc)}
        corr.process_event(event)
        assert len(corr._event_buffer) == 1

    @patch("elle.daemon.incidents.correlator.link_events")
    @patch("elle.daemon.incidents.correlator.update_incident")
    @patch("elle.daemon.incidents.correlator.attach_snapshot")
    @patch("elle.daemon.incidents.correlator.create_incident_draft")
    @patch("elle.daemon.incidents.correlator.extract_fingerprint", return_value="fp-123")
    @patch("elle.daemon.incidents.correlator.collect_snapshot")
    def test_critical_disabled_does_not_trigger_immediate(
        self, mock_snap, mock_fp, mock_create, mock_attach, mock_update, mock_link
    ):
        """With critical_triggers_immediate=False, critical events don't trigger immediately."""
        corr = IncidentCorrelator(
            critical_triggers_immediate=False,
            min_events_for_incident=100,
        )
        event = {"severity": "critical", "message": "OOM kill", "ts": datetime.now(timezone.utc)}
        result = corr.process_event(event)
        assert result is None  # Below threshold, no immediate trigger


class TestCreateFromCommandFailureMocked:
    """Tests for create_from_command_failure with mocked store functions."""

    @patch("elle.daemon.incidents.correlator.link_events")
    @patch("elle.daemon.incidents.correlator.update_incident")
    @patch("elle.daemon.incidents.correlator.attach_snapshot")
    @patch("elle.daemon.incidents.correlator.create_incident_draft")
    @patch("elle.daemon.incidents.correlator.extract_fingerprint", return_value="fp-123")
    @patch("elle.daemon.incidents.correlator.collect_snapshot")
    def test_empty_command_uses_default_name(
        self, mock_snap, mock_fp, mock_create, mock_attach, mock_update, mock_link
    ):
        """Empty command string uses 'command' as default."""
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-cmd"
        mock_create.return_value = mock_incident

        corr = IncidentCorrelator()
        result = corr.create_from_command_failure("", "some error", 1)
        assert result == "inc-cmd"
        call_kwargs = mock_create.call_args
        assert "command" in str(call_kwargs)

    @patch("elle.daemon.incidents.correlator.link_events")
    @patch("elle.daemon.incidents.correlator.update_incident")
    @patch("elle.daemon.incidents.correlator.attach_snapshot")
    @patch("elle.daemon.incidents.correlator.create_incident_draft")
    @patch("elle.daemon.incidents.correlator.extract_fingerprint", return_value="fp-123")
    @patch("elle.daemon.incidents.correlator.collect_snapshot")
    def test_long_stderr_truncated(self, mock_snap, mock_fp, mock_create, mock_attach, mock_update, mock_link):
        """Long stderr is truncated in symptoms and log_snippets."""
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-long"
        mock_create.return_value = mock_incident

        corr = IncidentCorrelator()
        long_stderr = "x" * 600
        corr.create_from_command_failure("apt update", long_stderr, 1)
        call_kwargs = mock_update.call_args
        # Symptoms should have truncated stderr
        symptoms = call_kwargs.kwargs.get("symptoms", call_kwargs[1].get("symptoms", []))
        assert any("..." in s for s in symptoms)


class TestInitializeWithAgenticHandler:
    """Tests for initialize_with_agentic_handler."""

    def test_wires_handler(self):
        """Initializes correlator with agentic handler callback."""
        reset_correlator()
        mock_handler = MagicMock()
        mock_handler.on_incident_created = MagicMock()

        with patch(
            "elle.daemon.incidents.agentic_handler.get_agentic_handler",
            return_value=mock_handler,
        ):
            corr = initialize_with_agentic_handler()
            assert corr._on_incident_created is mock_handler.on_incident_created

    def test_import_error_fallback(self):
        """Falls back to bare correlator on ImportError."""
        reset_correlator()

        with patch(
            "elle.daemon.incidents.agentic_handler.get_agentic_handler",
            side_effect=ImportError("no module"),
        ):
            corr = initialize_with_agentic_handler()
            assert corr._on_incident_created is None
