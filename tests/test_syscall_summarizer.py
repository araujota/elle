"""Tests for syscall summarizer."""

import pytest
from pathlib import Path

from elle.daemon.telemetry.ebpf.syscall_models import (
    SyscallEvent,
    SyscallSummary,
)
from elle.daemon.telemetry.ebpf.syscall_summarizer import (
    SyscallSummarizer,
    create_summarizer,
    summarize_events,
    _should_include_path,
    _normalize_path,
)


class TestPathFiltering:
    """Tests for path filtering logic."""

    def test_include_regular_paths(self):
        """Test that regular paths are included."""
        assert _should_include_path("/home/user/test.txt") is True
        assert _should_include_path("/tmp/output.log") is True
        assert _should_include_path("/etc/nginx/nginx.conf") is True
        assert _should_include_path("/var/www/index.html") is True

    def test_exclude_proc_paths(self):
        """Test that /proc paths are excluded."""
        assert _should_include_path("/proc/self/status") is False
        assert _should_include_path("/proc/1/cmdline") is False
        assert _should_include_path("/proc/meminfo") is False

    def test_exclude_sys_paths(self):
        """Test that /sys paths are excluded."""
        assert _should_include_path("/sys/class/net/eth0") is False
        assert _should_include_path("/sys/kernel/debug") is False

    def test_exclude_device_paths(self):
        """Test that special device paths are excluded."""
        assert _should_include_path("/dev/null") is False
        assert _should_include_path("/dev/urandom") is False
        assert _should_include_path("/dev/random") is False

    def test_exclude_library_paths(self):
        """Test that library paths are excluded."""
        assert _should_include_path("/usr/lib/locale/en_US") is False
        assert _should_include_path("/lib/x86_64-linux-gnu/libc.so.6") is False

    def test_empty_path_excluded(self):
        """Test that empty paths are excluded."""
        assert _should_include_path("") is False
        assert _should_include_path(None) is False  # type: ignore


class TestPathNormalization:
    """Tests for path normalization."""

    def test_normalize_absolute_path(self):
        """Test normalizing absolute paths."""
        result = _normalize_path("/home/user/test.txt")
        assert result == "/home/user/test.txt"

    def test_normalize_relative_path_with_cwd(self):
        """Test normalizing relative paths with cwd."""
        cwd = Path("/home/user")
        result = _normalize_path("documents/test.txt", cwd)
        assert result == "/home/user/documents/test.txt"

    def test_normalize_empty_path(self):
        """Test normalizing empty path."""
        result = _normalize_path("")
        assert result == ""


