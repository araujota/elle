"""Tests for precondition evaluation."""

import pytest

from elle.daemon.incidents.models import Fingerprint, Precondition, SystemSnapshot
from elle.daemon.incidents.preconditions import (
    PreconditionError,
    create_precondition,
    evaluate_expression,
    evaluate_precondition,
    evaluate_preconditions,
    infer_preconditions,
)


@pytest.fixture
def sample_snapshot():
    """Create a sample snapshot for testing."""
    return SystemSnapshot(
        os="Ubuntu 24.04",
        kernel="6.8.0",
        uptime_sec=3600,
        cpu_load=(1.5, 1.2, 1.0),
        mem_total_mb=16384,
        mem_free_mb=2000,
        mem_available_mb=4000,
        disks=(
            {"mount": "/", "used_pct": 85, "avail_gb": 20},
            {"mount": "/home", "used_pct": 60, "avail_gb": 100},
        ),
        interfaces=(
            {"name": "eth0", "state": "UP", "rx_err": 0, "tx_err": 0},
            {"name": "wlan0", "state": "DOWN", "rx_err": 5, "tx_err": 0},
        ),
        services=(
            {"name": "NetworkManager", "active": True, "failed": False},
            {"name": "docker", "active": True, "failed": False},
            {"name": "nginx", "active": False, "failed": True},
        ),
        docker_running=3,
        docker_exited=2,
    )


@pytest.fixture
def sample_fingerprint():
    """Create a sample fingerprint for testing."""
    return Fingerprint(
        disk_pressure=0.85,
        mem_pressure=0.75,
        cpu_pressure=1.5,
        oom_count_1h=2,
        service_failures_1h=1,
        docker_exited_count=2,
        entities=("service:nginx", "interface:wlan0"),
    )


class TestExpressionParsing:
    """Tests for expression parsing."""

    def test_parse_greater_than(self, sample_fingerprint):
        """Test parsing > operator."""
        result = evaluate_expression(
            "disk_pressure > 0.8",
            fingerprint=sample_fingerprint,
        )
        assert result is True

    def test_parse_less_than(self, sample_fingerprint):
        """Test parsing < operator."""
        result = evaluate_expression(
            "mem_pressure < 0.5",
            fingerprint=sample_fingerprint,
        )
        assert result is False

    def test_parse_equals(self, sample_fingerprint):
        """Test parsing == operator."""
        result = evaluate_expression(
            "oom_count_1h == 2",
            fingerprint=sample_fingerprint,
        )
        assert result is True

    def test_parse_not_equals(self, sample_fingerprint):
        """Test parsing != operator."""
        result = evaluate_expression(
            "oom_count_1h != 0",
            fingerprint=sample_fingerprint,
        )
        assert result is True

    def test_parse_greater_equal(self, sample_fingerprint):
        """Test parsing >= operator."""
        result = evaluate_expression(
            "cpu_pressure >= 1.5",
            fingerprint=sample_fingerprint,
        )
        assert result is True

    def test_parse_less_equal(self, sample_fingerprint):
        """Test parsing <= operator."""
        result = evaluate_expression(
            "cpu_pressure <= 2.0",
            fingerprint=sample_fingerprint,
        )
        assert result is True


class TestDiskPaths:
    """Tests for disk-related paths."""

    def test_disk_used_pct(self, sample_snapshot):
        """Test accessing disk./.used_pct."""
        result = evaluate_expression(
            "disk./.used_pct > 80",
            snapshot=sample_snapshot,
        )
        assert result is True

    def test_disk_home_used_pct(self, sample_snapshot):
        """Test accessing disk./home.used_pct."""
        result = evaluate_expression(
            "disk./home.used_pct > 50",
            snapshot=sample_snapshot,
        )
        assert result is True

    def test_disk_avail_gb(self, sample_snapshot):
        """Test accessing disk./.avail_gb."""
        result = evaluate_expression(
            "disk./.avail_gb < 50",
            snapshot=sample_snapshot,
        )
        assert result is True


