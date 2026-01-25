"""Tests for syscall tracing models."""

import pytest
from datetime import datetime, UTC

from elle.daemon.telemetry.ebpf.syscall_models import (
    NetworkConnection,
    SyscallCategory,
    SyscallEvent,
    SyscallSummary,
    SyscallTrace,
    SYSCALL_CATEGORIES,
    MAX_TRACED_EVENTS,
)


class TestSyscallEvent:
    """Tests for SyscallEvent model."""

    def test_create_file_read_event(self):
        """Test creating a file read event."""
        event = SyscallEvent(
            ts_ns=1234567890,
            syscall="openat",
            category="file_read",
            pid=1234,
            comm="test",
            path="/etc/passwd",
            flags=0,
            ret=3,
        )

        assert event.syscall == "openat"
        assert event.category == "file_read"
        assert event.path == "/etc/passwd"
        assert event.ret == 3

    def test_create_network_event(self):
        """Test creating a network connection event."""
        event = SyscallEvent(
            ts_ns=1234567890,
            syscall="connect",
            category="net_connect",
            pid=1234,
            comm="curl",
            addr="93.184.216.34",
            port=443,
            ret=0,
        )

        assert event.syscall == "connect"
        assert event.category == "net_connect"
        assert event.addr == "93.184.216.34"
        assert event.port == 443

    def test_event_is_frozen(self):
        """Test that events are immutable."""
        event = SyscallEvent(
            ts_ns=1234567890,
            syscall="openat",
            category="file_read",
            pid=1234,
            comm="test",
        )

        with pytest.raises(Exception):  # ValidationError or similar
            event.pid = 9999

    def test_optional_fields_default_none(self):
        """Test that optional fields default to None."""
        event = SyscallEvent(
            ts_ns=1234567890,
            syscall="clone",
            category="process_clone",
            pid=1234,
            comm="bash",
        )

        assert event.path is None
        assert event.addr is None
        assert event.port is None
        assert event.ret is None


class TestNetworkConnection:
    """Tests for NetworkConnection model."""

    def test_create_ipv4_connection(self):
        """Test creating an IPv4 connection."""
        conn = NetworkConnection(
            addr="192.168.1.1",
            port=8080,
            family="ipv4",
        )

        assert conn.addr == "192.168.1.1"
        assert conn.port == 8080
        assert conn.family == "ipv4"

    def test_create_ipv6_connection(self):
        """Test creating an IPv6 connection."""
        conn = NetworkConnection(
            addr="::1",
            port=443,
            family="ipv6",
        )

        assert conn.family == "ipv6"

    def test_port_validation(self):
        """Test that port must be in valid range."""
        # Valid port
        conn = NetworkConnection(addr="localhost", port=65535)
        assert conn.port == 65535

        # Invalid port should fail validation
        with pytest.raises(Exception):
            NetworkConnection(addr="localhost", port=65536)

        with pytest.raises(Exception):
            NetworkConnection(addr="localhost", port=-1)


class TestSyscallSummary:
    """Tests for SyscallSummary model."""

    def test_empty_summary(self):
        """Test creating an empty summary."""
        summary = SyscallSummary(
            command="echo hello",
            pid=1234,
            duration_ms=10,
            total_syscalls=0,
        )

        assert summary.command == "echo hello"
        assert summary.pid == 1234
        assert summary.duration_ms == 10
        assert summary.total_syscalls == 0
        assert summary.files_read == ()
        assert summary.files_written == ()
        assert summary.connections_made == ()
        assert summary.events_truncated is False

    def test_summary_with_file_operations(self):
        """Test summary with file operations."""
        summary = SyscallSummary(
            command="touch /tmp/test.txt",
            pid=1234,
            duration_ms=15,
            files_created=("/tmp/test.txt",),
            files_read=("/etc/passwd", "/etc/group"),
            total_syscalls=5,
        )

        assert summary.files_created == ("/tmp/test.txt",)
        assert len(summary.files_read) == 2
        assert "/etc/passwd" in summary.files_read

    def test_summary_with_network_operations(self):
        """Test summary with network connections."""
        conn = NetworkConnection(addr="example.com", port=443, family="ipv4")
        summary = SyscallSummary(
            command="curl https://example.com",
            pid=1234,
            duration_ms=500,
            connections_made=(conn,),
            total_syscalls=50,
        )

        assert len(summary.connections_made) == 1
        assert summary.connections_made[0].addr == "example.com"

    def test_summary_truncation_flag(self):
        """Test events_truncated flag."""
        summary = SyscallSummary(
            command="find / -name '*.txt'",
            pid=1234,
            duration_ms=5000,
            total_syscalls=MAX_TRACED_EVENTS,
            events_truncated=True,
        )

        assert summary.events_truncated is True


