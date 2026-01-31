"""Tests for multi-variate correlation engine."""

from __future__ import annotations

import pytest

from elle.daemon.telemetry.correlator_engine import (
    AuthBruteForce,
    CertificateCascade,
    CgroupOOMPressure,
    ContainerStorm,
    CorrelationAlert,
    CorrelationEngine,
    DiskIOStall,
    DNSDegradation,
    InodeExhaustion,
    MemoryExhaustionCascade,
    NetworkSaturation,
    SwapThrashing,
    ThermalThrottling,
    ZombieAccumulation,
)

# ===========================================================================
# CorrelationAlert model
# ===========================================================================


class TestCorrelationAlert:
    """Tests for the CorrelationAlert Pydantic model."""

    def test_create_alert(self):
        """Alert can be created with all required fields."""
        alert = CorrelationAlert(
            signature="test_sig",
            severity="warning",
            description="A test alert",
            contributing_metrics={"metric.a": 10.0},
            recommended_action="Take action",
        )
        assert alert.signature == "test_sig"
        assert alert.severity == "warning"
        assert alert.contributing_metrics == {"metric.a": 10.0}

    def test_detected_at_default(self):
        """detected_at is auto-populated when omitted."""
        alert = CorrelationAlert(
            signature="x",
            severity="critical",
            description="d",
            contributing_metrics={},
            recommended_action="r",
        )
        assert alert.detected_at is not None

    def test_serialization_roundtrip(self):
        """Alert can be dumped to dict and back."""
        alert = CorrelationAlert(
            signature="rt",
            severity="critical",
            description="roundtrip",
            contributing_metrics={"m": 1.0},
            recommended_action="none",
        )
        data = alert.model_dump()
        restored = CorrelationAlert(**data)
        assert restored.signature == "rt"
        assert restored.severity == "critical"


# ===========================================================================
# Individual Signature Tests
# ===========================================================================


class TestMemoryExhaustionCascade:
    """Tests for MemoryExhaustionCascade signature."""

    @pytest.fixture()
    def sig(self) -> MemoryExhaustionCascade:
        return MemoryExhaustionCascade()

    def test_triggers_with_oom(self, sig: MemoryExhaustionCascade):
        """High mem + high swap + OOM events triggers alert."""
        metrics = {"mem.used_pct": 90.0, "swap.used_pct": 60.0}
        events = {"oom_count": 2}
        alert = sig.check(metrics, events)
        assert alert is not None
        assert alert.severity == "critical"
        assert alert.signature == "memory_exhaustion_cascade"

    def test_triggers_with_service_failures(self, sig: MemoryExhaustionCascade):
        """High mem + high swap + service failures triggers alert."""
        metrics = {"mem.used_pct": 90.0, "swap.used_pct": 55.0}
        events = {"service_failures": 1}
        alert = sig.check(metrics, events)
        assert alert is not None

    def test_no_trigger_low_mem(self, sig: MemoryExhaustionCascade):
        """Low memory does not trigger even with swap and OOM."""
        metrics = {"mem.used_pct": 70.0, "swap.used_pct": 60.0}
        events = {"oom_count": 5}
        assert sig.check(metrics, events) is None

    def test_no_trigger_low_swap(self, sig: MemoryExhaustionCascade):
        """Low swap does not trigger even with high mem and OOM."""
        metrics = {"mem.used_pct": 95.0, "swap.used_pct": 40.0}
        events = {"oom_count": 1}
        assert sig.check(metrics, events) is None

    def test_no_trigger_no_events(self, sig: MemoryExhaustionCascade):
        """No OOM or service failures means no alert."""
        metrics = {"mem.used_pct": 95.0, "swap.used_pct": 80.0}
        events: dict[str, int] = {}
        assert sig.check(metrics, events) is None

    def test_boundary_mem_at_85(self, sig: MemoryExhaustionCascade):
        """Exactly 85% memory does not trigger (needs >85)."""
        metrics = {"mem.used_pct": 85.0, "swap.used_pct": 60.0}
        events = {"oom_count": 1}
        assert sig.check(metrics, events) is None

    def test_boundary_swap_at_50(self, sig: MemoryExhaustionCascade):
        """Exactly 50% swap does not trigger (needs >50)."""
        metrics = {"mem.used_pct": 90.0, "swap.used_pct": 50.0}
        events = {"oom_count": 1}
        assert sig.check(metrics, events) is None


