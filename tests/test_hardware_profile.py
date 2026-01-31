"""Tests for hardware profile detection and dynamic threshold scaling."""

from __future__ import annotations

from unittest.mock import MagicMock, mock_open, patch

import pytest

from elle.daemon.telemetry.hardware_profile import (
    DynamicThresholds,
    HardwareProfile,
    compute_dynamic_thresholds,
    detect_hardware,
)

# ===========================================================================
# HardwareProfile model
# ===========================================================================


class TestHardwareProfile:
    """Tests for HardwareProfile Pydantic model."""

    def test_create_defaults(self):
        """Default profile has sensible values."""
        profile = HardwareProfile()
        assert profile.cpu_count == 1
        assert profile.ram_total_mb == 0
        assert profile.disk_total_gb == {}
        assert profile.gpu_count == 0
        assert profile.profile_hash == ""

    def test_create_custom(self):
        """Custom values are stored correctly."""
        profile = HardwareProfile(
            cpu_count=16,
            ram_total_mb=32768,
            disk_total_gb={"/": 500.0, "/home": 1000.0},
            gpu_count=2,
        )
        assert profile.cpu_count == 16
        assert profile.ram_total_mb == 32768
        assert profile.disk_total_gb["/"] == 500.0
        assert profile.gpu_count == 2

    def test_detected_at_populated(self):
        """detected_at is auto-populated."""
        profile = HardwareProfile()
        assert profile.detected_at is not None

    def test_compute_hash_stability(self):
        """Same input produces the same hash."""
        profile = HardwareProfile(
            cpu_count=8,
            ram_total_mb=16384,
            disk_total_gb={"/": 256.0},
            gpu_count=0,
        )
        h1 = profile.compute_hash()
        h2 = profile.compute_hash()
        assert h1 == h2
        assert len(h1) == 16  # sha256[:16]

    def test_compute_hash_different_input(self):
        """Different configurations produce different hashes."""
        p1 = HardwareProfile(cpu_count=4, ram_total_mb=8192, disk_total_gb={"/": 256.0})
        p2 = HardwareProfile(cpu_count=8, ram_total_mb=8192, disk_total_gb={"/": 256.0})
        assert p1.compute_hash() != p2.compute_hash()

    def test_compute_hash_disk_order_invariant(self):
        """Disk order does not affect hash."""
        p1 = HardwareProfile(
            cpu_count=4,
            ram_total_mb=8192,
            disk_total_gb={"/": 100.0, "/home": 200.0},
        )
        p2 = HardwareProfile(
            cpu_count=4,
            ram_total_mb=8192,
            disk_total_gb={"/home": 200.0, "/": 100.0},
        )
        assert p1.compute_hash() == p2.compute_hash()

    def test_compute_hash_gpu_matters(self):
        """Changing GPU count changes hash."""
        p1 = HardwareProfile(cpu_count=4, ram_total_mb=8192, gpu_count=0)
        p2 = HardwareProfile(cpu_count=4, ram_total_mb=8192, gpu_count=1)
        assert p1.compute_hash() != p2.compute_hash()


# ===========================================================================
# DynamicThresholds model
# ===========================================================================


class TestDynamicThresholds:
    """Tests for DynamicThresholds defaults."""

    def test_defaults(self):
        """Default threshold values match specification."""
        t = DynamicThresholds()
        assert t.cpu_load_warning == 3.0
        assert t.cpu_load_critical == 4.0
        assert t.mem_used_pct_warning == 80.0
        assert t.mem_used_pct_critical == 92.0
        assert t.disk_used_pct_warning == 80.0
        assert t.disk_used_pct_critical == 92.0
        assert t.swap_used_pct_warning == 70.0
        assert t.swap_used_pct_critical == 90.0


# ===========================================================================
# compute_dynamic_thresholds
# ===========================================================================


