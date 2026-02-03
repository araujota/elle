"""Coverage tests for elle.daemon.telemetry.ebpf.syscall_models.

Targets the 5 missing lines (88% -> 100%): SyscallTrace properties
(has_trace, has_file_operations, has_network_operations, has_process_operations)
when summary is None vs populated.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from elle.daemon.telemetry.ebpf.syscall_models import (
    MAX_FILES_PER_CATEGORY,
    MAX_PATH_LENGTH,
    MAX_TRACED_EVENTS,
    RING_BUFFER_SIZE_KB,
    SYSCALL_CATEGORIES,
    NetworkConnection,
    SyscallEvent,
    SyscallSummary,
    SyscallTrace,
)

# ---------------------------------------------------------------------------
# SyscallEvent
# ---------------------------------------------------------------------------


class TestSyscallEvent:
    def test_minimal_event(self):
        ev = SyscallEvent(
            ts_ns=123456789,
            syscall="openat",
            category="file_read",
            pid=100,
            comm="cat",
        )
        assert ev.ts_ns == 123456789
        assert ev.syscall == "openat"
        assert ev.path is None
        assert ev.flags is None
        assert ev.mode is None
        assert ev.addr is None
        assert ev.port is None
        assert ev.ret is None

    def test_full_file_event(self):
        ev = SyscallEvent(
            ts_ns=1,
            syscall="openat",
            category="file_read",
            pid=1,
            comm="test",
            path="/etc/passwd",
            flags=0,
            mode=0o644,
            ret=3,
        )
        assert ev.path == "/etc/passwd"
        assert ev.flags == 0
        assert ev.mode == 0o644
        assert ev.ret == 3

    def test_network_event(self):
        ev = SyscallEvent(
            ts_ns=1,
            syscall="connect",
            category="net_connect",
            pid=1,
            comm="curl",
            addr="1.2.3.4",
            port=443,
            ret=0,
        )
        assert ev.addr == "1.2.3.4"
        assert ev.port == 443

    def test_frozen_model(self):
        ev = SyscallEvent(ts_ns=1, syscall="read", category="file_read", pid=1, comm="x")
        with pytest.raises(ValidationError):
            ev.pid = 999

    def test_pid_must_be_non_negative(self):
        with pytest.raises(ValidationError):
            SyscallEvent(ts_ns=1, syscall="read", category="file_read", pid=-1, comm="x")

    def test_port_validation(self):
        with pytest.raises(ValidationError):
            SyscallEvent(
                ts_ns=1,
                syscall="connect",
                category="net_connect",
                pid=1,
                comm="x",
                port=70000,
            )

    def test_comm_max_length(self):
        with pytest.raises(ValidationError):
            SyscallEvent(
                ts_ns=1,
                syscall="read",
                category="file_read",
                pid=1,
                comm="a" * 17,
            )


# ---------------------------------------------------------------------------
# NetworkConnection
# ---------------------------------------------------------------------------


class TestNetworkConnection:
    def test_defaults(self):
        nc = NetworkConnection(addr="127.0.0.1", port=80)
        assert nc.family == "ipv4"

    def test_ipv6(self):
        nc = NetworkConnection(addr="::1", port=443, family="ipv6")
        assert nc.family == "ipv6"

    def test_unix(self):
        nc = NetworkConnection(addr="/tmp/sock", port=0, family="unix")
        assert nc.family == "unix"

    def test_frozen(self):
        nc = NetworkConnection(addr="1.2.3.4", port=80)
        with pytest.raises(ValidationError):
            nc.port = 81


# ---------------------------------------------------------------------------
# SyscallSummary
# ---------------------------------------------------------------------------


class TestSyscallSummary:
    def test_minimal(self):
        s = SyscallSummary(command="ls", pid=1, duration_ms=10, total_syscalls=5)
        assert s.files_read == ()
        assert s.files_written == ()
        assert s.files_deleted == ()
        assert s.files_created == ()
        assert s.files_renamed == ()
        assert s.connections_made == ()
        assert s.programs_executed == ()
        assert s.processes_spawned == 0
        assert s.events_truncated is False
        assert s.start_time is None
        assert s.end_time is None

    def test_full(self):
        nc = NetworkConnection(addr="1.2.3.4", port=443)
        now = datetime.now(timezone.utc)
        s = SyscallSummary(
            command="apt install foo",
            pid=100,
            duration_ms=500,
            total_syscalls=200,
            files_read=("/etc/apt/sources.list",),
            files_written=("/var/cache/apt/foo.deb",),
            files_deleted=("/tmp/old",),
            files_created=("/tmp/new",),
            files_renamed=(("/tmp/a", "/tmp/b"),),
            connections_made=(nc,),
            programs_executed=("/usr/bin/dpkg",),
            processes_spawned=3,
            events_truncated=True,
            start_time=now,
            end_time=now,
        )
        assert len(s.files_read) == 1
        assert s.events_truncated is True
        assert s.processes_spawned == 3


# ---------------------------------------------------------------------------
# SyscallTrace -- properties (main coverage targets)
# ---------------------------------------------------------------------------


class TestSyscallTrace:
    def _make_summary(self, **kwargs):
        defaults = {"command": "test", "pid": 1, "duration_ms": 10, "total_syscalls": 1}
        defaults.update(kwargs)
        return SyscallSummary(**defaults)

    # -- has_trace --

    def test_has_trace_false_when_no_summary(self):
        t = SyscallTrace(enabled=True, summary=None)
        assert t.has_trace is False

    def test_has_trace_true_when_summary_present(self):
        t = SyscallTrace(enabled=True, summary=self._make_summary())
        assert t.has_trace is True

    # -- has_file_operations (covers the None-summary early-return AND the bool check) --

    def test_has_file_operations_false_no_summary(self):
        t = SyscallTrace(enabled=True, summary=None)
        assert t.has_file_operations is False

    def test_has_file_operations_false_empty_summary(self):
        t = SyscallTrace(enabled=True, summary=self._make_summary())
        assert t.has_file_operations is False

    def test_has_file_operations_true_files_read(self):
        t = SyscallTrace(
            enabled=True,
            summary=self._make_summary(files_read=("/etc/hosts",)),
        )
        assert t.has_file_operations is True

    def test_has_file_operations_true_files_written(self):
        t = SyscallTrace(
            enabled=True,
            summary=self._make_summary(files_written=("/tmp/out",)),
        )
        assert t.has_file_operations is True

    def test_has_file_operations_true_files_deleted(self):
        t = SyscallTrace(
            enabled=True,
            summary=self._make_summary(files_deleted=("/tmp/old",)),
        )
        assert t.has_file_operations is True

    def test_has_file_operations_true_files_created(self):
        t = SyscallTrace(
            enabled=True,
            summary=self._make_summary(files_created=("/tmp/new",)),
        )
        assert t.has_file_operations is True

    def test_has_file_operations_true_files_renamed(self):
        t = SyscallTrace(
            enabled=True,
            summary=self._make_summary(files_renamed=(("/a", "/b"),)),
        )
        assert t.has_file_operations is True

    # -- has_network_operations --

    def test_has_network_operations_false_no_summary(self):
        t = SyscallTrace(enabled=True, summary=None)
        assert t.has_network_operations is False

    def test_has_network_operations_false_empty(self):
        t = SyscallTrace(enabled=True, summary=self._make_summary())
        assert t.has_network_operations is False

    def test_has_network_operations_true(self):
        nc = NetworkConnection(addr="1.2.3.4", port=80)
        t = SyscallTrace(
            enabled=True,
            summary=self._make_summary(connections_made=(nc,)),
        )
        assert t.has_network_operations is True

    # -- has_process_operations --

    def test_has_process_operations_false_no_summary(self):
        t = SyscallTrace(enabled=True, summary=None)
        assert t.has_process_operations is False

    def test_has_process_operations_false_empty(self):
        t = SyscallTrace(enabled=True, summary=self._make_summary())
        assert t.has_process_operations is False

    def test_has_process_operations_true_programs(self):
        t = SyscallTrace(
            enabled=True,
            summary=self._make_summary(programs_executed=("/usr/bin/ls",)),
        )
        assert t.has_process_operations is True

    def test_has_process_operations_true_spawned(self):
        t = SyscallTrace(
            enabled=True,
            summary=self._make_summary(processes_spawned=2),
        )
        assert t.has_process_operations is True

    # -- defaults --

    def test_defaults(self):
        t = SyscallTrace()
        assert t.enabled is False
        assert t.summary is None
        assert t.error is None
        assert t.explanation is None

    def test_with_error(self):
        t = SyscallTrace(enabled=True, error="BPF load failed")
        assert t.error == "BPF load failed"
        assert t.has_trace is False

    def test_with_explanation(self):
        t = SyscallTrace(
            enabled=True,
            summary=self._make_summary(),
            explanation="Did nothing notable.",
        )
        assert t.explanation == "Did nothing notable."


# ---------------------------------------------------------------------------
# Constants and mapping
# ---------------------------------------------------------------------------


class TestConstants:
    def test_max_traced_events(self):
        assert MAX_TRACED_EVENTS == 10000

    def test_max_path_length(self):
        assert MAX_PATH_LENGTH == 256

    def test_max_files_per_category(self):
        assert MAX_FILES_PER_CATEGORY == 100

    def test_ring_buffer_size(self):
        assert RING_BUFFER_SIZE_KB == 64

    def test_syscall_categories_has_key_entries(self):
        assert SYSCALL_CATEGORIES["openat"] == "file_read"
        assert SYSCALL_CATEGORIES["connect"] == "net_connect"
        assert SYSCALL_CATEGORIES["execve"] == "process_exec"
        assert SYSCALL_CATEGORIES["clone"] == "process_clone"
        assert SYSCALL_CATEGORIES["unlink"] == "file_delete"
        assert SYSCALL_CATEGORIES["rename"] == "file_rename"
        assert SYSCALL_CATEGORIES["chmod"] == "file_chmod"
        assert SYSCALL_CATEGORIES["bind"] == "net_bind"
        assert SYSCALL_CATEGORIES["sendto"] == "net_send"
        assert SYSCALL_CATEGORIES["creat"] == "file_create"
        assert SYSCALL_CATEGORIES["write"] == "file_write"