class TestDiskIOStall:
    """Tests for DiskIOStall signature."""

    @pytest.fixture()
    def sig(self) -> DiskIOStall:
        return DiskIOStall()

    def test_triggers(self, sig: DiskIOStall):
        """High disk + high latency + high CPU triggers alert."""
        metrics = {
            "disk./.used_pct": 85.0,
            "io.latency_ms": 150.0,
            "cpu.load_1m": 5.0,
        }
        alert = sig.check(metrics, {})
        assert alert is not None
        assert alert.severity == "critical"

    def test_triggers_with_var(self, sig: DiskIOStall):
        """Uses /var disk metric when root is low."""
        metrics = {
            "disk./.used_pct": 10.0,
            "disk./var.used_pct": 90.0,
            "io.latency_ms": 200.0,
            "cpu.load_1m": 3.0,
        }
        assert sig.check(metrics, {}) is not None

    def test_no_trigger_low_disk(self, sig: DiskIOStall):
        """Low disk does not trigger."""
        metrics = {
            "disk./.used_pct": 50.0,
            "io.latency_ms": 200.0,
            "cpu.load_1m": 5.0,
        }
        assert sig.check(metrics, {}) is None

    def test_no_trigger_low_latency(self, sig: DiskIOStall):
        """Low latency does not trigger."""
        metrics = {
            "disk./.used_pct": 90.0,
            "io.latency_ms": 50.0,
            "cpu.load_1m": 5.0,
        }
        assert sig.check(metrics, {}) is None

    def test_no_trigger_low_cpu(self, sig: DiskIOStall):
        """Low CPU does not trigger."""
        metrics = {
            "disk./.used_pct": 90.0,
            "io.latency_ms": 200.0,
            "cpu.load_1m": 1.5,
        }
        assert sig.check(metrics, {}) is None

    def test_boundary_disk_at_80(self, sig: DiskIOStall):
        """Exactly 80% disk does not trigger (needs >80)."""
        metrics = {
            "disk./.used_pct": 80.0,
            "io.latency_ms": 200.0,
            "cpu.load_1m": 5.0,
        }
        assert sig.check(metrics, {}) is None


class TestNetworkSaturation:
    """Tests for NetworkSaturation signature."""

    @pytest.fixture()
    def sig(self) -> NetworkSaturation:
        return NetworkSaturation()

    def test_triggers_dns(self, sig: NetworkSaturation):
        """High retransmit + high DNS latency triggers."""
        metrics = {
            "net.tcp_retransmit_rate": 0.05,
            "dns.p95_ms": 600.0,
        }
        assert sig.check(metrics, {}) is not None

    def test_triggers_time_wait(self, sig: NetworkSaturation):
        """High retransmit + time_wait > 5000 triggers."""
        metrics = {
            "net.tcp_retransmit_rate": 0.03,
            "net.tcp_time_wait": 6000.0,
        }
        assert sig.check(metrics, {}) is not None

    def test_no_trigger_low_retransmit(self, sig: NetworkSaturation):
        """Low retransmit does not trigger."""
        metrics = {
            "net.tcp_retransmit_rate": 0.01,
            "dns.p95_ms": 1000.0,
        }
        assert sig.check(metrics, {}) is None

    def test_no_trigger_no_secondary(self, sig: NetworkSaturation):
        """High retransmit but neither DNS nor time_wait triggers."""
        metrics = {
            "net.tcp_retransmit_rate": 0.05,
            "dns.p95_ms": 100.0,
            "net.tcp_time_wait": 1000.0,
        }
        assert sig.check(metrics, {}) is None

    def test_boundary_retransmit_at_0_02(self, sig: NetworkSaturation):
        """Exactly 0.02 retransmit does not trigger (needs >0.02)."""
        metrics = {
            "net.tcp_retransmit_rate": 0.02,
            "dns.p95_ms": 600.0,
        }
        assert sig.check(metrics, {}) is None


