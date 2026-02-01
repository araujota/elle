"""Tests for daemon/incidents/preconditions.py coverage.

Covers: evaluate_expression unknown operator, evaluation exception,
evaluate_preconditions PreconditionError, _resolve_path for fingerprint
entities/snapshot disks/services/network/docker/memory/swap/cpu,
create_precondition validation, infer_preconditions for network/docker/service domains.
"""

from __future__ import annotations

import pytest

from elle.daemon.incidents.models import Fingerprint, Precondition, SystemSnapshot
from elle.daemon.incidents.preconditions import (
    PreconditionError,
    _resolve_path,
    create_precondition,
    evaluate_expression,
    evaluate_preconditions,
    infer_preconditions,
)


def _make_snapshot(**overrides) -> SystemSnapshot:
    defaults = {
        "os": "Ubuntu 24.04",
        "kernel": "6.5.0-generic",
        "uptime_sec": 86400,
        "hostname": "test-host",
        "cpu_load": (1.5, 1.0, 0.5),
        "mem_total_mb": 16384,
        "mem_free_mb": 4096,
        "mem_available_mb": 8192,
        "swap_total_mb": 2048,
        "swap_used_mb": 512,
        "disks": ({"mount": "/", "device": "/dev/sda1", "used_pct": 85, "avail_gb": 20},),
        "services": (
            {"name": "nginx", "active": True, "failed": False},
            {"name": "postgres", "active": False, "failed": True},
        ),
        "interfaces": (
            {"name": "eth0", "state": "up", "ip": "192.168.1.10", "rx_err": 0},
            {"name": "wlan0", "state": "DOWN", "ip": None, "rx_err": 5},
        ),
        "docker_running": 3,
        "docker_exited": 1,
    }
    defaults.update(overrides)
    return SystemSnapshot(**defaults)


def _make_fingerprint(**overrides) -> Fingerprint:
    defaults = {
        "disk_pressure": 0.85,
        "mem_pressure": 0.5,
        "oom_count_1h": 0,
        "entities": ("nginx", "postgres"),
    }
    defaults.update(overrides)
    return Fingerprint(**defaults)


class TestEvaluateExpressionEdgeCases:
    """Tests for evaluate_expression edge cases."""

    def test_empty_expression_returns_true(self):
        assert evaluate_expression("") is True

    def test_invalid_expression_format(self):
        with pytest.raises(PreconditionError, match="Invalid expression"):
            evaluate_expression("@@@ no valid tokens @@@")

    def test_comparison_exception_raises(self):
        """When comparison itself fails, PreconditionError is raised."""
        snapshot = _make_snapshot()
        # hostname is a string "test-host", comparing with > against an int
        # will raise TypeError in Python 3: '>' not supported between str and int
        with pytest.raises(PreconditionError, match="Error comparing"):
            evaluate_expression("hostname > 5", snapshot=snapshot)


class TestEvaluatePreconditions:
    """Tests for evaluate_preconditions."""

    def test_empty_preconditions_returns_full_ratio(self):
        ratio, results = evaluate_preconditions([])
        assert ratio == 1.0
        assert results == []

    def test_precondition_error_counts_as_false(self):
        """Invalid expression in a precondition counts as not matched."""
        bad_precond = Precondition(expression="invalid no op", required=True)
        ratio, results = evaluate_preconditions([bad_precond])
        assert ratio == 0.0
        assert results[0][1] is False

    def test_mixed_required_and_optional(self):
        snapshot = _make_snapshot()
        fp = _make_fingerprint()
        preconditions = [
            Precondition(expression="mem_pressure > 0.3", required=True),
            Precondition(expression="mem_pressure > 0.99", required=False),
        ]
        ratio, results = evaluate_preconditions(preconditions, snapshot=snapshot, fingerprint=fp)
        # Only required preconditions count toward ratio
        assert ratio == 1.0  # the one required precondition matches
        assert results[0][1] is True
        assert results[1][1] is False

    def test_no_required_returns_full_ratio(self):
        snapshot = _make_snapshot()
        fp = _make_fingerprint()
        preconditions = [
            Precondition(expression="mem_pressure > 0.99", required=False),
        ]
        ratio, results = evaluate_preconditions(preconditions, snapshot=snapshot, fingerprint=fp)
        assert ratio == 1.0


