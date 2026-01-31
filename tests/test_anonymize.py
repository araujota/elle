"""Tests for incident anonymization.

Tests the three-tier classification system:
- REDACT: Sensitive data is removed or hashed
- GENERALIZE: Data is normalized to categories
- PRESERVE: Safe data is kept intact
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import pytest

from elle.daemon.incidents.anonymize import (
    KNOWN_SERVICES,
    AnonymizationContext,
    AnonymizedIncidentReport,
    IncidentAnonymizer,
    set_anon_secret,
)
from elle.daemon.incidents.models import (
    Fingerprint,
    IncidentAction,
    IncidentReport,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_secret():
    """Reset anonymization secret before each test for deterministic results."""
    set_anon_secret(b"test-secret")
    yield
    set_anon_secret(None)


def _make_incident(**overrides) -> IncidentReport:
    """Build an IncidentReport with sensible defaults."""
    defaults = {
        "incident_id": "inc-001",
        "created_at": datetime(2024, 6, 15, 10, 30, 45, 123456),
        "updated_at": datetime(2024, 6, 15, 11, 45, 30, 654321),
        "domain": "service",
        "severity": "warning",
        "status": "open",
        "outcome": "unknown",
        "title": "Test incident",
        "trigger_source": "manual",
        "confidence": 0.0,
    }
    defaults.update(overrides)
    return IncidentReport(**defaults)


def _make_action(
    incident_id: str = "inc-001",
    step_index: int = 0,
    kind: str = "shell",
    success: bool = True,
    duration_ms: int | None = 100,
) -> IncidentAction:
    """Build an IncidentAction with sensible defaults."""
    return IncidentAction(
        incident_id=incident_id,
        step_index=step_index,
        kind=kind,
        success=success,
        duration_ms=duration_ms,
    )


# =============================================================================
# TestRedaction
# =============================================================================


class TestRedaction:
    """Tests for the REDACT tier -- sensitive values are hashed or removed."""

    def test_hostname_anonymized(self):
        """Hostname gets HMAC-hashed with 'anon-' prefix."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_hostname("prod-web-01")
        assert result.startswith("anon-")
        assert "prod-web-01" not in result

    def test_ipv4_not_in_output(self):
        """If hostname contains an IP-like string, it still gets anonymized."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_hostname("192.168.1.100")
        assert result.startswith("anon-")
        assert "192.168.1.100" not in result

    def test_username_anonymized(self):
        """Non-system users get 'user-' prefix hash."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_user("tyler")
        assert result.startswith("user-")
        assert "tyler" not in result

    def test_system_users_preserved(self):
        """root, nobody, www-data, nginx, postgres, mysql, redis are preserved."""
        ctx = AnonymizationContext(secret=b"test-secret")
        for user in ("root", "nobody", "www-data", "nginx", "postgres", "mysql", "redis"):
            assert ctx.anonymize_user(user) == user

    def test_file_paths_anonymized(self):
        """Paths with user segments get generalized."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_path("/home/tyler/myapp/config.yml")
        assert "tyler" not in result
        assert result == "home/[path]"

    def test_etc_paths_keep_structure(self):
        """/etc/nginx/... keeps first two levels then [config]."""
        ctx = AnonymizationContext(secret=b"test-secret")
        assert ctx.anonymize_path("/etc/nginx/nginx.conf") == "/etc/nginx/[config]"
        assert ctx.anonymize_path("/etc/ssh/sshd_config") == "/etc/ssh/[config]"

    def test_var_paths_keep_structure(self):
        """/var/log/... keeps first two levels then [config]."""
        ctx = AnonymizationContext(secret=b"test-secret")
        assert ctx.anonymize_path("/var/log/syslog") == "/var/log/[config]"
        assert ctx.anonymize_path("/var/lib/elle/elle.db") == "/var/lib/[config]"

    def test_unknown_paths_hashed(self):
        """Non-standard absolute paths get 'path-' prefix hash."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_path("/custom/secret/data")
        assert result.startswith("path-")
        assert "secret" not in result

    def test_service_names_anonymized(self):
        """Unknown services get 'svc-' prefix hash."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_service_name("my-proprietary-app")
        assert result.startswith("svc-")
        assert "my-proprietary-app" not in result

    def test_device_names_anonymized(self):
        """Devices get 'disk-N' naming."""
        ctx = AnonymizationContext(secret=b"test-secret")
        assert ctx.anonymize_device("/dev/sda1") == "disk-0"
        assert ctx.anonymize_device("/dev/nvme0n1p1") == "disk-1"
        # Same device repeated returns same value
        assert ctx.anonymize_device("/dev/sda1") == "disk-0"


# =============================================================================
# TestGeneralization
# =============================================================================


class TestGeneralization:
    """Tests for the GENERALIZE tier -- values normalized to categories."""

    def test_mount_point_root(self):
        """'/' maps to 'root'."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        assert anonymizer._generalize_mount_point("/") == "root"

    def test_mount_point_home(self):
        """'/home' maps to 'home'."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        assert anonymizer._generalize_mount_point("/home") == "home"

    def test_mount_point_var(self):
        """'/var' maps to 'var'."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        assert anonymizer._generalize_mount_point("/var") == "var"

    def test_mount_point_unknown(self):
        """Unknown mount maps to 'other'."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        assert anonymizer._generalize_mount_point("/data/warehouse") == "other"

    def test_timestamp_rounded_to_hour(self):
        """minute/second/microsecond all zeroed."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        dt = datetime(2024, 6, 15, 10, 30, 45, 123456)
        result = anonymizer._generalize_timestamp(dt)
        assert result == datetime(2024, 6, 15, 10, 0, 0, 0)
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0

    def test_interface_eth_generalized(self):
        """eth0 becomes 'ethN'."""
        ctx = AnonymizationContext(secret=b"test-secret")
        assert ctx.anonymize_interface("eth0") == "ethN"

    def test_interface_ens_generalized(self):
        """ens192 becomes 'ensN'."""
        ctx = AnonymizationContext(secret=b"test-secret")
        assert ctx.anonymize_interface("ens192") == "ensN"

    def test_interface_lo_preserved(self):
        """lo and localhost are preserved."""
        ctx = AnonymizationContext(secret=b"test-secret")
        assert ctx.anonymize_interface("lo") == "lo"
        assert ctx.anonymize_interface("localhost") == "localhost"


# =============================================================================
# TestPreservation
# =============================================================================