class TestThermalThrottling:
    """Tests for ThermalThrottling signature."""

    @pytest.fixture()
    def sig(self) -> ThermalThrottling:
        return ThermalThrottling()

    def test_triggers(self, sig: ThermalThrottling):
        """High temp + low freq ratio + high load triggers."""
        metrics = {
            "thermal.max_temp_c": 90.0,
            "thermal.freq_ratio": 0.6,
            "cpu.load_1m": 2.0,
        }
        assert sig.check(metrics, {}) is not None

    def test_no_trigger_low_temp(self, sig: ThermalThrottling):
        """Low temperature does not trigger."""
        metrics = {
            "thermal.max_temp_c": 60.0,
            "thermal.freq_ratio": 0.6,
            "cpu.load_1m": 2.0,
        }
        assert sig.check(metrics, {}) is None

    def test_no_trigger_normal_freq(self, sig: ThermalThrottling):
        """Normal freq ratio does not trigger."""
        metrics = {
            "thermal.max_temp_c": 90.0,
            "thermal.freq_ratio": 0.9,
            "cpu.load_1m": 2.0,
        }
        assert sig.check(metrics, {}) is None

    def test_no_trigger_low_load(self, sig: ThermalThrottling):
        """Low CPU load does not trigger."""
        metrics = {
            "thermal.max_temp_c": 90.0,
            "thermal.freq_ratio": 0.6,
            "cpu.load_1m": 0.5,
        }
        assert sig.check(metrics, {}) is None

    def test_boundary_temp_at_80(self, sig: ThermalThrottling):
        """Exactly 80C does not trigger (needs >80)."""
        metrics = {
            "thermal.max_temp_c": 80.0,
            "thermal.freq_ratio": 0.5,
            "cpu.load_1m": 5.0,
        }
        assert sig.check(metrics, {}) is None


class TestContainerStorm:
    """Tests for ContainerStorm signature."""

    @pytest.fixture()
    def sig(self) -> ContainerStorm:
        return ContainerStorm()

    def test_triggers_high_cpu(self, sig: ContainerStorm):
        """Docker exits + high CPU triggers."""
        metrics = {"cpu.load_1m": 4.0, "mem.used_pct": 50.0}
        events = {"docker_exited": 5}
        assert sig.check(metrics, events) is not None

    def test_triggers_high_mem(self, sig: ContainerStorm):
        """Docker exits + high memory triggers."""
        metrics = {"cpu.load_1m": 1.0, "mem.used_pct": 90.0}
        events = {"docker_exited": 5}
        assert sig.check(metrics, events) is not None

    def test_no_trigger_few_exits(self, sig: ContainerStorm):
        """3 or fewer exits does not trigger."""
        metrics = {"cpu.load_1m": 10.0, "mem.used_pct": 95.0}
        events = {"docker_exited": 3}
        assert sig.check(metrics, events) is None

    def test_no_trigger_low_resources(self, sig: ContainerStorm):
        """Low CPU and memory does not trigger."""
        metrics = {"cpu.load_1m": 1.0, "mem.used_pct": 50.0}
        events = {"docker_exited": 10}
        assert sig.check(metrics, events) is None

    def test_boundary_exits_at_3(self, sig: ContainerStorm):
        """Exactly 3 exits does not trigger (needs >3)."""
        metrics = {"cpu.load_1m": 10.0, "mem.used_pct": 95.0}
        events = {"docker_exited": 3}
        assert sig.check(metrics, events) is None