class TestComputeDynamicThresholds:
    """Tests for compute_dynamic_thresholds scaling logic."""

    def test_default_small_system(self):
        """Small system (1 CPU, low RAM, small disk) gets base thresholds."""
        profile = HardwareProfile(
            cpu_count=1,
            ram_total_mb=2048,
            disk_total_gb={"/": 50.0},
        )
        t = compute_dynamic_thresholds(profile)
        assert t.cpu_load_warning == pytest.approx(0.75)
        assert t.cpu_load_critical == pytest.approx(1.0)
        assert t.mem_used_pct_warning == 80.0
        assert t.mem_used_pct_critical == 92.0
        assert t.disk_used_pct_warning == 80.0
        assert t.disk_used_pct_critical == 92.0

    def test_multi_core_cpu(self):
        """Multi-core CPU scales load thresholds."""
        profile = HardwareProfile(cpu_count=16, ram_total_mb=8192)
        t = compute_dynamic_thresholds(profile)
        assert t.cpu_load_warning == pytest.approx(12.0)  # 16 * 0.75
        assert t.cpu_load_critical == pytest.approx(16.0)  # 16 * 1.0

    def test_high_ram_raises_mem_thresholds(self):
        """RAM > 64GB (65536 MB) raises memory thresholds."""
        profile = HardwareProfile(
            cpu_count=4,
            ram_total_mb=131072,  # 128 GB
        )
        t = compute_dynamic_thresholds(profile)
        assert t.mem_used_pct_warning == 85.0
        assert t.mem_used_pct_critical == 95.0

    def test_exactly_64gb_uses_default(self):
        """Exactly 64GB (65536 MB) does NOT raise thresholds (needs >65536)."""
        profile = HardwareProfile(
            cpu_count=4,
            ram_total_mb=65536,
        )
        t = compute_dynamic_thresholds(profile)
        assert t.mem_used_pct_warning == 80.0
        assert t.mem_used_pct_critical == 92.0

    def test_large_disk_raises_disk_thresholds(self):
        """Disk > 1TB (1000 GB) raises disk thresholds."""
        profile = HardwareProfile(
            cpu_count=4,
            ram_total_mb=16384,
            disk_total_gb={"/": 2000.0},
        )
        t = compute_dynamic_thresholds(profile)
        assert t.disk_used_pct_warning == 85.0
        assert t.disk_used_pct_critical == 95.0

    def test_exactly_1tb_uses_default(self):
        """Exactly 1TB (1000 GB) does NOT raise disk thresholds."""
        profile = HardwareProfile(
            cpu_count=4,
            ram_total_mb=16384,
            disk_total_gb={"/": 1000.0},
        )
        t = compute_dynamic_thresholds(profile)
        assert t.disk_used_pct_warning == 80.0
        assert t.disk_used_pct_critical == 92.0

    def test_empty_disk_map(self):
        """No disks => default disk thresholds."""
        profile = HardwareProfile(cpu_count=4, ram_total_mb=8192, disk_total_gb={})
        t = compute_dynamic_thresholds(profile)
        assert t.disk_used_pct_warning == 80.0
        assert t.disk_used_pct_critical == 92.0

    def test_multiple_disks_uses_max(self):
        """Largest disk determines threshold scaling."""
        profile = HardwareProfile(
            cpu_count=4,
            ram_total_mb=8192,
            disk_total_gb={"/": 200.0, "/home": 1500.0, "/var": 100.0},
        )
        t = compute_dynamic_thresholds(profile)
        # /home is 1500 GB > 1000 => raised thresholds
        assert t.disk_used_pct_warning == 85.0
        assert t.disk_used_pct_critical == 95.0

    def test_high_ram_and_large_disk(self):
        """Both high RAM and large disk raise their respective thresholds."""
        profile = HardwareProfile(
            cpu_count=32,
            ram_total_mb=262144,  # 256 GB
            disk_total_gb={"/": 4000.0},
            gpu_count=2,
        )
        t = compute_dynamic_thresholds(profile)
        assert t.cpu_load_warning == pytest.approx(24.0)
        assert t.cpu_load_critical == pytest.approx(32.0)
        assert t.mem_used_pct_warning == 85.0
        assert t.mem_used_pct_critical == 95.0
        assert t.disk_used_pct_warning == 85.0
        assert t.disk_used_pct_critical == 95.0


# ===========================================================================
# detect_hardware
# ===========================================================================