class TestPreservation:
    """Tests for the PRESERVE tier -- safe data kept intact."""

    def test_known_services_preserved(self):
        """nginx, postgresql, docker, ssh etc preserved as-is."""
        ctx = AnonymizationContext(secret=b"test-secret")
        for svc in ("nginx", "postgresql", "docker", "ssh", "sshd", "redis", "mysql"):
            assert ctx.anonymize_service_name(svc) == svc

    def test_known_services_with_suffix(self):
        """nginx.service also preserved."""
        ctx = AnonymizationContext(secret=b"test-secret")
        assert ctx.anonymize_service_name("nginx.service") == "nginx.service"
        assert ctx.anonymize_service_name("docker.service") == "docker.service"

    def test_fingerprint_preserved(self):
        """Fingerprint model passed through unchanged."""
        fp = Fingerprint(disk_pressure=0.92, mem_pressure=0.65, oom_count_1h=5)
        incident = _make_incident(fingerprint=fp)
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(incident)

        assert result.fingerprint.disk_pressure == 0.92
        assert result.fingerprint.mem_pressure == 0.65
        assert result.fingerprint.oom_count_1h == 5

    def test_domain_severity_status_preserved(self):
        """Enum fields domain, severity, status preserved."""
        incident = _make_incident(domain="net", severity="critical", status="resolved")
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(incident)

        assert result.domain == "net"
        assert result.severity == "critical"
        assert result.status == "resolved"

    def test_confidence_preserved(self):
        """Confidence float preserved."""
        incident = _make_incident(confidence=0.87)
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(incident)
        assert result.confidence == 0.87

    def test_outcome_preserved(self):
        """Outcome enum preserved."""
        incident = _make_incident(outcome="improved")
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(incident)
        assert result.outcome == "improved"


# =============================================================================
# TestAnonymizeIncidentReport
# =============================================================================


class TestAnonymizeIncidentReport:
    """Tests for full incident anonymization via IncidentAnonymizer.anonymize."""

    def test_full_incident_anonymized(self):
        """Complete incident with all fields is anonymized correctly."""
        fp = Fingerprint(disk_pressure=0.8, mem_pressure=0.5)
        incident = _make_incident(
            incident_id="full-001",
            domain="disk",
            severity="error",
            status="mitigated",
            outcome="improved",
            trigger_source="telemetry",
            confidence=0.75,
            fingerprint=fp,
            time_to_mitigate_sec=60,
            time_to_resolve_sec=180,
        )
        actions = [
            _make_action(incident_id="full-001", step_index=0, kind="shell", duration_ms=200),
            _make_action(incident_id="full-001", step_index=1, kind="verify", duration_ms=100),
        ]
        hashes_pre = {"packages": "aaa", "service:nginx.service": "bbb"}
        hashes_post = {"packages": "aaa", "service:nginx.service": "ccc"}

        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(
            incident,
            actions=actions,
            surface_hashes_pre=hashes_pre,
            surface_hashes_post=hashes_post,
        )

        assert isinstance(result, AnonymizedIncidentReport)
        assert result.incident_id == "full-001"
        assert result.domain == "disk"
        assert result.severity == "error"
        assert result.confidence == 0.75
        assert result.fingerprint.disk_pressure == 0.8
        assert result.action_summary.total_actions == 2
        assert result.surface_hashes_pre == hashes_pre
        assert result.surface_drift["service:nginx.service"] is True
        assert result.time_to_mitigate_sec == 60
        assert result.time_to_resolve_sec == 180
        assert result.anonymization_version == "1.0"

    def test_empty_incident(self):
        """Minimal incident with defaults is anonymized without error."""
        incident = _make_incident()
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(incident)

        assert isinstance(result, AnonymizedIncidentReport)
        assert result.incident_id == "inc-001"
        assert result.domain == "service"
        assert result.action_summary.total_actions == 0
        assert result.surface_hashes_pre is None
        assert result.surface_hashes_post is None

    def test_incident_with_no_actions(self):
        """Empty action list produces a default ActionSummary."""
        incident = _make_incident()
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(incident, actions=[])

        summary = result.action_summary
        assert summary.total_actions == 0
        assert summary.successful_actions == 0
        assert summary.failed_actions == 0
        assert summary.shell_count == 0
        assert summary.total_duration_ms == 0
        assert summary.avg_duration_ms == 0

    def test_incident_with_actions(self):
        """Actions produce correct ActionSummary counts."""
        actions = [
            _make_action(step_index=0, kind="shell", success=True, duration_ms=100),
            _make_action(step_index=1, kind="capability", success=True, duration_ms=200),
            _make_action(step_index=2, kind="edit", success=False, duration_ms=50),
        ]
        incident = _make_incident()
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(incident, actions=actions)

        summary = result.action_summary
        assert summary.total_actions == 3
        assert summary.successful_actions == 2
        assert summary.failed_actions == 1
        assert summary.shell_count == 1
        assert summary.capability_count == 1
        assert summary.edit_count == 1

    def test_surface_hashes_preserved(self):
        """surface_hashes_pre/post are passed through as-is."""
        hashes_pre = {"packages": "hash1", "config:/etc/nginx/nginx.conf": "hash2"}
        hashes_post = {"packages": "hash1", "config:/etc/nginx/nginx.conf": "hash3"}

        incident = _make_incident()
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(
            incident,
            surface_hashes_pre=hashes_pre,
            surface_hashes_post=hashes_post,
        )

        assert result.surface_hashes_pre == hashes_pre
        assert result.surface_hashes_post == hashes_post
        # Verify it is a copy, not the original reference
        assert result.surface_hashes_pre is not hashes_pre

    def test_surface_drift_computed(self):
        """Drift computed from pre/post hash differences."""
        hashes_pre = {"packages": "same", "scheduler": "old_val", "privilege": "pval"}
        hashes_post = {"packages": "same", "scheduler": "new_val", "privilege": "pval"}

        incident = _make_incident()
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(
            incident,
            surface_hashes_pre=hashes_pre,
            surface_hashes_post=hashes_post,
        )

        assert result.surface_drift["packages"] is False
        assert result.surface_drift["scheduler"] is True
        assert result.surface_drift["privilege"] is False

    def test_original_hash_computed(self):
        """SHA256 of incident_id:created_at."""
        created = datetime(2024, 6, 15, 10, 30, 45)
        incident = _make_incident(incident_id="hash-test", created_at=created)
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(incident)

        expected_content = f"hash-test:{created.isoformat()}"
        expected_hash = hashlib.sha256(expected_content.encode()).hexdigest()
        assert result.original_hash == expected_hash

    def test_detail_level_hashes_vs_detailed(self):
        """'hashes' excludes control surfaces, 'detailed' includes them."""
        incident = _make_incident()
        control_pre = {"services": [{"unit_name": "nginx", "enabled": True, "active_state": "active"}]}
        control_post = {"services": [{"unit_name": "nginx", "enabled": False, "active_state": "inactive"}]}

        anonymizer = IncidentAnonymizer(secret=b"test-secret")

        # hashes mode: no control surfaces in output
        result_hashes = anonymizer.anonymize(
            incident,
            control_surface_pre=control_pre,
            control_surface_post=control_post,
            detail_level="hashes",
        )
        assert result_hashes.detail_level == "hashes"
        assert result_hashes.control_surface_pre is None
        assert result_hashes.control_surface_post is None

        # detailed mode: control surfaces included (anonymized)
        result_detailed = anonymizer.anonymize(
            incident,
            control_surface_pre=control_pre,
            control_surface_post=control_post,
            detail_level="detailed",
        )
        assert result_detailed.detail_level == "detailed"
        assert result_detailed.control_surface_pre is not None
        assert result_detailed.control_surface_post is not None