class TestDNSDegradation:
    """Tests for DNSDegradation signature."""

    @pytest.fixture()
    def sig(self) -> DNSDegradation:
        return DNSDegradation()

    def test_triggers(self, sig: DNSDegradation):
        """High DNS latency + service failures triggers."""
        metrics = {"dns.p95_ms": 300.0}
        events = {"service_failures": 2}
        assert sig.check(metrics, events) is not None

    def test_no_trigger_low_dns(self, sig: DNSDegradation):
        """Low DNS latency does not trigger."""
        metrics = {"dns.p95_ms": 100.0}
        events = {"service_failures": 5}
        assert sig.check(metrics, events) is None

    def test_no_trigger_no_failures(self, sig: DNSDegradation):
        """No service failures does not trigger."""
        metrics = {"dns.p95_ms": 500.0}
        events: dict[str, int] = {}
        assert sig.check(metrics, events) is None

    def test_boundary_dns_at_200(self, sig: DNSDegradation):
        """Exactly 200ms DNS does not trigger (needs >200)."""
        metrics = {"dns.p95_ms": 200.0}
        events = {"service_failures": 1}
        assert sig.check(metrics, events) is None


class TestAuthBruteForce:
    """Tests for AuthBruteForce signature."""

    @pytest.fixture()
    def sig(self) -> AuthBruteForce:
        return AuthBruteForce()

    def test_triggers(self, sig: AuthBruteForce):
        """High auth failures + established connections triggers."""
        metrics = {"net.tcp_established": 50.0}
        events = {"auth_failures": 15}
        assert sig.check(metrics, events) is not None

    def test_no_trigger_few_failures(self, sig: AuthBruteForce):
        """10 or fewer failures does not trigger."""
        metrics = {"net.tcp_established": 50.0}
        events = {"auth_failures": 10}
        assert sig.check(metrics, events) is None

    def test_no_trigger_no_connections(self, sig: AuthBruteForce):
        """Zero established connections does not trigger."""
        metrics = {"net.tcp_established": 0.0}
        events = {"auth_failures": 20}
        assert sig.check(metrics, events) is None

    def test_boundary_failures_at_10(self, sig: AuthBruteForce):
        """Exactly 10 failures does not trigger (needs >10)."""
        metrics = {"net.tcp_established": 100.0}
        events = {"auth_failures": 10}
        assert sig.check(metrics, events) is None


class TestSwapThrashing:
    """Tests for SwapThrashing signature."""

    @pytest.fixture()
    def sig(self) -> SwapThrashing:
        return SwapThrashing()

    def test_triggers(self, sig: SwapThrashing):
        """High swap stddev + high IO utilization triggers."""
        metrics = {
            "swap.used_pct": 50.0,
            "swap.stddev_1h": 15.0,
            "io.util_pct": 80.0,
            "cpu.load_1m": 3.0,
        }
        assert sig.check(metrics, {}) is not None

    def test_no_trigger_low_stddev(self, sig: SwapThrashing):
        """Low swap stddev does not trigger."""
        metrics = {
            "swap.stddev_1h": 5.0,
            "io.util_pct": 90.0,
        }
        assert sig.check(metrics, {}) is None

    def test_no_trigger_low_io(self, sig: SwapThrashing):
        """Low IO utilization does not trigger."""
        metrics = {
            "swap.stddev_1h": 20.0,
            "io.util_pct": 50.0,
        }
        assert sig.check(metrics, {}) is None

    def test_boundary_stddev_at_10(self, sig: SwapThrashing):
        """Exactly 10 stddev does not trigger (needs >10)."""
        metrics = {
            "swap.stddev_1h": 10.0,
            "io.util_pct": 80.0,
        }
        assert sig.check(metrics, {}) is None