class TestResolvePathFingerprint:
    """Tests for _resolve_path with fingerprint fields."""

    def test_resolve_fingerprint_entities_membership(self):
        fp = _make_fingerprint(entities=("nginx", "postgres"))
        result = _resolve_path("entities.nginx", None, fp)
        assert result is True

    def test_resolve_fingerprint_entities_missing(self):
        fp = _make_fingerprint(entities=("nginx",))
        result = _resolve_path("entities.apache", None, fp)
        assert result is False

    def test_resolve_fingerprint_simple_field(self):
        fp = _make_fingerprint(mem_pressure=0.85)
        result = _resolve_path("mem_pressure", None, fp)
        assert result == 0.85


class TestResolvePathSnapshot:
    """Tests for _resolve_path with snapshot fields."""

    def test_resolve_disk_field(self):
        snapshot = _make_snapshot()
        result = _resolve_path("disk./.used_pct", snapshot, None)
        assert result == 85

    def test_resolve_disk_missing_mount(self):
        snapshot = _make_snapshot()
        result = _resolve_path("disk./nonexistent.used_pct", snapshot, None)
        assert result == 0

    def test_resolve_services_field(self):
        snapshot = _make_snapshot()
        result = _resolve_path("services.nginx.active", snapshot, None)
        assert result is True

    def test_resolve_services_missing_name(self):
        snapshot = _make_snapshot()
        result = _resolve_path("services.unknown.active", snapshot, None)
        assert result is False

    def test_resolve_net_interface(self):
        snapshot = _make_snapshot()
        result = _resolve_path("net.eth0.state", snapshot, None)
        assert result == "up"

    def test_resolve_interfaces_alias(self):
        snapshot = _make_snapshot()
        result = _resolve_path("interfaces.eth0.ip", snapshot, None)
        assert result == "192.168.1.10"

    def test_resolve_net_missing_interface(self):
        snapshot = _make_snapshot()
        result = _resolve_path("net.missing0.state", snapshot, None)
        assert result == "UNKNOWN"

    def test_resolve_docker_running(self):
        snapshot = _make_snapshot()
        result = _resolve_path("docker.running", snapshot, None)
        assert result == 3

    def test_resolve_docker_exited(self):
        snapshot = _make_snapshot()
        result = _resolve_path("docker.exited", snapshot, None)
        assert result == 1

    def test_resolve_docker_no_subfield(self):
        snapshot = _make_snapshot()
        result = _resolve_path("docker", snapshot, None)
        # docker with no sub-parts returns 0
        assert result == 0

    def test_resolve_mem_total(self):
        snapshot = _make_snapshot()
        assert _resolve_path("mem.total", snapshot, None) == 16384

    def test_resolve_mem_free(self):
        snapshot = _make_snapshot()
        assert _resolve_path("mem.free", snapshot, None) == 4096

    def test_resolve_mem_available(self):
        snapshot = _make_snapshot()
        assert _resolve_path("mem.available", snapshot, None) == 8192

    def test_resolve_swap_total(self):
        snapshot = _make_snapshot()
        assert _resolve_path("swap.total", snapshot, None) == 2048

    def test_resolve_swap_used(self):
        snapshot = _make_snapshot()
        assert _resolve_path("swap.used", snapshot, None) == 512

    def test_resolve_cpu_load_1m(self):
        snapshot = _make_snapshot()
        assert _resolve_path("cpu.load_1m", snapshot, None) == 1.5

    def test_resolve_cpu_load_5m(self):
        snapshot = _make_snapshot()
        assert _resolve_path("cpu.load_5m", snapshot, None) == 1.0

    def test_resolve_cpu_load_15m(self):
        snapshot = _make_snapshot()
        assert _resolve_path("cpu.load_15m", snapshot, None) == 0.5

    def test_resolve_unknown_path_returns_none(self):
        snapshot = _make_snapshot()
        result = _resolve_path("completely.unknown.path", snapshot, None)
        assert result is None


