"""Tests for eBPF event normalization."""

from elle.daemon.telemetry.ebpf.normalizer import (
    _extract_ebpf_entity,
    extract_entity_from_message,
    get_ebpf_severity_override,
    is_ebpf_event,
    normalize_ebpf_event,
)


class TestIsEBPFEvent:
    """Tests for eBPF event detection."""

    def test_ebpf_event(self):
        """Test detection of eBPF events."""
        raw = {"_SOURCE": "ebpf", "MESSAGE": "test"}
        assert is_ebpf_event(raw) is True

    def test_journal_event(self):
        """Test that journal events are not eBPF."""
        raw = {"_SOURCE": "journal", "MESSAGE": "test"}
        assert is_ebpf_event(raw) is False

    def test_missing_source(self):
        """Test event without source field."""
        raw = {"MESSAGE": "test"}
        assert is_ebpf_event(raw) is False


class TestNormalizeEBPFEvent:
    """Tests for eBPF event normalization."""

    def test_normalize_oom_event(self):
        """Test normalizing an OOM kill event."""
        raw = {
            "_SOURCE": "ebpf",
            "_EBPF_EVENT_TYPE": "oom_kill",
            "_EBPF_CATEGORY": "oom",
            "MESSAGE": "OOM killer invoked, killed process nginx (PID 1234)",
            "PRIORITY": "2",
            "_PID": "1234",
            "_COMM": "nginx",
        }
        event = normalize_ebpf_event(raw)

        assert event is not None
        assert event.source == "ebpf"
        assert event.category == "oom"
        assert event.severity == "critical"
        assert "nginx" in event.message
        assert event.entity == "process:nginx"

    def test_normalize_block_io_event(self):
        """Test normalizing a block I/O error event."""
        raw = {
            "_SOURCE": "ebpf",
            "_EBPF_EVENT_TYPE": "block_rq_error",
            "_EBPF_CATEGORY": "disk",
            "MESSAGE": "Block I/O error on device sda (error -5)",
            "PRIORITY": "3",
            "_EBPF_MAJOR": 8,
            "_EBPF_MINOR": 0,
        }
        event = normalize_ebpf_event(raw)

        assert event is not None
        assert event.category == "disk"
        assert event.entity == "disk:8:0"

    def test_normalize_net_drop_event(self):
        """Test normalizing a network drop event."""
        raw = {
            "_SOURCE": "ebpf",
            "_EBPF_EVENT_TYPE": "net_drop",
            "_EBPF_CATEGORY": "net",
            "MESSAGE": "Network packet dropped on eth0: NO_SOCKET",
            "PRIORITY": "4",
            "_EBPF_IFNAME": "eth0",
        }
        event = normalize_ebpf_event(raw)

        assert event is not None
        assert event.category == "net"
        assert event.entity == "interface:eth0"

    def test_normalize_process_exit_event(self):
        """Test normalizing a process crash event."""
        raw = {
            "_SOURCE": "ebpf",
            "_EBPF_EVENT_TYPE": "process_exit",
            "_EBPF_CATEGORY": "service",
            "MESSAGE": "Process mysql (PID 5678) killed by signal 9",
            "PRIORITY": "4",
            "_PID": "5678",
            "_COMM": "mysql",
            "_EBPF_SIGNAL": 9,
        }
        event = normalize_ebpf_event(raw)

        assert event is not None
        assert event.category == "service"
        assert event.entity == "process:mysql"

    def test_normalize_thermal_event(self):
        """Test normalizing a thermal zone event."""
        raw = {
            "_SOURCE": "ebpf",
            "_EBPF_EVENT_TYPE": "thermal_zone_trip",
            "_EBPF_CATEGORY": "thermal",
            "MESSAGE": "Thermal zone 0 trip (critical) at 95C",
            "PRIORITY": "4",
            "_EBPF_ZONE": 0,
        }
        event = normalize_ebpf_event(raw)

        assert event is not None
        assert event.category == "thermal"
        assert event.entity == "thermal_zone:0"

    def test_normalize_empty_message_returns_none(self):
        """Test that empty messages return None."""
        raw = {
            "_SOURCE": "ebpf",
            "MESSAGE": "",
        }
        assert normalize_ebpf_event(raw) is None

    def test_normalize_missing_message_returns_none(self):
        """Test that missing messages return None."""
        raw = {"_SOURCE": "ebpf"}
        assert normalize_ebpf_event(raw) is None


