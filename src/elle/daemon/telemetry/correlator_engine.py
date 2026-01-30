"""Multi-variate correlation engine for telemetry analysis.

Defines 12 correlation signatures that detect compound system
issues by analyzing multiple metrics simultaneously.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class CorrelationAlert(BaseModel):
    """Alert emitted when a correlation signature matches."""

    signature: str
    severity: Literal["warning", "critical"]
    description: str
    contributing_metrics: dict[str, float]
    recommended_action: str
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CorrelationSignature:
    """A multi-variate correlation pattern."""

    def __init__(
        self,
        name: str,
        severity: Literal["warning", "critical"],
        description: str,
        required_metrics: list[str],
        recommended_action: str,
    ):
        self.name = name
        self.severity = severity
        self.description = description
        self.required_metrics = required_metrics
        self.recommended_action = recommended_action

    def check(
        self, metrics: dict[str, float], events: dict[str, int]
    ) -> CorrelationAlert | None:
        """Check if this signature matches. Override in subclasses."""
        raise NotImplementedError


class MemoryExhaustionCascade(CorrelationSignature):
    def __init__(self) -> None:
        super().__init__(
            name="memory_exhaustion_cascade",
            severity="critical",
            description="Memory exhaustion causing cascading failures",
            required_metrics=["mem.used_pct", "swap.used_pct"],
            recommended_action="Identify memory-hungry processes, consider adding swap or increasing RAM",
        )

    def check(self, metrics: dict[str, float], events: dict[str, int]) -> CorrelationAlert | None:
        mem = metrics.get("mem.used_pct", 0)
        swap = metrics.get("swap.used_pct", 0)
        oom = events.get("oom_count", 0)
        svc_fail = events.get("service_failures", 0)
        if mem > 85 and swap > 50 and (oom > 0 or svc_fail > 0):
            return CorrelationAlert(
                signature=self.name, severity=self.severity, description=self.description,
                contributing_metrics={"mem.used_pct": mem, "swap.used_pct": swap, "oom_count": float(oom), "service_failures": float(svc_fail)},
                recommended_action=self.recommended_action,
            )
        return None


class DiskIOStall(CorrelationSignature):
    def __init__(self) -> None:
        super().__init__(
            name="disk_io_stall",
            severity="critical",
            description="Disk I/O stall causing system-wide slowdown",
            required_metrics=["io.util_pct", "cpu.load_1m"],
            recommended_action="Check for runaway I/O processes, verify disk health, consider I/O scheduler tuning",
        )

    def check(self, metrics: dict[str, float], events: dict[str, int]) -> CorrelationAlert | None:
        disk_max = max(metrics.get("disk./.used_pct", 0), metrics.get("disk./var.used_pct", 0))
        io_lat = metrics.get("io.latency_ms", 0)
        cpu = metrics.get("cpu.load_1m", 0)
        if disk_max > 80 and io_lat > 100 and cpu > 2.0:
            return CorrelationAlert(
                signature=self.name, severity=self.severity, description=self.description,
                contributing_metrics={"disk_max_pct": disk_max, "io.latency_ms": io_lat, "cpu.load_1m": cpu},
                recommended_action=self.recommended_action,
            )
        return None


class NetworkSaturation(CorrelationSignature):
    def __init__(self) -> None:
        super().__init__(
            name="network_saturation",
            severity="warning",
            description="Network saturation detected across multiple indicators",
            required_metrics=["net.tcp_retransmit_rate"],
            recommended_action="Check for network congestion, review connection pool sizes, investigate DNS resolution",
        )

    def check(self, metrics: dict[str, float], events: dict[str, int]) -> CorrelationAlert | None:
        retrans = metrics.get("net.tcp_retransmit_rate", 0)
        dns_p95 = metrics.get("dns.p95_ms", 0)
        tw = metrics.get("net.tcp_time_wait", 0)
        if retrans > 0.02 and (dns_p95 > 500 or tw > 5000):
            return CorrelationAlert(
                signature=self.name, severity=self.severity, description=self.description,
                contributing_metrics={"net.tcp_retransmit_rate": retrans, "dns.p95_ms": dns_p95, "net.tcp_time_wait": tw},
                recommended_action=self.recommended_action,
            )
        return None


class ThermalThrottling(CorrelationSignature):
    def __init__(self) -> None:
        super().__init__(
            name="thermal_throttling",
            severity="warning",
            description="Thermal throttling reducing CPU performance",
            required_metrics=["thermal.max_temp_c", "thermal.freq_ratio", "cpu.load_1m"],
            recommended_action="Check cooling system, clean dust, reduce workload, verify airflow",
        )

    def check(self, metrics: dict[str, float], events: dict[str, int]) -> CorrelationAlert | None:
        temp = metrics.get("thermal.max_temp_c", 0)
        freq = metrics.get("thermal.freq_ratio", 1.0)
        load = metrics.get("cpu.load_1m", 0)
        if temp > 80 and freq < 0.8 and load > 1.0:
            return CorrelationAlert(
                signature=self.name, severity=self.severity, description=self.description,
                contributing_metrics={"thermal.max_temp_c": temp, "thermal.freq_ratio": freq, "cpu.load_1m": load},
                recommended_action=self.recommended_action,
            )
        return None


class ContainerStorm(CorrelationSignature):
    def __init__(self) -> None:
        super().__init__(
            name="container_storm",
            severity="critical",
            description="Multiple container exits with high resource usage",
            required_metrics=["cpu.load_1m", "mem.used_pct"],
            recommended_action="Check container logs, review resource limits, investigate orchestrator health",
        )

    def check(self, metrics: dict[str, float], events: dict[str, int]) -> CorrelationAlert | None:
        docker_exit = events.get("docker_exited", 0)
        cpu = metrics.get("cpu.load_1m", 0)
        mem = metrics.get("mem.used_pct", 0)
        if docker_exit > 3 and (cpu > 3.0 or mem > 85):
            return CorrelationAlert(
                signature=self.name, severity=self.severity, description=self.description,
                contributing_metrics={"docker_exited": float(docker_exit), "cpu.load_1m": cpu, "mem.used_pct": mem},
                recommended_action=self.recommended_action,
            )
        return None


class DNSDegradation(CorrelationSignature):
    def __init__(self) -> None:
        super().__init__(
            name="dns_degradation",
            severity="warning",
            description="DNS resolution degradation causing service failures",
            required_metrics=["dns.p95_ms"],
            recommended_action="Check DNS resolver health, verify /etc/resolv.conf, investigate network path to DNS",
        )

    def check(self, metrics: dict[str, float], events: dict[str, int]) -> CorrelationAlert | None:
        dns_p95 = metrics.get("dns.p95_ms", 0)
        svc_fail = events.get("service_failures", 0)
        if dns_p95 > 200 and svc_fail > 0:
            return CorrelationAlert(
                signature=self.name, severity=self.severity, description=self.description,
                contributing_metrics={"dns.p95_ms": dns_p95, "service_failures": float(svc_fail)},
                recommended_action=self.recommended_action,
            )
        return None


class AuthBruteForce(CorrelationSignature):
    def __init__(self) -> None:
        super().__init__(
            name="auth_brute_force",
            severity="critical",
            description="Authentication brute force with connection surge",
            required_metrics=[],
            recommended_action="Block source IPs via fail2ban or firewall, check for compromised credentials",
        )

    def check(self, metrics: dict[str, float], events: dict[str, int]) -> CorrelationAlert | None:
        auth_fail = events.get("auth_failures", 0)
        established = metrics.get("net.tcp_established", 0)
        if auth_fail > 10 and established > 0:
            return CorrelationAlert(
                signature=self.name, severity=self.severity, description=self.description,
                contributing_metrics={"auth_failures": float(auth_fail), "net.tcp_established": established},
                recommended_action=self.recommended_action,
            )
        return None


class SwapThrashing(CorrelationSignature):
    def __init__(self) -> None:
        super().__init__(
            name="swap_thrashing",
            severity="warning",
            description="Swap thrashing causing I/O pressure",
            required_metrics=["swap.used_pct", "io.util_pct"],
            recommended_action="Identify memory pressure source, increase RAM, adjust swappiness, add swap space",
        )

    def check(self, metrics: dict[str, float], events: dict[str, int]) -> CorrelationAlert | None:
        swap = metrics.get("swap.used_pct", 0)
        io_util = metrics.get("io.util_pct", 0)
        cpu = metrics.get("cpu.load_1m", 0)
        swap_stddev = metrics.get("swap.stddev_1h", 0)
        if swap_stddev > 10 and io_util > 70:
            return CorrelationAlert(
                signature=self.name, severity=self.severity, description=self.description,
                contributing_metrics={"swap.used_pct": swap, "swap.stddev_1h": swap_stddev, "io.util_pct": io_util, "cpu.load_1m": cpu},
                recommended_action=self.recommended_action,
            )
        return None


class CertificateCascade(CorrelationSignature):
    def __init__(self) -> None:
        super().__init__(
            name="certificate_cascade",
            severity="critical",
            description="Expiring certificate causing service failures",
            required_metrics=[],
            recommended_action="Renew certificates immediately, check automated renewal process",
        )

    def check(self, metrics: dict[str, float], events: dict[str, int]) -> CorrelationAlert | None:
        cert_days = metrics.get("cert.days_until_expiry_min", 365)
        svc_fail = events.get("service_failures", 0)
        if cert_days < 7 and svc_fail > 0:
            return CorrelationAlert(
                signature=self.name, severity=self.severity, description=self.description,
                contributing_metrics={"cert.days_until_expiry_min": cert_days, "service_failures": float(svc_fail)},
                recommended_action=self.recommended_action,
            )
        return None


class InodeExhaustion(CorrelationSignature):
    def __init__(self) -> None:
        super().__init__(
            name="inode_exhaustion",
            severity="critical",
            description="Inode exhaustion despite available disk space",
            required_metrics=["disk./.inode_pct"],
            recommended_action="Find directories with many small files (find / -xdev -printf '%h\n' | sort | uniq -c | sort -rn), clean up temp/log files",
        )

    def check(self, metrics: dict[str, float], events: dict[str, int]) -> CorrelationAlert | None:
        inode = max(metrics.get("disk./.inode_pct", 0), metrics.get("disk./var.inode_pct", 0))
        disk = max(metrics.get("disk./.used_pct", 0), metrics.get("disk./var.used_pct", 0))
        if inode > 90 and disk < 80:
            return CorrelationAlert(
                signature=self.name, severity=self.severity, description=self.description,
                contributing_metrics={"inode_max_pct": inode, "disk_max_pct": disk},
                recommended_action=self.recommended_action,
            )
        return None


class ZombieAccumulation(CorrelationSignature):
    def __init__(self) -> None:
        super().__init__(
            name="zombie_accumulation",
            severity="warning",
            description="Zombie process accumulation indicating parent process issues",
            required_metrics=["proc.zombie_count", "proc.total_count"],
            recommended_action="Identify parent processes of zombies (ps -eo ppid,pid,state,cmd | grep Z), investigate parent health",
        )

    def check(self, metrics: dict[str, float], events: dict[str, int]) -> CorrelationAlert | None:
        zombies = metrics.get("proc.zombie_count", 0)
        total = metrics.get("proc.total_count", 0)
        if zombies > 10 and total > 500:
            return CorrelationAlert(
                signature=self.name, severity=self.severity, description=self.description,
                contributing_metrics={"proc.zombie_count": zombies, "proc.total_count": total},
                recommended_action=self.recommended_action,
            )
        return None


class CgroupOOMPressure(CorrelationSignature):
    def __init__(self) -> None:
        super().__init__(
            name="cgroup_oom_pressure",
            severity="critical",
            description="Cgroup memory limit causing OOM kills",
            required_metrics=["cgroup.max_mem_pct"],
            recommended_action="Review cgroup memory limits, check for memory leaks in contained services, consider raising limits",
        )

    def check(self, metrics: dict[str, float], events: dict[str, int]) -> CorrelationAlert | None:
        cgroup_mem = metrics.get("cgroup.max_mem_pct", 0)
        oom = events.get("oom_count", 0)
        if cgroup_mem > 90 and oom > 0:
            return CorrelationAlert(
                signature=self.name, severity=self.severity, description=self.description,
                contributing_metrics={"cgroup.max_mem_pct": cgroup_mem, "oom_count": float(oom)},
                recommended_action=self.recommended_action,
            )
        return None


class CorrelationEngine:
    """Evaluates all correlation signatures against current metrics."""

    def __init__(self) -> None:
        self.signatures: list[CorrelationSignature] = [
            MemoryExhaustionCascade(),
            DiskIOStall(),
            NetworkSaturation(),
            ThermalThrottling(),
            ContainerStorm(),
            DNSDegradation(),
            AuthBruteForce(),
            SwapThrashing(),
            CertificateCascade(),
            InodeExhaustion(),
            ZombieAccumulation(),
            CgroupOOMPressure(),
        ]

    def evaluate(
        self, metrics: dict[str, float], events: dict[str, int]
    ) -> list[CorrelationAlert]:
        """Evaluate all signatures and return active alerts."""
        alerts = []
        for sig in self.signatures:
            alert = sig.check(metrics, events)
            if alert is not None:
                alerts.append(alert)
        return alerts
