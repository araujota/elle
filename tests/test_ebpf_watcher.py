"""Tests for EBPFWatcher."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.telemetry.ebpf.models import (
    EBPFEventType,
    EBPFProgramStatus,
    EBPFRawEvent,
    EBPFWatcherStatus,
)
from elle.daemon.telemetry.ebpf.watcher import EBPFWatcher
from elle.daemon.telemetry.queue import TelemetryQueue


@pytest.fixture
def mock_queue():
    """Create a mock telemetry queue."""
    queue = MagicMock(spec=TelemetryQueue)
    queue.put_nowait = MagicMock(return_value=True)
    return queue


@pytest.fixture
def shutdown_event():
    """Create a shutdown event."""
    return asyncio.Event()


class TestEBPFWatcherInit:
    """Tests for EBPFWatcher initialization."""

    def test_init_default_programs(self, mock_queue, shutdown_event):
        """Test initialization with default programs."""
        watcher = EBPFWatcher(mock_queue, shutdown_event)
        assert watcher._requested_programs == [
            "oom", "block_io", "process", "thermal", "net_drops"
        ]

    def test_init_custom_programs(self, mock_queue, shutdown_event):
        """Test initialization with custom program list."""
        watcher = EBPFWatcher(
            mock_queue,
            shutdown_event,
            programs=["oom", "block_io"],
        )
        assert watcher._requested_programs == ["oom", "block_io"]

    def test_init_not_running(self, mock_queue, shutdown_event):
        """Test that watcher starts in not running state."""
        watcher = EBPFWatcher(mock_queue, shutdown_event)
        assert watcher.running is False
        assert watcher.total_events == 0


class TestEBPFWatcherStatus:
    """Tests for EBPFWatcher status reporting."""

    def test_get_status_disabled(self, mock_queue, shutdown_event):
        """Test status when eBPF is not available."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(False, "BCC not installed"),
        ):
            watcher = EBPFWatcher(mock_queue, shutdown_event)
            status = watcher.get_status()

            assert isinstance(status, EBPFWatcherStatus)
            assert status.enabled is False

    def test_get_status_enabled(self, mock_queue, shutdown_event):
        """Test status when eBPF is available."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(True, "Running as root"),
        ):
            with patch(
                "elle.daemon.telemetry.ebpf.capabilities.check_capabilities",
            ) as mock_check:
                mock_check.return_value = MagicMock(available=True)
                with patch(
                    "elle.daemon.telemetry.ebpf.capabilities.get_missing_capabilities",
                    return_value=[],
                ):
                    watcher = EBPFWatcher(mock_queue, shutdown_event)
                    status = watcher.get_status()

                    assert status.enabled is True
                    assert status.backend == "bcc"


class TestEBPFWatcherEventHandling:
    """Tests for EBPFWatcher event handling."""

    def test_event_to_raw_dict_oom(self, mock_queue, shutdown_event):
        """Test converting OOM event to raw dict."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(True, "Running as root"),
        ):
            watcher = EBPFWatcher(mock_queue, shutdown_event)

            event = EBPFRawEvent(
                ts_ns=1234567890,
                cpu=0,
                event_type=EBPFEventType.OOM_KILL,
                pid=1234,
                tgid=1234,
                uid=1000,
                comm="nginx",
                data={"oom_score_adj": 0},
            )

            raw = watcher._event_to_raw_dict(event)

            assert raw["_SOURCE"] == "ebpf"
            assert raw["_EBPF_EVENT_TYPE"] == "oom_kill"
            assert raw["_EBPF_CATEGORY"] == "oom"
            assert raw["PRIORITY"] == "2"  # Critical
            assert raw["_PID"] == "1234"
            assert raw["_COMM"] == "nginx"
            assert "OOM" in raw["MESSAGE"]

    def test_event_to_raw_dict_process_exit(self, mock_queue, shutdown_event):
        """Test converting process exit event to raw dict."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(True, "Running as root"),
        ):
            watcher = EBPFWatcher(mock_queue, shutdown_event)

            event = EBPFRawEvent(
                ts_ns=1234567890,
                cpu=1,
                event_type=EBPFEventType.PROCESS_EXIT,
                pid=5678,
                comm="mysql",
                data={"exit_code": 0, "signal": 9},
            )

            raw = watcher._event_to_raw_dict(event)

            assert raw["_SOURCE"] == "ebpf"
            assert raw["_EBPF_EVENT_TYPE"] == "process_exit"
            assert raw["_EBPF_CATEGORY"] == "service"
            assert "killed by signal" in raw["MESSAGE"]

    def test_event_to_raw_dict_block_io(self, mock_queue, shutdown_event):
        """Test converting block I/O event to raw dict."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(True, "Running as root"),
        ):
            watcher = EBPFWatcher(mock_queue, shutdown_event)

            event = EBPFRawEvent(
                ts_ns=1234567890,
                cpu=2,
                event_type=EBPFEventType.BLOCK_RQ_ERROR,
                data={"dev": "sda", "error": -5},
            )

            raw = watcher._event_to_raw_dict(event)

            assert raw["_SOURCE"] == "ebpf"
            assert raw["_EBPF_EVENT_TYPE"] == "block_rq_error"
            assert raw["_EBPF_CATEGORY"] == "disk"
            assert "I/O error" in raw["MESSAGE"]

    def test_handle_event_increments_counter(self, mock_queue, shutdown_event):
        """Test that handling events increments counter."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(True, "Running as root"),
        ):
            watcher = EBPFWatcher(mock_queue, shutdown_event)
            assert watcher.total_events == 0

            event = EBPFRawEvent(
                ts_ns=1234567890,
                cpu=0,
                event_type=EBPFEventType.OOM_KILL,
            )

            watcher._handle_event(event)
            assert watcher.total_events == 1

            watcher._handle_event(event)
            assert watcher.total_events == 2

    def test_handle_event_queues_raw_dict(self, mock_queue, shutdown_event):
        """Test that handled events are queued."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(True, "Running as root"),
        ):
            watcher = EBPFWatcher(mock_queue, shutdown_event)

            event = EBPFRawEvent(
                ts_ns=1234567890,
                cpu=0,
                event_type=EBPFEventType.NET_DROP,
                data={"reason": "NO_SOCKET", "ifname": "eth0"},
            )

            watcher._handle_event(event)

            mock_queue.put_nowait.assert_called_once()
            call_args = mock_queue.put_nowait.call_args[0][0]
            assert call_args["_SOURCE"] == "ebpf"
            assert call_args["_EBPF_EVENT_TYPE"] == "net_drop"


