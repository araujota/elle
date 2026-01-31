from __future__ import annotations

"""Tests for efficacy tracking and machine-specific success learning.

All database interactions are mocked so tests run without PostgreSQL.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from elle.daemon.incidents.efficacy import (
    DECAY_HALF_LIFE_DAYS,
    MIN_SAMPLES_FOR_CONFIDENCE,
    PRIOR_SUCCESS_RATE,
    DomainEfficacy,
    EfficacyContext,
    EfficacyStats,
    EntityEfficacy,
    SolutionApproachEfficacy,
)
from elle.daemon.incidents.efficacy_tracker import (
    _compute_approach_signature,
    _compute_decayed_rate,
    _extract_key_commands,
    _normalize_command,
    _summarize_preconditions,
    _update_approach_efficacy,
    _update_domain_efficacy,
    _update_entity_efficacy,
    bootstrap_from_historical,
    get_all_domain_efficacy,
    get_approach_efficacy,
    get_domain_efficacy,
    get_efficacy_context,
    get_efficacy_stats,
    get_entity_efficacy,
    record_outcome,
)
from elle.daemon.incidents.models import (
    ConfidenceBreakdown,
    DecisionRecord,
    Fingerprint,
    IncidentReport,
    Precondition,
    Provenance,
)

# ---------------------------------------------------------------------------
# Helpers for building mock DB rows and incidents
# ---------------------------------------------------------------------------

_PATCH_GET_CONN = "elle.daemon.incidents.efficacy_tracker.get_conn"


def _make_incident(
    incident_id: str = "inc-001",
    domain: str = "net",
    entities: tuple[str, ...] = (),
    decision: dict | None = None,
    decision_record: DecisionRecord | None = None,
    preconditions: tuple[Precondition, ...] = (),
    outcome: str = "unknown",
    time_to_resolve: int | None = None,
) -> IncidentReport:
    """Build a minimal IncidentReport for testing."""
    return IncidentReport(
        incident_id=incident_id,
        title="Test incident",
        domain=domain,
        fingerprint=Fingerprint(entities=entities),
        decision=decision or {},
        decision_record=decision_record,
        preconditions=preconditions,
        outcome=outcome,
        time_to_resolve_sec=time_to_resolve,
    )


def _mock_conn_ctx():
    """Return a mock context manager mimicking ``get_conn(schema=...)``."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_conn)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx, mock_conn, mock_cursor


def _make_domain_row(
    domain: str = "net",
    total: int = 5,
    improved: int = 3,
    partial: int = 1,
    no_change: int = 1,
    worse: int = 0,
    success_rate: float = 0.75,
    last_updated: str | None = None,
) -> dict:
    return {
        "domain": domain,
        "total_incidents": total,
        "improved_count": improved,
        "partial_count": partial,
        "no_change_count": no_change,
        "worse_count": worse,
        "success_rate": success_rate,
        "last_updated": last_updated or datetime.now(timezone.utc).isoformat(),
    }


def _make_entity_row(
    entity: str = "service:nginx",
    total: int = 5,
    improved: int = 3,
    partial: int = 1,
    no_change: int = 1,
    worse: int = 0,
    success_rate: float = 0.75,
    last_updated: str | None = None,
) -> dict:
    return {
        "entity": entity,
        "total_incidents": total,
        "improved_count": improved,
        "partial_count": partial,
        "no_change_count": no_change,
        "worse_count": worse,
        "success_rate": success_rate,
        "last_updated": last_updated or datetime.now(timezone.utc).isoformat(),
    }


def _make_approach_row(
    approach_signature: str = "abc123def456",
    domain: str = "service",
    precondition_summary: str = "service.failed",
    key_commands_json: str = '["systemctl restart"]',
    total_uses: int = 5,
    improved: int = 4,
    partial: int = 0,
    no_change: int = 1,
    worse: int = 0,
    success_rate: float = 0.9,
    avg_time_to_resolve_sec: int | None = 120,
    last_used: str | None = None,
) -> dict:
    return {
        "approach_signature": approach_signature,
        "domain": domain,
        "precondition_summary": precondition_summary,
        "key_commands_json": key_commands_json,
        "total_uses": total_uses,
        "improved_count": improved,
        "partial_count": partial,
        "no_change_count": no_change,
        "worse_count": worse,
        "success_rate": success_rate,
        "avg_time_to_resolve_sec": avg_time_to_resolve_sec,
        "last_used": last_used or datetime.now(timezone.utc).isoformat(),
    }


# ============================================================================
# Model tests (no DB required)
# ============================================================================


class TestEfficacyModels:
    """Tests for efficacy data models."""

    def test_domain_efficacy_defaults(self):
        """Test DomainEfficacy default values."""
        eff = DomainEfficacy(domain="net")
        assert eff.domain == "net"
        assert eff.total_incidents == 0
        assert eff.success_rate == 0.5
        assert eff.improved_count == 0

    def test_domain_efficacy_all_fields(self):
        """Test DomainEfficacy with all fields set."""
        now = datetime.utcnow()
        eff = DomainEfficacy(
            domain="disk",
            total_incidents=20,
            improved_count=10,
            partial_count=5,
            no_change_count=3,
            worse_count=2,
            success_rate=0.72,
            last_updated=now,
        )
        assert eff.total_incidents == 20
        assert eff.worse_count == 2
        assert eff.last_updated == now

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

    def test_entity_efficacy_defaults(self):
        """Test EntityEfficacy default values."""
        eff = EntityEfficacy(entity="disk:/")
        assert eff.total_incidents == 0
        assert eff.improved_count == 0
        assert eff.success_rate == 0.5

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

    def test_solution_approach_efficacy_defaults(self):
        """Test SolutionApproachEfficacy defaults."""
        eff = SolutionApproachEfficacy(
            approach_signature="xyz",
            domain="net",
        )
        assert eff.total_uses == 0
        assert eff.avg_time_to_resolve_sec is None
        assert eff.precondition_summary == ""
        assert eff.key_commands == ()

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

    def test_efficacy_context_defaults(self):
        """Test EfficacyContext default values."""
        ctx = EfficacyContext()
        assert ctx.domain_success_rate is None
        assert ctx.domain_sample_size == 0
        assert ctx.entity_success_rates == {}
        assert ctx.approach_success_rate is None
        assert ctx.approach_sample_size == 0

    def test_efficacy_stats_defaults(self):
        """Test EfficacyStats default values."""
        stats = EfficacyStats()
        assert stats.total_finalized_incidents == 0
        assert stats.overall_success_rate == 0.5
        assert stats.domain_stats == ()
        assert stats.top_entities == ()
        assert stats.top_approaches == ()


