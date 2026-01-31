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