class TestCertificateCascade:
    """Tests for CertificateCascade signature."""

    @pytest.fixture()
    def sig(self) -> CertificateCascade:
        return CertificateCascade()

    def test_triggers(self, sig: CertificateCascade):
        """Cert expiring soon + service failures triggers."""
        metrics = {"cert.days_until_expiry_min": 3.0}
        events = {"service_failures": 1}
        assert sig.check(metrics, events) is not None

    def test_no_trigger_far_expiry(self, sig: CertificateCascade):
        """Cert far from expiry does not trigger."""
        metrics = {"cert.days_until_expiry_min": 30.0}
        events = {"service_failures": 5}
        assert sig.check(metrics, events) is None

    def test_no_trigger_no_failures(self, sig: CertificateCascade):
        """No service failures does not trigger."""
        metrics = {"cert.days_until_expiry_min": 2.0}
        events: dict[str, int] = {}
        assert sig.check(metrics, events) is None

    def test_no_trigger_missing_cert_metric(self, sig: CertificateCascade):
        """Missing cert metric defaults to 365 days, so no trigger."""
        metrics: dict[str, float] = {}
        events = {"service_failures": 10}
        assert sig.check(metrics, events) is None

    def test_boundary_days_at_7(self, sig: CertificateCascade):
        """Exactly 7 days does not trigger (needs <7)."""
        metrics = {"cert.days_until_expiry_min": 7.0}
        events = {"service_failures": 1}
        assert sig.check(metrics, events) is None


class TestInodeExhaustion:
    """Tests for InodeExhaustion signature."""

    @pytest.fixture()
    def sig(self) -> InodeExhaustion:
        return InodeExhaustion()

    def test_triggers(self, sig: InodeExhaustion):
        """High inodes + low disk triggers."""
        metrics = {
            "disk./.inode_pct": 95.0,
            "disk./.used_pct": 50.0,
        }
        assert sig.check(metrics, {}) is not None

    def test_triggers_via_var(self, sig: InodeExhaustion):
        """Uses /var inode metric when root is low."""
        metrics = {
            "disk./.inode_pct": 10.0,
            "disk./var.inode_pct": 95.0,
            "disk./.used_pct": 50.0,
            "disk./var.used_pct": 50.0,
        }
        assert sig.check(metrics, {}) is not None

    def test_no_trigger_low_inodes(self, sig: InodeExhaustion):
        """Low inode usage does not trigger."""
        metrics = {
            "disk./.inode_pct": 50.0,
            "disk./.used_pct": 30.0,
        }
        assert sig.check(metrics, {}) is None

    def test_no_trigger_high_disk(self, sig: InodeExhaustion):
        """High disk usage does not trigger (disk >= 80)."""
        metrics = {
            "disk./.inode_pct": 95.0,
            "disk./.used_pct": 85.0,
        }
        assert sig.check(metrics, {}) is None

    def test_boundary_inode_at_90(self, sig: InodeExhaustion):
        """Exactly 90% inodes does not trigger (needs >90)."""
        metrics = {
            "disk./.inode_pct": 90.0,
            "disk./.used_pct": 50.0,
        }
        assert sig.check(metrics, {}) is None


class TestZombieAccumulation:
    """Tests for ZombieAccumulation signature."""

    @pytest.fixture()
    def sig(self) -> ZombieAccumulation:
        return ZombieAccumulation()

    def test_triggers(self, sig: ZombieAccumulation):
        """Many zombies + high process count triggers."""
        metrics = {"proc.zombie_count": 15.0, "proc.total_count": 600.0}
        assert sig.check(metrics, {}) is not None

    def test_no_trigger_few_zombies(self, sig: ZombieAccumulation):
        """Few zombies does not trigger."""
        metrics = {"proc.zombie_count": 5.0, "proc.total_count": 600.0}
        assert sig.check(metrics, {}) is None

    def test_no_trigger_low_total(self, sig: ZombieAccumulation):
        """Low total process count does not trigger."""
        metrics = {"proc.zombie_count": 20.0, "proc.total_count": 200.0}
        assert sig.check(metrics, {}) is None

    def test_boundary_zombies_at_10(self, sig: ZombieAccumulation):
        """Exactly 10 zombies does not trigger (needs >10)."""
        metrics = {"proc.zombie_count": 10.0, "proc.total_count": 600.0}
        assert sig.check(metrics, {}) is None

    def test_boundary_total_at_500(self, sig: ZombieAccumulation):
        """Exactly 500 total does not trigger (needs >500)."""
        metrics = {"proc.zombie_count": 15.0, "proc.total_count": 500.0}
        assert sig.check(metrics, {}) is None


