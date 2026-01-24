"""Tests for eBPF capability checking."""

import os
from unittest.mock import MagicMock, mock_open, patch

import pytest

from elle.daemon.telemetry.ebpf.capabilities import (
    CAP_BPF,
    CAP_NET_ADMIN,
    CAP_PERFMON,
    CAP_SYS_ADMIN,
    MIN_KERNEL_VERSION,
    CapabilityCheck,
    _bpf_fs_available,
    _check_effective_capability,
    _get_kernel_version,
    _is_root,
    can_use_ebpf,
    check_capabilities,
    get_kernel_version_string,
    get_missing_capabilities,
)


class TestKernelVersion:
    """Tests for kernel version detection."""

    def test_parse_kernel_version_from_proc(self):
        """Test parsing kernel version from /proc/version."""
        mock_content = "Linux version 6.8.0-40-generic (buildd@lcy02) (gcc 13.2.0) #40-Ubuntu SMP"
        with patch("builtins.open", mock_open(read_data=mock_content)):
            version = _get_kernel_version()
            assert version == (6, 8, 0)

    def test_parse_kernel_version_fallback_to_uname(self):
        """Test fallback to uname when /proc/version fails."""
        mock_uname = MagicMock()
        mock_uname.release = "5.15.0-generic"

        with patch("builtins.open", side_effect=FileNotFoundError):
            with patch("os.uname", return_value=mock_uname):
                version = _get_kernel_version()
                assert version == (5, 15, 0)

    def test_kernel_version_string(self):
        """Test kernel version string formatting."""
        with patch(
            "elle.daemon.telemetry.ebpf.capabilities._get_kernel_version",
            return_value=(6, 8, 0),
        ):
            assert get_kernel_version_string() == "6.8.0"


class TestCapabilityChecks:
    """Tests for capability checking functions."""

    def test_is_root_when_root(self):
        """Test root detection when running as root."""
        with patch("os.geteuid", return_value=0):
            assert _is_root() is True

    def test_is_root_when_not_root(self):
        """Test root detection when not running as root."""
        with patch("os.geteuid", return_value=1000):
            assert _is_root() is False

    def test_check_effective_capability(self):
        """Test effective capability checking from /proc/self/status."""
        # CapEff with CAP_BPF (bit 39) set
        cap_eff_with_bpf = f"CapEff:\t{1 << CAP_BPF:016x}\n"
        mock_content = f"Name:\ttest\n{cap_eff_with_bpf}"

        with patch("builtins.open", mock_open(read_data=mock_content)):
            assert _check_effective_capability(CAP_BPF) is True

    def test_check_effective_capability_missing(self):
        """Test when capability is not in effective set."""
        # CapEff with no capabilities
        mock_content = "Name:\ttest\nCapEff:\t0000000000000000\n"

        with patch("builtins.open", mock_open(read_data=mock_content)):
            assert _check_effective_capability(CAP_BPF) is False

    def test_bpf_fs_available_when_mounted(self):
        """Test BPF filesystem detection when mounted."""
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.is_dir", return_value=True):
                with patch(
                    "builtins.open",
                    mock_open(read_data="bpf /sys/fs/bpf bpf rw 0 0\n"),
                ):
                    assert _bpf_fs_available() is True

    def test_bpf_fs_not_available(self):
        """Test BPF filesystem detection when not available."""
        with patch("pathlib.Path.exists", return_value=False):
            assert _bpf_fs_available() is False


class TestCheckCapabilities:
    """Tests for the main check_capabilities function."""

    def test_available_when_root(self):
        """Test capabilities check when running as root."""
        with patch("os.geteuid", return_value=0):
            with patch(
                "elle.daemon.telemetry.ebpf.capabilities._get_kernel_version",
                return_value=(6, 8, 0),
            ):
                with patch(
                    "elle.daemon.telemetry.ebpf.capabilities._bpf_fs_available",
                    return_value=True,
                ):
                    with patch(
                        "elle.daemon.telemetry.ebpf.capabilities._bcc_available",
                        return_value=True,
                    ):
                        check = check_capabilities()
                        assert check.available is True
                        assert "root" in check.reason.lower()

    def test_not_available_kernel_too_old(self):
        """Test when kernel is too old."""
        with patch(
            "elle.daemon.telemetry.ebpf.capabilities._get_kernel_version",
            return_value=(4, 19, 0),
        ):
            check = check_capabilities()
            assert check.available is False
            assert "too old" in check.reason.lower()

    def test_not_available_missing_bcc(self):
        """Test when BCC is not installed."""
        with patch("os.geteuid", return_value=0):
            with patch(
                "elle.daemon.telemetry.ebpf.capabilities._get_kernel_version",
                return_value=(6, 8, 0),
            ):
                with patch(
                    "elle.daemon.telemetry.ebpf.capabilities._bpf_fs_available",
                    return_value=True,
                ):
                    with patch(
                        "elle.daemon.telemetry.ebpf.capabilities._bcc_available",
                        return_value=False,
                    ):
                        check = check_capabilities()
                        assert check.available is False
                        assert "bcc" in check.reason.lower()


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_can_use_ebpf(self):
        """Test can_use_ebpf convenience function."""
        with patch(
            "elle.daemon.telemetry.ebpf.capabilities.check_capabilities",
            return_value=CapabilityCheck(
                available=True,
                kernel_version=(6, 8, 0),
                has_cap_bpf=True,
                has_cap_perfmon=True,
                has_cap_net_admin=True,
                has_cap_sys_admin=False,
                bpf_fs_available=True,
                reason="Running as root",
            ),
        ):
            available, reason = can_use_ebpf()
            assert available is True
            assert "root" in reason.lower()

    def test_get_missing_capabilities(self):
        """Test getting list of missing capabilities."""
        with patch(
            "elle.daemon.telemetry.ebpf.capabilities.check_capabilities",
            return_value=CapabilityCheck(
                available=False,
                kernel_version=(6, 8, 0),
                has_cap_bpf=False,
                has_cap_perfmon=False,
                has_cap_net_admin=True,
                has_cap_sys_admin=False,
                bpf_fs_available=True,
                reason="Missing capabilities",
            ),
        ):
            missing = get_missing_capabilities()
            assert "CAP_BPF" in missing
            assert "CAP_PERFMON" in missing
            assert len(missing) == 2


class TestMinKernelVersion:
    """Tests for minimum kernel version constant."""

    def test_min_kernel_version_is_5_8(self):
        """Test that minimum kernel version is 5.8."""
        assert MIN_KERNEL_VERSION == (5, 8)

    def test_capability_constants(self):
        """Test that capability constants are correct."""
        # These are from include/uapi/linux/capability.h
        assert CAP_NET_ADMIN == 12
        assert CAP_SYS_ADMIN == 21
        assert CAP_BPF == 39
        assert CAP_PERFMON == 38
