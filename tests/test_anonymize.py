"""Tests for incident anonymization.

Tests the three-tier classification system:
- REDACT: Sensitive data is removed or hashed
- GENERALIZE: Data is normalized to categories
- PRESERVE: Safe data is kept intact
"""

from __future__ import annotations

from datetime import datetime

import pytest

from elle.daemon.incidents.anonymize import (
    AnonymizationContext,
    AnonymizedIncidentReport,
    IncidentAnonymizer,
    anonymize_incident,
    set_anon_secret,
)
from elle.daemon.incidents.models import (
    Fingerprint,
    IncidentAction,
    IncidentReport,
)


@pytest.fixture(autouse=True)
def reset_secret():
    """Reset anonymization secret before each test for deterministic results."""
    set_anon_secret(b"test-secret-for-deterministic-hashes")
    yield
    set_anon_secret(None)


# =============================================================================
# AnonymizationContext Tests
# =============================================================================


class TestAnonymizationContext:
    """Tests for AnonymizationContext."""

    def test_hostname_anonymization(self):
        """Hostnames should be consistently anonymized."""
        ctx = AnonymizationContext(secret=b"test")

        anon1 = ctx.anonymize_hostname("prod-web-01")
        anon2 = ctx.anonymize_hostname("prod-web-01")
        anon3 = ctx.anonymize_hostname("staging-web-01")

        # Same input -> same output
        assert anon1 == anon2
        # Different input -> different output
        assert anon1 != anon3
        # Has expected prefix
        assert anon1.startswith("anon-")
        # Original hostname not in output
        assert "prod-web-01" not in anon1

    def test_hostname_empty(self):
        """Empty hostname should return empty string."""
        ctx = AnonymizationContext(secret=b"test")
        assert ctx.anonymize_hostname("") == ""

    def test_service_name_known_preserved(self):
        """Well-known services should be preserved."""
        ctx = AnonymizationContext(secret=b"test")

        for service in ["nginx", "nginx.service", "postgresql", "docker", "sshd"]:
            anon = ctx.anonymize_service_name(service)
            assert anon == service, f"Known service {service} should be preserved"

    def test_service_name_custom_anonymized(self):
        """Custom services should be anonymized."""
        ctx = AnonymizationContext(secret=b"test")

        anon1 = ctx.anonymize_service_name("my-custom-app.service")
        anon2 = ctx.anonymize_service_name("my-custom-app.service")
        anon3 = ctx.anonymize_service_name("another-app.service")

        # Same input -> same output
        assert anon1 == anon2
        # Different input -> different output
        assert anon1 != anon3
        # Has expected prefix
        assert anon1.startswith("svc-")
        # Original name not in output
        assert "my-custom-app" not in anon1

    def test_path_generalization_known_roots(self):
        """Paths under known roots should be generalized."""
        ctx = AnonymizationContext(secret=b"test")

        # Config paths keep first two levels (preserves service name like nginx)
        assert ctx.anonymize_path("/etc/nginx/nginx.conf") == "/etc/nginx/[config]"
        assert ctx.anonymize_path("/var/log/app.log") == "/var/log/[config]"

        # Root paths normalize to categories
        assert ctx.anonymize_path("/home") == "home/[path]"
        assert ctx.anonymize_path("/tmp") == "tmp/[path]"
        assert ctx.anonymize_path("/etc") == "etc/[path]"
        assert ctx.anonymize_path("/var") == "var/[path]"

        # Unknown paths get hashed
        assert ctx.anonymize_path("/custom/path").startswith("path-")

    def test_path_generalization_unknown(self):
        """Unknown paths should be hashed."""
        ctx = AnonymizationContext(secret=b"test")

        anon = ctx.anonymize_path("/custom/special/path")
        # Should have path prefix
        assert anon.startswith("path-")

    def test_interface_anonymization(self):
        """Network interfaces should be anonymized with type preserved."""
        ctx = AnonymizationContext(secret=b"test")

        # Type prefix preserved, rest anonymized
        assert ctx.anonymize_interface("eth0") == "ethN"
        assert ctx.anonymize_interface("ens192") == "ensN"
        assert ctx.anonymize_interface("wlan0") == "wlanN"

        # Loopback preserved
        assert ctx.anonymize_interface("lo") == "lo"

        # Unknown interfaces get generic name
        anon1 = ctx.anonymize_interface("custom0")
        anon2 = ctx.anonymize_interface("custom1")
        assert anon1.startswith("if")
        assert anon1 != anon2

    def test_device_anonymization(self):
        """Device paths should be anonymized."""
        ctx = AnonymizationContext(secret=b"test")

        anon1 = ctx.anonymize_device("/dev/sda1")
        anon2 = ctx.anonymize_device("/dev/sda1")
        anon3 = ctx.anonymize_device("/dev/nvme0n1p1")

        # Same input -> same output
        assert anon1 == anon2
        # Different devices get different indices
        assert anon1 != anon3
        # Format check
        assert anon1 == "disk-0"
        assert anon3 == "disk-1"

    def test_user_anonymization_system_users(self):
        """System users should be preserved."""
        ctx = AnonymizationContext(secret=b"test")

        for user in ["root", "nobody", "www-data", "nginx", "postgres"]:
            assert ctx.anonymize_user(user) == user

    def test_user_anonymization_custom_users(self):
        """Custom users should be anonymized."""
        ctx = AnonymizationContext(secret=b"test")

        anon = ctx.anonymize_user("john")
        assert anon.startswith("user-")
        assert "john" not in anon


