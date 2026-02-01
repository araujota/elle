"""Coverage tests for elle.daemon.telemetry.ebpf.capabilities and syscall_explainer.

capabilities.py: targets 32 missing lines (70.2% -> 90%+).
syscall_explainer.py: targets 32 missing lines (77.6% -> 90%+).
"""

from __future__ import annotations

from unittest.mock import MagicMock, mock_open, patch

# ---------------------------------------------------------------------------
# syscall_explainer coverage
# ---------------------------------------------------------------------------
from elle.daemon.telemetry.ebpf.syscall_explainer import (
    MAX_DISPLAY_ITEMS,
    _format_connections,
    _format_path_list,
    _format_rename_list,
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


class TestFormatPathList:
    def test_empty(self):
        assert _format_path_list(()) == []

    def test_few_paths(self):
        paths = ("/etc/hosts", "/etc/passwd")
        result = _format_path_list(paths)
        assert len(result) == 2
        assert "  /etc/hosts" in result

    def test_truncation(self):
        paths = tuple(f"/file_{i}" for i in range(MAX_DISPLAY_ITEMS + 5))
        result = _format_path_list(paths)
        assert len(result) == MAX_DISPLAY_ITEMS + 1
        assert "... and 5 more" in result[-1]

    def test_custom_max(self):
        paths = ("/a", "/b", "/c", "/d")
        result = _format_path_list(paths, max_items=2)
        assert len(result) == 3  # 2 items + 1 truncation
        assert "... and 2 more" in result[-1]


class TestFormatConnections:
    def test_empty(self):
        assert _format_connections(()) == []

    def test_single(self):
        nc = NetworkConnection(addr="1.2.3.4", port=443)
        result = _format_connections((nc,))
        assert len(result) == 1
        assert "1.2.3.4:443" in result[0]
        assert "ipv4" in result[0]

    def test_truncation(self):
        conns = tuple(
            NetworkConnection(addr=f"10.0.0.{i}", port=8000 + i)
            for i in range(MAX_DISPLAY_ITEMS + 3)
        )
        result = _format_connections(conns)
        assert len(result) == MAX_DISPLAY_ITEMS + 1
        assert "... and 3 more" in result[-1]


class TestFormatRenameList:
    def test_empty(self):
        assert _format_rename_list(()) == []

    def test_single_rename(self):
        renames = (("/old", "/new"),)
        result = _format_rename_list(renames)
        assert len(result) == 1
        assert "/old -> /new" in result[0]

    def test_truncation(self):
        renames = tuple(
            (f"/old_{i}", f"/new_{i}") for i in range(MAX_DISPLAY_ITEMS + 2)
        )
        result = _format_rename_list(renames)
        assert len(result) == MAX_DISPLAY_ITEMS + 1
        assert "... and 2 more" in result[-1]


class TestExplainSummary:
    def _make_summary(self, **kwargs):
        defaults = {"command": "test", "pid": 1, "duration_ms": 10, "total_syscalls": 5}
        defaults.update(kwargs)
        return SyscallSummary(**defaults)

    def test_no_notable_operations(self):
        s = self._make_summary()
        text = explain_summary(s)
        assert "No notable file, network, or process operations detected" in text
        assert "internal computations" in text

    def test_file_operations(self):
        s = self._make_summary(
            files_read=("/etc/hosts",),
            files_written=("/tmp/out",),
            files_created=("/tmp/new",),
            files_deleted=("/tmp/old",),
            files_renamed=(("/a", "/b"),),
        )
        text = explain_summary(s)
        assert "File Operations:" in text
        assert "Read 1 file" in text
        assert "Wrote to 1 file" in text
        assert "Created 1 file" in text
        assert "Deleted 1 file" in text
        assert "Renamed 1 file" in text

    def test_network_operations(self):
        nc = NetworkConnection(addr="1.2.3.4", port=443)
        s = self._make_summary(connections_made=(nc,))
        text = explain_summary(s)
        assert "Network Operations:" in text
        assert "Made 1 connection" in text

    def test_process_operations(self):
        s = self._make_summary(
            programs_executed=("/usr/bin/ls",),
            processes_spawned=3,
        )
        text = explain_summary(s)
        assert "Process Operations:" in text
        assert "Executed 1 program" in text
        assert "Spawned 3 child process" in text

    def test_truncated_events(self):
        s = self._make_summary(events_truncated=True)
        text = explain_summary(s)
        assert "truncated" in text.lower()

    def test_header_includes_command(self):
        s = self._make_summary(command="apt install nginx")
        text = explain_summary(s)
        assert "Command: apt install nginx" in text
        assert "Duration: 10ms" in text


class TestExplainTrace:
    def _make_summary(self, **kwargs):
        defaults = {"command": "test", "pid": 1, "duration_ms": 10, "total_syscalls": 5}
        defaults.update(kwargs)
        return SyscallSummary(**defaults)

    def test_not_enabled(self):
        t = SyscallTrace(enabled=False)
        text = explain_trace(t)
        assert "not enabled" in text.lower()

    def test_error(self):
        t = SyscallTrace(enabled=True, error="BPF load failed")
        text = explain_trace(t)
        assert "BPF load failed" in text

    def test_no_summary(self):
        t = SyscallTrace(enabled=True)
        text = explain_trace(t)
        assert "No syscall data" in text

    def test_has_explanation(self):
        s = self._make_summary()
        t = SyscallTrace(
            enabled=True,
            summary=s,
            explanation="Custom explanation",
        )
        text = explain_trace(t)
        assert text == "Custom explanation"

    def test_generates_explanation(self):
        s = self._make_summary(files_read=("/etc/hosts",))
        t = SyscallTrace(enabled=True, summary=s)
        text = explain_trace(t)
        assert "File Operations:" in text


class TestCreateBriefSummary:
    def _make_summary(self, **kwargs):
        defaults = {"command": "test", "pid": 1, "duration_ms": 10, "total_syscalls": 5}
        defaults.update(kwargs)
        return SyscallSummary(**defaults)

    def test_no_operations(self):
        s = self._make_summary()
        text = create_brief_summary(s)
        assert "5 syscalls (no notable I/O)" in text

    def test_single_file_created(self):
        s = self._make_summary(files_created=("/tmp/new",))
        text = create_brief_summary(s)
        assert "created 1 file" in text

    def test_multiple_files_written(self):
        s = self._make_summary(files_written=("/a", "/b", "/c"))
        text = create_brief_summary(s)
        assert "wrote 3 files" in text

    def test_files_deleted(self):
        s = self._make_summary(files_deleted=("/old",))
        text = create_brief_summary(s)
        assert "deleted 1 file" in text

    def test_files_renamed(self):
        s = self._make_summary(files_renamed=(("/a", "/b"),))
        text = create_brief_summary(s)
        assert "renamed 1 file" in text

    def test_files_read(self):
        s = self._make_summary(files_read=("/etc/hosts", "/etc/passwd"))
        text = create_brief_summary(s)
        assert "read 2 files" in text

    def test_connections(self):
        nc = NetworkConnection(addr="1.2.3.4", port=80)
        s = self._make_summary(connections_made=(nc,))
        text = create_brief_summary(s)
        assert "made 1 connection" in text

    def test_programs(self):
        s = self._make_summary(programs_executed=("/bin/ls",))
        text = create_brief_summary(s)
        assert "executed 1 program" in text

    def test_processes_spawned(self):
        s = self._make_summary(processes_spawned=2)
        text = create_brief_summary(s)
        assert "spawned 2 processes" in text

    def test_combined(self):
        s = self._make_summary(
            files_created=("/a",),
            files_read=("/b",),
            processes_spawned=1,
        )
        text = create_brief_summary(s)
        assert "created 1 file" in text
        assert "read 1 file" in text
        assert "spawned 1 process" in text


class TestCreateTraceWithExplanation:
    def test_creates_trace(self):
        s = SyscallSummary(
            command="test",
            pid=1,
            duration_ms=10,
            total_syscalls=5,
            files_read=("/etc/hosts",),
        )
        trace = create_trace_with_explanation(s)
        assert trace.enabled is True
        assert trace.summary is s
        assert trace.error is None
        assert trace.explanation is not None
        assert "File Operations:" in trace.explanation


class TestFormatTraceForDisplay:
    def _make_summary(self, **kwargs):
        defaults = {"command": "test", "pid": 1, "duration_ms": 10, "total_syscalls": 5}
        defaults.update(kwargs)
        return SyscallSummary(**defaults)

    def test_not_enabled_no_colors(self):
        t = SyscallTrace(enabled=False)
        text = format_trace_for_display(t, use_colors=False)
        assert "not enabled" in text.lower()

    def test_error_no_colors(self):
        t = SyscallTrace(enabled=True, error="fail")
        text = format_trace_for_display(t, use_colors=False)
        assert "Tracing error: fail" in text

    def test_no_summary_no_colors(self):
        t = SyscallTrace(enabled=True, summary=None)
        text = format_trace_for_display(t, use_colors=False)
        assert "No trace data" in text

    def test_with_summary_no_colors(self):
        s = self._make_summary(files_read=("/etc/hosts",))
        t = SyscallTrace(enabled=True, summary=s)
        text = format_trace_for_display(t, use_colors=False)
        assert "Syscall Trace: test" in text
        assert "Duration: 10ms" in text

    def test_with_summary_truncated_no_colors(self):
        s = self._make_summary(events_truncated=True)
        t = SyscallTrace(enabled=True, summary=s)
        text = format_trace_for_display(t, use_colors=False)
        assert "Truncated" in text

    def test_with_explanation(self):
        s = self._make_summary()
        t = SyscallTrace(enabled=True, summary=s, explanation="Custom")
        text = format_trace_for_display(t, use_colors=False)
        assert "Custom" in text

    def test_without_explanation_generates_one(self):
        s = self._make_summary()
        t = SyscallTrace(enabled=True, summary=s)
        text = format_trace_for_display(t, use_colors=False)
        assert "No notable file, network, or process operations" in text

    def test_colors_import_failure(self):
        """When Colors import fails, falls back to no-color."""
        s = self._make_summary()
        t = SyscallTrace(enabled=True, summary=s)
        with patch.dict("sys.modules", {"elle.cli.terminal.renderer": None}):
            text = format_trace_for_display(t, use_colors=True)
        # Should still produce output without crashing
        assert "Syscall Trace: test" in text

    def test_not_enabled_with_colors_import_fail(self):
        t = SyscallTrace(enabled=False)
        with patch.dict("sys.modules", {"elle.cli.terminal.renderer": None}):
            text = format_trace_for_display(t, use_colors=True)
        assert "not enabled" in text.lower()

    def test_error_with_colors_import_fail(self):
        t = SyscallTrace(enabled=True, error="boom")
        with patch.dict("sys.modules", {"elle.cli.terminal.renderer": None}):
            text = format_trace_for_display(t, use_colors=True)
        assert "boom" in text

    def test_not_enabled_with_real_colors(self):
        """Test not-enabled path with successful Colors import."""
        mock_colors = MagicMock()
        mock_colors.DIM = "\033[2m"
        mock_colors.RESET = "\033[0m"
        mock_renderer = MagicMock()
        mock_renderer.Colors = mock_colors
        t = SyscallTrace(enabled=False)
        with patch.dict("sys.modules", {"elle.cli.terminal.renderer": mock_renderer}):
            text = format_trace_for_display(t, use_colors=True)
        assert "not enabled" in text.lower()

    def test_error_with_real_colors(self):
        """Test error path with successful Colors import."""
        mock_colors = MagicMock()
        mock_colors.RED = "\033[31m"
        mock_colors.RESET = "\033[0m"
        mock_renderer = MagicMock()
        mock_renderer.Colors = mock_colors
        t = SyscallTrace(enabled=True, error="BPF failed")
        with patch.dict("sys.modules", {"elle.cli.terminal.renderer": mock_renderer}):
            text = format_trace_for_display(t, use_colors=True)
        assert "BPF failed" in text

    def test_no_summary_with_real_colors(self):
        """Test no-summary path with successful Colors import."""
        mock_colors = MagicMock()
        mock_colors.DIM = "\033[2m"
        mock_colors.RESET = "\033[0m"
        mock_renderer = MagicMock()
        mock_renderer.Colors = mock_colors
        t = SyscallTrace(enabled=True, summary=None)
        with patch.dict("sys.modules", {"elle.cli.terminal.renderer": mock_renderer}):
            text = format_trace_for_display(t, use_colors=True)
        assert "No trace data" in text

    def test_summary_with_real_colors(self):
        """Test summary header path with successful Colors import."""
        mock_colors = MagicMock()
        mock_colors.BOLD = "\033[1m"
        mock_colors.RESET = "\033[0m"
        mock_renderer = MagicMock()
        mock_renderer.Colors = mock_colors
        s = self._make_summary()
        t = SyscallTrace(enabled=True, summary=s)
        with patch.dict("sys.modules", {"elle.cli.terminal.renderer": mock_renderer}):
            text = format_trace_for_display(t, use_colors=True)
        assert "test" in text

    def test_truncated_with_real_colors(self):
        """Test truncation message path with successful Colors import."""
        mock_colors = MagicMock()
        mock_colors.BOLD = "\033[1m"
        mock_colors.YELLOW = "\033[33m"
        mock_colors.RESET = "\033[0m"
        mock_renderer = MagicMock()
        mock_renderer.Colors = mock_colors
        s = self._make_summary(events_truncated=True)
        t = SyscallTrace(enabled=True, summary=s)
        with patch.dict("sys.modules", {"elle.cli.terminal.renderer": mock_renderer}):
            text = format_trace_for_display(t, use_colors=True)
        assert "Truncated" in text


# ---------------------------------------------------------------------------
# ebpf/capabilities.py coverage
# ---------------------------------------------------------------------------

from elle.daemon.telemetry.ebpf.capabilities import (
    CAP_BPF,
    CAP_NET_ADMIN,
    CAP_PERFMON,
    CAP_SYS_ADMIN,
    CapabilityCheck,
    _bcc_available,
    _bpf_fs_available,
    _check_capability,
    _check_effective_capability,
    _get_kernel_version,
    _is_root,
    can_use_ebpf,
    check_capabilities,
    get_kernel_version_string,
    get_missing_capabilities,
)


class TestCapabilityCheckDataclass:
    def test_creation(self):
        cc = CapabilityCheck(
            available=True,
            kernel_version=(6, 8, 0),
            has_cap_bpf=True,
            has_cap_perfmon=True,
            has_cap_net_admin=True,
            has_cap_sys_admin=False,
            bpf_fs_available=True,
            reason="Running as root",
        )
        assert cc.available is True
        assert cc.kernel_version == (6, 8, 0)
        assert cc.reason == "Running as root"


class TestGetKernelVersion:
    def test_from_proc_version(self):
        content = "Linux version 6.8.0-40-generic (gcc ...)"
        with patch("builtins.open", mock_open(read_data=content)):
            ver = _get_kernel_version()
        assert ver == (6, 8, 0)

    def test_proc_version_failure_fallback_to_uname(self):
        fake_uname = MagicMock()
        fake_uname.release = "5.15.0-91-generic"
        with patch("builtins.open", side_effect=OSError("no procfs")):
            with patch("os.uname", return_value=fake_uname):
                ver = _get_kernel_version()
        assert ver == (5, 15, 0)

    def test_uname_minimal_version(self):
        fake_uname = MagicMock()
        fake_uname.release = "6.1"
        with patch("builtins.open", side_effect=OSError):
            with patch("os.uname", return_value=fake_uname):
                ver = _get_kernel_version()
        assert ver == (6, 1, 0)

    def test_total_failure_returns_zero(self):
        with patch("builtins.open", side_effect=OSError):
            with patch("os.uname", side_effect=OSError):
                ver = _get_kernel_version()
        assert ver == (0, 0, 0)

    def test_proc_version_no_match(self):
        content = "not a kernel version string"
        fake_uname = MagicMock()
        fake_uname.release = "6.5.0-rc1"
        with patch("builtins.open", mock_open(read_data=content)):
            with patch("os.uname", return_value=fake_uname):
                ver = _get_kernel_version()
        # Falls through to uname
        assert ver == (6, 5, 0)


class TestCheckCapability:
    def test_success(self):
        mock_libc = MagicMock()
        mock_libc.prctl.return_value = 1
        with patch("ctypes.CDLL", return_value=mock_libc):
            result = _check_capability(CAP_BPF)
        assert result is True

    def test_failure(self):
        mock_libc = MagicMock()
        mock_libc.prctl.return_value = 0
        with patch("ctypes.CDLL", return_value=mock_libc):
            result = _check_capability(CAP_BPF)
        assert result is False

    def test_exception(self):
        with patch("ctypes.CDLL", side_effect=OSError("no libc")):
            result = _check_capability(CAP_BPF)
        assert result is False


class TestCheckEffectiveCapability:
    def test_has_capability(self):
        # CAP_BPF = 39, so bit 39 set: mask = 1 << 39
        mask = 1 << CAP_BPF
        content = f"Name:\ttest\nCapEff:\t{mask:016x}\n"
        with patch("builtins.open", mock_open(read_data=content)):
            result = _check_effective_capability(CAP_BPF)
        assert result is True

    def test_missing_capability(self):
        content = "Name:\ttest\nCapEff:\t0000000000000000\n"
        with patch("builtins.open", mock_open(read_data=content)):
            result = _check_effective_capability(CAP_BPF)
        assert result is False

    def test_file_read_error(self):
        with patch("builtins.open", side_effect=OSError("no procfs")):
            result = _check_effective_capability(CAP_BPF)
        assert result is False


class TestIsRoot:
    def test_root(self):
        with patch("os.geteuid", return_value=0):
            assert _is_root() is True

    def test_non_root(self):
        with patch("os.geteuid", return_value=1000):
            assert _is_root() is False


class TestBpfFsAvailable:
    def test_not_exists(self):
        with patch("pathlib.Path.exists", return_value=False):
            assert _bpf_fs_available() is False

    def test_exists_bpf_mount(self):
        mounts = "none /sys/fs/bpf bpf rw 0 0\n"
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=mounts)):
                result = _bpf_fs_available()
        assert result is True

    def test_exists_wrong_fs_type(self):
        mounts = "none /sys/fs/bpf tmpfs rw 0 0\n"
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=mounts)):
                result = _bpf_fs_available()
        assert result is False

    def test_exists_no_mount_entry_fallback_is_dir(self):
        mounts = "none /sys/fs/cgroup cgroup2 rw 0 0\n"
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=mounts)):
                with patch("pathlib.Path.is_dir", return_value=True):
                    result = _bpf_fs_available()
        assert result is True

    def test_mount_read_error(self):
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", side_effect=OSError):
                with patch("pathlib.Path.is_dir", return_value=False):
                    result = _bpf_fs_available()
        assert result is False


