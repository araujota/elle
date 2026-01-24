"""Tests for telemetry probes."""

import pytest
from unittest.mock import patch, AsyncMock
from datetime import UTC, datetime

from elle.daemon.config import ThresholdConfig
from elle.daemon.telemetry.probes import (
    MemoryProbe,
    DiskProbe,
    NetworkProbe,
    ThermalProbe,
    SmartProbe,
    create_default_probes,
)


class TestMemoryProbe:
    """Tests for MemoryProbe."""

    @pytest.fixture
    def probe(self):
        return MemoryProbe(ThresholdConfig(memory_warning_pct=0.85))

    @pytest.mark.asyncio
    async def test_probe_runs_successfully(self, probe):
        """Probe should complete without error."""
        result = await probe.run()
        assert result.probe_name == "memory"
        assert result.success is True
        assert "memtotal_kb" in result.data or len(result.data) == 0

    @pytest.mark.asyncio
    async def test_probe_tracks_last_run(self, probe):
        """Probe should track last run time."""
        assert probe.last_run is None
        await probe.run()
        assert probe.last_run is not None
        assert (datetime.now(UTC) - probe.last_run).total_seconds() < 2

    @pytest.mark.asyncio
    async def test_high_memory_generates_event(self, probe):
        """High memory pressure should generate warning event."""
        # Mock meminfo with high usage
        mock_data = {
            "memtotal_kb": 8000000,
            "memavailable_kb": 800000,  # 90% used
        }

        with patch.object(probe, "_read_meminfo", return_value=mock_data):
            result = await probe.run()

        assert result.success is True
        assert result.data.get("mem_pressure", 0) > 0.85
        assert len(result.events) > 0
        assert result.events[0].category == "oom"

    @pytest.mark.asyncio
    async def test_normal_memory_no_event(self, probe):
        """Normal memory usage should not generate event."""
        # Mock meminfo with normal usage
        mock_data = {
            "memtotal_kb": 8000000,
            "memavailable_kb": 4000000,  # 50% used
        }

        with patch.object(probe, "_read_meminfo", return_value=mock_data):
            result = await probe.run()

        assert result.success is True
        assert len(result.events) == 0


class TestDiskProbe:
    """Tests for DiskProbe."""

    @pytest.fixture
    def probe(self):
        return DiskProbe(ThresholdConfig(disk_warning_pct=0.90))

    @pytest.mark.asyncio
    async def test_probe_runs_successfully(self, probe):
        """Probe should complete without error."""
        result = await probe.run()
        assert result.probe_name == "disk"
        assert result.success is True

    @pytest.mark.asyncio
    async def test_high_disk_generates_event(self, probe):
        """High disk usage should generate warning event."""
        mock_mounts = [
            {"mount": "/", "device": "/dev/sda1", "used_pct": 95, "free_gb": 5}
        ]

        with patch.object(probe, "_get_mounts", return_value=mock_mounts):
            result = await probe.run()

        assert result.success is True
        assert len(result.events) > 0
        assert result.events[0].category == "disk"
        assert "95" in result.events[0].message or "95.0" in result.events[0].message


class TestNetworkProbe:
    """Tests for NetworkProbe."""

    @pytest.fixture
    def probe(self):
        return NetworkProbe(ThresholdConfig(network_error_threshold=10))

    @pytest.mark.asyncio
    async def test_probe_runs_successfully(self, probe):
        """Probe should complete without error."""
        result = await probe.run()
        assert result.probe_name == "network"
        assert result.success is True

    @pytest.mark.asyncio
    async def test_link_down_generates_event(self, probe):
        """Link down should generate warning event."""
        mock_interfaces = [
            {"name": "eth0", "state": "down", "rx_errors": 0, "tx_errors": 0}
        ]

        with patch.object(probe, "_get_interfaces", return_value=mock_interfaces):
            result = await probe.run()

        assert result.success is True
        assert len(result.events) > 0
        assert "down" in result.events[0].message.lower()

    @pytest.mark.asyncio
    async def test_error_increase_generates_event(self, probe):
        """New errors should generate warning event."""
        mock_interfaces = [
            {"name": "eth0", "state": "up", "rx_errors": 100, "tx_errors": 50}
        ]

        # First run to establish baseline
        with patch.object(probe, "_get_interfaces", return_value=mock_interfaces):
            await probe.run()

        # Second run with increased errors
        mock_interfaces[0]["rx_errors"] = 200
        with patch.object(probe, "_get_interfaces", return_value=mock_interfaces):
            result = await probe.run()

        assert result.success is True
        # Should have event for new errors (100 new rx_errors > threshold of 10)
        assert any("error" in e.message.lower() for e in result.events)


class TestThermalProbe:
    """Tests for ThermalProbe."""

    @pytest.fixture
    def probe(self):
        return ThermalProbe(ThresholdConfig(thermal_warning_c=80.0))

    @pytest.mark.asyncio
    async def test_probe_runs_successfully(self, probe):
        """Probe should complete without error."""
        result = await probe.run()
        assert result.probe_name == "thermal"
        assert result.success is True

    @pytest.mark.asyncio
    async def test_high_temp_generates_event(self, probe):
        """High temperature should generate warning event."""
        mock_temps = [
            {"zone": "thermal_zone0", "type": "x86_pkg_temp", "celsius": 95}
        ]

        with patch.object(probe, "_get_temps", return_value=mock_temps):
            result = await probe.run()

        assert result.success is True
        assert len(result.events) > 0
        assert result.events[0].category == "thermal"
        assert "95" in result.events[0].message


class TestSmartProbe:
    """Tests for SmartProbe."""

    @pytest.fixture
    def probe(self):
        return SmartProbe(ThresholdConfig())

    @pytest.mark.asyncio
    async def test_probe_runs_successfully(self, probe):
        """Probe should complete without error."""
        # SMART probe may fail without real disks, that's ok
        result = await probe.run()
        assert result.probe_name == "smart"
        # May fail in test environment without smartctl

    @pytest.mark.asyncio
    async def test_smart_failure_generates_critical_event(self, probe):
        """SMART failure should generate critical event."""
        mock_disks = [
            {"device": "/dev/sda", "health": "FAILED", "pct_used": 0, "media_errors": 0}
        ]

        with patch.object(probe, "_get_smart_info", return_value=mock_disks):
            result = await probe.run()

        assert result.success is True
        assert len(result.events) > 0
        assert result.events[0].severity == "critical"
        assert result.events[0].category == "smart"


class TestCreateDefaultProbes:
    """Tests for probe factory function."""

    def test_creates_all_probes(self):
        """Should create all 5 default probes."""
        probes = create_default_probes()
        assert len(probes) == 5

        names = {p.name for p in probes}
        assert names == {"memory", "disk", "network", "thermal", "smart"}

    def test_probes_use_thresholds(self):
        """Probes should use provided thresholds."""
        thresholds = ThresholdConfig(memory_warning_pct=0.50)
        probes = create_default_probes(thresholds)

        memory_probe = next(p for p in probes if p.name == "memory")
        assert memory_probe._thresholds.memory_warning_pct == 0.50

    def test_probe_intervals(self):
        """Probes should have correct default intervals."""
        probes = create_default_probes()

        intervals = {p.name: p.interval for p in probes}

        assert intervals["memory"] == 30
        assert intervals["disk"] == 300
        assert intervals["network"] == 60
        assert intervals["thermal"] == 60
        assert intervals["smart"] == 3600