# =============================================================================
# IncidentAnonymizer Tests
# =============================================================================


class TestIncidentAnonymizer:
    """Tests for IncidentAnonymizer."""

    def test_timestamp_generalization(self):
        """Timestamps should be rounded to the hour."""
        anonymizer = IncidentAnonymizer(secret=b"test")
        dt = datetime(2024, 3, 15, 14, 32, 45, 123456)

        result = anonymizer._generalize_timestamp(dt)

        assert result.year == 2024
        assert result.month == 3
        assert result.day == 15
        assert result.hour == 14
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0

    def test_basic_anonymization(self):
        """Basic incident anonymization preserves safe fields."""
        anonymizer = IncidentAnonymizer(secret=b"test")

        incident = IncidentReport(
            incident_id="test-123",
            created_at=datetime(2024, 3, 15, 14, 32, 45),
            updated_at=datetime(2024, 3, 15, 15, 0, 0),
            domain="service",
            severity="error",
            status="resolved",
            outcome="improved",
            title="nginx crashed on prod-web-01",
            summary="Service nginx failed at /home/admin/app",
            trigger_source="telemetry",
            trigger_command="systemctl restart nginx",
            confidence=0.85,
            time_to_mitigate_sec=120,
            time_to_resolve_sec=300,
        )

        result = anonymizer.anonymize(incident)

        # PRESERVE: ID, domain, severity, status, outcome
        assert result.incident_id == "test-123"
        assert result.domain == "service"
        assert result.severity == "error"
        assert result.status == "resolved"
        assert result.outcome == "improved"
        assert result.trigger_source == "telemetry"
        assert result.confidence == 0.85
        assert result.time_to_mitigate_sec == 120
        assert result.time_to_resolve_sec == 300

        # GENERALIZE: timestamps rounded to hour
        assert result.created_at_hour.minute == 0
        assert result.created_at_hour.second == 0

        # REDACT: title, summary, trigger_command not in output
        assert not hasattr(result, "title")
        assert not hasattr(result, "summary")
        assert not hasattr(result, "trigger_command")

        # Metadata
        assert result.anonymization_version == "1.0"
        assert result.original_hash != ""

    def test_fingerprint_preserved(self):
        """Fingerprint metrics should be preserved."""
        anonymizer = IncidentAnonymizer(secret=b"test")

        fingerprint = Fingerprint(
            disk_pressure=0.95,
            mem_pressure=0.75,
            cpu_pressure=2.5,
            oom_count_1h=3,
            entities=("nginx.service", "/dev/sda1"),
        )

        incident = IncidentReport(
            incident_id="test-123",
            domain="disk",
            severity="error",
            title="Disk full",
            fingerprint=fingerprint,
        )

        result = anonymizer.anonymize(incident)

        # Fingerprint metrics preserved
        assert result.fingerprint.disk_pressure == 0.95
        assert result.fingerprint.mem_pressure == 0.75
        assert result.fingerprint.cpu_pressure == 2.5
        assert result.fingerprint.oom_count_1h == 3

    def test_telemetry_anonymization(self):
        """Telemetry snapshots should be anonymized correctly."""
        anonymizer = IncidentAnonymizer(secret=b"test")

        telemetry = {
            "hostname": "prod-web-01",
            "uptime_sec": 86400,
            "cpu": {
                "load_1m": 2.5,
                "load_5m": 2.0,
                "load_15m": 1.5,
                "psi_some_pct": 5.0,
            },
            "memory": {
                "total_mb": 16384,
                "available_mb": 4096,
                "psi_some_pct": 10.0,
            },
            "disk": {
                "io_wait_pct": 15.0,
                "psi_some_pct": 5.0,
                "disks": [
                    {"mount": "/", "used_pct": 85.0, "avail_gb": 50.0, "device": "/dev/sda1"},
                    {"mount": "/home", "used_pct": 60.0, "avail_gb": 200.0, "device": "/dev/sda2"},
                ],
            },
            "network": {
                "interfaces_up": 2,
                "interfaces_down": 0,
                "default_route_present": True,
                "dns_resolution_ok": True,
                "interfaces": [
                    {"name": "eth0", "state": "UP", "rx_errors": 0, "tx_errors": 0},
                    {"name": "docker0", "state": "UP", "rx_errors": 0, "tx_errors": 0},
                ],
            },
            "kernel": {
                "oom_events_1h": 2,
                "thermal_throttle_active": False,
            },
            "services": [
                {
                    "service": "nginx.service",
                    "pid": 1234,
                    "rss_mb": 256,
                    "active_state": "active",
                },
                {
                    "service": "my-custom-app.service",
                    "pid": 5678,
                    "rss_mb": 512,
                    "active_state": "active",
                },
            ],
        }

        incident = IncidentReport(
            incident_id="test-123",
            domain="service",
            severity="error",
            title="Service issue",
        )

        result = anonymizer.anonymize(incident, telemetry_pre=telemetry)

        # Check telemetry was anonymized
        assert result.telemetry_pre is not None
        pre = result.telemetry_pre

        # PRESERVE: metrics
        assert pre["cpu"]["load_1m"] == 2.5
        assert pre["memory"]["total_mb"] == 16384
        assert pre["kernel"]["oom_events_1h"] == 2
        assert pre["uptime_sec"] == 86400

        # REDACT: hostname not in output
        assert "hostname" not in pre

        # GENERALIZE: mount points
        assert pre["disk"]["disks"][0]["mount"] == "root"
        assert pre["disk"]["disks"][1]["mount"] == "home"

        # GENERALIZE: devices
        assert pre["disk"]["disks"][0]["device"] == "disk-0"

        # GENERALIZE: interfaces
        assert pre["network"]["interfaces"][0]["name"] == "ethN"

        # PRESERVE: known service names
        assert pre["services"][0]["service"] == "nginx.service"

        # REDACT: custom service names
        assert pre["services"][1]["service"].startswith("svc-")
        assert "my-custom-app" not in pre["services"][1]["service"]

        # REDACT: pid not in service data
        assert "pid" not in pre["services"][0]

    def test_action_summary_computation(self):
        """Action summary should correctly aggregate action data."""
        anonymizer = IncidentAnonymizer(secret=b"test")

        actions = [
            IncidentAction(
                incident_id="test-123",
                step_index=0,
                kind="shell",
                command="systemctl restart nginx",
                success=True,
                duration_ms=500,
            ),
            IncidentAction(
                incident_id="test-123",
                step_index=1,
                kind="capability",
                success=True,
                duration_ms=200,
            ),
            IncidentAction(
                incident_id="test-123",
                step_index=2,
                kind="verify",
                success=False,
                duration_ms=100,
            ),
            IncidentAction(
                incident_id="test-123",
                step_index=3,
                kind="shell",
                success=True,
                duration_ms=300,
            ),
        ]

        incident = IncidentReport(
            incident_id="test-123",
            domain="service",
            severity="error",
            title="Test",
        )

        result = anonymizer.anonymize(incident, actions=actions)

        summary = result.action_summary
        assert summary.total_actions == 4
        assert summary.successful_actions == 3
        assert summary.failed_actions == 1
        assert summary.shell_count == 2
        assert summary.capability_count == 1
        assert summary.verify_count == 1
        assert summary.total_duration_ms == 1100
        assert summary.avg_duration_ms == 275

    def test_surface_hashes_preserved(self):
        """Surface hash keys and values should be preserved as-is."""
        anonymizer = IncidentAnonymizer(secret=b"test")

        surface_hashes = {
            "packages": "abc123",
            "scheduler": "def456",
            "service:nginx.service": "ghi789",
            "service:my-custom-app.service": "jkl012",
            "config:/etc/nginx/nginx.conf": "mno345",
            "config:/home/admin/app/config.yml": "pqr678",
        }

        incident = IncidentReport(
            incident_id="test-123",
            domain="service",
            severity="error",
            title="Test",
        )

        result = anonymizer.anonymize(incident, surface_hashes_pre=surface_hashes)

        assert result.surface_hashes_pre is not None
        hashes = result.surface_hashes_pre

        # All keys and values preserved exactly (hashes are one-way, keys are public patterns)
        assert hashes["packages"] == "abc123"
        assert hashes["scheduler"] == "def456"
        assert hashes["service:nginx.service"] == "ghi789"
        assert hashes["service:my-custom-app.service"] == "jkl012"
        assert hashes["config:/etc/nginx/nginx.conf"] == "mno345"
        assert hashes["config:/home/admin/app/config.yml"] == "pqr678"

        # Verify it's a copy, not the original
        assert hashes is not surface_hashes

    def test_surface_drift_computation(self):
        """Surface drift should be computed from pre/post hashes."""
        anonymizer = IncidentAnonymizer(secret=b"test")

        hashes_pre = {
            "packages": "abc123",
            "scheduler": "def456",
        }
        hashes_post = {
            "packages": "abc123",  # Same
            "scheduler": "xyz789",  # Changed
        }

        incident = IncidentReport(
            incident_id="test-123",
            domain="service",
            severity="error",
            title="Test",
        )

        result = anonymizer.anonymize(
            incident,
            surface_hashes_pre=hashes_pre,
            surface_hashes_post=hashes_post,
        )

        assert result.surface_drift["packages"] is False  # No change
        assert result.surface_drift["scheduler"] is True  # Changed