class TestSyscallTrace:
    """Tests for SyscallTrace model."""

    def test_disabled_trace(self):
        """Test trace that was not enabled."""
        trace = SyscallTrace(enabled=False)

        assert trace.enabled is False
        assert trace.has_trace is False
        assert trace.summary is None

    def test_trace_with_error(self):
        """Test trace that failed."""
        trace = SyscallTrace(
            enabled=True,
            error="Failed to attach eBPF program",
        )

        assert trace.enabled is True
        assert trace.has_trace is False
        assert trace.error == "Failed to attach eBPF program"

    def test_trace_with_summary(self):
        """Test trace with valid summary."""
        summary = SyscallSummary(
            command="ls -la",
            pid=1234,
            duration_ms=20,
            files_read=("/home/user",),
            total_syscalls=10,
        )

        trace = SyscallTrace(
            enabled=True,
            summary=summary,
            explanation="Listed directory contents",
        )

        assert trace.enabled is True
        assert trace.has_trace is True
        assert trace.summary.command == "ls -la"
        assert trace.explanation == "Listed directory contents"

    def test_has_file_operations_property(self):
        """Test has_file_operations property."""
        # No file operations
        summary1 = SyscallSummary(
            command="sleep 1",
            pid=1234,
            duration_ms=1000,
            total_syscalls=2,
        )
        trace1 = SyscallTrace(enabled=True, summary=summary1)
        assert trace1.has_file_operations is False

        # With file operations
        summary2 = SyscallSummary(
            command="cat /etc/passwd",
            pid=1234,
            duration_ms=10,
            files_read=("/etc/passwd",),
            total_syscalls=5,
        )
        trace2 = SyscallTrace(enabled=True, summary=summary2)
        assert trace2.has_file_operations is True

    def test_has_network_operations_property(self):
        """Test has_network_operations property."""
        conn = NetworkConnection(addr="google.com", port=80)

        # No network operations
        summary1 = SyscallSummary(
            command="ls",
            pid=1234,
            duration_ms=10,
            total_syscalls=5,
        )
        trace1 = SyscallTrace(enabled=True, summary=summary1)
        assert trace1.has_network_operations is False

        # With network operations
        summary2 = SyscallSummary(
            command="curl http://google.com",
            pid=1234,
            duration_ms=500,
            connections_made=(conn,),
            total_syscalls=50,
        )
        trace2 = SyscallTrace(enabled=True, summary=summary2)
        assert trace2.has_network_operations is True


class TestSyscallCategories:
    """Tests for syscall category mapping."""

    def test_file_operations_mapped(self):
        """Test that file operations are properly mapped."""
        assert SYSCALL_CATEGORIES["openat"] == "file_read"
        assert SYSCALL_CATEGORIES["write"] == "file_write"
        assert SYSCALL_CATEGORIES["unlink"] == "file_delete"
        assert SYSCALL_CATEGORIES["mkdir"] == "file_create"
        assert SYSCALL_CATEGORIES["rename"] == "file_rename"

    def test_network_operations_mapped(self):
        """Test that network operations are properly mapped."""
        assert SYSCALL_CATEGORIES["connect"] == "net_connect"
        assert SYSCALL_CATEGORIES["bind"] == "net_bind"
        assert SYSCALL_CATEGORIES["sendto"] == "net_send"

    def test_process_operations_mapped(self):
        """Test that process operations are properly mapped."""
        assert SYSCALL_CATEGORIES["execve"] == "process_exec"
        assert SYSCALL_CATEGORIES["clone"] == "process_clone"
        assert SYSCALL_CATEGORIES["fork"] == "process_clone"
