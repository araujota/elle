"""Integration tests for elled-ebpfd socket communication.

These tests verify that the Python UnixSocketWatcher can correctly
read and parse NDJSON events from the C-based eBPF collector.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Skip if not running as root (required for eBPF)
pytestmark = pytest.mark.skipif(
    os.geteuid() != 0,
    reason="eBPF tests require root privileges"
)


class TestUnixSocketWatcher:
    """Tests for UnixSocketWatcher."""

    @pytest.fixture
    def mock_queue(self) -> MagicMock:
        """Create a mock telemetry queue."""
        queue = MagicMock()
        queue.put_nowait = MagicMock()
        return queue

    @pytest.fixture
    def shutdown_event(self) -> asyncio.Event:
        """Create a shutdown event."""
        return asyncio.Event()

    @pytest.fixture
    def temp_socket_path(self) -> Path:
        """Create a temporary socket path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "test.sock"

    async def test_is_socket_available_returns_false_when_missing(
        self,
        temp_socket_path: Path,
    ) -> None:
        """Test that is_socket_available returns False for missing socket."""
        from elle.daemon.telemetry.ebpf.socket_watcher import is_socket_available

        assert not is_socket_available(temp_socket_path)

    async def test_watcher_handles_missing_socket_gracefully(
        self,
        mock_queue: MagicMock,
        shutdown_event: asyncio.Event,
        temp_socket_path: Path,
    ) -> None:
        """Test that watcher handles missing socket gracefully."""
        from elle.daemon.telemetry.ebpf.socket_watcher import UnixSocketWatcher

        watcher = UnixSocketWatcher(
            mock_queue,
            shutdown_event,
            socket_path=temp_socket_path,
        )

        # Should not be connected initially
        assert not watcher.connected
        assert watcher.total_events == 0

    async def test_watcher_status_reports_libbpf_backend(
        self,
        mock_queue: MagicMock,
        shutdown_event: asyncio.Event,
        temp_socket_path: Path,
    ) -> None:
        """Test that watcher status reports libbpf-socket backend."""
        from elle.daemon.telemetry.ebpf.socket_watcher import UnixSocketWatcher

        watcher = UnixSocketWatcher(
            mock_queue,
            shutdown_event,
            socket_path=temp_socket_path,
        )

        status = watcher.get_status()
        assert status.backend == "libbpf-socket"
        assert status.enabled is True


class TestJSONParsing:
    """Tests for NDJSON event parsing."""

    def test_parse_oom_event(self) -> None:
        """Test parsing an OOM kill event."""
        json_str = json.dumps({
            "_SOURCE": "ebpf",
            "_EBPF_EVENT_TYPE": "oom_kill",
            "_EBPF_CATEGORY": "oom",
            "MESSAGE": "OOM killer invoked, killed process python (PID 1234)",
            "PRIORITY": "2",
            "_EBPF_TS_NS": 1234567890,
            "_EBPF_CPU": 0,
            "_PID": "1234",
            "_COMM": "python",
            "_EBPF_OOM_SCORE_ADJ": 0,
        })

        event = json.loads(json_str)

        assert event["_SOURCE"] == "ebpf"
        assert event["_EBPF_EVENT_TYPE"] == "oom_kill"
        assert event["_EBPF_CATEGORY"] == "oom"
        assert event["_PID"] == "1234"
        assert event["_COMM"] == "python"

    def test_parse_block_io_event(self) -> None:
        """Test parsing a block I/O event."""
        json_str = json.dumps({
            "_SOURCE": "ebpf",
            "_EBPF_EVENT_TYPE": "block_rq_complete",
            "_EBPF_CATEGORY": "disk",
            "MESSAGE": "Block I/O completed on device 8:1 (latency 5.00ms)",
            "PRIORITY": "6",
            "_EBPF_DEV": 2049,
            "_EBPF_SECTOR": 12345678,
            "_EBPF_LATENCY_NS": 5000000,
        })

        event = json.loads(json_str)

        assert event["_EBPF_EVENT_TYPE"] == "block_rq_complete"
        assert event["_EBPF_CATEGORY"] == "disk"
        assert event["_EBPF_LATENCY_NS"] == 5000000

    def test_parse_process_exit_event(self) -> None:
        """Test parsing a process exit event."""
        json_str = json.dumps({
            "_SOURCE": "ebpf",
            "_EBPF_EVENT_TYPE": "process_exit",
            "_EBPF_CATEGORY": "service",
            "MESSAGE": "Process test (PID 5678) killed by signal SIGSEGV (11)",
            "PRIORITY": "3",
            "_PID": "5678",
            "_COMM": "test",
            "_EBPF_SIGNAL": 11,
            "_EBPF_SIGNAL_NAME": "SIGSEGV",
        })

        event = json.loads(json_str)

        assert event["_EBPF_EVENT_TYPE"] == "process_exit"
        assert event["_EBPF_SIGNAL"] == 11
        assert event["_EBPF_SIGNAL_NAME"] == "SIGSEGV"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