# ============================================================================
# Decayed rate tests (pure computation, no DB)
# ============================================================================


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
        assert 0.6 < rate < 0.9

    def test_decay_over_time(self):
        """Test that rate decays toward prior over time."""
        now = datetime.utcnow()
        old = now - timedelta(days=DECAY_HALF_LIFE_DAYS * 2)

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

    def test_all_partial(self):
        """Test rate with all partial outcomes."""
        now = datetime.utcnow()
        rate = _compute_decayed_rate(
            improved=0,
            partial=10,
            no_change=0,
            worse=0,
            total=10,
            last_updated=now,
            now=now,
        )
        # partial weight is 0.6, so weighted_sum/total = 0.6
        # raw_rate = (0.6 + 0.5) / 1.5 = 0.733...
        assert 0.65 < rate < 0.85

    def test_all_no_change(self):
        """Test rate with all no_change outcomes."""
        now = datetime.utcnow()
        rate = _compute_decayed_rate(
            improved=0,
            partial=0,
            no_change=10,
            worse=0,
            total=10,
            last_updated=now,
            now=now,
        )
        # no_change weight = 0.0, so weighted_sum/total = 0.0
        # raw_rate = (0.0 + 0.5) / 1.5 = 0.333...
        assert 0.2 < rate < 0.5

    def test_low_sample_confidence(self):
        """Test that low sample sizes regress more toward prior."""
        now = datetime.utcnow()
        rate_low = _compute_decayed_rate(
            improved=1,
            partial=0,
            no_change=0,
            worse=0,
            total=1,
            last_updated=now,
            now=now,
        )
        rate_high = _compute_decayed_rate(
            improved=10,
            partial=0,
            no_change=0,
            worse=0,
            total=10,
            last_updated=now,
            now=now,
        )
        # Low sample count should be closer to prior
        assert abs(rate_low - PRIOR_SUCCESS_RATE) < abs(rate_high - PRIOR_SUCCESS_RATE)

    def test_confidence_at_min_samples(self):
        """Confidence reaches 1.0 at MIN_SAMPLES_FOR_CONFIDENCE."""
        now = datetime.utcnow()
        rate_at_min = _compute_decayed_rate(
            improved=MIN_SAMPLES_FOR_CONFIDENCE,
            partial=0,
            no_change=0,
            worse=0,
            total=MIN_SAMPLES_FOR_CONFIDENCE,
            last_updated=now,
            now=now,
        )
        rate_above_min = _compute_decayed_rate(
            improved=MIN_SAMPLES_FOR_CONFIDENCE * 2,
            partial=0,
            no_change=0,
            worse=0,
            total=MIN_SAMPLES_FOR_CONFIDENCE * 2,
            last_updated=now,
            now=now,
        )
        # Both should have full confidence, so rates should be identical
        assert abs(rate_at_min - rate_above_min) < 0.01

    def test_one_half_life_decay(self):
        """After one half-life, decay_factor is 0.5."""
        now = datetime.utcnow()
        one_hl = now - timedelta(days=DECAY_HALF_LIFE_DAYS)
        rate = _compute_decayed_rate(
            improved=10,
            partial=0,
            no_change=0,
            worse=0,
            total=10,
            last_updated=one_hl,
            now=now,
        )
        # Should be between prior (0.5) and perfect recent (>0.9)
        assert 0.5 < rate < 0.9

    def test_rate_is_float(self):
        """The return type must be float."""
        now = datetime.utcnow()
        rate = _compute_decayed_rate(
            improved=3,
            partial=2,
            no_change=1,
            worse=0,
            total=6,
            last_updated=now,
            now=now,
        )
        assert isinstance(rate, float)

    def test_rate_bounded_0_1(self):
        """Result is always between 0.0 and 1.0."""
        now = datetime.utcnow()
        for improved, worse in [(0, 100), (100, 0), (50, 50)]:
            total = improved + worse
            rate = _compute_decayed_rate(
                improved=improved,
                partial=0,
                no_change=0,
                worse=worse,
                total=total,
                last_updated=now,
                now=now,
            )
            assert 0.0 <= rate <= 1.0


# ============================================================================
# Command normalization tests (pure functions, no DB)
# ============================================================================