# =============================================================================
# Convenience Function Tests
# =============================================================================


class TestAnonymizeIncident:
    """Tests for the anonymize_incident convenience function."""

    def test_anonymize_incident_function(self):
        """The convenience function should work correctly."""
        incident = IncidentReport(
            incident_id="test-123",
            domain="oom",
            severity="critical",
            title="OOM killer activated",
        )

        result = anonymize_incident(incident)

        assert isinstance(result, AnonymizedIncidentReport)
        assert result.incident_id == "test-123"
        assert result.domain == "oom"
        assert result.severity == "critical"


# =============================================================================
# Round-trip Consistency Tests
# =============================================================================


class TestRoundTripConsistency:
    """Tests for consistent anonymization across multiple calls."""

    def test_same_incident_same_output(self):
        """Same incident should produce identical anonymized output."""
        incident = IncidentReport(
            incident_id="test-123",
            domain="service",
            severity="error",
            title="nginx crashed on prod-web-01",
            fingerprint=Fingerprint(
                disk_pressure=0.5,
                mem_pressure=0.3,
            ),
        )

        result1 = anonymize_incident(incident)
        result2 = anonymize_incident(incident)

        assert result1.incident_id == result2.incident_id
        assert result1.original_hash == result2.original_hash
        assert result1.domain == result2.domain
        assert result1.fingerprint.disk_pressure == result2.fingerprint.disk_pressure

    def test_different_incidents_different_hashes(self):
        """Different incidents should have different original hashes."""
        incident1 = IncidentReport(
            incident_id="test-123",
            domain="service",
            severity="error",
            title="Test 1",
        )
        incident2 = IncidentReport(
            incident_id="test-456",
            domain="service",
            severity="error",
            title="Test 2",
        )

        result1 = anonymize_incident(incident1)
        result2 = anonymize_incident(incident2)

        assert result1.original_hash != result2.original_hash


