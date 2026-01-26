"""Tests for syscall explainer."""

from elle.daemon.telemetry.ebpf.syscall_explainer import (
    create_brief_summary,
    create_trace_with_explanation,
    explain_summary,
    explain_trace,
    format_trace_for_display,
)
from elle.daemon.telemetry.ebpf.syscall_models import (
    NetworkConnection,
    SyscallSummary,
    SyscallTrace,
)


class TestExplainSummary:
    """Tests for explain_summary function."""

    def test_empty_summary(self):
        """Test explaining an empty summary."""
        summary = SyscallSummary(
            command="echo hello",
            pid=1234,
            duration_ms=5,
            total_syscalls=2,
        )

        explanation = explain_summary(summary)

        assert "Command: echo hello" in explanation
        assert "Duration: 5ms" in explanation
        assert "2 syscalls" in explanation
        assert "No notable file, network, or process operations" in explanation

    def test_file_operations(self):
        """Test explaining file operations."""
        summary = SyscallSummary(
            command="touch /tmp/test.txt",
            pid=1234,
            duration_ms=10,
            files_created=("/tmp/test.txt",),
            total_syscalls=5,
        )

        explanation = explain_summary(summary)

        assert "File Operations:" in explanation
        assert "Created 1 file(s):" in explanation
        assert "/tmp/test.txt" in explanation

    def test_multiple_file_operations(self):
        """Test explaining multiple file operation types."""
        summary = SyscallSummary(
            command="complex file ops",
            pid=1234,
            duration_ms=100,
            files_read=("/etc/passwd", "/etc/group"),
            files_written=("/tmp/output.txt",),
            files_deleted=("/tmp/old.txt",),
            total_syscalls=20,
        )

        explanation = explain_summary(summary)

        assert "File Operations:" in explanation
        assert "Read 2 file(s):" in explanation
        assert "Wrote to 1 file(s):" in explanation
        assert "Deleted 1 file(s):" in explanation
        assert "/etc/passwd" in explanation
        assert "/tmp/output.txt" in explanation
        assert "/tmp/old.txt" in explanation

    def test_network_operations(self):
        """Test explaining network operations."""
        conn1 = NetworkConnection(addr="93.184.216.34", port=443, family="ipv4")
        conn2 = NetworkConnection(addr="8.8.8.8", port=53, family="ipv4")

        summary = SyscallSummary(
            command="curl https://example.com",
            pid=1234,
            duration_ms=500,
            connections_made=(conn1, conn2),
            total_syscalls=50,
        )

        explanation = explain_summary(summary)

        assert "Network Operations:" in explanation
        assert "Made 2 connection(s):" in explanation
        assert "93.184.216.34:443" in explanation
        assert "8.8.8.8:53" in explanation

    def test_process_operations(self):
        """Test explaining process operations."""
        summary = SyscallSummary(
            command="bash -c 'ls && pwd'",
            pid=1234,
            duration_ms=50,
            programs_executed=("/bin/ls", "/bin/pwd"),
            processes_spawned=2,
            total_syscalls=30,
        )

        explanation = explain_summary(summary)

        assert "Process Operations:" in explanation
        assert "Executed 2 program(s):" in explanation
        assert "/bin/ls" in explanation
        assert "/bin/pwd" in explanation
        assert "Spawned 2 child process(es)" in explanation

    def test_mixed_operations(self):
        """Test explaining mixed operations."""
        conn = NetworkConnection(addr="api.example.com", port=443, family="ipv4")

        summary = SyscallSummary(
            command="curl https://api.example.com > /tmp/response.json",
            pid=1234,
            duration_ms=1000,
            files_created=("/tmp/response.json",),
            connections_made=(conn,),
            total_syscalls=100,
        )

        explanation = explain_summary(summary)

        assert "File Operations:" in explanation
        assert "Network Operations:" in explanation
        assert "/tmp/response.json" in explanation
        assert "api.example.com:443" in explanation

    def test_truncation_note(self):
        """Test that truncation is noted in explanation."""
        summary = SyscallSummary(
            command="find / -name '*.txt'",
            pid=1234,
            duration_ms=5000,
            total_syscalls=10000,
            events_truncated=True,
        )

        explanation = explain_summary(summary)

        assert "truncated" in explanation.lower()

    def test_long_file_list_truncated(self):
        """Test that long file lists are truncated in display."""
        files = tuple(f"/path/to/file{i}.txt" for i in range(50))

        summary = SyscallSummary(
            command="find /path",
            pid=1234,
            duration_ms=1000,
            files_read=files,
            total_syscalls=100,
        )

        explanation = explain_summary(summary)

        # Should show first 10 and indicate more
        assert "/path/to/file0.txt" in explanation
        assert "/path/to/file9.txt" in explanation
        assert "... and" in explanation
        assert "more" in explanation


