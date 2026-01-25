"""Tests for eBPF telemetry models."""

import pytest
from datetime import datetime

from elle.daemon.telemetry.ebpf.models import (
    EBPF_EVENT_CATEGORIES,
    EBPF_EVENT_SEVERITIES,
    EBPFEventType,
    EBPFProgramStatus,
    EBPFRawEvent,
    EBPFWatcherStatus,
)


class TestEBPFRawEvent:
    """Tests for EBPFRawEvent model."""

    def test_create_minimal_event(self):
        """Test creating an event with minimal fields."""
        event = EBPFRawEvent(
            ts_ns=1234567890,
            cpu=0,
            event_type="oom_kill",
        )
        assert event.ts_ns == 1234567890
        assert event.cpu == 0
        assert event.event_type == "oom_kill"
        assert event.pid is None
        assert event.comm is None
        assert event.data == {}

    def test_create_full_event(self):
        """Test creating an event with all fields."""
        event = EBPFRawEvent(
            ts_ns=9876543210,
            cpu=3,
            event_type="process_exit",
            pid=1234,
            tgid=1234,
            uid=1000,
            comm="nginx",
            data={"exit_code": 0, "signal": 9},
        )
        assert event.pid == 1234
        assert event.tgid == 1234
        assert event.uid == 1000
        assert event.comm == "nginx"
        assert event.data["signal"] == 9

    def test_event_is_immutable(self):
        """Test that events are immutable (frozen)."""
        event = EBPFRawEvent(
            ts_ns=1234567890,
            cpu=0,
            event_type="oom_kill",
        )
        with pytest.raises(Exception):  # ValidationError or AttributeError
            event.cpu = 1

    def test_comm_max_length(self):
        """Test that comm field enforces max length."""
        # Should work with <= 16 chars
        event = EBPFRawEvent(
            ts_ns=1,
            cpu=0,
            event_type="test",
            comm="a" * 16,
        )
        assert len(event.comm) == 16

        # Should fail with > 16 chars
        with pytest.raises(Exception):
            EBPFRawEvent(
                ts_ns=1,
                cpu=0,
                event_type="test",
                comm="a" * 17,
            )


class TestEBPFProgramStatus:
    """Tests for EBPFProgramStatus model."""

    def test_create_default_status(self):
        """Test creating status with defaults."""
        status = EBPFProgramStatus(
            name="oom",
            category="oom",
        )
        assert status.name == "oom"
        assert status.category == "oom"
        assert status.loaded is False
        assert status.attached is False
        assert status.events_count == 0
        assert status.last_event is None

    def test_create_active_status(self):
        """Test creating status for active program."""
        now = datetime.now()
        status = EBPFProgramStatus(
            name="block_io",
            category="disk",
            loaded=True,
            attached=True,
            hook_type="tracepoint",
            hook_name="block:block_rq_complete",
            events_count=100,
            errors_count=2,
            last_event=now,
        )
        assert status.loaded is True
        assert status.attached is True
        assert status.events_count == 100
        assert status.last_event == now


class TestEBPFWatcherStatus:
    """Tests for EBPFWatcherStatus model."""

    def test_create_disabled_status(self):
        """Test creating status when eBPF is disabled."""
        status = EBPFWatcherStatus(
            enabled=False,
            capabilities_ok=False,
            missing_capabilities=("CAP_BPF", "CAP_PERFMON"),
        )
        assert status.enabled is False
        assert len(status.missing_capabilities) == 2
        assert status.programs == ()

    def test_create_active_status(self):
        """Test creating status for active watcher."""
        program_status = EBPFProgramStatus(
            name="oom",
            category="oom",
            loaded=True,
            attached=True,
        )
        status = EBPFWatcherStatus(
            enabled=True,
            capabilities_ok=True,
            programs=(program_status,),
            total_events=500,
            uptime_sec=3600,
            backend="bcc",
            kernel_version="6.8.0",
        )
        assert status.enabled is True
        assert len(status.programs) == 1
        assert status.total_events == 500
        assert status.kernel_version == "6.8.0"


class TestEventMappings:
    """Tests for event category and severity mappings."""

    def test_all_event_types_have_category(self):
        """Test that all event types have a category mapping."""
        event_types = [
            EBPFEventType.OOM_KILL,
            EBPFEventType.OOM_SCORE_UPDATE,
            EBPFEventType.BLOCK_RQ_COMPLETE,
            EBPFEventType.BLOCK_RQ_ERROR,
            EBPFEventType.NET_DROP,
            EBPFEventType.NET_RETRANSMIT,
            EBPFEventType.PROCESS_EXIT,
            EBPFEventType.SIGNAL_DELIVER,
            EBPFEventType.THERMAL_ZONE_TRIP,
            EBPFEventType.CPU_FREQUENCY,
        ]
        for event_type in event_types:
            assert event_type in EBPF_EVENT_CATEGORIES, f"Missing category for {event_type}"

    def test_all_event_types_have_severity(self):
        """Test that all event types have a severity mapping."""
        event_types = [
            EBPFEventType.OOM_KILL,
            EBPFEventType.OOM_SCORE_UPDATE,
            EBPFEventType.BLOCK_RQ_COMPLETE,
            EBPFEventType.BLOCK_RQ_ERROR,
            EBPFEventType.NET_DROP,
            EBPFEventType.NET_RETRANSMIT,
            EBPFEventType.PROCESS_EXIT,
            EBPFEventType.SIGNAL_DELIVER,
            EBPFEventType.THERMAL_ZONE_TRIP,
            EBPFEventType.CPU_FREQUENCY,
        ]
        for event_type in event_types:
            assert event_type in EBPF_EVENT_SEVERITIES, f"Missing severity for {event_type}"

    def test_oom_kill_is_critical(self):
        """Test that OOM kills are marked as critical."""
        assert EBPF_EVENT_SEVERITIES[EBPFEventType.OOM_KILL] == "critical"

    def test_block_error_is_error(self):
        """Test that block I/O errors are marked as error."""
        assert EBPF_EVENT_SEVERITIES[EBPFEventType.BLOCK_RQ_ERROR] == "error"

    def test_categories_are_valid(self):
        """Test that all categories are valid telemetry categories."""
        valid_categories = {"oom", "disk", "net", "service", "thermal", "syscall", "other"}
        for category in EBPF_EVENT_CATEGORIES.values():
            assert category in valid_categories, f"Invalid category: {category}"