# =============================================================================
# TestKnownServicesAllowlist
# =============================================================================


class TestKnownServicesAllowlist:
    """Tests for the KNOWN_SERVICES allowlist."""

    def test_all_known_services_preserved(self):
        """Every entry in KNOWN_SERVICES is preserved by anonymize_service_name."""
        ctx = AnonymizationContext(secret=b"test-secret")
        for svc in KNOWN_SERVICES:
            assert ctx.anonymize_service_name(svc) == svc, f"{svc} was not preserved"

    def test_unknown_service_redacted(self):
        """'my-custom-app' gets hashed."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_service_name("my-custom-app")
        assert result.startswith("svc-")
        assert "my-custom-app" not in result

    def test_case_sensitivity(self):
        """'NGINX' is NOT in known services (case sensitive)."""
        assert "NGINX" not in KNOWN_SERVICES
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_service_name("NGINX")
        # NGINX (all caps) is not known, so it should be hashed
        assert result.startswith("svc-")

    def test_service_suffix_handling(self):
        """'docker' and 'docker.service' both preserved."""
        ctx = AnonymizationContext(secret=b"test-secret")
        assert ctx.anonymize_service_name("docker") == "docker"
        assert ctx.anonymize_service_name("docker.service") == "docker.service"


# =============================================================================
# TestEdgeCases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases in anonymization."""

    def test_empty_strings(self):
        """Empty hostname/path/service/interface/device/user all return ''."""
        ctx = AnonymizationContext(secret=b"test-secret")
        assert ctx.anonymize_hostname("") == ""
        assert ctx.anonymize_path("") == ""
        assert ctx.anonymize_service_name("") == ""
        assert ctx.anonymize_interface("") == ""
        assert ctx.anonymize_device("") == ""
        assert ctx.anonymize_user("") == ""

    def test_consistency(self):
        """Same value anonymized twice gives same result within same context."""
        ctx = AnonymizationContext(secret=b"test-secret")

        hostname1 = ctx.anonymize_hostname("myhost")
        hostname2 = ctx.anonymize_hostname("myhost")
        assert hostname1 == hostname2

        path1 = ctx.anonymize_path("/custom/secret")
        path2 = ctx.anonymize_path("/custom/secret")
        assert path1 == path2

        svc1 = ctx.anonymize_service_name("my-app")
        svc2 = ctx.anonymize_service_name("my-app")
        assert svc1 == svc2

        user1 = ctx.anonymize_user("alice")
        user2 = ctx.anonymize_user("alice")
        assert user1 == user2

        dev1 = ctx.anonymize_device("/dev/sda")
        dev2 = ctx.anonymize_device("/dev/sda")
        assert dev1 == dev2

        iface1 = ctx.anonymize_interface("bond0")
        iface2 = ctx.anonymize_interface("bond0")
        assert iface1 == iface2

    def test_different_contexts_different_hashes(self):
        """Two AnonymizationContext with different secrets produce different hashes."""
        ctx_a = AnonymizationContext(secret=b"secret-a")
        ctx_b = AnonymizationContext(secret=b"secret-b")

        hostname_a = ctx_a.anonymize_hostname("prod-db")
        hostname_b = ctx_b.anonymize_hostname("prod-db")
        assert hostname_a != hostname_b

        svc_a = ctx_a.anonymize_service_name("custom-svc")
        svc_b = ctx_b.anonymize_service_name("custom-svc")
        assert svc_a != svc_b

        user_a = ctx_a.anonymize_user("alice")
        user_b = ctx_b.anonymize_user("alice")
        assert user_a != user_b

    def test_action_summary_with_mixed_kinds(self):
        """Actions with shell, capability, edit, verify, rollback, privileged, gui kinds all counted."""
        actions = [
            _make_action(step_index=0, kind="shell", success=True, duration_ms=10),
            _make_action(step_index=1, kind="capability", success=True, duration_ms=20),
            _make_action(step_index=2, kind="edit", success=True, duration_ms=30),
            _make_action(step_index=3, kind="verify", success=False, duration_ms=40),
            _make_action(step_index=4, kind="rollback", success=True, duration_ms=50),
            _make_action(step_index=5, kind="privileged", success=True, duration_ms=60),
            _make_action(step_index=6, kind="gui", success=False, duration_ms=70),
        ]
        incident = _make_incident()
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(incident, actions=actions)

        s = result.action_summary
        assert s.total_actions == 7
        assert s.successful_actions == 5
        assert s.failed_actions == 2
        assert s.shell_count == 1
        assert s.capability_count == 1
        assert s.edit_count == 1
        assert s.verify_count == 1
        assert s.rollback_count == 1
        assert s.privileged_count == 1
        assert s.gui_count == 1


# =============================================================================
# TestActionSummary
# =============================================================================


class TestActionSummary:
    """Tests for ActionSummary timing computations."""

    def test_action_summary_timing(self):
        """Durations summed and averaged correctly."""
        actions = [
            _make_action(step_index=0, kind="shell", duration_ms=100),
            _make_action(step_index=1, kind="shell", duration_ms=200),
            _make_action(step_index=2, kind="shell", duration_ms=300),
        ]
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        summary = anonymizer._compute_action_summary(actions)

        assert summary.total_duration_ms == 600
        assert summary.avg_duration_ms == 200  # 600 // 3

    def test_action_summary_empty_durations(self):
        """Actions with None duration_ms are handled -- they are excluded from timing."""
        actions = [
            _make_action(step_index=0, kind="shell", duration_ms=None),
            _make_action(step_index=1, kind="shell", duration_ms=400),
            _make_action(step_index=2, kind="shell", duration_ms=None),
        ]
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        summary = anonymizer._compute_action_summary(actions)

        assert summary.total_actions == 3
        # Only one action has a non-None duration
        assert summary.total_duration_ms == 400
        assert summary.avg_duration_ms == 400  # 400 // 1