class TestServicePaths:
    """Tests for service-related paths."""

    def test_service_active(self, sample_snapshot):
        """Test accessing services.NetworkManager.active."""
        result = evaluate_expression(
            "services.NetworkManager.active == true",
            snapshot=sample_snapshot,
        )
        assert result is True

    def test_service_failed(self, sample_snapshot):
        """Test accessing services.nginx.failed."""
        result = evaluate_expression(
            "services.nginx.failed == true",
            snapshot=sample_snapshot,
        )
        assert result is True

    def test_service_not_active(self, sample_snapshot):
        """Test accessing inactive service."""
        result = evaluate_expression(
            "services.nginx.active == false",
            snapshot=sample_snapshot,
        )
        assert result is True


class TestNetworkPaths:
    """Tests for network-related paths."""

    def test_interface_state(self, sample_snapshot):
        """Test accessing interfaces.eth0.state."""
        result = evaluate_expression(
            "interfaces.eth0.state == UP",
            snapshot=sample_snapshot,
        )
        assert result is True

    def test_interface_down(self, sample_snapshot):
        """Test detecting interface down."""
        result = evaluate_expression(
            "interfaces.wlan0.state == DOWN",
            snapshot=sample_snapshot,
        )
        assert result is True


class TestDockerPaths:
    """Tests for docker-related paths."""

    def test_docker_running(self, sample_snapshot):
        """Test accessing docker.running."""
        result = evaluate_expression(
            "docker.running > 0",
            snapshot=sample_snapshot,
        )
        assert result is True

    def test_docker_exited(self, sample_snapshot):
        """Test accessing docker.exited."""
        result = evaluate_expression(
            "docker.exited > 0",
            snapshot=sample_snapshot,
        )
        assert result is True


class TestMemoryPaths:
    """Tests for memory-related paths."""

    def test_mem_total(self, sample_snapshot):
        """Test accessing mem.total."""
        result = evaluate_expression(
            "mem.total > 8000",
            snapshot=sample_snapshot,
        )
        assert result is True

    def test_mem_available(self, sample_snapshot):
        """Test accessing mem.available."""
        result = evaluate_expression(
            "mem.available < 5000",
            snapshot=sample_snapshot,
        )
        assert result is True


class TestCPUPaths:
    """Tests for CPU-related paths."""

    def test_cpu_load_1m(self, sample_snapshot):
        """Test accessing cpu.load_1m."""
        result = evaluate_expression(
            "cpu.load_1m > 1.0",
            snapshot=sample_snapshot,
        )
        assert result is True

    def test_cpu_load_5m(self, sample_snapshot):
        """Test accessing cpu.load_5m."""
        result = evaluate_expression(
            "cpu.load_5m > 1.0",
            snapshot=sample_snapshot,
        )
        assert result is True


class TestFingerprintPaths:
    """Tests for fingerprint-related paths."""

    def test_fingerprint_oom_count(self, sample_fingerprint):
        """Test accessing oom_count_1h."""
        result = evaluate_expression(
            "oom_count_1h > 0",
            fingerprint=sample_fingerprint,
        )
        assert result is True

    def test_fingerprint_docker_exited(self, sample_fingerprint):
        """Test accessing docker_exited_count."""
        result = evaluate_expression(
            "docker_exited_count > 0",
            fingerprint=sample_fingerprint,
        )
        assert result is True


class TestValueParsing:
    """Tests for value parsing."""

    def test_parse_true(self, sample_snapshot):
        """Test parsing 'true'."""
        result = evaluate_expression(
            "services.docker.active == true",
            snapshot=sample_snapshot,
        )
        assert result is True

    def test_parse_false(self, sample_snapshot):
        """Test parsing 'false'."""
        result = evaluate_expression(
            "services.nginx.active == false",
            snapshot=sample_snapshot,
        )
        assert result is True

    def test_parse_float(self, sample_fingerprint):
        """Test parsing float values."""
        result = evaluate_expression(
            "disk_pressure > 0.5",
            fingerprint=sample_fingerprint,
        )
        assert result is True

    def test_parse_int(self, sample_fingerprint):
        """Test parsing int values."""
        result = evaluate_expression(
            "oom_count_1h == 2",
            fingerprint=sample_fingerprint,
        )
        assert result is True