class TestSyscallSummarizer:
    """Tests for SyscallSummarizer class."""

    def test_empty_summary(self):
        """Test summarizer with no events."""
        summarizer = create_summarizer("echo hello", pid=1234)
        summary = summarizer.get_summary()

        assert summary.command == "echo hello"
        assert summary.pid == 1234
        assert summary.total_syscalls == 0
        assert summary.files_read == ()
        assert summary.files_written == ()
        assert summary.events_truncated is False

    def test_categorize_file_read(self):
        """Test categorizing file read events."""
        summarizer = create_summarizer("cat /etc/passwd", pid=1234)

        event = SyscallEvent(
            ts_ns=1000000,
            syscall="openat",
            category="file_read",
            pid=1234,
            comm="cat",
            path="/etc/passwd",
            flags=0,
            ret=3,
        )
        summarizer.add_event(event)

        summary = summarizer.get_summary()
        assert "/etc/passwd" in summary.files_read
        assert summary.total_syscalls == 1

    def test_categorize_file_write(self):
        """Test categorizing file write events."""
        summarizer = create_summarizer("echo test > /tmp/out.txt", pid=1234)

        event = SyscallEvent(
            ts_ns=1000000,
            syscall="openat",
            category="file_write",
            pid=1234,
            comm="bash",
            path="/tmp/out.txt",
            flags=0x41,  # O_WRONLY | O_CREAT
            ret=3,
        )
        summarizer.add_event(event)

        summary = summarizer.get_summary()
        assert "/tmp/out.txt" in summary.files_written

    def test_categorize_file_create(self):
        """Test categorizing file create events."""
        summarizer = create_summarizer("touch /tmp/new.txt", pid=1234)

        event = SyscallEvent(
            ts_ns=1000000,
            syscall="openat",
            category="file_create",
            pid=1234,
            comm="touch",
            path="/tmp/new.txt",
            flags=0x42,  # O_CREAT | O_RDWR
            ret=3,
        )
        summarizer.add_event(event)

        summary = summarizer.get_summary()
        assert "/tmp/new.txt" in summary.files_created

    def test_categorize_file_delete(self):
        """Test categorizing file delete events."""
        summarizer = create_summarizer("rm /tmp/old.txt", pid=1234)

        event = SyscallEvent(
            ts_ns=1000000,
            syscall="unlinkat",
            category="file_delete",
            pid=1234,
            comm="rm",
            path="/tmp/old.txt",
            ret=0,
        )
        summarizer.add_event(event)

        summary = summarizer.get_summary()
        assert "/tmp/old.txt" in summary.files_deleted

    def test_categorize_network_connect(self):
        """Test categorizing network connection events."""
        summarizer = create_summarizer("curl https://example.com", pid=1234)

        event = SyscallEvent(
            ts_ns=1000000,
            syscall="connect",
            category="net_connect",
            pid=1234,
            comm="curl",
            addr="93.184.216.34",
            port=443,
            ret=0,
        )
        summarizer.add_event(event)

        summary = summarizer.get_summary()
        assert len(summary.connections_made) == 1
        assert summary.connections_made[0].addr == "93.184.216.34"
        assert summary.connections_made[0].port == 443

    def test_categorize_process_exec(self):
        """Test categorizing process execution events."""
        summarizer = create_summarizer("bash -c 'ls'", pid=1234)

        event = SyscallEvent(
            ts_ns=1000000,
            syscall="execve",
            category="process_exec",
            pid=1234,
            comm="bash",
            path="/bin/ls",
            ret=0,
        )
        summarizer.add_event(event)

        summary = summarizer.get_summary()
        assert "/bin/ls" in summary.programs_executed

    def test_categorize_process_clone(self):
        """Test categorizing process clone events."""
        summarizer = create_summarizer("parallel echo ::: 1 2 3", pid=1234)

        for i in range(3):
            event = SyscallEvent(
                ts_ns=1000000 + i,
                syscall="clone",
                category="process_clone",
                pid=1234,
                comm="parallel",
                ret=1235 + i,
            )
            summarizer.add_event(event)

        summary = summarizer.get_summary()
        assert summary.processes_spawned == 3

    def test_deduplication(self):
        """Test that duplicate paths are deduplicated."""
        summarizer = create_summarizer("grep pattern /etc/passwd", pid=1234)

        # Add same file read multiple times
        for i in range(5):
            event = SyscallEvent(
                ts_ns=1000000 + i,
                syscall="openat",
                category="file_read",
                pid=1234,
                comm="grep",
                path="/etc/passwd",
                ret=3,
            )
            summarizer.add_event(event)

        summary = summarizer.get_summary()
        assert len(summary.files_read) == 1  # Deduplicated
        assert summary.total_syscalls == 5  # But count is correct

    def test_duration_calculation(self):
        """Test duration calculation from timestamps."""
        summarizer = create_summarizer("sleep 1", pid=1234)

        # First event at t=0
        event1 = SyscallEvent(
            ts_ns=1_000_000_000,  # 1 second in ns
            syscall="clone",
            category="process_clone",
            pid=1234,
            comm="sleep",
        )
        summarizer.add_event(event1)

        # Last event at t=1100ms
        event2 = SyscallEvent(
            ts_ns=2_100_000_000,  # 2.1 seconds in ns
            syscall="clone",
            category="process_clone",
            pid=1234,
            comm="sleep",
        )
        summarizer.add_event(event2)

        summary = summarizer.get_summary()
        assert summary.duration_ms == 1100  # 1100ms between first and last event

    def test_truncation_at_max_events(self):
        """Test that events are truncated at max limit."""
        summarizer = SyscallSummarizer(
            command="stress test",
            pid=1234,
            max_events=100,
        )

        # Add more than max events
        for i in range(150):
            event = SyscallEvent(
                ts_ns=1000000 + i,
                syscall="openat",
                category="file_read",
                pid=1234,
                comm="stress",
                path=f"/tmp/file{i}.txt",
            )
            added = summarizer.add_event(event)
            if i >= 100:
                assert added is False

        summary = summarizer.get_summary()
        assert summary.total_syscalls == 100
        assert summary.events_truncated is True

    def test_excluded_paths_not_in_summary(self):
        """Test that excluded paths don't appear in summary."""
        summarizer = create_summarizer("cat /proc/self/status", pid=1234)

        event = SyscallEvent(
            ts_ns=1000000,
            syscall="openat",
            category="file_read",
            pid=1234,
            comm="cat",
            path="/proc/self/status",
            ret=3,
        )
        summarizer.add_event(event)

        summary = summarizer.get_summary()
        assert len(summary.files_read) == 0  # /proc is excluded

    def test_reset(self):
        """Test resetting the summarizer."""
        summarizer = create_summarizer("test", pid=1234)

        event = SyscallEvent(
            ts_ns=1000000,
            syscall="openat",
            category="file_read",
            pid=1234,
            comm="test",
            path="/tmp/test.txt",
        )
        summarizer.add_event(event)

        # Verify event was added
        summary1 = summarizer.get_summary()
        assert summary1.total_syscalls == 1

        # Reset and verify clean state
        summarizer.reset()
        summary2 = summarizer.get_summary()
        assert summary2.total_syscalls == 0
        assert summary2.files_read == ()


class TestSummarizeEventsFunction:
    """Tests for the summarize_events convenience function."""

    def test_summarize_empty_list(self):
        """Test summarizing empty event list."""
        summary = summarize_events(
            events=[],
            command="test",
            pid=1234,
        )

        assert summary.total_syscalls == 0
        assert summary.command == "test"

    def test_summarize_event_list(self):
        """Test summarizing a list of events."""
        events = [
            SyscallEvent(
                ts_ns=1000000,
                syscall="openat",
                category="file_read",
                pid=1234,
                comm="cat",
                path="/etc/passwd",
            ),
            SyscallEvent(
                ts_ns=1000001,
                syscall="openat",
                category="file_read",
                pid=1234,
                comm="cat",
                path="/etc/group",
            ),
        ]

        summary = summarize_events(
            events=events,
            command="cat /etc/passwd /etc/group",
            pid=1234,
        )

        assert summary.total_syscalls == 2
        assert "/etc/passwd" in summary.files_read
        assert "/etc/group" in summary.files_read