# =============================================================================
# TestAnonymizeTelemetry
# =============================================================================


class TestAnonymizeTelemetry:
    """Tests for _anonymize_telemetry -- REDACT/GENERALIZE/PRESERVE telemetry data."""

    def test_cpu_preserved(self):
        """CPU metrics are preserved as-is."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        telemetry = {"cpu": {"load_1m": 1.5, "load_5m": 1.2, "load_15m": 0.9}}
        result = anonymizer._anonymize_telemetry(telemetry, ctx)
        assert result["cpu"] == {"load_1m": 1.5, "load_5m": 1.2, "load_15m": 0.9}

    def test_memory_preserved(self):
        """Memory metrics are preserved as-is."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        telemetry = {"memory": {"total_mb": 16384, "available_mb": 8192, "used_pct": 50.0}}
        result = anonymizer._anonymize_telemetry(telemetry, ctx)
        assert result["memory"]["total_mb"] == 16384

    def test_disk_anonymized(self):
        """Disk mount points generalized, devices anonymized."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        telemetry = {
            "disk": {
                "io_wait_pct": 5.0,
                "io_errors_1h": 0,
                "psi_some_pct": 1.0,
                "psi_full_pct": 0.5,
                "disks": [
                    {
                        "mount": "/",
                        "used_pct": 80.0,
                        "avail_gb": 50.0,
                        "device": "/dev/sda1",
                        "read_only": False,
                    },
                    {
                        "mount": "/home",
                        "used_pct": 60.0,
                        "avail_gb": 200.0,
                        "device": "/dev/sdb1",
                        "read_only": False,
                    },
                ],
            }
        }
        result = anonymizer._anonymize_telemetry(telemetry, ctx)
        disk = result["disk"]
        assert disk["io_wait_pct"] == 5.0
        assert len(disk["disks"]) == 2
        assert disk["disks"][0]["mount"] == "root"
        assert disk["disks"][1]["mount"] == "home"
        assert disk["disks"][0]["device"].startswith("disk-")
        assert disk["disks"][0]["used_pct"] == 80.0

    def test_network_anonymized(self):
        """Network interfaces anonymized, metrics preserved."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        telemetry = {
            "network": {
                "interfaces_up": 2,
                "interfaces_down": 0,
                "default_route_present": True,
                "dns_resolution_ok": True,
                "interfaces": [
                    {"name": "eth0", "state": "UP", "rx_errors": 0, "tx_errors": 0},
                    {"name": "wlan0", "state": "UP", "rx_errors": 1, "tx_errors": 0},
                ],
            }
        }
        result = anonymizer._anonymize_telemetry(telemetry, ctx)
        net = result["network"]
        assert net["interfaces_up"] == 2
        assert net["dns_resolution_ok"] is True
        assert net["interfaces"][0]["name"] == "ethN"
        assert net["interfaces"][1]["name"] == "wlanN"
        assert net["interfaces"][0]["state"] == "UP"

    def test_kernel_preserved(self):
        """Kernel signals are preserved as-is."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        telemetry = {"kernel": {"oom_kills": 3, "panics": 0}}
        result = anonymizer._anonymize_telemetry(telemetry, ctx)
        assert result["kernel"] == {"oom_kills": 3, "panics": 0}

    def test_services_anonymized(self):
        """Service names anonymized for unknown services, preserved for known."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        telemetry = {
            "services": [
                {
                    "service": "nginx",
                    "rss_mb": 128,
                    "cpu_pct": 2.5,
                    "active_state": "active",
                    "restart_count": 0,
                    "last_exit_code": 0,
                    "cgroup_memory_limit_mb": 512,
                },
                {
                    "service": "my-custom-app",
                    "rss_mb": 256,
                    "cpu_pct": 10.0,
                    "active_state": "active",
                    "restart_count": 3,
                    "last_exit_code": None,
                    "cgroup_memory_limit_mb": None,
                },
            ]
        }
        result = anonymizer._anonymize_telemetry(telemetry, ctx)
        assert result["services"][0]["service"] == "nginx"
        assert result["services"][1]["service"].startswith("svc-")
        assert result["services"][0]["rss_mb"] == 128
        assert result["services"][1]["restart_count"] == 3

    def test_uptime_preserved(self):
        """Uptime seconds are preserved."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        telemetry = {"uptime_sec": 86400}
        result = anonymizer._anonymize_telemetry(telemetry, ctx)
        assert result["uptime_sec"] == 86400

    def test_pydantic_model_telemetry(self):
        """Telemetry passed as a Pydantic model is handled via model_dump()."""
        from unittest.mock import MagicMock

        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        mock_model = MagicMock()
        mock_model.model_dump.return_value = {"cpu": {"load": 1.0}, "uptime_sec": 100}

        result = anonymizer._anonymize_telemetry(mock_model, ctx)
        assert result["cpu"] == {"load": 1.0}
        assert result["uptime_sec"] == 100
        mock_model.model_dump.assert_called_once()

    def test_non_dict_non_model_returns_empty(self):
        """Non-dict, non-model telemetry returns empty dict."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        result = anonymizer._anonymize_telemetry("not a dict", ctx)
        assert result == {}

    def test_disk_empty_disks_list(self):
        """Disk section with no disks."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        telemetry = {"disk": {"io_wait_pct": 0.0, "disks": []}}
        result = anonymizer._anonymize_telemetry(telemetry, ctx)
        assert result["disk"]["disks"] == []

    def test_network_empty_interfaces(self):
        """Network section with no interfaces."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        telemetry = {"network": {"interfaces_up": 0, "interfaces": []}}
        result = anonymizer._anonymize_telemetry(telemetry, ctx)
        assert result["network"]["interfaces"] == []


# =============================================================================
# TestAnonymizeControlSurface
# =============================================================================