class TestCommandNormalization:
    """Tests for command normalization."""

    def test_normalize_systemctl(self):
        assert _normalize_command("systemctl restart nginx") == "systemctl restart"
        assert _normalize_command("systemctl status postgresql") == "systemctl status"

    def test_normalize_systemctl_single_word(self):
        """systemctl alone returns just the base."""
        assert _normalize_command("systemctl") == "systemctl"

    def test_normalize_service(self):
        """The 'service' base follows the same pattern as systemctl."""
        assert _normalize_command("service nginx restart") == "service nginx"

    def test_normalize_apt(self):
        assert _normalize_command("apt install python3") == "apt install"
        assert _normalize_command("apt-get update") == "apt update"

    def test_normalize_apt_single(self):
        """'apt' alone falls through to the default base."""
        assert _normalize_command("apt") == "apt"

    def test_normalize_rm(self):
        assert _normalize_command("rm -rf /tmp/cache") == "rm -rf"
        assert _normalize_command("rm /tmp/file.txt") == "rm"

    def test_normalize_rm_multiple_flags(self):
        assert _normalize_command("rm -r -f /var/log/old") == "rm -r -f"

    def test_normalize_docker(self):
        assert _normalize_command("docker restart mycontainer") == "docker restart"
        assert _normalize_command("docker logs -f app") == "docker logs"

    def test_normalize_docker_single(self):
        """'docker' alone returns just the base."""
        assert _normalize_command("docker") == "docker"

    def test_normalize_chmod(self):
        assert _normalize_command("chmod 755 /tmp/script.sh") == "chmod"

    def test_normalize_chown(self):
        assert _normalize_command("chown root:root /etc/config") == "chown"

    def test_normalize_empty(self):
        assert _normalize_command("") is None
        assert _normalize_command("   ") is None

    def test_normalize_none_input(self):
        """None-ish empty string returns None."""
        result = _normalize_command("")
        assert result is None

    def test_normalize_unknown_command(self):
        """Unknown commands return just the base command."""
        assert _normalize_command("ls -la /tmp") == "ls"
        assert _normalize_command("cat /etc/hosts") == "cat"
        assert _normalize_command("ping 8.8.8.8") == "ping"


# ============================================================================
# Approach signature & precondition helpers (pure functions)
# ============================================================================


class TestApproachSignature:
    """Tests for _compute_approach_signature."""

    def test_signature_with_decision_commands(self):
        """Signature is computed when decision dict contains commands."""
        incident = _make_incident(
            domain="service",
            decision={"commands": ["systemctl restart nginx"]},
        )
        sig = _compute_approach_signature(incident)
        assert sig is not None
        assert len(sig) == 16  # sha256[:16]

    def test_signature_with_decision_record_commands(self):
        """Signature from decision_record.planned_commands."""
        dr = DecisionRecord(
            chosen_approach="restart",
            confidence=ConfidenceBreakdown(overall=0.8),
            provenance=Provenance(),
            planned_commands=("systemctl restart nginx",),
        )
        incident = _make_incident(domain="service", decision_record=dr)
        sig = _compute_approach_signature(incident)
        assert sig is not None
        assert len(sig) == 16

    def test_signature_no_commands_returns_none(self):
        """No commands means no approach signature."""
        incident = _make_incident(domain="net")
        sig = _compute_approach_signature(incident)
        assert sig is None

    def test_signature_empty_commands_list_returns_none(self):
        """Empty commands list returns None."""
        incident = _make_incident(domain="net", decision={"commands": []})
        sig = _compute_approach_signature(incident)
        assert sig is None

    def test_same_commands_same_signature(self):
        """Identical commands + domain produce the same signature."""
        incident_a = _make_incident(
            domain="service",
            decision={"commands": ["systemctl restart"]},
        )
        incident_b = _make_incident(
            domain="service",
            decision={"commands": ["systemctl restart"]},
        )
        assert _compute_approach_signature(incident_a) == _compute_approach_signature(incident_b)

    def test_different_domain_different_signature(self):
        """Different domains with same commands produce different signatures."""
        incident_a = _make_incident(
            domain="service",
            decision={"commands": ["systemctl restart"]},
        )
        incident_b = _make_incident(
            domain="net",
            decision={"commands": ["systemctl restart"]},
        )
        assert _compute_approach_signature(incident_a) != _compute_approach_signature(incident_b)

    def test_signature_with_preconditions(self):
        """Preconditions are included in the signature."""
        p = Precondition(expression="disk./.used_pct > 95", required=True)
        incident_a = _make_incident(
            domain="disk",
            decision={"commands": ["rm -rf /tmp/*"]},
            preconditions=(p,),
        )
        incident_b = _make_incident(
            domain="disk",
            decision={"commands": ["rm -rf /tmp/*"]},
        )
        sig_a = _compute_approach_signature(incident_a)
        sig_b = _compute_approach_signature(incident_b)
        assert sig_a != sig_b

    def test_decision_commands_not_a_list(self):
        """Non-list value for decision['commands'] produces no crash."""
        incident = _make_incident(
            domain="service",
            decision={"commands": "not a list"},
        )
        # The isinstance check should skip non-list, resulting in None
        sig = _compute_approach_signature(incident)
        assert sig is None


class TestSummarizePreconditions:
    """Tests for _summarize_preconditions."""

    def test_no_preconditions(self):
        incident = _make_incident()
        assert _summarize_preconditions(incident) == ""

    def test_required_preconditions(self):
        p1 = Precondition(expression="disk./.used_pct > 95", required=True)
        p2 = Precondition(expression="mem_pressure > 0.8", required=True)
        incident = _make_incident(preconditions=(p1, p2))
        result = _summarize_preconditions(incident)
        # Sorted, so "disk..." comes before "mem..."
        assert "disk./.used_pct > 95" in result
        assert "mem_pressure > 0.8" in result

    def test_non_required_preconditions_excluded(self):
        p1 = Precondition(expression="important", required=True)
        p2 = Precondition(expression="optional", required=False)
        incident = _make_incident(preconditions=(p1, p2))
        result = _summarize_preconditions(incident)
        assert "important" in result
        assert "optional" not in result