# =============================================================================
# Security Tests
# =============================================================================


class TestSecurity:
    """Tests for security properties of anonymization."""

    def test_sensitive_data_not_in_output(self):
        """Sensitive data should not appear in anonymized output."""
        incident = IncidentReport(
            incident_id="test-123",
            domain="service",
            severity="error",
            title="nginx crashed on prod-web-01.example.com",
            summary="Service nginx failed at /home/admin/secret-app with API_KEY=sk-12345",
            trigger_command="curl -H 'Authorization: Bearer secret-token' https://api.example.com",
            log_snippets=(
                "ERROR: Connection failed for user john@example.com",
                "Password: hunter2",
            ),
        )

        result = anonymize_incident(incident)

        # Convert to dict/string for searching
        output = str(result.model_dump())

        # Check that sensitive values don't appear
        sensitive_values = [
            "prod-web-01",
            "example.com",
            "/home/admin",
            "secret-app",
            "API_KEY",
            "sk-12345",
            "secret-token",
            "john@example.com",
            "hunter2",
        ]

        for value in sensitive_values:
            assert value not in output, f"Sensitive value '{value}' found in output"

    def test_telemetry_no_hostnames(self):
        """Telemetry should not contain hostnames."""
        telemetry = {
            "hostname": "secret-server.internal.corp",
            "cpu": {"load_1m": 1.0},
        }

        incident = IncidentReport(
            incident_id="test-123",
            domain="service",
            severity="error",
            title="Test",
        )

        result = anonymize_incident(incident, telemetry_pre=telemetry)

        output = str(result.model_dump())
        assert "secret-server" not in output
        assert "internal.corp" not in output