class TestDetectHardware:
    """Tests for detect_hardware with mocked system calls."""

    def test_detect_cpu_count(self):
        """Reads CPU count from os.cpu_count()."""
        with (
            patch("os.cpu_count", return_value=8),
            patch("builtins.open", side_effect=OSError),
            patch("os.statvfs", side_effect=OSError),
            patch("os.popen") as mock_popen,
        ):
            mock_popen.return_value = MagicMock(read=MagicMock(return_value=""), close=MagicMock())
            profile = detect_hardware()
            assert profile.cpu_count == 8

    def test_detect_cpu_count_none_fallback(self):
        """os.cpu_count() returning None falls back to 1."""
        with (
            patch("os.cpu_count", return_value=None),
            patch("builtins.open", side_effect=OSError),
            patch("os.statvfs", side_effect=OSError),
            patch("os.popen") as mock_popen,
        ):
            mock_popen.return_value = MagicMock(read=MagicMock(return_value=""), close=MagicMock())
            profile = detect_hardware()
            assert profile.cpu_count == 1

    def test_detect_ram_from_meminfo(self):
        """Reads RAM from /proc/meminfo."""
        meminfo = "MemTotal:       16384000 kB\nMemFree:         1000000 kB\n"
        with (
            patch("os.cpu_count", return_value=4),
            patch("builtins.open", mock_open(read_data=meminfo)),
            patch("os.statvfs", side_effect=OSError),
            patch("os.popen") as mock_popen,
        ):
            mock_popen.return_value = MagicMock(read=MagicMock(return_value=""), close=MagicMock())
            profile = detect_hardware()
            # 16384000 kB // 1024 = 16000 MB
            assert profile.ram_total_mb == 16000

    def test_detect_ram_meminfo_missing(self):
        """Missing /proc/meminfo results in 0 MB RAM."""
        with (
            patch("os.cpu_count", return_value=4),
            patch("builtins.open", side_effect=OSError),
            patch("os.statvfs", side_effect=OSError),
            patch("os.popen") as mock_popen,
        ):
            mock_popen.return_value = MagicMock(read=MagicMock(return_value=""), close=MagicMock())
            profile = detect_hardware()
            assert profile.ram_total_mb == 0

    def test_detect_disk_via_statvfs(self):
        """Reads disk sizes via os.statvfs."""
        mock_st = MagicMock()
        mock_st.f_blocks = 1000000
        mock_st.f_frsize = 4096  # Total = 1000000 * 4096 = 4096000000 B ~ 3.8 GB

        with (
            patch("os.cpu_count", return_value=2),
            patch("builtins.open", side_effect=OSError),
            patch("os.statvfs", return_value=mock_st),
            patch("os.popen") as mock_popen,
        ):
            mock_popen.return_value = MagicMock(read=MagicMock(return_value=""), close=MagicMock())
            profile = detect_hardware()
            assert "/" in profile.disk_total_gb
            expected_gb = round((1000000 * 4096) / (1024**3), 1)
            assert profile.disk_total_gb["/"] == pytest.approx(expected_gb)

    def test_detect_disk_statvfs_failure(self):
        """OSError from statvfs results in empty disk map."""
        with (
            patch("os.cpu_count", return_value=2),
            patch("builtins.open", side_effect=OSError),
            patch("os.statvfs", side_effect=OSError),
            patch("os.popen") as mock_popen,
        ):
            mock_popen.return_value = MagicMock(read=MagicMock(return_value=""), close=MagicMock())
            profile = detect_hardware()
            assert profile.disk_total_gb == {}

    def test_detect_gpu_nvidia(self):
        """Detects GPU via nvidia-smi output."""
        with (
            patch("os.cpu_count", return_value=4),
            patch("builtins.open", side_effect=OSError),
            patch("os.statvfs", side_effect=OSError),
            patch("os.popen") as mock_popen,
        ):
            mock_result = MagicMock()
            mock_result.read.return_value = "2\n"
            mock_result.close = MagicMock()
            mock_popen.return_value = mock_result
            profile = detect_hardware()
            assert profile.gpu_count == 2

    def test_detect_gpu_absent(self):
        """No nvidia-smi output results in gpu_count=0."""
        with (
            patch("os.cpu_count", return_value=4),
            patch("builtins.open", side_effect=OSError),
            patch("os.statvfs", side_effect=OSError),
            patch("os.popen") as mock_popen,
        ):
            mock_result = MagicMock()
            mock_result.read.return_value = ""
            mock_result.close = MagicMock()
            mock_popen.return_value = mock_result
            profile = detect_hardware()
            assert profile.gpu_count == 0

    def test_detect_gpu_error(self):
        """OSError from nvidia-smi detection results in gpu_count=0."""
        with (
            patch("os.cpu_count", return_value=4),
            patch("builtins.open", side_effect=OSError),
            patch("os.statvfs", side_effect=OSError),
            patch("os.popen", side_effect=OSError),
        ):
            profile = detect_hardware()
            assert profile.gpu_count == 0

    def test_profile_hash_populated(self):
        """detect_hardware populates profile_hash."""
        with (
            patch("os.cpu_count", return_value=4),
            patch("builtins.open", side_effect=OSError),
            patch("os.statvfs", side_effect=OSError),
            patch("os.popen") as mock_popen,
        ):
            mock_popen.return_value = MagicMock(read=MagicMock(return_value=""), close=MagicMock())
            profile = detect_hardware()
            assert profile.profile_hash != ""
            assert len(profile.profile_hash) == 16