class TestEBPFWatcherMessageFormatting:
    """Tests for message formatting."""

    def test_format_message_oom_kill(self, mock_queue, shutdown_event):
        """Test OOM kill message formatting."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(True, "Running as root"),
        ):
            watcher = EBPFWatcher(mock_queue, shutdown_event)

            event = EBPFRawEvent(
                ts_ns=1,
                cpu=0,
                event_type=EBPFEventType.OOM_KILL,
                pid=1234,
                comm="nginx",
            )

            message = watcher._format_message(event)
            assert "OOM killer" in message
            assert "nginx" in message
            assert "1234" in message

    def test_format_message_block_error(self, mock_queue, shutdown_event):
        """Test block I/O error message formatting."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(True, "Running as root"),
        ):
            watcher = EBPFWatcher(mock_queue, shutdown_event)

            event = EBPFRawEvent(
                ts_ns=1,
                cpu=0,
                event_type=EBPFEventType.BLOCK_RQ_ERROR,
                data={"dev": "sda", "error": 5},
            )

            message = watcher._format_message(event)
            assert "Block I/O error" in message
            assert "sda" in message

    def test_format_message_net_drop(self, mock_queue, shutdown_event):
        """Test network drop message formatting."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(True, "Running as root"),
        ):
            watcher = EBPFWatcher(mock_queue, shutdown_event)

            event = EBPFRawEvent(
                ts_ns=1,
                cpu=0,
                event_type=EBPFEventType.NET_DROP,
                data={"reason": "NO_SOCKET", "ifname": "eth0"},
            )

            message = watcher._format_message(event)
            assert "dropped" in message
            assert "eth0" in message

    def test_format_message_process_exit_signal(self, mock_queue, shutdown_event):
        """Test process exit with signal message formatting."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(True, "Running as root"),
        ):
            watcher = EBPFWatcher(mock_queue, shutdown_event)

            event = EBPFRawEvent(
                ts_ns=1,
                cpu=0,
                event_type=EBPFEventType.PROCESS_EXIT,
                pid=5678,
                comm="mysql",
                data={"exit_code": 0, "signal": 9},
            )

            message = watcher._format_message(event)
            assert "killed by signal" in message
            assert "mysql" in message

    def test_format_message_process_exit_normal(self, mock_queue, shutdown_event):
        """Test process exit with exit code message formatting."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(True, "Running as root"),
        ):
            watcher = EBPFWatcher(mock_queue, shutdown_event)

            event = EBPFRawEvent(
                ts_ns=1,
                cpu=0,
                event_type=EBPFEventType.PROCESS_EXIT,
                pid=1234,
                comm="app",
                data={"exit_code": 1, "signal": 0},
            )

            message = watcher._format_message(event)
            assert "exited with code" in message
            assert "app" in message

    def test_format_message_thermal(self, mock_queue, shutdown_event):
        """Test thermal zone trip message formatting."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(True, "Running as root"),
        ):
            watcher = EBPFWatcher(mock_queue, shutdown_event)

            event = EBPFRawEvent(
                ts_ns=1,
                cpu=0,
                event_type=EBPFEventType.THERMAL_ZONE_TRIP,
                data={"zone": 0, "trip_type": "critical", "temp": 95},
            )

            message = watcher._format_message(event)
            assert "Thermal zone" in message
            assert "critical" in message
            assert "95" in message


class TestEBPFWatcherLifecycle:
    """Tests for EBPFWatcher lifecycle."""

    @pytest.mark.asyncio
    async def test_start_when_not_available(self, mock_queue, shutdown_event):
        """Test that start exits early when eBPF not available."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(False, "BCC not installed"),
        ):
            watcher = EBPFWatcher(mock_queue, shutdown_event)

            # Start should complete immediately without error
            await watcher.start()

            assert watcher.running is False

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self, mock_queue, shutdown_event):
        """Test that stop cleans up resources."""
        with patch(
            "elle.daemon.telemetry.ebpf.watcher.can_use_ebpf",
            return_value=(True, "Running as root"),
        ):
            watcher = EBPFWatcher(mock_queue, shutdown_event)

            # Add a mock program
            mock_program = MagicMock()
            watcher._programs = [mock_program]
            watcher._running = True

            await watcher.stop()

            mock_program.unload.assert_called_once()
            assert watcher._programs == []
            assert watcher.running is False