class TestCgroupOOMPressure:
    """Tests for CgroupOOMPressure signature."""

    @pytest.fixture()
    def sig(self) -> CgroupOOMPressure:
        return CgroupOOMPressure()

    def test_triggers(self, sig: CgroupOOMPressure):
        """High cgroup mem + OOM events triggers."""
        metrics = {"cgroup.max_mem_pct": 95.0}
        events = {"oom_count": 2}
        assert sig.check(metrics, events) is not None

    def test_no_trigger_low_cgroup(self, sig: CgroupOOMPressure):
        """Low cgroup memory does not trigger."""
        metrics = {"cgroup.max_mem_pct": 50.0}
        events = {"oom_count": 5}
        assert sig.check(metrics, events) is None

    def test_no_trigger_no_oom(self, sig: CgroupOOMPressure):
        """No OOM events does not trigger."""
        metrics = {"cgroup.max_mem_pct": 99.0}
        events: dict[str, int] = {}
        assert sig.check(metrics, events) is None

    def test_boundary_cgroup_at_90(self, sig: CgroupOOMPressure):
        """Exactly 90% cgroup does not trigger (needs >90)."""
        metrics = {"cgroup.max_mem_pct": 90.0}
        events = {"oom_count": 1}
        assert sig.check(metrics, events) is None


# ===========================================================================
# CorrelationEngine
# ===========================================================================


class TestCorrelationEngine:
    """Tests for the CorrelationEngine."""

    @pytest.fixture()
    def engine(self) -> CorrelationEngine:
        return CorrelationEngine()

    def test_has_all_12_signatures(self, engine: CorrelationEngine):
        """Engine registers all 12 signatures."""
        assert len(engine.signatures) == 12

    def test_evaluate_empty_metrics(self, engine: CorrelationEngine):
        """No alerts when metrics and events are empty."""
        alerts = engine.evaluate({}, {})
        assert alerts == []

    def test_evaluate_single_alert(self, engine: CorrelationEngine):
        """One matching signature produces one alert."""
        metrics = {
            "mem.used_pct": 95.0,
            "swap.used_pct": 80.0,
        }
        events = {"oom_count": 3}
        alerts = engine.evaluate(metrics, events)
        sigs = [a.signature for a in alerts]
        assert "memory_exhaustion_cascade" in sigs

    def test_evaluate_multiple_alerts(self, engine: CorrelationEngine):
        """Multiple matching signatures produce multiple alerts."""
        metrics = {
            "mem.used_pct": 95.0,
            "swap.used_pct": 80.0,
            "disk./.inode_pct": 95.0,
            "disk./.used_pct": 50.0,
        }
        events = {"oom_count": 3}
        alerts = engine.evaluate(metrics, events)
        sigs = [a.signature for a in alerts]
        assert "memory_exhaustion_cascade" in sigs
        assert "inode_exhaustion" in sigs
        assert len(alerts) >= 2

    def test_evaluate_returns_list_of_alerts(self, engine: CorrelationEngine):
        """Return type is a list of CorrelationAlert objects."""
        alerts = engine.evaluate({}, {})
        assert isinstance(alerts, list)

    def test_evaluate_no_false_positives(self, engine: CorrelationEngine):
        """Normal metrics produce zero alerts."""
        metrics = {
            "mem.used_pct": 40.0,
            "swap.used_pct": 10.0,
            "cpu.load_1m": 0.5,
            "disk./.used_pct": 30.0,
            "io.latency_ms": 5.0,
            "net.tcp_retransmit_rate": 0.001,
            "dns.p95_ms": 10.0,
            "thermal.max_temp_c": 45.0,
            "thermal.freq_ratio": 1.0,
        }
        events: dict[str, int] = {}
        alerts = engine.evaluate(metrics, events)
        assert alerts == []