class TestBccAvailable:
    def test_available(self):
        fake_bcc = MagicMock()
        with patch.dict("sys.modules", {"bcc": fake_bcc}):
            assert _bcc_available() is True

    def test_not_available(self):
        with patch.dict("sys.modules", {"bcc": None}):
            assert _bcc_available() is False


class TestCheckCapabilities:
    def _mock_all(self, kernel=(6, 8, 0), root=False, cap_bpf=False,
                  cap_perfmon=False, cap_net_admin=False, cap_sys_admin=False,
                  bpf_fs=True, bcc=True):
        """Return patches that set up a controlled capability environment."""
        return [
            patch(
                "elle.daemon.telemetry.ebpf.capabilities._get_kernel_version",
                return_value=kernel,
            ),
            patch(
                "elle.daemon.telemetry.ebpf.capabilities._is_root",
                return_value=root,
            ),
            patch(
                "elle.daemon.telemetry.ebpf.capabilities._check_effective_capability",
                side_effect=lambda cap: {
                    CAP_BPF: cap_bpf,
                    CAP_PERFMON: cap_perfmon,
                    CAP_NET_ADMIN: cap_net_admin,
                    CAP_SYS_ADMIN: cap_sys_admin,
                }.get(cap, False),
            ),
            patch(
                "elle.daemon.telemetry.ebpf.capabilities._bpf_fs_available",
                return_value=bpf_fs,
            ),
            patch(
                "elle.daemon.telemetry.ebpf.capabilities._bcc_available",
                return_value=bcc,
            ),
        ]

    def test_old_kernel(self):
        patches = self._mock_all(kernel=(4, 19, 0))
        for p in patches:
            p.start()
        try:
            cc = check_capabilities()
            assert cc.available is False
            assert "too old" in cc.reason
        finally:
            for p in patches:
                p.stop()

    def test_no_bpf_fs(self):
        patches = self._mock_all(bpf_fs=False)
        for p in patches:
            p.start()
        try:
            cc = check_capabilities()
            assert cc.available is False
            assert "BPF filesystem" in cc.reason
        finally:
            for p in patches:
                p.stop()

    def test_no_bcc(self):
        patches = self._mock_all(bcc=False)
        for p in patches:
            p.start()
        try:
            cc = check_capabilities()
            assert cc.available is False
            assert "BCC" in cc.reason
        finally:
            for p in patches:
                p.stop()

    def test_root(self):
        patches = self._mock_all(root=True)
        for p in patches:
            p.start()
        try:
            cc = check_capabilities()
            assert cc.available is True
            assert "root" in cc.reason.lower()
        finally:
            for p in patches:
                p.stop()

    def test_cap_bpf_and_perfmon(self):
        patches = self._mock_all(cap_bpf=True, cap_perfmon=True)
        for p in patches:
            p.start()
        try:
            cc = check_capabilities()
            assert cc.available is True
            assert "CAP_BPF" in cc.reason
        finally:
            for p in patches:
                p.stop()

    def test_cap_sys_admin_legacy(self):
        patches = self._mock_all(cap_sys_admin=True)
        for p in patches:
            p.start()
        try:
            cc = check_capabilities()
            assert cc.available is True
            assert "legacy" in cc.reason.lower()
        finally:
            for p in patches:
                p.stop()

    def test_missing_capabilities(self):
        patches = self._mock_all()
        for p in patches:
            p.start()
        try:
            cc = check_capabilities()
            assert cc.available is False
            assert "Missing" in cc.reason
            assert "CAP_BPF" in cc.reason
            assert "CAP_PERFMON" in cc.reason
        finally:
            for p in patches:
                p.stop()


