"""Tests for efficacy tracking and machine-specific success learning."""

from __future__ import annotations

from datetime import datetime, timedelta

from elle.daemon.incidents.efficacy import (
    DECAY_HALF_LIFE_DAYS,
    PRIOR_SUCCESS_RATE,
    DomainEfficacy,
    EfficacyContext,
    EntityEfficacy,
    SolutionApproachEfficacy,
)
from elle.daemon.incidents.efficacy_tracker import (
    _compute_decayed_rate,
    _normalize_command,
    get_all_domain_efficacy,
    get_domain_efficacy,
    get_efficacy_context,
    get_efficacy_stats,
    get_entity_efficacy,
    record_outcome,
)
from elle.daemon.incidents.models import Fingerprint
from elle.daemon.incidents.store import create_incident_draft, update_incident


class TestEfficacyModels:
    """Tests for efficacy data models."""

    def test_domain_efficacy_defaults(self):
        """Test DomainEfficacy default values."""
        eff = DomainEfficacy(domain="net")
        assert eff.domain == "net"
        assert eff.total_incidents == 0
        assert eff.success_rate == 0.5
        assert eff.improved_count == 0

    def test_entity_efficacy_creation(self):
        """Test EntityEfficacy creation."""
        eff = EntityEfficacy(
            entity="service:nginx",
            total_incidents=10,
            improved_count=7,
            partial_count=2,
            no_change_count=1,
            worse_count=0,
            success_rate=0.85,
        )
        assert eff.entity == "service:nginx"
        assert eff.total_incidents == 10
        assert eff.success_rate == 0.85

    def test_solution_approach_efficacy(self):
        """Test SolutionApproachEfficacy creation."""
        eff = SolutionApproachEfficacy(
            approach_signature="abc123",
            domain="service",
            precondition_summary="service.failed",
            key_commands=("systemctl restart", "systemctl status"),
            total_uses=5,
            improved_count=4,
            success_rate=0.9,
            avg_time_to_resolve_sec=120,
        )
        assert eff.approach_signature == "abc123"
        assert len(eff.key_commands) == 2
        assert eff.avg_time_to_resolve_sec == 120

    def test_efficacy_context(self):
        """Test EfficacyContext creation."""
        ctx = EfficacyContext(
            domain_success_rate=0.75,
            domain_sample_size=20,
            entity_success_rates={"service:nginx": 0.8, "disk:/": 0.6},
            approach_success_rate=0.9,
            approach_sample_size=5,
        )
        assert ctx.domain_success_rate == 0.75
        assert len(ctx.entity_success_rates) == 2


class TestDecayedRate:
    """Tests for time-decayed success rate computation."""

    def test_all_improved(self):
        """Test rate with all improved outcomes."""
        now = datetime.utcnow()
        rate = _compute_decayed_rate(
            improved=10,
            partial=0,
            no_change=0,
            worse=0,
            total=10,
            last_updated=now,
            now=now,
        )
        # Should be close to 1.0 (perfect success)
        assert rate > 0.9

    def test_all_worse(self):
        """Test rate with all worse outcomes."""
        now = datetime.utcnow()
        rate = _compute_decayed_rate(
            improved=0,
            partial=0,
            no_change=0,
            worse=10,
            total=10,
            last_updated=now,
            now=now,
        )
        # Should be close to 0.0 (complete failure)
        assert rate < 0.2

    def test_mixed_outcomes(self):
        """Test rate with mixed outcomes."""
        now = datetime.utcnow()
        rate = _compute_decayed_rate(
            improved=5,
            partial=3,
            no_change=2,
            worse=0,
            total=10,
            last_updated=now,
            now=now,
        )
        # Should be moderately high
        assert 0.6 < rate < 0.9

    def test_decay_over_time(self):
        """Test that rate decays toward prior over time."""
        now = datetime.utcnow()
        old = now - timedelta(days=DECAY_HALF_LIFE_DAYS * 2)  # Two half-lives

        recent_rate = _compute_decayed_rate(
            improved=10,
            partial=0,
            no_change=0,
            worse=0,
            total=10,
            last_updated=now,
            now=now,
        )
        old_rate = _compute_decayed_rate(
            improved=10,
            partial=0,
            no_change=0,
            worse=0,
            total=10,
            last_updated=old,
            now=now,
        )

        # Old rate should be closer to prior (0.5)
        assert old_rate < recent_rate
        assert abs(old_rate - PRIOR_SUCCESS_RATE) < abs(recent_rate - PRIOR_SUCCESS_RATE)

    def test_zero_total(self):
        """Test rate with zero incidents returns prior."""
        now = datetime.utcnow()
        rate = _compute_decayed_rate(
            improved=0,
            partial=0,
            no_change=0,
            worse=0,
            total=0,
            last_updated=now,
            now=now,
        )
        assert rate == PRIOR_SUCCESS_RATE