class TestAnonymizeControlSurface:
    """Tests for _anonymize_control_surface."""

    def test_services_anonymized(self):
        """Service unit_name anonymized, boolean/string fields preserved."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        surface = {
            "services": [
                {
                    "unit_name": "nginx",
                    "enabled": True,
                    "active_state": "active",
                    "restart_policy": "always",
                    "cpu_quota": "50%",
                    "memory_max": "512M",
                    "dropin_count": 2,
                    "effective_hash": "abc123",
                }
            ]
        }
        result = anonymizer._anonymize_control_surface(surface, ctx)
        assert result["services"][0]["unit_name"] == "nginx"  # known service
        assert result["services"][0]["enabled"] is True
        assert result["services"][0]["restart_policy"] == "always"
        assert result["services"][0]["effective_hash"] == "abc123"

    def test_configs_anonymized(self):
        """Config paths anonymized, mode and sha256 preserved."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        surface = {
            "configs": [
                {
                    "path": "/etc/nginx/nginx.conf",
                    "mode": "0644",
                    "sha256": "deadbeef123",
                    "fragment_count": 3,
                }
            ]
        }
        result = anonymizer._anonymize_control_surface(surface, ctx)
        assert result["configs"][0]["path"] == "/etc/nginx/[config]"
        assert result["configs"][0]["mode"] == "0644"
        assert result["configs"][0]["sha256"] == "deadbeef123"

    def test_packages_preserved(self):
        """Packages are public, preserved as-is."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        surface = {"packages": {"packages": [{"name": "nginx", "version": "1.22"}]}}
        result = anonymizer._anonymize_control_surface(surface, ctx)
        assert result["packages"] == surface["packages"]

    def test_scheduler_preserved(self):
        """Scheduler data preserved."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        surface = {"scheduler": {"cron_hash": "abc"}}
        result = anonymizer._anonymize_control_surface(surface, ctx)
        assert result["scheduler"] == {"cron_hash": "abc"}

    def test_privilege_fields(self):
        """Privilege section preserves hashes and modes."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        surface = {
            "privilege": {
                "sudoers_hash": "aaa",
                "groups_hash": "bbb",
                "polkit_rules_hash": "ccc",
                "selinux_mode": "enforcing",
                "apparmor_mode": "enforce",
            }
        }
        result = anonymizer._anonymize_control_surface(surface, ctx)
        assert result["privilege"]["sudoers_hash"] == "aaa"
        assert result["privilege"]["selinux_mode"] == "enforcing"
        assert result["privilege"]["apparmor_mode"] == "enforce"

    def test_network_control_preserved(self):
        """network_control preserved."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        surface = {"network_control": {"firewall": "ufw", "dns": "systemd-resolved"}}
        result = anonymizer._anonymize_control_surface(surface, ctx)
        assert result["network_control"]["firewall"] == "ufw"

    def test_kernel_policy_preserved(self):
        """kernel_policy preserved."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        surface = {"kernel_policy": {"version": "5.15.0", "sysctl_hash": "xyz"}}
        result = anonymizer._anonymize_control_surface(surface, ctx)
        assert result["kernel_policy"]["version"] == "5.15.0"

    def test_hardware_preserved(self):
        """hardware preserved."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        surface = {"hardware": {"cpu_model": "i9", "ram_gb": 32}}
        result = anonymizer._anonymize_control_surface(surface, ctx)
        assert result["hardware"]["cpu_model"] == "i9"

    def test_pydantic_model_surface(self):
        """Control surface as Pydantic model uses model_dump()."""
        from unittest.mock import MagicMock

        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        mock_model = MagicMock()
        mock_model.model_dump.return_value = {"packages": [{"name": "foo"}]}
        result = anonymizer._anonymize_control_surface(mock_model, ctx)
        assert result["packages"] == [{"name": "foo"}]

    def test_non_dict_non_model_returns_empty(self):
        """Non-dict, non-model surface returns empty dict."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        result = anonymizer._anonymize_control_surface(42, ctx)
        assert result == {}

    def test_unknown_service_anonymized(self):
        """Custom services get svc- prefix hash."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        surface = {
            "services": [
                {
                    "unit_name": "my-private-app",
                    "enabled": True,
                    "active_state": "active",
                    "restart_policy": "",
                    "cpu_quota": "",
                    "memory_max": "",
                    "dropin_count": 0,
                    "effective_hash": "",
                }
            ]
        }
        result = anonymizer._anonymize_control_surface(surface, ctx)
        assert result["services"][0]["unit_name"].startswith("svc-")


# =============================================================================
# TestDriftExplanations
# =============================================================================


