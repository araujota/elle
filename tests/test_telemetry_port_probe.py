"""Tests for PortListenerProbe."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from elle.daemon.telemetry.port_probe import (
    SENSITIVE_PORTS,
    TRUSTED_PROCESSES,
    Listener,
    PortListenerProbe,
)


@pytest.fixture
def event_queue():
    """Create an event queue for testing."""
    return asyncio.Queue()


@pytest.fixture
def probe(event_queue):
    """Create a PortListenerProbe for testing."""
    return PortListenerProbe(event_queue, scan_interval_sec=60)


class TestPortListenerProbe:
    """Tests for PortListenerProbe."""

    def test_init(self, probe, event_queue):
        """Test probe initialization."""
        assert probe._queue is event_queue
        assert probe._scan_interval == 60
        assert probe._running is False
        assert len(probe._baseline) == 0

    def test_sensitive_ports_defined(self):
        """Test sensitive ports are properly defined."""
        assert 22 in SENSITIVE_PORTS  # SSH
        assert 80 in SENSITIVE_PORTS  # HTTP
        assert 443 in SENSITIVE_PORTS  # HTTPS
        assert 3306 in SENSITIVE_PORTS  # MySQL
        assert 5432 in SENSITIVE_PORTS  # PostgreSQL
        assert 6379 in SENSITIVE_PORTS  # Redis

    def test_trusted_processes_defined(self):
        """Test trusted processes are defined for sensitive ports."""
        assert "sshd" in TRUSTED_PROCESSES[22]
        assert "nginx" in TRUSTED_PROCESSES[80]
        assert "nginx" in TRUSTED_PROCESSES[443]

    def test_is_running(self, probe):
        """Test is_running property."""
        assert probe.is_running is False
        probe._running = True
        assert probe.is_running is True

    def test_stats(self, probe):
        """Test stats property."""
        stats = probe.stats
        assert "scans_completed" in stats
        assert "new_listeners_detected" in stats
        assert "sensitive_alerts" in stats
        assert "errors" in stats

    def test_listener_namedtuple(self):
        """Test Listener named tuple."""
        listener = Listener(
            port=22,
            proto="tcp",
            address="0.0.0.0",
            pid=1234,
            comm="sshd",
            is_wildcard=True,
        )

        assert listener.port == 22
        assert listener.proto == "tcp"
        assert listener.is_wildcard is True

    def test_hex_to_ip_v4(self, probe):
        """Test IPv4 hex to IP conversion."""
        # 0.0.0.0 in little-endian hex
        result = probe._hex_to_ip("00000000")
        assert result == "0.0.0.0"

        # 127.0.0.1 in little-endian hex (0100007F)
        result = probe._hex_to_ip("0100007F")
        assert result == "127.0.0.1"

    def test_hex_to_ip_v6(self, probe):
        """Test IPv6 hex to IP conversion."""
        # All zeros = ::
        result = probe._hex_to_ip("00000000000000000000000000000000")
        assert result == "::"

        # Loopback
        result = probe._hex_to_ip("00000000000000000000000001000000")
        assert result == "::1"

    def test_get_baseline(self, probe):
        """Test getting baseline listeners."""
        # Initially empty
        assert len(probe.get_baseline()) == 0

        # Add to baseline
        probe._baseline[(22, "tcp", "0.0.0.0")] = Listener(
            port=22, proto="tcp", address="0.0.0.0", pid=1, comm="sshd", is_wildcard=True
        )

        baseline = probe.get_baseline()
        assert len(baseline) == 1
        assert baseline[0].port == 22

    def test_get_sensitive_listeners(self, probe):
        """Test getting sensitive port listeners."""
        # Add listeners - use port 9000 as non-sensitive port
        probe._baseline[(22, "tcp", "0.0.0.0")] = Listener(
            port=22, proto="tcp", address="0.0.0.0", pid=1, comm="sshd", is_wildcard=True
        )
        probe._baseline[(9000, "tcp", "0.0.0.0")] = Listener(
            port=9000, proto="tcp", address="0.0.0.0", pid=2, comm="app", is_wildcard=True
        )

        sensitive = probe.get_sensitive_listeners()
        assert len(sensitive) == 1
        assert sensitive[0].port == 22

    @pytest.mark.asyncio
    async def test_emit_new_listener_event(self, probe, event_queue):
        """Test emitting new listener event."""
        listener = Listener(
            port=8080,
            proto="tcp",
            address="0.0.0.0",
            pid=1234,
            comm="myapp",
            is_wildcard=True,
        )

        await probe._emit_new_listener_event(listener)

        assert not event_queue.empty()
        event = event_queue.get_nowait()
        assert event.category == "net"
        assert event.severity == "info"
        assert "8080" in event.message
        assert "myapp" in event.message

    @pytest.mark.asyncio
    async def test_emit_sensitive_port_alert(self, probe, event_queue):
        """Test emitting sensitive port alert."""
        listener = Listener(
            port=22,
            proto="tcp",
            address="0.0.0.0",
            pid=9999,
            comm="nc",  # netcat - not trusted
            is_wildcard=True,
        )

        await probe._emit_sensitive_port_alert(listener)

        assert not event_queue.empty()
        event = event_queue.get_nowait()
        assert event.category == "net"
        assert event.severity == "error"
        assert "Unauthorized" in event.message
        assert "ssh" in event.message.lower()

    @pytest.mark.asyncio
    async def test_scan_and_check_new_listener(self, probe, event_queue):
        """Test scan detecting new listener."""
        # Set up initial baseline
        probe._baseline = {}

        # Mock _get_all_listeners to return a new listener
        async def mock_get_listeners():
            return [
                Listener(
                    port=8080,
                    proto="tcp",
                    address="0.0.0.0",
                    pid=1234,
                    comm="newapp",
                    is_wildcard=True,
                )
            ]

        probe._get_all_listeners = mock_get_listeners

        await probe._scan_and_check()

        assert probe._stats["new_listeners_detected"] == 1
        assert not event_queue.empty()

    @pytest.mark.asyncio
    async def test_scan_and_check_sensitive_alert(self, probe, event_queue):
        """Test scan detecting untrusted process on sensitive port."""
        probe._baseline = {}

        async def mock_get_listeners():
            return [
                Listener(
                    port=22,
                    proto="tcp",
                    address="0.0.0.0",
                    pid=9999,
                    comm="evil",  # Not trusted
                    is_wildcard=True,
                )
            ]

        probe._get_all_listeners = mock_get_listeners

        await probe._scan_and_check()

        assert probe._stats["sensitive_alerts"] == 1

    @pytest.mark.asyncio
    async def test_scan_and_check_trusted_process(self, probe, event_queue):
        """Test scan with trusted process on sensitive port."""
        probe._baseline = {}

        async def mock_get_listeners():
            return [
                Listener(
                    port=22,
                    proto="tcp",
                    address="0.0.0.0",
                    pid=1,
                    comm="sshd",  # Trusted
                    is_wildcard=True,
                )
            ]

        probe._get_all_listeners = mock_get_listeners

        await probe._scan_and_check()

        # Should detect as new but not as sensitive alert
        assert probe._stats["new_listeners_detected"] == 1
        assert probe._stats["sensitive_alerts"] == 0

    @pytest.mark.asyncio
    async def test_run_now(self, probe):
        """Test running scan immediately."""
        probe._baseline = {}

        async def mock_get_listeners():
            return []

        probe._get_all_listeners = mock_get_listeners

        events = await probe.run_now()
        assert isinstance(events, list)

    @pytest.mark.asyncio
    async def test_stop(self, probe):
        """Test stopping the probe."""
        probe._running = True
        await probe.stop()
        assert probe._running is False

    @pytest.mark.asyncio
    async def test_publish_event_queue_full(self, event_queue):
        """Test handling full queue."""
        small_queue = asyncio.Queue(maxsize=1)
        probe = PortListenerProbe(small_queue)

        # Fill queue
        await small_queue.put(MagicMock())

        # Try to publish
        event = MagicMock()
        await probe._publish_event(event)

        # Error count should increase
        assert probe._stats["errors"] >= 1


class TestPortCategories:
    """Tests for port categorization."""

    def test_sensitive_port_names(self):
        """Test sensitive port names are meaningful."""
        assert SENSITIVE_PORTS[22] == "ssh"
        assert SENSITIVE_PORTS[80] == "http"
        assert SENSITIVE_PORTS[443] == "https"
        assert SENSITIVE_PORTS[3306] == "mysql"
        assert SENSITIVE_PORTS[5432] == "postgres"
        assert SENSITIVE_PORTS[6379] == "redis"
        assert SENSITIVE_PORTS[27017] == "mongodb"