class TestCommandNormalization:
    """Tests for command normalization."""

    def test_normalize_systemctl(self):
        """Test systemctl command normalization."""
        assert _normalize_command("systemctl restart nginx") == "systemctl restart"
        assert _normalize_command("systemctl status postgresql") == "systemctl status"

    def test_normalize_apt(self):
        """Test apt command normalization."""
        assert _normalize_command("apt install python3") == "apt install"
        assert _normalize_command("apt-get update") == "apt update"

    def test_normalize_rm(self):
        """Test rm command normalization."""
        assert _normalize_command("rm -rf /tmp/cache") == "rm -rf"
        assert _normalize_command("rm /tmp/file.txt") == "rm"

    def test_normalize_docker(self):
        """Test docker command normalization."""
        assert _normalize_command("docker restart mycontainer") == "docker restart"
        assert _normalize_command("docker logs -f app") == "docker logs"

    def test_normalize_empty(self):
        """Test empty command returns None."""
        assert _normalize_command("") is None
        assert _normalize_command("   ") is None


class TestEfficacyTracking:
    """Tests for efficacy tracking operations."""

    def test_record_outcome_creates_domain_efficacy(self, incidents_conn):
        """Test that recording outcome creates domain efficacy record."""
        conn = incidents_conn

        # Create an incident
        incident = create_incident_draft(
            title="Test incident",
            domain="net",
            conn=conn,
        )

        # Update with fingerprint containing entities
        incident = update_incident(
            incident.incident_id,
            fingerprint=Fingerprint(entities=("interface:eth0",)),
            conn=conn,
        )

        # Record outcome
        record_outcome(incident, "improved", conn=conn)
        conn.commit()

        # Check domain efficacy was created
        domain_eff = get_domain_efficacy("net", conn=conn)
        assert domain_eff is not None
        assert domain_eff.total_incidents == 1
        assert domain_eff.improved_count == 1

    def test_record_outcome_updates_entity_efficacy(self, incidents_conn):
        """Test that recording outcome updates entity efficacy."""
        conn = incidents_conn

        incident = create_incident_draft(title="Test", domain="service", conn=conn)
        incident = update_incident(
            incident.incident_id,
            fingerprint=Fingerprint(entities=("service:nginx", "service:php-fpm")),
            conn=conn,
        )

        record_outcome(incident, "improved", conn=conn)
        conn.commit()

        nginx_eff = get_entity_efficacy("service:nginx", conn=conn)
        assert nginx_eff is not None
        assert nginx_eff.improved_count == 1

        php_eff = get_entity_efficacy("service:php-fpm", conn=conn)
        assert php_eff is not None
        assert php_eff.improved_count == 1

    def test_multiple_outcomes_aggregate(self, incidents_conn):
        """Test that multiple outcomes are aggregated correctly."""
        conn = incidents_conn

        # Create and record multiple incidents
        for outcome in ["improved", "improved", "partial", "no_change"]:
            incident = create_incident_draft(title=f"Test {outcome}", domain="disk", conn=conn)
            record_outcome(incident, outcome, conn=conn)

        conn.commit()

        domain_eff = get_domain_efficacy("disk", conn=conn)
        assert domain_eff.total_incidents == 4
        assert domain_eff.improved_count == 2
        assert domain_eff.partial_count == 1
        assert domain_eff.no_change_count == 1

    def test_get_efficacy_context(self, incidents_conn):
        """Test getting efficacy context for retrieval."""
        conn = incidents_conn

        # Create some incidents to build context
        for i in range(5):
            incident = create_incident_draft(title=f"Test {i}", domain="net", conn=conn)
            incident = update_incident(
                incident.incident_id,
                fingerprint=Fingerprint(entities=("interface:eth0",)),
                conn=conn,
            )
            record_outcome(incident, "improved" if i < 4 else "no_change", conn=conn)

        conn.commit()

        ctx = get_efficacy_context(
            domain="net",
            entities=("interface:eth0",),
            conn=conn,
        )

        assert ctx.domain_success_rate is not None
        assert ctx.domain_sample_size == 5
        assert "interface:eth0" in ctx.entity_success_rates

    def test_get_efficacy_stats(self, incidents_conn):
        """Test getting aggregated efficacy stats."""
        conn = incidents_conn

        # Create incidents across multiple domains
        for domain in ["net", "disk", "service"]:
            for _ in range(3):
                incident = create_incident_draft(title=f"Test {domain}", domain=domain, conn=conn)
                record_outcome(incident, "improved", conn=conn)

        conn.commit()

        stats = get_efficacy_stats(conn=conn)
        assert stats.total_finalized_incidents == 9
        assert len(stats.domain_stats) == 3

    def test_get_all_domain_efficacy(self, incidents_conn):
        """Test getting all domain efficacy records."""
        conn = incidents_conn

        for domain in ["net", "disk"]:
            incident = create_incident_draft(title=f"Test {domain}", domain=domain, conn=conn)
            record_outcome(incident, "improved", conn=conn)

        conn.commit()

        all_domains = get_all_domain_efficacy(conn=conn)
        assert len(all_domains) == 2
        domains = {d.domain for d in all_domains}
        assert "net" in domains
        assert "disk" in domains