class TestCreatePrecondition:
    """Tests for create_precondition validation."""

    def test_valid_expression(self):
        p = create_precondition("disk./.used_pct > 80", "Disk usage high")
        assert p.expression == "disk./.used_pct > 80"
        assert p.description == "Disk usage high"
        assert p.required is True

    def test_empty_expression_allowed(self):
        p = create_precondition("", "Empty is OK")
        assert p.expression == ""

    def test_invalid_expression_raises(self):
        with pytest.raises(PreconditionError, match="Invalid expression"):
            create_precondition("no operator here at all")

    def test_optional_precondition(self):
        p = create_precondition("mem_pressure > 0.5", required=False)
        assert p.required is False


class TestInferPreconditions:
    """Tests for infer_preconditions across various domains."""

    def test_infer_disk_domain(self):
        snapshot = _make_snapshot(disks=({"mount": "/", "used_pct": 92},))
        fp = _make_fingerprint(disk_pressure=0.92)
        preconditions = infer_preconditions(snapshot, fp, "disk")
        assert len(preconditions) >= 1
        exprs = [p.expression for p in preconditions]
        assert any("disk./.used_pct" in e for e in exprs)

    def test_infer_oom_domain(self):
        snapshot = _make_snapshot()
        fp = _make_fingerprint(mem_pressure=0.95)
        preconditions = infer_preconditions(snapshot, fp, "oom")
        exprs = [p.expression for p in preconditions]
        assert any("mem_pressure" in e for e in exprs)

    def test_infer_oom_events(self):
        snapshot = _make_snapshot()
        fp = _make_fingerprint(oom_count_1h=3)
        preconditions = infer_preconditions(snapshot, fp, "other")
        exprs = [p.expression for p in preconditions]
        assert any("oom_count_1h > 0" in e for e in exprs)

    def test_infer_net_domain(self):
        snapshot = _make_snapshot(
            interfaces=({"name": "wlan0", "state": "DOWN", "ip": None},),
        )
        fp = _make_fingerprint()
        preconditions = infer_preconditions(snapshot, fp, "net")
        exprs = [p.expression for p in preconditions]
        assert any("interfaces.wlan0.state" in e for e in exprs)

    def test_infer_docker_domain(self):
        snapshot = _make_snapshot(docker_exited=2)
        fp = _make_fingerprint()
        preconditions = infer_preconditions(snapshot, fp, "docker")
        exprs = [p.expression for p in preconditions]
        assert any("docker.exited > 0" in e for e in exprs)

    def test_infer_service_domain(self):
        snapshot = _make_snapshot(
            services=({"name": "postgres", "active": False, "failed": True},),
        )
        fp = _make_fingerprint()
        preconditions = infer_preconditions(snapshot, fp, "service")
        exprs = [p.expression for p in preconditions]
        assert any("services.postgres.failed" in e for e in exprs)

    def test_infer_net_up_interfaces_skipped(self):
        """Interfaces with state 'UP' are not included in net preconditions."""
        snapshot = _make_snapshot(
            interfaces=({"name": "eth0", "state": "UP", "ip": "10.0.0.1"},),
            disks=(),  # no disks to avoid disk preconditions
        )
        fp = _make_fingerprint(disk_pressure=0.1)
        preconditions = infer_preconditions(snapshot, fp, "net")
        # Only net-related preconditions; UP interfaces produce none
        net_preconds = [p for p in preconditions if "interfaces" in p.expression]
        assert len(net_preconds) == 0

    def test_infer_docker_no_exited(self):
        """Docker domain with no exited containers produces no docker precondition."""
        snapshot = _make_snapshot(docker_exited=0, disks=())
        fp = _make_fingerprint(disk_pressure=0.1)
        preconditions = infer_preconditions(snapshot, fp, "docker")
        exprs = [p.expression for p in preconditions]
        assert not any("docker.exited" in e for e in exprs)