class TestExtractKeyCommands:
    """Tests for _extract_key_commands."""

    def test_from_decision_dict(self):
        incident = _make_incident(decision={"commands": ["systemctl restart nginx"]})
        cmds = _extract_key_commands(incident)
        assert "systemctl restart" in cmds

    def test_from_decision_record(self):
        dr = DecisionRecord(
            chosen_approach="restart",
            confidence=ConfidenceBreakdown(overall=0.8),
            provenance=Provenance(),
            planned_commands=("apt install curl",),
        )
        incident = _make_incident(decision_record=dr)
        cmds = _extract_key_commands(incident)
        assert "apt install" in cmds

    def test_deduplication(self):
        """Duplicate commands are deduplicated."""
        dr = DecisionRecord(
            chosen_approach="restart",
            confidence=ConfidenceBreakdown(overall=0.8),
            provenance=Provenance(),
            planned_commands=("systemctl restart nginx",),
        )
        incident = _make_incident(
            decision={"commands": ["systemctl restart nginx"]},
            decision_record=dr,
        )
        cmds = _extract_key_commands(incident)
        assert cmds.count("systemctl restart") == 1

    def test_empty_commands(self):
        incident = _make_incident()
        assert _extract_key_commands(incident) == ()

    def test_sorted_output(self):
        incident = _make_incident(
            decision={"commands": ["docker restart", "apt install nginx"]},
        )
        cmds = _extract_key_commands(incident)
        assert cmds == tuple(sorted(cmds))


# ============================================================================
# _update_domain_efficacy (mocked DB)
# ============================================================================


class TestUpdateDomainEfficacy:
    """Tests for _update_domain_efficacy with mocked cursor."""

    def test_new_domain_improved(self):
        """First recording for a domain with 'improved' outcome."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None  # no existing row
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        now = datetime.now(timezone.utc)

        _update_domain_efficacy("net", "improved", now, mock_conn)

        # Two SQL calls: SELECT then INSERT
        assert mock_cursor.execute.call_count == 2
        insert_args = mock_cursor.execute.call_args_list[1][0][1]
        assert insert_args[0] == "net"  # domain
        assert insert_args[1] == 1  # total
        assert insert_args[2] == 1  # improved
        assert insert_args[3] == 0  # partial
        assert insert_args[4] == 0  # no_change
        assert insert_args[5] == 0  # worse

    def test_new_domain_worse(self):
        """First recording with 'worse' outcome."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        now = datetime.now(timezone.utc)

        _update_domain_efficacy("disk", "worse", now, mock_conn)

        insert_args = mock_cursor.execute.call_args_list[1][0][1]
        assert insert_args[0] == "disk"
        assert insert_args[1] == 1  # total
        assert insert_args[2] == 0  # improved
        assert insert_args[5] == 1  # worse

    def test_existing_domain_update(self):
        """Update an existing domain row."""
        existing_row = _make_domain_row(
            domain="net",
            total=5,
            improved=3,
            partial=1,
            no_change=1,
            worse=0,
        )
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = existing_row
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        now = datetime.now(timezone.utc)

        _update_domain_efficacy("net", "partial", now, mock_conn)

        insert_args = mock_cursor.execute.call_args_list[1][0][1]
        assert insert_args[0] == "net"
        assert insert_args[1] == 6  # total = 5 + 1
        assert insert_args[2] == 3  # improved unchanged
        assert insert_args[3] == 2  # partial = 1 + 1

    def test_each_outcome_counter(self):
        """Each outcome type increments only its counter."""
        for outcome, idx in [("improved", 2), ("partial", 3), ("no_change", 4), ("worse", 5)]:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            now = datetime.now(timezone.utc)

            _update_domain_efficacy("test", outcome, now, mock_conn)

            insert_args = mock_cursor.execute.call_args_list[1][0][1]
            for i in [2, 3, 4, 5]:
                expected = 1 if i == idx else 0
                assert insert_args[i] == expected, f"outcome={outcome}, idx={i}"


# ============================================================================
# _update_entity_efficacy (mocked DB)
# ============================================================================