class TestExtractEBPFEntity:
    """Tests for entity extraction from eBPF events."""

    def test_extract_oom_entity(self):
        """Test extracting entity from OOM event."""
        raw = {"_COMM": "python3"}
        entity = _extract_ebpf_entity(raw, "oom")
        assert entity == "process:python3"

    def test_extract_disk_entity_from_major_minor(self):
        """Test extracting disk entity from major/minor."""
        raw = {"_EBPF_MAJOR": 8, "_EBPF_MINOR": 16}
        entity = _extract_ebpf_entity(raw, "disk")
        assert entity == "disk:8:16"

    def test_extract_disk_entity_from_dev(self):
        """Test extracting disk entity from device name."""
        raw = {"_EBPF_DEV": "nvme0n1"}
        entity = _extract_ebpf_entity(raw, "disk")
        assert entity == "disk:nvme0n1"

    def test_extract_net_entity_from_ifname(self):
        """Test extracting network entity from interface name."""
        raw = {"_EBPF_IFNAME": "enp3s0"}
        entity = _extract_ebpf_entity(raw, "net")
        assert entity == "interface:enp3s0"

    def test_extract_net_entity_from_ifindex(self):
        """Test extracting network entity from interface index."""
        raw = {"_EBPF_IFINDEX": 2}
        entity = _extract_ebpf_entity(raw, "net")
        assert entity == "interface:ifindex_2"

    def test_extract_service_entity_from_comm(self):
        """Test extracting service entity from comm."""
        raw = {"_COMM": "nginx"}
        entity = _extract_ebpf_entity(raw, "service")
        assert entity == "process:nginx"

    def test_extract_service_entity_from_pid(self):
        """Test extracting service entity from PID."""
        raw = {"_PID": "12345"}
        entity = _extract_ebpf_entity(raw, "service")
        assert entity == "pid:12345"

    def test_extract_thermal_entity_from_zone(self):
        """Test extracting thermal entity from zone."""
        raw = {"_EBPF_ZONE": 1}
        entity = _extract_ebpf_entity(raw, "thermal")
        assert entity == "thermal_zone:1"

    def test_extract_thermal_entity_from_cpu(self):
        """Test extracting thermal entity from CPU."""
        raw = {"_EBPF_CPU": 3}
        entity = _extract_ebpf_entity(raw, "thermal")
        assert entity == "cpu:3"

    def test_extract_no_entity(self):
        """Test extraction when no entity data available."""
        raw = {}
        entity = _extract_ebpf_entity(raw, "other")
        assert entity is None


class TestSeverityOverride:
    """Tests for severity override based on event specifics."""

    def test_process_exit_with_fatal_signal(self):
        """Test severity override for process killed by SIGKILL."""
        raw = {"_EBPF_EVENT_TYPE": "process_exit", "_EBPF_SIGNAL": 9}
        assert get_ebpf_severity_override(raw) == "error"

    def test_process_exit_with_sigsegv(self):
        """Test severity override for process killed by SIGSEGV."""
        raw = {"_EBPF_EVENT_TYPE": "process_exit", "_EBPF_SIGNAL": 11}
        assert get_ebpf_severity_override(raw) == "error"

    def test_process_exit_with_sigabrt(self):
        """Test severity override for process killed by SIGABRT."""
        raw = {"_EBPF_EVENT_TYPE": "process_exit", "_EBPF_SIGNAL": 6}
        assert get_ebpf_severity_override(raw) == "error"

    def test_process_exit_normal(self):
        """Test no override for normal process exit."""
        raw = {"_EBPF_EVENT_TYPE": "process_exit", "_EBPF_SIGNAL": 0}
        assert get_ebpf_severity_override(raw) is None

    def test_block_error_eio(self):
        """Test severity override for EIO error."""
        raw = {"_EBPF_EVENT_TYPE": "block_rq_error", "_EBPF_ERROR": -5}
        assert get_ebpf_severity_override(raw) == "critical"

    def test_block_error_enospc(self):
        """Test severity override for ENOSPC error."""
        raw = {"_EBPF_EVENT_TYPE": "block_rq_error", "_EBPF_ERROR": -28}
        assert get_ebpf_severity_override(raw) == "critical"

    def test_other_event_no_override(self):
        """Test no override for other events."""
        raw = {"_EBPF_EVENT_TYPE": "net_drop"}
        assert get_ebpf_severity_override(raw) is None


class TestExtractEntityFromMessage:
    """Tests for entity extraction from message text."""

    def test_extract_process_from_message(self):
        """Test extracting process name from message."""
        message = "Process nginx (PID 1234) killed by signal 9"
        entity = extract_entity_from_message(message)
        assert entity == "process:nginx"

    def test_extract_device_from_message(self):
        """Test extracting device name from message."""
        message = "Block I/O error on device sda"
        entity = extract_entity_from_message(message)
        assert entity == "disk:sda"

    def test_extract_interface_from_message(self):
        """Test extracting interface from message."""
        message = "Network packet dropped on eth0"
        entity = extract_entity_from_message(message)
        assert entity == "interface:eth0"

    def test_extract_thermal_zone_from_message(self):
        """Test extracting thermal zone from message."""
        message = "Thermal zone 0 trip (critical)"
        entity = extract_entity_from_message(message)
        assert entity == "thermal_zone:0"

    def test_no_entity_in_message(self):
        """Test when no entity pattern matches."""
        message = "Unknown event occurred"
        entity = extract_entity_from_message(message)
        assert entity is None
