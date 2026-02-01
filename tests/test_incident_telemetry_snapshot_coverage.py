"""Tests for telemetry_snapshot.py to boost coverage.

Tests collection functions with mocked /proc, subprocess, and socket calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.incidents.telemetry_snapshot import (
    CPUPressure,
    DiskInfo,
    DiskIOPressure,
    GPUDevice,
    GPUMetrics,
    InterfaceInfo,
    KernelHardwareSignals,
    MemoryPressure,
    NetworkLiveness,
    ServiceRuntimeSnapshot,
    TelemetrySnapshot,
    _collect_cpu_pressure,
    _collect_disk_io_pressure,
    _collect_gpu_metrics,
    _collect_kernel_hardware_signals,
    _collect_memory_pressure,
    _collect_network_liveness,
    _collect_service_runtime,
    _get_hostname,
    _get_uptime_sec,
    _parse_psi_file,
    collect_telemetry_snapshot,
)

# ---------------------------------------------------------------------------
# Model instantiation tests
# ---------------------------------------------------------------------------


class TestModels:
    @pytest.mark.timeout(10)
    def test_cpu_pressure_defaults(self):
        cpu = CPUPressure(load_1m=0.5, load_5m=0.3, load_15m=0.2)
        assert cpu.runnable_tasks == 0
        assert cpu.cpu_steal_pct == 0.0
        assert cpu.psi_some_pct == 0.0

    @pytest.mark.timeout(10)
    def test_memory_pressure_defaults(self):
        mem = MemoryPressure(total_mb=8192, available_mb=4096)
        assert mem.swap_total_mb == 0
        assert mem.oom_kills_1h == 0

    @pytest.mark.timeout(10)
    def test_disk_info(self):
        d = DiskInfo(mount="/", used_pct=50.0, avail_gb=100.0)
        assert d.device == ""
        assert d.read_only is False

    @pytest.mark.timeout(10)
    def test_disk_io_pressure_defaults(self):
        dio = DiskIOPressure()
        assert dio.disks == ()
        assert dio.io_wait_pct == 0.0

    @pytest.mark.timeout(10)
    def test_interface_info(self):
        i = InterfaceInfo(name="eth0", state="UP")
        assert i.rx_errors == 0
        assert i.tx_errors == 0

    @pytest.mark.timeout(10)
    def test_network_liveness_defaults(self):
        nl = NetworkLiveness()
        assert nl.interfaces == ()
        assert nl.default_route_present is False

    @pytest.mark.timeout(10)
    def test_service_runtime_snapshot(self):
        s = ServiceRuntimeSnapshot(service="nginx.service")
        assert s.pid is None
        assert s.active_state == "unknown"

    @pytest.mark.timeout(10)
    def test_kernel_hardware_signals_defaults(self):
        k = KernelHardwareSignals()
        assert k.oom_events_1h == 0
        assert k.thermal_throttle_active is False

    @pytest.mark.timeout(10)
    def test_gpu_device(self):
        g = GPUDevice(index=0, name="RTX 4090")
        assert g.memory_used_mb == 0
        assert g.ecc_errors == 0

    @pytest.mark.timeout(10)
    def test_gpu_metrics_defaults(self):
        gm = GPUMetrics()
        assert gm.available is False
        assert gm.device_count == 0

    @pytest.mark.timeout(10)
    def test_telemetry_snapshot(self):
        ts = TelemetrySnapshot(
            cpu=CPUPressure(load_1m=1.0, load_5m=0.5, load_15m=0.3),
            memory=MemoryPressure(total_mb=8192, available_mb=4096),
            disk=DiskIOPressure(),
            network=NetworkLiveness(),
            kernel=KernelHardwareSignals(),
        )
        assert ts.hostname == ""
        assert ts.uptime_sec == 0
        assert isinstance(ts.gpu, GPUMetrics)


# ---------------------------------------------------------------------------
# _parse_psi_file tests
# ---------------------------------------------------------------------------


class TestParsePsiFile:
    @pytest.mark.timeout(10)
    def test_valid_psi(self, tmp_path):
        psi_file = tmp_path / "psi_cpu"
        psi_file.write_text(
            "some avg10=5.50 avg60=3.20 avg300=1.10 total=123456\nfull avg10=2.30 avg60=1.00 avg300=0.50 total=654321\n"
        )

        some, full = _parse_psi_file(str(psi_file))
        assert some == 5.5
        assert full == 2.3

    @pytest.mark.timeout(10)
    def test_missing_file(self):
        some, full = _parse_psi_file("/nonexistent/path")
        assert some == 0.0
        assert full == 0.0

    @pytest.mark.timeout(10)
    def test_partial_psi(self, tmp_path):
        psi_file = tmp_path / "psi_partial"
        psi_file.write_text("some avg10=1.23 avg60=0.50 avg300=0.10 total=1000\n")

        some, full = _parse_psi_file(str(psi_file))
        assert some == 1.23
        assert full == 0.0


# ---------------------------------------------------------------------------
# Collection function tests
# ---------------------------------------------------------------------------


class TestCollectCpuPressure:
    @pytest.mark.timeout(10)
    def test_with_mocked_proc(self, tmp_path):
        loadavg = tmp_path / "loadavg"
        loadavg.write_text("1.50 0.80 0.40 3/500 12345\n")

        stat = tmp_path / "stat"
        stat.write_text("cpu 100 20 50 5000 30 10 5 15 0 0\n")

        with patch("elle.daemon.incidents.telemetry_snapshot.Path") as MockPath:

            def path_side_effect(p):
                if p == "/proc/loadavg":
                    return loadavg
                elif p == "/proc/stat":
                    return stat
                else:
                    m = MagicMock()
                    m.read_text.side_effect = FileNotFoundError
                    return m

            MockPath.side_effect = path_side_effect
            with patch("elle.daemon.incidents.telemetry_snapshot._parse_psi_file", return_value=(0.0, 0.0)):
                result = _collect_cpu_pressure()

        assert isinstance(result, CPUPressure)
        assert result.load_1m == 1.5
        assert result.load_5m == 0.8
        assert result.load_15m == 0.4
        assert result.runnable_tasks == 3
        assert result.total_tasks == 500

    @pytest.mark.timeout(10)
    def test_with_file_error(self):
        with patch("elle.daemon.incidents.telemetry_snapshot.Path") as MockPath:
            MockPath.return_value.read_text.side_effect = FileNotFoundError
            with patch("elle.daemon.incidents.telemetry_snapshot._parse_psi_file", return_value=(0.0, 0.0)):
                result = _collect_cpu_pressure()

        assert isinstance(result, CPUPressure)
        assert result.load_1m == 0.0


class TestCollectMemoryPressure:
    @pytest.mark.timeout(10)
    def test_with_mocked_meminfo(self, tmp_path):
        meminfo = tmp_path / "meminfo"
        meminfo.write_text(
            "MemTotal:       16384000 kB\n"
            "MemFree:         8192000 kB\n"
            "MemAvailable:   12288000 kB\n"
            "SwapTotal:       4096000 kB\n"
            "SwapFree:        2048000 kB\n"
        )

        with patch("elle.daemon.incidents.telemetry_snapshot.Path") as MockPath:
            MockPath.side_effect = lambda p: meminfo if "/proc/meminfo" in str(p) else MagicMock()
            with patch("elle.daemon.incidents.telemetry_snapshot._parse_psi_file", return_value=(0.0, 0.0)):
                result = _collect_memory_pressure(oom_kills_1h=2)

        assert isinstance(result, MemoryPressure)
        assert result.oom_kills_1h == 2


class TestCollectDiskIoPressure:
    @pytest.mark.timeout(10)
    def test_returns_disk_io_pressure(self):
        mock_statvfs = MagicMock()
        mock_statvfs.f_blocks = 1000
        mock_statvfs.f_bavail = 500
        mock_statvfs.f_frsize = 4096
        mock_statvfs.f_flag = 0

        with patch("elle.daemon.incidents.telemetry_snapshot.os.statvfs", return_value=mock_statvfs):
            with patch("elle.daemon.incidents.telemetry_snapshot.Path") as MockPath:
                MockPath.return_value.read_text.side_effect = FileNotFoundError
                with patch("elle.daemon.incidents.telemetry_snapshot._parse_psi_file", return_value=(0.0, 0.0)):
                    result = _collect_disk_io_pressure()

        assert isinstance(result, DiskIOPressure)


class TestCollectNetworkLiveness:
    @pytest.mark.timeout(10)
    def test_with_interfaces(self):
        ip_result = MagicMock()
        ip_result.returncode = 0
        ip_result.stdout = '[{"ifname":"eth0","operstate":"UP","stats64":{"rx":{"errors":0},"tx":{"errors":0}}},{"ifname":"lo","operstate":"UNKNOWN"}]'

        route_result = MagicMock()
        route_result.returncode = 0
        route_result.stdout = "default via 10.0.0.1 dev eth0"

        def run_side_effect(cmd, **kwargs):
            if "link" in cmd:
                return ip_result
            elif "route" in cmd:
                return route_result
            return MagicMock(returncode=1, stdout="")

        with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", side_effect=run_side_effect):
            with patch("elle.daemon.incidents.telemetry_snapshot.socket.gethostbyname"):
                with patch("elle.daemon.incidents.telemetry_snapshot.socket.setdefaulttimeout"):
                    result = _collect_network_liveness()

        assert isinstance(result, NetworkLiveness)
        assert result.interfaces_up == 1  # eth0 is UP, lo is skipped
        assert result.default_route_present is True

    @pytest.mark.timeout(10)
    def test_all_failures(self):
        with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", side_effect=Exception("no ip")):
            with patch("elle.daemon.incidents.telemetry_snapshot.socket.gethostbyname", side_effect=Exception):
                with patch("elle.daemon.incidents.telemetry_snapshot.socket.setdefaulttimeout"):
                    result = _collect_network_liveness()

        assert isinstance(result, NetworkLiveness)
        assert result.interfaces_up == 0
        assert result.dns_resolution_ok is False


class TestCollectServiceRuntime:
    @pytest.mark.timeout(10)
    def test_active_service(self):
        result_mock = MagicMock()
        result_mock.returncode = 0
        result_mock.stdout = (
            "MainPID=1234\n"
            "ActiveState=active\n"
            "NRestarts=2\n"
            "ExecMainStatus=0\n"
            "MemoryCurrent=104857600\n"
            "CPUUsageNSec=50000000\n"
        )

        with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", return_value=result_mock):
            with patch("elle.daemon.incidents.telemetry_snapshot.Path") as MockPath:
                MockPath.return_value.exists.return_value = False
                result = _collect_service_runtime("nginx.service")

        assert result is not None
        assert result.pid == 1234
        assert result.active_state == "active"
        assert result.restart_count == 2
        assert result.last_exit_code == 0
        assert result.rss_mb == 100  # 104857600 / (1024*1024)

    @pytest.mark.timeout(10)
    def test_service_not_found(self):
        result_mock = MagicMock()
        result_mock.returncode = 4

        with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", return_value=result_mock):
            result = _collect_service_runtime("nonexistent.service")

        assert result is None

    @pytest.mark.timeout(10)
    def test_exception_handling(self):
        with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", side_effect=Exception("fail")):
            result = _collect_service_runtime("bad.service")

        assert result is None


class TestCollectKernelHardwareSignals:
    @pytest.mark.timeout(10)
    def test_defaults(self):
        with patch("elle.daemon.incidents.telemetry_snapshot.Path") as MockPath:
            MockPath.return_value.exists.return_value = False
            with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", side_effect=FileNotFoundError):
                result = _collect_kernel_hardware_signals(oom_events_1h=3, kernel_warnings_1h=1)

        assert isinstance(result, KernelHardwareSignals)
        assert result.oom_events_1h == 3
        assert result.kernel_warnings_1h == 1


class TestCollectGpuMetrics:
    @pytest.mark.timeout(10)
    def test_no_nvidia(self):
        with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", side_effect=FileNotFoundError):
            result = _collect_gpu_metrics()

        assert isinstance(result, GPUMetrics)
        assert result.available is False

    @pytest.mark.timeout(10)
    def test_nvidia_available(self):
        result_mock = MagicMock()
        result_mock.returncode = 0
        result_mock.stdout = "0, NVIDIA RTX 4090, GPU-abc, 8000, 24000, 75.0, 65, 300, Not Active\n"

        with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", return_value=result_mock):
            result = _collect_gpu_metrics()

        assert result.available is True
        assert result.device_count == 1
        assert len(result.devices) == 1
        assert result.devices[0].name == "NVIDIA RTX 4090"
        assert result.max_temperature_c == 65

    @pytest.mark.timeout(10)
    def test_nvidia_failure(self):
        result_mock = MagicMock()
        result_mock.returncode = 1

        with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", return_value=result_mock):
            result = _collect_gpu_metrics()

        assert result.available is False


class TestHelpers:
    @pytest.mark.timeout(10)
    def test_get_hostname(self):
        with patch("elle.daemon.incidents.telemetry_snapshot.socket.gethostname", return_value="myhost"):
            assert _get_hostname() == "myhost"

    @pytest.mark.timeout(10)
    def test_get_hostname_error(self):
        with patch("elle.daemon.incidents.telemetry_snapshot.socket.gethostname", side_effect=Exception):
            assert _get_hostname() == "unknown"

    @pytest.mark.timeout(10)
    def test_get_uptime_sec(self, tmp_path):
        uptime = tmp_path / "uptime"
        uptime.write_text("12345.67 54321.00\n")

        with patch("elle.daemon.incidents.telemetry_snapshot.Path") as MockPath:
            MockPath.side_effect = lambda p: uptime if "/proc/uptime" in str(p) else MagicMock()
            result = _get_uptime_sec()

        assert result == 12345

    @pytest.mark.timeout(10)
    def test_get_uptime_sec_error(self):
        with patch("elle.daemon.incidents.telemetry_snapshot.Path") as MockPath:
            MockPath.return_value.read_text.side_effect = FileNotFoundError
            assert _get_uptime_sec() == 0


class TestCollectTelemetrySnapshot:
    @pytest.mark.timeout(10)
    def test_full_snapshot(self):
        with patch("elle.daemon.incidents.telemetry_snapshot._collect_cpu_pressure") as cpu:
            cpu.return_value = CPUPressure(load_1m=1.0, load_5m=0.5, load_15m=0.3)
            with patch("elle.daemon.incidents.telemetry_snapshot._collect_memory_pressure") as mem:
                mem.return_value = MemoryPressure(total_mb=8192, available_mb=4096)
                with patch("elle.daemon.incidents.telemetry_snapshot._collect_disk_io_pressure") as disk:
                    disk.return_value = DiskIOPressure()
                    with patch("elle.daemon.incidents.telemetry_snapshot._collect_network_liveness") as net:
                        net.return_value = NetworkLiveness()
                        with patch("elle.daemon.incidents.telemetry_snapshot._collect_kernel_hardware_signals") as kern:
                            kern.return_value = KernelHardwareSignals()
                            with patch("elle.daemon.incidents.telemetry_snapshot._collect_gpu_metrics") as gpu:
                                gpu.return_value = GPUMetrics()
                                with patch("elle.daemon.incidents.telemetry_snapshot._collect_service_runtime") as svc:
                                    svc.return_value = ServiceRuntimeSnapshot(service="nginx.service")
                                    with patch(
                                        "elle.daemon.incidents.telemetry_snapshot._get_hostname", return_value="h"
                                    ):
                                        with patch(
                                            "elle.daemon.incidents.telemetry_snapshot._get_uptime_sec", return_value=100
                                        ):
                                            result = collect_telemetry_snapshot(
                                                implicated_services=["nginx.service"],
                                                oom_kills_1h=1,
                                            )

        assert isinstance(result, TelemetrySnapshot)
        assert result.hostname == "h"
        assert result.uptime_sec == 100
        assert len(result.services) == 1

    @pytest.mark.timeout(10)
    def test_no_services(self):
        with patch("elle.daemon.incidents.telemetry_snapshot._collect_cpu_pressure") as cpu:
            cpu.return_value = CPUPressure(load_1m=0.0, load_5m=0.0, load_15m=0.0)
            with patch("elle.daemon.incidents.telemetry_snapshot._collect_memory_pressure") as mem:
                mem.return_value = MemoryPressure(total_mb=1024, available_mb=512)
                with patch("elle.daemon.incidents.telemetry_snapshot._collect_disk_io_pressure") as disk:
                    disk.return_value = DiskIOPressure()
                    with patch("elle.daemon.incidents.telemetry_snapshot._collect_network_liveness") as net:
                        net.return_value = NetworkLiveness()
                        with patch("elle.daemon.incidents.telemetry_snapshot._collect_kernel_hardware_signals") as kern:
                            kern.return_value = KernelHardwareSignals()
                            with patch("elle.daemon.incidents.telemetry_snapshot._collect_gpu_metrics") as gpu:
                                gpu.return_value = GPUMetrics()
                                with patch("elle.daemon.incidents.telemetry_snapshot._get_hostname", return_value="x"):
                                    with patch(
                                        "elle.daemon.incidents.telemetry_snapshot._get_uptime_sec", return_value=0
                                    ):
                                        result = collect_telemetry_snapshot()

        assert result.services == ()