class TestCanUseEbpf:
    def test_returns_tuple(self):
        with patch(
            "elle.daemon.telemetry.ebpf.capabilities.check_capabilities",
            return_value=CapabilityCheck(
                available=True,
                kernel_version=(6, 8, 0),
                has_cap_bpf=True,
                has_cap_perfmon=True,
                has_cap_net_admin=False,
                has_cap_sys_admin=False,
                bpf_fs_available=True,
                reason="Has CAP_BPF and CAP_PERFMON",
            ),
        ):
            avail, reason = can_use_ebpf()
        assert avail is True
        assert "CAP_BPF" in reason


class TestGetMissingCapabilities:
    def test_both_missing(self):
        with patch(
            "elle.daemon.telemetry.ebpf.capabilities.check_capabilities",
            return_value=CapabilityCheck(
                available=False,
                kernel_version=(6, 8, 0),
                has_cap_bpf=False,
                has_cap_perfmon=False,
                has_cap_net_admin=False,
                has_cap_sys_admin=False,
                bpf_fs_available=True,
                reason="Missing",
            ),
        ):
            missing = get_missing_capabilities()
        assert "CAP_BPF" in missing
        assert "CAP_PERFMON" in missing

    def test_none_missing(self):
        with patch(
            "elle.daemon.telemetry.ebpf.capabilities.check_capabilities",
            return_value=CapabilityCheck(
                available=True,
                kernel_version=(6, 8, 0),
                has_cap_bpf=True,
                has_cap_perfmon=True,
                has_cap_net_admin=True,
                has_cap_sys_admin=True,
                bpf_fs_available=True,
                reason="root",
            ),
        ):
            missing = get_missing_capabilities()
        assert missing == []


class TestGetKernelVersionString:
    def test_format(self):
        with patch(
            "elle.daemon.telemetry.ebpf.capabilities._get_kernel_version",
            return_value=(6, 8, 0),
        ):
            assert get_kernel_version_string() == "6.8.0"

    def test_format_with_patch(self):
        with patch(
            "elle.daemon.telemetry.ebpf.capabilities._get_kernel_version",
            return_value=(5, 15, 91),
        ):
            assert get_kernel_version_string() == "5.15.91"