class TestUpdateEntityEfficacy:
    """Tests for _update_entity_efficacy with mocked cursor."""

    def test_new_entity(self):
        """First recording for an entity."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        now = datetime.now(timezone.utc)

        _update_entity_efficacy("service:nginx", "improved", now, mock_conn)

        insert_args = mock_cursor.execute.call_args_list[1][0][1]
        assert insert_args[0] == "service:nginx"
        assert insert_args[1] == 1  # total
        assert insert_args[2] == 1  # improved

    def test_existing_entity_update(self):
        """Update existing entity row."""
        existing = _make_entity_row(
            entity="disk:/",
            total=3,
            improved=2,
            partial=0,
            no_change=1,
            worse=0,
        )
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = existing
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        now = datetime.now(timezone.utc)

        _update_entity_efficacy("disk:/", "worse", now, mock_conn)

        insert_args = mock_cursor.execute.call_args_list[1][0][1]
        assert insert_args[0] == "disk:/"
        assert insert_args[1] == 4  # total
        assert insert_args[5] == 1  # worse

    def test_each_outcome_counter_entity(self):
        """Each outcome increments only its counter on entities."""
        for outcome, idx in [("improved", 2), ("partial", 3), ("no_change", 4), ("worse", 5)]:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            now = datetime.now(timezone.utc)

            _update_entity_efficacy("e", outcome, now, mock_conn)

            insert_args = mock_cursor.execute.call_args_list[1][0][1]
            for i in [2, 3, 4, 5]:
                expected = 1 if i == idx else 0
                assert insert_args[i] == expected


# ============================================================================
# _update_approach_efficacy (mocked DB)
# ============================================================================


class TestUpdateApproachEfficacy:
    """Tests for _update_approach_efficacy with mocked cursor."""

    def test_new_approach(self):
        """First recording for an approach."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        now = datetime.now(timezone.utc)

        _update_approach_efficacy(
            approach_signature="sig1",
            domain="service",
            precondition_summary="",
            key_commands=("systemctl restart",),
            outcome="improved",
            time_to_resolve=60,
            now=now,
            conn=mock_conn,
        )

        insert_args = mock_cursor.execute.call_args_list[1][0][1]
        assert insert_args[0] == "sig1"  # approach_signature
        assert insert_args[1] == "service"  # domain
        assert insert_args[4] == 1  # total_uses
        assert insert_args[5] == 1  # improved
        assert insert_args[10] == 60  # avg_time

    def test_new_approach_no_time(self):
        """New approach with no time_to_resolve."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        now = datetime.now(timezone.utc)

        _update_approach_efficacy(
            approach_signature="sig2",
            domain="net",
            precondition_summary="",
            key_commands=("ping",),
            outcome="no_change",
            time_to_resolve=None,
            now=now,
            conn=mock_conn,
        )

        insert_args = mock_cursor.execute.call_args_list[1][0][1]
        assert insert_args[10] is None  # avg_time

    def test_existing_approach_incremental_avg(self):
        """Existing approach: incremental average of time_to_resolve."""
        existing = _make_approach_row(
            total_uses=4,
            improved=3,
            avg_time_to_resolve_sec=100,
        )
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = existing
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        now = datetime.now(timezone.utc)

        _update_approach_efficacy(
            approach_signature="abc123def456",
            domain="service",
            precondition_summary="",
            key_commands=("systemctl restart",),
            outcome="improved",
            time_to_resolve=200,
            now=now,
            conn=mock_conn,
        )

        insert_args = mock_cursor.execute.call_args_list[1][0][1]
        # total = 5, new avg = (100*4 + 200) / 5 = 120
        assert insert_args[4] == 5  # total_uses
        assert insert_args[10] == 120  # avg_time

    def test_existing_approach_no_prior_avg(self):
        """Existing approach with no prior avg_time_to_resolve_sec."""
        existing = _make_approach_row(
            total_uses=2,
            avg_time_to_resolve_sec=None,
        )
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = existing
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        now = datetime.now(timezone.utc)

        _update_approach_efficacy(
            approach_signature="abc123def456",
            domain="service",
            precondition_summary="",
            key_commands=("systemctl restart",),
            outcome="improved",
            time_to_resolve=90,
            now=now,
            conn=mock_conn,
        )

        insert_args = mock_cursor.execute.call_args_list[1][0][1]
        assert insert_args[10] == 90

    def test_existing_approach_no_new_time(self):
        """Existing approach: new outcome has no time_to_resolve."""
        existing = _make_approach_row(
            total_uses=3,
            avg_time_to_resolve_sec=80,
        )
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = existing
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        now = datetime.now(timezone.utc)

        _update_approach_efficacy(
            approach_signature="abc123def456",
            domain="service",
            precondition_summary="",
            key_commands=("systemctl restart",),
            outcome="partial",
            time_to_resolve=None,
            now=now,
            conn=mock_conn,
        )

        insert_args = mock_cursor.execute.call_args_list[1][0][1]
        # old avg preserved
        assert insert_args[10] == 80


# ============================================================================
# record_outcome (mocked get_conn)
# ============================================================================


class TestRecordOutcome:
    """Tests for record_outcome with mocked database."""

    @patch(_PATCH_GET_CONN)
    def test_record_improved_outcome(self, mock_get_conn):
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        mock_cursor.fetchone.return_value = None  # no existing rows

        incident = _make_incident(
            domain="net",
            entities=("interface:eth0",),
            decision={"commands": ["ip link set eth0 up"]},
        )

        record_outcome(incident, "improved")

        # Should have been called: get_conn invoked
        mock_get_conn.assert_called_once()

    @patch(_PATCH_GET_CONN)
    def test_record_outcome_updates_all_entities(self, mock_get_conn):
        """Each entity gets its own efficacy update."""
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        mock_cursor.fetchone.return_value = None

        incident = _make_incident(
            domain="service",
            entities=("service:nginx", "service:php-fpm"),
        )

        record_outcome(incident, "improved")

        # Should have entity update calls (SELECT + INSERT each):
        # 1 domain (2 SQL) + 2 entities (2 SQL each) = 6 SQL calls minimum
        assert mock_cursor.execute.call_count >= 6

    @patch(_PATCH_GET_CONN)
    def test_record_outcome_with_approach(self, mock_get_conn):
        """When commands exist, approach efficacy is also updated."""
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        mock_cursor.fetchone.return_value = None

        incident = _make_incident(
            domain="service",
            entities=(),
            decision={"commands": ["systemctl restart nginx"]},
        )

        record_outcome(incident, "partial")

        # domain (2) + approach (2) = 4 SQL calls minimum
        assert mock_cursor.execute.call_count >= 4

    @patch(_PATCH_GET_CONN)
    def test_record_outcome_no_approach_when_no_commands(self, mock_get_conn):
        """No approach update when there are no commands."""
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        mock_cursor.fetchone.return_value = None

        incident = _make_incident(domain="net", entities=())
        record_outcome(incident, "no_change")

        # Only domain update: 2 SQL calls (SELECT + INSERT)
        assert mock_cursor.execute.call_count == 2


# ============================================================================
# get_domain_efficacy (mocked)
# ============================================================================


class TestGetDomainEfficacy:
    """Tests for get_domain_efficacy with mocked DB."""

    @patch(_PATCH_GET_CONN)
    def test_found(self, mock_get_conn):
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        mock_cursor.fetchone.return_value = _make_domain_row(domain="net")

        result = get_domain_efficacy("net")

        assert result is not None
        assert isinstance(result, DomainEfficacy)
        assert result.domain == "net"
        assert result.total_incidents == 5

    @patch(_PATCH_GET_CONN)
    def test_not_found(self, mock_get_conn):
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        mock_cursor.fetchone.return_value = None

        result = get_domain_efficacy("unknown_domain")
        assert result is None


# ============================================================================
# get_entity_efficacy (mocked)
# ============================================================================


class TestGetEntityEfficacy:
    """Tests for get_entity_efficacy with mocked DB."""

    @patch(_PATCH_GET_CONN)
    def test_found(self, mock_get_conn):
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        mock_cursor.fetchone.return_value = _make_entity_row(entity="service:nginx")

        result = get_entity_efficacy("service:nginx")

        assert result is not None
        assert isinstance(result, EntityEfficacy)
        assert result.entity == "service:nginx"

    @patch(_PATCH_GET_CONN)
    def test_not_found(self, mock_get_conn):
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        mock_cursor.fetchone.return_value = None

        result = get_entity_efficacy("disk:/nonexistent")
        assert result is None


# ============================================================================
# get_approach_efficacy (mocked)
# ============================================================================


class TestGetApproachEfficacy:
    """Tests for get_approach_efficacy with mocked DB."""

    @patch(_PATCH_GET_CONN)
    def test_found(self, mock_get_conn):
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        mock_cursor.fetchone.return_value = _make_approach_row()

        result = get_approach_efficacy("abc123def456")

        assert result is not None
        assert isinstance(result, SolutionApproachEfficacy)
        assert result.approach_signature == "abc123def456"
        assert result.domain == "service"
        assert result.avg_time_to_resolve_sec == 120

    @patch(_PATCH_GET_CONN)
    def test_not_found(self, mock_get_conn):
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        mock_cursor.fetchone.return_value = None

        result = get_approach_efficacy("nonexistent")
        assert result is None

    @patch(_PATCH_GET_CONN)
    def test_null_precondition_summary(self, mock_get_conn):
        """NULL precondition_summary in DB is coerced to empty string."""
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        row = _make_approach_row(precondition_summary=None)
        mock_cursor.fetchone.return_value = row

        result = get_approach_efficacy("abc123def456")
        assert result.precondition_summary == ""


# ============================================================================
# get_efficacy_context (mocked)
# ============================================================================


class TestGetEfficacyContext:
    """Tests for get_efficacy_context with mocked sub-calls."""

    @patch("elle.daemon.incidents.efficacy_tracker.get_approach_efficacy")
    @patch("elle.daemon.incidents.efficacy_tracker.get_entity_efficacy")
    @patch("elle.daemon.incidents.efficacy_tracker.get_domain_efficacy")
    def test_full_context(self, mock_dom, mock_ent, mock_app):
        """Context with domain, entity, and approach data."""
        mock_dom.return_value = DomainEfficacy(
            domain="net",
            total_incidents=10,
            success_rate=0.8,
        )
        mock_ent.return_value = EntityEfficacy(
            entity="interface:eth0",
            total_incidents=5,
            success_rate=0.75,
        )
        mock_app.return_value = SolutionApproachEfficacy(
            approach_signature="sig1",
            domain="net",
            total_uses=3,
            success_rate=0.9,
        )

        incident = _make_incident(
            domain="net",
            decision={"commands": ["ip link set eth0 up"]},
        )

        ctx = get_efficacy_context(
            domain="net",
            entities=("interface:eth0",),
            incident=incident,
        )

        assert ctx.domain_success_rate == 0.8
        assert ctx.domain_sample_size == 10
        assert "interface:eth0" in ctx.entity_success_rates
        assert ctx.approach_success_rate == 0.9
        assert ctx.approach_sample_size == 3

    @patch("elle.daemon.incidents.efficacy_tracker.get_approach_efficacy")
    @patch("elle.daemon.incidents.efficacy_tracker.get_entity_efficacy")
    @patch("elle.daemon.incidents.efficacy_tracker.get_domain_efficacy")
    def test_no_data(self, mock_dom, mock_ent, mock_app):
        """Context when no efficacy data exists."""
        mock_dom.return_value = None
        mock_ent.return_value = None
        mock_app.return_value = None

        ctx = get_efficacy_context(domain="net")

        assert ctx.domain_success_rate is None
        assert ctx.domain_sample_size == 0
        assert ctx.entity_success_rates == {}
        assert ctx.approach_success_rate is None
        assert ctx.approach_sample_size == 0

    @patch("elle.daemon.incidents.efficacy_tracker.get_approach_efficacy")
    @patch("elle.daemon.incidents.efficacy_tracker.get_entity_efficacy")
    @patch("elle.daemon.incidents.efficacy_tracker.get_domain_efficacy")
    def test_no_entities(self, mock_dom, mock_ent, mock_app):
        """No entity lookup if entities is None."""
        mock_dom.return_value = None
        mock_app.return_value = None

        ctx = get_efficacy_context(domain="net", entities=None)

        mock_ent.assert_not_called()
        assert ctx.entity_success_rates == {}

    @patch("elle.daemon.incidents.efficacy_tracker.get_approach_efficacy")
    @patch("elle.daemon.incidents.efficacy_tracker.get_entity_efficacy")
    @patch("elle.daemon.incidents.efficacy_tracker.get_domain_efficacy")
    def test_no_incident(self, mock_dom, mock_ent, mock_app):
        """No approach lookup if incident is None."""
        mock_dom.return_value = None

        ctx = get_efficacy_context(domain="net", entities=None, incident=None)

        mock_app.assert_not_called()
        assert ctx.approach_success_rate is None

    @patch("elle.daemon.incidents.efficacy_tracker.get_approach_efficacy")
    @patch("elle.daemon.incidents.efficacy_tracker.get_entity_efficacy")
    @patch("elle.daemon.incidents.efficacy_tracker.get_domain_efficacy")
    def test_incident_without_commands_no_approach(self, mock_dom, mock_ent, mock_app):
        """If incident has no commands, approach_signature is None => no lookup."""
        mock_dom.return_value = None

        incident = _make_incident(domain="net")
        ctx = get_efficacy_context(domain="net", incident=incident)

        mock_app.assert_not_called()
        assert ctx.approach_success_rate is None

    @patch("elle.daemon.incidents.efficacy_tracker.get_approach_efficacy")
    @patch("elle.daemon.incidents.efficacy_tracker.get_entity_efficacy")
    @patch("elle.daemon.incidents.efficacy_tracker.get_domain_efficacy")
    def test_multiple_entities(self, mock_dom, mock_ent, mock_app):
        """Multiple entities each get looked up."""
        mock_dom.return_value = None
        mock_app.return_value = None

        def entity_side_effect(entity):
            if entity == "service:nginx":
                return EntityEfficacy(entity="service:nginx", success_rate=0.9)
            return None

        mock_ent.side_effect = entity_side_effect

        ctx = get_efficacy_context(
            domain="service",
            entities=("service:nginx", "service:php"),
        )

        assert mock_ent.call_count == 2
        assert "service:nginx" in ctx.entity_success_rates
        assert ctx.entity_success_rates["service:nginx"] == 0.9
        assert "service:php" not in ctx.entity_success_rates


# ============================================================================
# get_all_domain_efficacy (mocked)
# ============================================================================


class TestGetAllDomainEfficacy:
    """Tests for get_all_domain_efficacy with mocked DB."""

    @patch(_PATCH_GET_CONN)
    def test_returns_list(self, mock_get_conn):
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        mock_cursor.fetchall.return_value = [
            _make_domain_row(domain="net", total=10),
            _make_domain_row(domain="disk", total=5),
        ]

        result = get_all_domain_efficacy()

        assert len(result) == 2
        assert result[0].domain == "net"
        assert result[1].domain == "disk"

    @patch(_PATCH_GET_CONN)
    def test_empty(self, mock_get_conn):
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        mock_cursor.fetchall.return_value = []

        result = get_all_domain_efficacy()
        assert result == []


# ============================================================================
# get_efficacy_stats (mocked)
# ============================================================================


class TestGetEfficacyStats:
    """Tests for get_efficacy_stats with mocked DB."""

    @patch("elle.daemon.incidents.efficacy_tracker.get_all_domain_efficacy")
    @patch(_PATCH_GET_CONN)
    def test_with_data(self, mock_get_conn, mock_all_domains):
        # get_conn is called three times (overall stats, domain stats via
        # get_all_domain_efficacy, and top entities/approaches).
        # We need to set up separate context managers for each call.
        # The first call fetches total and avg, the third fetches entities + approaches.

        # First call context: overall total and avg
        ctx1, conn1, cursor1 = _mock_conn_ctx()
        total_row = {"total": 15}
        avg_row = {"avg": 0.82}
        cursor1.fetchone.side_effect = [total_row, avg_row]

        # Third call context: top entities and approaches
        ctx3, conn3, cursor3 = _mock_conn_ctx()
        entity_rows = [
            _make_entity_row(entity="service:nginx", total=8, success_rate=0.85),
        ]
        approach_rows = [
            _make_approach_row(
                approach_signature="sig1",
                total_uses=5,
                success_rate=0.95,
            ),
        ]
        cursor3.fetchall.side_effect = [entity_rows, approach_rows]

        mock_get_conn.side_effect = [ctx1, ctx3]

        mock_all_domains.return_value = [
            DomainEfficacy(domain="net", total_incidents=10, success_rate=0.85),
            DomainEfficacy(domain="disk", total_incidents=5, success_rate=0.70),
        ]

        stats = get_efficacy_stats()

        assert stats.total_finalized_incidents == 15
        assert stats.overall_success_rate == 0.82
        assert len(stats.domain_stats) == 2
        assert len(stats.top_entities) == 1
        assert stats.top_entities[0].entity == "service:nginx"
        assert len(stats.top_approaches) == 1

    @patch("elle.daemon.incidents.efficacy_tracker.get_all_domain_efficacy")
    @patch(_PATCH_GET_CONN)
    def test_empty_stats(self, mock_get_conn, mock_all_domains):
        """No data produces sensible defaults."""
        ctx1, conn1, cursor1 = _mock_conn_ctx()
        cursor1.fetchone.side_effect = [
            {"total": None},  # SUM returns None when no rows
            {"avg": None},
        ]

        ctx3, conn3, cursor3 = _mock_conn_ctx()
        cursor3.fetchall.side_effect = [[], []]

        mock_get_conn.side_effect = [ctx1, ctx3]
        mock_all_domains.return_value = []

        stats = get_efficacy_stats()

        assert stats.total_finalized_incidents == 0
        assert stats.overall_success_rate == 0.5
        assert len(stats.domain_stats) == 0
        assert len(stats.top_entities) == 0
        assert len(stats.top_approaches) == 0

    @patch("elle.daemon.incidents.efficacy_tracker.get_all_domain_efficacy")
    @patch(_PATCH_GET_CONN)
    def test_null_total_row(self, mock_get_conn, mock_all_domains):
        """When cursor.fetchone() returns a row with None total."""
        ctx1, conn1, cursor1 = _mock_conn_ctx()
        cursor1.fetchone.side_effect = [
            None,  # fetchone returns None row for total
            {"avg": None},
        ]

        ctx3, conn3, cursor3 = _mock_conn_ctx()
        cursor3.fetchall.side_effect = [[], []]

        mock_get_conn.side_effect = [ctx1, ctx3]
        mock_all_domains.return_value = []

        stats = get_efficacy_stats()
        assert stats.total_finalized_incidents == 0


# ============================================================================
# bootstrap_from_historical (mocked)
# ============================================================================


class TestBootstrapFromHistorical:
    """Tests for bootstrap_from_historical with mocked store."""

    @patch("elle.daemon.incidents.efficacy_tracker.record_outcome")
    @patch("elle.daemon.incidents.store.list_incidents")
    def test_processes_finalized(self, mock_list, mock_record):
        """Processes resolved and mitigated incidents with valid outcomes."""
        resolved = [
            _make_incident(incident_id="r1", outcome="improved"),
            _make_incident(incident_id="r2", outcome="worse"),
        ]
        mitigated = [
            _make_incident(incident_id="m1", outcome="partial"),
        ]
        mock_list.side_effect = [resolved, mitigated]

        count = bootstrap_from_historical()

        assert count == 3
        assert mock_record.call_count == 3

    @patch("elle.daemon.incidents.efficacy_tracker.record_outcome")
    @patch("elle.daemon.incidents.store.list_incidents")
    def test_skips_unknown_outcomes(self, mock_list, mock_record):
        """Incidents with 'unknown' outcome are skipped."""
        incidents = [
            _make_incident(incident_id="u1", outcome="unknown"),
            _make_incident(incident_id="u2", outcome="improved"),
        ]
        mock_list.side_effect = [incidents, []]

        count = bootstrap_from_historical()

        assert count == 1
        mock_record.assert_called_once()

    @patch("elle.daemon.incidents.efficacy_tracker.record_outcome")
    @patch("elle.daemon.incidents.store.list_incidents")
    def test_empty(self, mock_list, mock_record):
        """No incidents to process."""
        mock_list.side_effect = [[], []]

        count = bootstrap_from_historical()

        assert count == 0
        mock_record.assert_not_called()

    @patch("elle.daemon.incidents.efficacy_tracker.record_outcome")
    @patch("elle.daemon.incidents.store.list_incidents")
    def test_calls_list_incidents_twice(self, mock_list, mock_record):
        """list_incidents is called once for resolved and once for mitigated."""
        mock_list.side_effect = [[], []]

        bootstrap_from_historical()

        assert mock_list.call_count == 2
        mock_list.assert_any_call(status="resolved", limit=10000)
        mock_list.assert_any_call(status="mitigated", limit=10000)

    @patch("elle.daemon.incidents.efficacy_tracker.record_outcome")
    @patch("elle.daemon.incidents.store.list_incidents")
    def test_all_four_valid_outcomes(self, mock_list, mock_record):
        """All four valid outcome types are processed."""
        incidents = [
            _make_incident(incident_id="i1", outcome="improved"),
            _make_incident(incident_id="i2", outcome="partial"),
            _make_incident(incident_id="i3", outcome="no_change"),
            _make_incident(incident_id="i4", outcome="worse"),
        ]
        mock_list.side_effect = [incidents, []]

        count = bootstrap_from_historical()
        assert count == 4

    @patch("elle.daemon.incidents.efficacy_tracker.record_outcome")
    @patch("elle.daemon.incidents.store.list_incidents")
    def test_passes_incident_outcome_to_record(self, mock_list, mock_record):
        """record_outcome is called with the incident's own outcome."""
        inc = _make_incident(incident_id="i1", outcome="partial")
        mock_list.side_effect = [[inc], []]

        bootstrap_from_historical()

        mock_record.assert_called_once_with(inc, "partial")