class TestDriftExplanations:
    """Tests for _compute_drift_explanations and _explain_* methods."""

    def test_service_added(self):
        """Service present in post but not pre."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"services": []}
        post = {"services": [{"unit_name": "nginx", "enabled": True, "active_state": "active"}]}
        drift = {"service:nginx": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert len(explanations) == 1
        assert "added" in explanations[0].explanation

    def test_service_removed(self):
        """Service present in pre but not post."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"services": [{"unit_name": "nginx", "enabled": True, "active_state": "active"}]}
        post = {"services": []}
        drift = {"service:nginx": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "removed" in explanations[0].explanation

    def test_service_not_in_either(self):
        """Service not found in pre or post."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"services": []}
        post = {"services": []}
        drift = {"service:ghost": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "changed" in explanations[0].explanation

    def test_service_enabled_changed(self):
        """Service enabled flag changed."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"services": [{"unit_name": "nginx", "enabled": True}]}
        post = {"services": [{"unit_name": "nginx", "enabled": False}]}
        drift = {"service:nginx": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "disabled" in explanations[0].explanation
        assert explanations[0].before_value is not None
        assert explanations[0].after_value is not None

    def test_service_restart_policy_changed(self):
        """Service restart policy changed."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"services": [{"unit_name": "nginx", "restart_policy": "no"}]}
        post = {"services": [{"unit_name": "nginx", "restart_policy": "always"}]}
        drift = {"service:nginx": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "restart policy" in explanations[0].explanation

    def test_service_memory_max_changed(self):
        """Service memory limit changed."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"services": [{"unit_name": "nginx", "memory_max": "256M"}]}
        post = {"services": [{"unit_name": "nginx", "memory_max": "512M"}]}
        drift = {"service:nginx": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "memory limit" in explanations[0].explanation

    def test_service_cpu_quota_changed(self):
        """Service CPU quota changed."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"services": [{"unit_name": "nginx", "cpu_quota": "50%"}]}
        post = {"services": [{"unit_name": "nginx", "cpu_quota": "100%"}]}
        drift = {"service:nginx": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "CPU quota" in explanations[0].explanation

    def test_service_no_specific_changes(self):
        """Service changed but no specific tracked field differs."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"services": [{"unit_name": "nginx", "active_state": "active"}]}
        post = {"services": [{"unit_name": "nginx", "active_state": "inactive"}]}
        drift = {"service:nginx": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "configuration changed" in explanations[0].explanation

    def test_config_created(self):
        """Config present in post but not pre."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"configs": []}
        post = {"configs": [{"path": "/etc/nginx/nginx.conf", "sha256": "abc", "mode": "0644"}]}
        drift = {"config:/etc/nginx/nginx.conf": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "created" in explanations[0].explanation

    def test_config_deleted(self):
        """Config present in pre but not post."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"configs": [{"path": "/etc/nginx/nginx.conf", "sha256": "abc", "mode": "0644"}]}
        post = {"configs": []}
        drift = {"config:/etc/nginx/nginx.conf": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "deleted" in explanations[0].explanation

    def test_config_not_in_either(self):
        """Config not found in pre or post."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"configs": []}
        post = {"configs": []}
        drift = {"config:/etc/ghost": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "changed" in explanations[0].explanation

    def test_config_contents_modified(self):
        """Config sha256 changed."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"configs": [{"path": "/etc/nginx/nginx.conf", "sha256": "aaa111bbb", "mode": "0644"}]}
        post = {"configs": [{"path": "/etc/nginx/nginx.conf", "sha256": "ccc222ddd", "mode": "0644"}]}
        drift = {"config:/etc/nginx/nginx.conf": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "contents modified" in explanations[0].explanation
        assert explanations[0].before_value is not None
        assert explanations[0].after_value is not None

    def test_config_mode_changed(self):
        """Config mode changed."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"configs": [{"path": "/etc/ssh/sshd_config", "sha256": "same", "mode": "0644"}]}
        post = {"configs": [{"path": "/etc/ssh/sshd_config", "sha256": "same", "mode": "0600"}]}
        drift = {"config:/etc/ssh/sshd_config": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "mode" in explanations[0].explanation

    def test_config_no_specific_changes(self):
        """Config changed but no specific tracked field differs."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"configs": [{"path": "/etc/hosts", "sha256": "same", "mode": "0644"}]}
        post = {"configs": [{"path": "/etc/hosts", "sha256": "same", "mode": "0644"}]}
        drift = {"config:/etc/hosts": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "changed" in explanations[0].explanation

    def test_packages_drift(self):
        """Package surface drift with added and removed packages."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"packages": {"packages": [{"name": "nginx"}, {"name": "curl"}]}}
        post = {"packages": {"packages": [{"name": "nginx"}, {"name": "wget"}]}}
        drift = {"packages": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "1 added" in explanations[0].explanation
        assert "1 removed" in explanations[0].explanation
        assert explanations[0].before_value is not None

    def test_packages_no_changes(self):
        """Package surface present but no package names changed."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {"packages": {"packages": [{"name": "nginx"}]}}
        post = {"packages": {"packages": [{"name": "nginx"}]}}
        drift = {"packages": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "Package state changed" in explanations[0].explanation

    def test_simple_drift_scheduler(self):
        """Scheduler drift gets simple explanation."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre = {}
        post = {}
        drift = {"scheduler": True}

        explanations = anonymizer._compute_drift_explanations(pre, post, drift, ctx)
        assert "Scheduler changed" in explanations[0].explanation

    def test_simple_drift_privilege(self):
        """Privilege drift gets simple explanation."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        drift = {"privilege": False}
        explanations = anonymizer._compute_drift_explanations({}, {}, drift, ctx)
        assert "Privilege configuration unchanged" in explanations[0].explanation

    def test_simple_drift_network_control(self):
        """Network control drift."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        drift = {"network_control": True}
        explanations = anonymizer._compute_drift_explanations({}, {}, drift, ctx)
        assert "Network control plane changed" in explanations[0].explanation

    def test_simple_drift_kernel_policy(self):
        """Kernel policy drift."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        drift = {"kernel_policy": True}
        explanations = anonymizer._compute_drift_explanations({}, {}, drift, ctx)
        assert "Kernel policy changed" in explanations[0].explanation

    def test_simple_drift_hardware(self):
        """Hardware drift."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        drift = {"hardware": False}
        explanations = anonymizer._compute_drift_explanations({}, {}, drift, ctx)
        assert "Hardware identity unchanged" in explanations[0].explanation

    def test_unknown_surface_key(self):
        """Unknown surface key falls through to default."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        drift = {"custom_thing": True}
        explanations = anonymizer._compute_drift_explanations({}, {}, drift, ctx)
        assert "custom_thing changed" in explanations[0].explanation

    def test_unknown_surface_key_unchanged(self):
        """Unknown surface key unchanged."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")
        drift = {"custom_thing": False}
        explanations = anonymizer._compute_drift_explanations({}, {}, drift, ctx)
        assert "custom_thing unchanged" in explanations[0].explanation

    def test_drift_with_pydantic_models(self):
        """Control surfaces as Pydantic models use model_dump()."""
        from unittest.mock import MagicMock

        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        ctx = AnonymizationContext(secret=b"test-secret")

        pre_model = MagicMock()
        pre_model.model_dump.return_value = {"services": []}
        post_model = MagicMock()
        post_model.model_dump.return_value = {"services": [{"unit_name": "nginx"}]}
        drift = {"service:nginx": True}

        explanations = anonymizer._compute_drift_explanations(pre_model, post_model, drift, ctx)
        assert len(explanations) == 1


# =============================================================================
# TestAnonymizationContextPaths
# =============================================================================


class TestAnonymizationContextPaths:
    """Additional tests for path generalization edge cases."""

    def test_etc_two_levels_only(self):
        """'/etc' alone goes to 'etc/[path]'."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_path("/etc")
        assert result == "etc/[path]"

    def test_var_two_levels_only(self):
        """'/var' alone goes to 'var/[path]'."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_path("/var")
        assert result == "var/[path]"

    def test_usr_path(self):
        """'/usr/bin/foo' goes to 'usr/[path]'."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_path("/usr/bin/foo")
        assert result == "usr/[path]"

    def test_tmp_path(self):
        """'/tmp/...' goes to 'tmp/[path]'."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_path("/tmp/some-file")
        assert result == "tmp/[path]"

    def test_boot_path(self):
        """'/boot/...' goes to 'boot/[path]'."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_path("/boot/vmlinuz")
        assert result == "boot/[path]"

    def test_srv_path(self):
        """'/srv/...' goes to 'data/[path]'."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_path("/srv/myapp")
        assert result == "data/[path]"

    def test_opt_path(self):
        """'/opt/...' goes to 'opt/[path]'."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_path("/opt/something")
        assert result == "opt/[path]"

    def test_mnt_path(self):
        """'/mnt/...' goes to 'mnt/[path]'."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_path("/mnt/usb")
        assert result == "mnt/[path]"

    def test_media_path(self):
        """'/media/...' goes to 'media/[path]'."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_path("/media/cdrom")
        assert result == "media/[path]"

    def test_relative_path_hashed(self):
        """Relative paths (no leading /) are hashed."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_path("relative/path/file.txt")
        assert result.startswith("path-")

    def test_root_path(self):
        """'/' path generalization."""
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx.anonymize_path("/")
        # "/" splits to ["", ""], len(parts) > 1, root = "/", which matches "root"
        assert result == "root/[path]"

    def test_path_caching(self):
        """Same path returns same anonymized value."""
        ctx = AnonymizationContext(secret=b"test-secret")
        r1 = ctx.anonymize_path("/custom/something/deep")
        r2 = ctx.anonymize_path("/custom/something/deep")
        assert r1 == r2


# =============================================================================
# TestAnonymizationContextInterfaces
# =============================================================================


class TestAnonymizationContextInterfaces:
    """Additional tests for interface anonymization."""

    def test_enp_interface(self):
        """enp3s0 becomes 'enpN'."""
        ctx = AnonymizationContext(secret=b"test-secret")
        assert ctx.anonymize_interface("enp3s0") == "enpN"

    def test_wlp_interface(self):
        """wlp2s0 becomes 'wlpN'."""
        ctx = AnonymizationContext(secret=b"test-secret")
        assert ctx.anonymize_interface("wlp2s0") == "wlpN"

    def test_unknown_interface_indexed(self):
        """Unknown interface like bond0 gets indexed 'ifN'."""
        ctx = AnonymizationContext(secret=b"test-secret")
        r1 = ctx.anonymize_interface("bond0")
        r2 = ctx.anonymize_interface("br-docker0")
        assert r1 == "if0"
        assert r2 == "if1"

    def test_unknown_interface_cached(self):
        """Same unknown interface returns same index."""
        ctx = AnonymizationContext(secret=b"test-secret")
        r1 = ctx.anonymize_interface("bond0")
        r2 = ctx.anonymize_interface("bond0")
        assert r1 == r2 == "if0"


# =============================================================================
# TestAnonymizeIncidentConvenienceFunction
# =============================================================================


class TestAnonymizeIncidentConvenience:
    """Tests for the anonymize_incident() convenience function."""

    def test_basic_anonymization(self):
        """anonymize_incident() wraps IncidentAnonymizer.anonymize()."""
        from elle.daemon.incidents.anonymize import anonymize_incident

        incident = _make_incident()
        result = anonymize_incident(incident)
        assert isinstance(result, AnonymizedIncidentReport)
        assert result.incident_id == "inc-001"

    def test_with_all_params(self):
        """anonymize_incident() passes all parameters through."""
        from elle.daemon.incidents.anonymize import anonymize_incident

        incident = _make_incident()
        actions = [_make_action(kind="shell")]
        hashes_pre = {"a": "b"}
        hashes_post = {"a": "c"}
        control_pre = {"services": []}
        control_post = {"services": []}
        telemetry_pre = {"cpu": {}}
        telemetry_post = {"cpu": {}}

        result = anonymize_incident(
            incident=incident,
            actions=actions,
            telemetry_pre=telemetry_pre,
            telemetry_post=telemetry_post,
            surface_hashes_pre=hashes_pre,
            surface_hashes_post=hashes_post,
            control_surface_pre=control_pre,
            control_surface_post=control_post,
            detail_level="detailed",
        )
        assert result.detail_level == "detailed"
        assert result.action_summary.total_actions == 1
        assert result.control_surface_pre is not None

    def test_default_detail_level(self):
        """Default detail_level is 'hashes'."""
        from elle.daemon.incidents.anonymize import anonymize_incident

        incident = _make_incident()
        result = anonymize_incident(incident)
        assert result.detail_level == "hashes"


# =============================================================================
# TestAnonymizeSecretManagement
# =============================================================================


class TestAnonymizeSecretManagement:
    """Tests for _get_anon_secret and set_anon_secret."""

    def test_get_anon_secret_generates_when_none(self):
        """_get_anon_secret generates a secret when not set."""
        from elle.daemon.incidents.anonymize import _get_anon_secret

        set_anon_secret(None)  # Clear
        # Force clear the global
        import elle.daemon.incidents.anonymize as anon_module

        anon_module._ANON_SECRET = None
        secret = _get_anon_secret()
        assert isinstance(secret, bytes)
        assert len(secret) == 32

    def test_set_anon_secret_overrides(self):
        """set_anon_secret() overrides the global secret."""
        from elle.daemon.incidents.anonymize import _get_anon_secret

        set_anon_secret(b"custom-secret")
        assert _get_anon_secret() == b"custom-secret"

    def test_anon_context_uses_installation_secret(self):
        """When no secret provided, AnonymizationContext uses installation secret."""
        set_anon_secret(b"installation-level")
        ctx = AnonymizationContext()
        # Hashing should work
        result = ctx.anonymize_hostname("test-host")
        assert result.startswith("anon-")


# =============================================================================
# TestAnonymizedModels
# =============================================================================


class TestAnonymizedModels:
    """Tests for Pydantic model validation edge cases."""

    def test_action_summary_defaults(self):
        """ActionSummary with defaults."""
        from elle.daemon.incidents.anonymize import ActionSummary

        summary = ActionSummary()
        assert summary.total_actions == 0
        assert summary.successful_actions == 0
        assert summary.failed_actions == 0
        assert summary.shell_count == 0
        assert summary.total_duration_ms == 0
        assert summary.avg_duration_ms == 0

    def test_drift_explanation_defaults(self):
        """DriftExplanation with defaults."""
        from elle.daemon.incidents.anonymize import DriftExplanation

        de = DriftExplanation(surface="test", changed=True)
        assert de.surface == "test"
        assert de.changed is True
        assert de.explanation == ""
        assert de.before_value is None
        assert de.after_value is None

    def test_drift_explanation_with_values(self):
        """DriftExplanation with all fields."""
        from elle.daemon.incidents.anonymize import DriftExplanation

        de = DriftExplanation(
            surface="service:nginx",
            changed=True,
            explanation="nginx was restarted",
            before_value="active",
            after_value="inactive",
        )
        assert de.explanation == "nginx was restarted"
        assert de.before_value == "active"

    def test_anonymized_report_defaults(self):
        """AnonymizedIncidentReport defaults."""
        now = datetime(2024, 1, 1, 0, 0, 0)
        report = AnonymizedIncidentReport(
            incident_id="test-001",
            created_at_hour=now,
            updated_at_hour=now,
        )
        assert report.domain == "other"
        assert report.severity == "warning"
        assert report.status == "open"
        assert report.outcome == "unknown"
        assert report.confidence == 0.0
        assert report.anonymization_version == "1.0"
        assert report.detail_level == "hashes"
        assert report.original_hash == ""

    def test_anonymized_report_frozen(self):
        """AnonymizedIncidentReport is frozen (immutable)."""
        now = datetime(2024, 1, 1, 0, 0, 0)
        report = AnonymizedIncidentReport(
            incident_id="test-001",
            created_at_hour=now,
            updated_at_hour=now,
        )
        with pytest.raises(Exception):
            report.incident_id = "other"


# =============================================================================
# TestAnonymizeWithIncidentTelemetry
# =============================================================================


class TestAnonymizeWithIncidentTelemetry:
    """Tests for anonymize() when telemetry comes from the incident model directly."""

    def test_telemetry_from_incident_model(self):
        """When no external telemetry passed, use incident's own telemetry_pre/post."""
        incident = _make_incident(
            telemetry_pre={"cpu": {"load": 5.0}, "uptime_sec": 1000},
            telemetry_post={"cpu": {"load": 2.0}, "uptime_sec": 1100},
        )
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(incident)
        assert result.telemetry_pre is not None
        assert result.telemetry_pre["cpu"] == {"load": 5.0}
        assert result.telemetry_post is not None
        assert result.telemetry_post["cpu"] == {"load": 2.0}

    def test_external_telemetry_overrides_incident(self):
        """External telemetry takes priority over incident telemetry."""
        incident = _make_incident(
            telemetry_pre={"cpu": {"load": 5.0}, "uptime_sec": 0},
        )
        external_pre = {"memory": {"total": 16384}, "uptime_sec": 2000}

        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(incident, telemetry_pre=external_pre)
        # External overrides incident
        assert result.telemetry_pre is not None
        assert "memory" in result.telemetry_pre

    def test_control_surface_from_incident(self):
        """When no external control surface, use incident's own."""
        incident = _make_incident(
            control_surface_pre={"packages": {"packages": [{"name": "nginx"}]}},
            control_surface_post={"packages": {"packages": [{"name": "nginx"}, {"name": "curl"}]}},
        )
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(
            incident,
            surface_hashes_pre={"packages": "a"},
            surface_hashes_post={"packages": "b"},
            detail_level="detailed",
        )
        assert result.control_surface_pre is not None
        assert result.control_surface_post is not None


# =============================================================================
# TestMountPointGeneralization
# =============================================================================


class TestMountPointGeneralization:
    """Tests for _generalize_mount_point."""

    def test_all_known_mounts(self):
        """Test all entries in MOUNT_CATEGORIES."""
        from elle.daemon.incidents.anonymize import MOUNT_CATEGORIES

        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        for mount, category in MOUNT_CATEGORIES.items():
            assert anonymizer._generalize_mount_point(mount) == category

    def test_empty_mount(self):
        """Empty mount returns empty string."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        assert anonymizer._generalize_mount_point("") == ""

    def test_unknown_mount(self):
        """Unknown mount returns 'other'."""
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        assert anonymizer._generalize_mount_point("/data/warehouse") == "other"


# =============================================================================
# TestPreserveSurfaceHashes
# =============================================================================


class TestPreserveSurfaceHashes:
    """Tests for _preserve_surface_hashes."""

    def test_none_returns_none(self):
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        assert anonymizer._preserve_surface_hashes(None) is None

    def test_returns_copy(self):
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        original = {"key": "value"}
        result = anonymizer._preserve_surface_hashes(original)
        assert result == original
        assert result is not original

    def test_empty_dict(self):
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        assert anonymizer._preserve_surface_hashes({}) == {}


# =============================================================================
# TestComputeIncidentHash
# =============================================================================


class TestComputeIncidentHash:
    """Tests for _compute_incident_hash."""

    def test_deterministic(self):
        """Same incident produces same hash."""
        incident = _make_incident()
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        h1 = anonymizer._compute_incident_hash(incident)
        h2 = anonymizer._compute_incident_hash(incident)
        assert h1 == h2

    def test_different_incidents_different_hashes(self):
        """Different incidents produce different hashes."""
        i1 = _make_incident(incident_id="a")
        i2 = _make_incident(incident_id="b")
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        assert anonymizer._compute_incident_hash(i1) != anonymizer._compute_incident_hash(i2)


# =============================================================================
# TestHmacHash
# =============================================================================


class TestHmacHash:
    """Tests for the _hmac_hash method."""

    def test_with_prefix(self):
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx._hmac_hash("value", "pre-")
        assert result.startswith("pre-")
        assert len(result) > 4

    def test_without_prefix(self):
        ctx = AnonymizationContext(secret=b"test-secret")
        result = ctx._hmac_hash("value")
        assert not result.startswith("pre-")
        assert len(result) == 12  # 12 hex chars

    def test_deterministic(self):
        ctx = AnonymizationContext(secret=b"test-secret")
        r1 = ctx._hmac_hash("same-input", "p-")
        r2 = ctx._hmac_hash("same-input", "p-")
        assert r1 == r2

    def test_different_inputs(self):
        ctx = AnonymizationContext(secret=b"test-secret")
        r1 = ctx._hmac_hash("input-a")
        r2 = ctx._hmac_hash("input-b")
        assert r1 != r2


# =============================================================================
# TestSurfaceDriftWithAsymmetricKeys
# =============================================================================


class TestSurfaceDriftWithAsymmetricKeys:
    """Test surface drift when pre and post have different key sets."""

    def test_key_only_in_post(self):
        """Key in post but not pre means drift=True."""
        hashes_pre = {"a": "1"}
        hashes_post = {"a": "1", "b": "2"}

        incident = _make_incident()
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(
            incident,
            surface_hashes_pre=hashes_pre,
            surface_hashes_post=hashes_post,
        )
        assert result.surface_drift["a"] is False
        assert result.surface_drift["b"] is True  # None != "2"

    def test_key_only_in_pre(self):
        """Key in pre but not post means drift=True."""
        hashes_pre = {"a": "1", "b": "2"}
        hashes_post = {"a": "1"}

        incident = _make_incident()
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer.anonymize(
            incident,
            surface_hashes_pre=hashes_pre,
            surface_hashes_post=hashes_post,
        )
        assert result.surface_drift["a"] is False
        assert result.surface_drift["b"] is True  # "2" != None


# =============================================================================
# TestExplainSimpleDrift
# =============================================================================


class TestExplainSimpleDrift:
    """Tests for _explain_simple_drift."""

    def test_changed(self):
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer._explain_simple_drift("Test Surface", True)
        assert result == "Test Surface changed"

    def test_unchanged(self):
        anonymizer = IncidentAnonymizer(secret=b"test-secret")
        result = anonymizer._explain_simple_drift("Test Surface", False)
        assert result == "Test Surface unchanged"