class TestInvalidExpressions:
    """Tests for invalid expression handling."""

    def test_invalid_format(self):
        """Test that invalid format raises error."""
        with pytest.raises(PreconditionError):
            evaluate_expression("not a valid expression")

    def test_invalid_operator(self):
        """Test that invalid operator raises error."""
        with pytest.raises(PreconditionError):
            create_precondition("value ?? 5")

    def test_empty_expression(self):
        """Test that empty expression returns True."""
        result = evaluate_expression("")
        assert result is True


class TestPreconditionEvaluation:
    """Tests for Precondition evaluation."""

    def test_evaluate_single_precondition(self, sample_fingerprint):
        """Test evaluating a single precondition."""
        precond = Precondition(
            expression="disk_pressure > 0.8",
            description="High disk usage",
        )

        result = evaluate_precondition(precond, fingerprint=sample_fingerprint)
        assert result is True

    def test_evaluate_multiple_preconditions(self, sample_fingerprint, sample_snapshot):
        """Test evaluating multiple preconditions."""
        preconds = [
            Precondition(expression="disk_pressure > 0.8"),
            Precondition(expression="oom_count_1h > 0"),
            Precondition(expression="mem_pressure < 0.5"),  # This will be False
        ]

        ratio, results = evaluate_preconditions(
            preconds,
            snapshot=sample_snapshot,
            fingerprint=sample_fingerprint,
        )

        # 2 out of 3 required preconditions matched
        assert ratio == pytest.approx(2 / 3, 0.01)
        assert len(results) == 3

    def test_optional_precondition(self, sample_fingerprint):
        """Test that optional preconditions don't affect ratio."""
        preconds = [
            Precondition(expression="disk_pressure > 0.8", required=True),
            Precondition(expression="mem_pressure > 0.9", required=False),  # False but optional
        ]

        ratio, results = evaluate_preconditions(
            preconds,
            fingerprint=sample_fingerprint,
        )

        # Only required preconditions count for ratio
        assert ratio == 1.0


class TestCreatePrecondition:
    """Tests for precondition creation."""

    def test_create_valid(self):
        """Test creating a valid precondition."""
        precond = create_precondition(
            "disk./.used_pct > 90",
            description="Disk nearly full",
        )

        assert precond.expression == "disk./.used_pct > 90"
        assert precond.description == "Disk nearly full"
        assert precond.required is True

    def test_create_optional(self):
        """Test creating an optional precondition."""
        precond = create_precondition(
            "mem_pressure > 0.5",
            required=False,
        )

        assert precond.required is False

    def test_create_invalid(self):
        """Test that invalid expression raises error."""
        with pytest.raises(PreconditionError):
            create_precondition("not valid")


class TestInferPreconditions:
    """Tests for precondition inference."""

    def test_infer_disk_preconditions(self, sample_snapshot, sample_fingerprint):
        """Test inferring disk-related preconditions."""
        preconds = infer_preconditions(
            sample_snapshot,
            sample_fingerprint,
            domain="disk",
        )

        # Should have at least one disk precondition
        disk_preconds = [p for p in preconds if "disk" in p.expression]
        assert len(disk_preconds) > 0

    def test_infer_oom_preconditions(self, sample_snapshot, sample_fingerprint):
        """Test inferring OOM-related preconditions."""
        preconds = infer_preconditions(
            sample_snapshot,
            sample_fingerprint,
            domain="oom",
        )

        # Should have OOM count precondition
        oom_preconds = [p for p in preconds if "oom" in p.expression]
        assert len(oom_preconds) > 0

    def test_infer_docker_preconditions(self, sample_snapshot, sample_fingerprint):
        """Test inferring docker-related preconditions."""
        preconds = infer_preconditions(
            sample_snapshot,
            sample_fingerprint,
            domain="docker",
        )

        # Should have docker precondition if exited containers
        docker_preconds = [p for p in preconds if "docker" in p.expression]
        assert len(docker_preconds) > 0