# ============================================================================
# Integration-style tests combining multiple mocked layers
# ============================================================================


class TestRecordOutcomeIntegration:
    """Higher-level tests for record_outcome exercising the full path."""

    @patch(_PATCH_GET_CONN)
    def test_record_with_preconditions_and_commands(self, mock_get_conn):
        """Incident with preconditions and commands exercises approach path."""
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        mock_cursor.fetchone.return_value = None

        p = Precondition(expression="disk./.used_pct > 95", required=True)
        dr = DecisionRecord(
            chosen_approach="clean temp",
            confidence=ConfidenceBreakdown(overall=0.7),
            provenance=Provenance(),
            planned_commands=("rm -rf /tmp/cache",),
        )
        incident = _make_incident(
            domain="disk",
            entities=("disk:/",),
            preconditions=(p,),
            decision_record=dr,
            time_to_resolve=45,
        )

        record_outcome(incident, "improved")

        # domain + entity + approach = at least 6 SQL calls
        assert mock_cursor.execute.call_count >= 6

    @patch("elle.daemon.incidents.efficacy_tracker.parse_datetime")
    @patch(_PATCH_GET_CONN)
    def test_record_outcome_worse_on_existing_domain(self, mock_get_conn, mock_parse_dt):
        """Recording 'worse' on an existing domain updates counters correctly."""
        # parse_datetime returns naive datetime to match utcnow() in source
        mock_parse_dt.return_value = datetime.utcnow()

        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx

        existing_domain = _make_domain_row(
            domain="net",
            total=10,
            improved=8,
            worse=0,
        )
        # First fetchone for domain SELECT, then None for entities/approaches
        mock_cursor.fetchone.side_effect = [existing_domain]

        incident = _make_incident(domain="net", entities=())
        record_outcome(incident, "worse")

        # Check the INSERT args for domain
        insert_call_args = mock_cursor.execute.call_args_list[1][0][1]
        assert insert_call_args[1] == 11  # total incremented
        assert insert_call_args[5] == 1  # worse incremented from 0

    @patch(_PATCH_GET_CONN)
    def test_record_outcome_with_multiple_entities_and_approach(self, mock_get_conn):
        """Full path: domain + multiple entities + approach."""
        ctx, mock_conn, mock_cursor = _mock_conn_ctx()
        mock_get_conn.return_value = ctx
        mock_cursor.fetchone.return_value = None

        incident = _make_incident(
            domain="service",
            entities=("service:a", "service:b", "service:c"),
            decision={"commands": ["systemctl restart a", "systemctl restart b"]},
            time_to_resolve=30,
        )

        record_outcome(incident, "improved")

        # domain(2) + 3 entities(6) + approach(2) = 10 SQL calls
        assert mock_cursor.execute.call_count >= 10