class TestExplainTrace:
    """Tests for explain_trace function."""

    def test_disabled_trace(self):
        """Test explaining a disabled trace."""
        trace = SyscallTrace(enabled=False)
        explanation = explain_trace(trace)

        assert "not enabled" in explanation.lower()

    def test_trace_with_error(self):
        """Test explaining a trace that failed."""
        trace = SyscallTrace(
            enabled=True,
            error="Failed to attach eBPF program",
        )

        explanation = explain_trace(trace)

        assert "failed" in explanation.lower()
        assert "Failed to attach eBPF program" in explanation

    def test_trace_with_no_summary(self):
        """Test explaining a trace with no summary."""
        trace = SyscallTrace(enabled=True, summary=None)
        explanation = explain_trace(trace)

        assert "No syscall data" in explanation

    def test_trace_uses_existing_explanation(self):
        """Test that trace uses existing explanation if present."""
        summary = SyscallSummary(
            command="test",
            pid=1234,
            duration_ms=10,
            total_syscalls=5,
        )

        trace = SyscallTrace(
            enabled=True,
            summary=summary,
            explanation="Custom explanation text",
        )

        explanation = explain_trace(trace)

        assert explanation == "Custom explanation text"


class TestCreateBriefSummary:
    """Tests for create_brief_summary function."""

    def test_empty_summary(self):
        """Test brief summary with no operations."""
        summary = SyscallSummary(
            command="sleep 1",
            pid=1234,
            duration_ms=1000,
            total_syscalls=5,
        )

        brief = create_brief_summary(summary)

        assert "5 syscalls" in brief
        assert "no notable I/O" in brief

    def test_single_file_created(self):
        """Test brief summary with one file created."""
        summary = SyscallSummary(
            command="touch /tmp/test.txt",
            pid=1234,
            duration_ms=10,
            files_created=("/tmp/test.txt",),
            total_syscalls=5,
        )

        brief = create_brief_summary(summary)

        assert brief == "created 1 file"

    def test_multiple_files(self):
        """Test brief summary with multiple files."""
        summary = SyscallSummary(
            command="complex",
            pid=1234,
            duration_ms=100,
            files_created=("/tmp/a.txt", "/tmp/b.txt"),
            files_read=("/etc/passwd",),
            total_syscalls=10,
        )

        brief = create_brief_summary(summary)

        assert "created 2 files" in brief
        assert "read 1 file" in brief

    def test_connections(self):
        """Test brief summary with network connections."""
        conn = NetworkConnection(addr="example.com", port=443)

        summary = SyscallSummary(
            command="curl",
            pid=1234,
            duration_ms=500,
            connections_made=(conn,),
            total_syscalls=50,
        )

        brief = create_brief_summary(summary)

        assert "made 1 connection" in brief

    def test_multiple_connections(self):
        """Test brief summary with multiple connections."""
        conns = tuple(NetworkConnection(addr=f"server{i}.example.com", port=443) for i in range(3))

        summary = SyscallSummary(
            command="parallel curl",
            pid=1234,
            duration_ms=1000,
            connections_made=conns,
            total_syscalls=150,
        )

        brief = create_brief_summary(summary)

        assert "made 3 connections" in brief

    def test_process_spawning(self):
        """Test brief summary with process spawning."""
        summary = SyscallSummary(
            command="make -j4",
            pid=1234,
            duration_ms=5000,
            processes_spawned=4,
            programs_executed=("/usr/bin/gcc", "/usr/bin/ld"),
            total_syscalls=500,
        )

        brief = create_brief_summary(summary)

        assert "executed 2 programs" in brief
        assert "spawned 4 processes" in brief


class TestCreateTraceWithExplanation:
    """Tests for create_trace_with_explanation function."""

    def test_creates_trace_with_explanation(self):
        """Test that function creates trace with explanation."""
        summary = SyscallSummary(
            command="ls -la",
            pid=1234,
            duration_ms=20,
            files_read=("/home/user",),
            total_syscalls=10,
        )

        trace = create_trace_with_explanation(summary)

        assert trace.enabled is True
        assert trace.summary == summary
        assert trace.explanation is not None
        assert "Command: ls -la" in trace.explanation
        assert trace.error is None


class TestFormatTraceForDisplay:
    """Tests for format_trace_for_display function."""

    def test_format_disabled_trace(self):
        """Test formatting a disabled trace."""
        trace = SyscallTrace(enabled=False)
        output = format_trace_for_display(trace, use_colors=False)

        assert "not enabled" in output.lower()

    def test_format_error_trace(self):
        """Test formatting a trace with error."""
        trace = SyscallTrace(
            enabled=True,
            error="Permission denied",
        )

        output = format_trace_for_display(trace, use_colors=False)

        assert "Permission denied" in output

    def test_format_valid_trace_no_colors(self):
        """Test formatting a valid trace without colors."""
        summary = SyscallSummary(
            command="echo hello",
            pid=1234,
            duration_ms=5,
            total_syscalls=2,
        )

        trace = SyscallTrace(
            enabled=True,
            summary=summary,
        )

        output = format_trace_for_display(trace, use_colors=False)

        assert "Syscall Trace: echo hello" in output
        assert "Duration: 5ms" in output
        assert "Syscalls: 2" in output

    def test_format_truncated_trace(self):
        """Test formatting a truncated trace."""
        summary = SyscallSummary(
            command="find /",
            pid=1234,
            duration_ms=5000,
            total_syscalls=10000,
            events_truncated=True,
        )

        trace = SyscallTrace(
            enabled=True,
            summary=summary,
        )

        output = format_trace_for_display(trace, use_colors=False)

        assert "Truncated" in output
