"""Tests for incident comparison and diffing.

Verifies structural comparison between two IncidentReport instances,
covering identical reports, added/removed/changed fields, nested
structures (decisions, fingerprints), and output rendering.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from elle.daemon.incidents.differ import (
    ActionDiff,
    DecisionDiff,
    IncidentDiff,
    IncidentDiffer,
    SnapshotDiff,
    render_incident_diff,
)
from elle.daemon.incidents.models import (
    ConfidenceBreakdown,
    DecisionRecord,
    Fingerprint,
    IncidentCitation,
    IncidentReport,
    ManPageCitation,
    Provenance,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BASE_TIME = datetime(2025, 1, 15, 10, 0, 0)


@pytest.fixture
def base_fingerprint() -> Fingerprint:
    """Minimal fingerprint with default values."""
    return Fingerprint()


@pytest.fixture
def base_report(base_fingerprint: Fingerprint) -> IncidentReport:
    """Minimal, self-consistent incident report."""
    return IncidentReport(
        incident_id="aaaa-1111-bbbb-2222",
        created_at=_BASE_TIME,
        updated_at=_BASE_TIME,
        domain="net",
        severity="warning",
        status="open",
        title="DNS resolution slow",
        summary="DNS queries taking >500ms.",
        symptoms=("slow DNS", "timeout on resolve"),
        suspected_causes=("upstream DNS flap",),
        root_cause=None,
        outcome="unknown",
        fingerprint=base_fingerprint,
    )


@pytest.fixture
def second_report(base_fingerprint: Fingerprint) -> IncidentReport:
    """A second report that differs from base_report in several fields."""
    return IncidentReport(
        incident_id="cccc-3333-dddd-4444",
        created_at=_BASE_TIME + timedelta(hours=3),
        updated_at=_BASE_TIME + timedelta(hours=3),
        domain="net",
        severity="error",
        status="mitigated",
        title="DNS resolution failing",
        summary="DNS queries now failing entirely.",
        symptoms=("slow DNS", "SERVFAIL responses"),
        suspected_causes=("upstream DNS flap", "local resolver crash"),
        root_cause="systemd-resolved crash",
        outcome="improved",
        fingerprint=base_fingerprint,
    )


def _make_report(
    *,
    incident_id: str = "id-default",
    created_at: datetime = _BASE_TIME,
    domain: str = "other",
    severity: str = "info",
    status: str = "open",
    title: str = "Default title",
    symptoms: tuple[str, ...] = (),
    suspected_causes: tuple[str, ...] = (),
    root_cause: str | None = None,
    outcome: str = "unknown",
    fingerprint: Fingerprint | None = None,
    decision: dict | None = None,
    decision_record: DecisionRecord | None = None,
) -> IncidentReport:
    """Helper to build IncidentReport with sensible defaults."""
    return IncidentReport(
        incident_id=incident_id,
        created_at=created_at,
        updated_at=created_at,
        domain=domain,
        severity=severity,
        status=status,
        title=title,
        symptoms=symptoms,
        suspected_causes=suspected_causes,
        root_cause=root_cause,
        outcome=outcome,
        fingerprint=fingerprint or Fingerprint(),
        decision=decision or {},
        decision_record=decision_record,
    )


# ---------------------------------------------------------------------------
# Model unit tests
# ---------------------------------------------------------------------------


class TestDecisionDiffModel:
    """Tests for the DecisionDiff Pydantic model."""

    def test_defaults(self) -> None:
        dd = DecisionDiff()
        assert dd.approach_changed is False
        assert dd.approach_1 == ""
        assert dd.confidence_delta == 0.0
        assert dd.sources_added == ()
        assert dd.sources_removed == ()

    def test_frozen(self) -> None:
        dd = DecisionDiff(approach_changed=True, approach_1="a")
        with pytest.raises(Exception):
            dd.approach_1 = "b"  # type: ignore[misc]


class TestSnapshotDiffModel:
    """Tests for the SnapshotDiff Pydantic model."""

    def test_defaults(self) -> None:
        sd = SnapshotDiff()
        assert sd.memory_delta_mb == 0
        assert sd.services_started == ()
        assert sd.packages_installed == ()

    def test_with_values(self) -> None:
        sd = SnapshotDiff(
            memory_delta_mb=-256,
            services_started=("nginx",),
            containers_stopped=("redis",),
        )
        assert sd.memory_delta_mb == -256
        assert "nginx" in sd.services_started
        assert "redis" in sd.containers_stopped


class TestActionDiffModel:
    """Tests for the ActionDiff Pydantic model."""

    def test_defaults(self) -> None:
        ad = ActionDiff()
        assert ad.actions_in_1_only == ()
        assert ad.success_rate_1 == 0.0

    def test_with_values(self) -> None:
        ad = ActionDiff(
            actions_in_1_only=("systemctl restart nginx",),
            actions_in_both=("apt update",),
            success_rate_1=1.0,
            success_rate_2=0.5,
        )
        assert len(ad.actions_in_1_only) == 1
        assert ad.success_rate_2 == 0.5


class TestIncidentDiffModel:
    """Tests for the IncidentDiff Pydantic model."""

    def test_minimal_creation(self) -> None:
        diff = IncidentDiff(
            incident1_id="aaa",
            incident2_id="bbb",
            incident1_created=_BASE_TIME,
            incident2_created=_BASE_TIME,
        )
        assert diff.same_domain is True
        assert diff.titles_match is False
        assert diff.fingerprint_similarity == 0.0


# ---------------------------------------------------------------------------
# IncidentDiffer.diff() tests
# ---------------------------------------------------------------------------


class TestDiffIdenticalReports:
    """Diffing two identical reports should show no meaningful delta."""

    def test_identical_reports_same_domain(self, base_report: IncidentReport) -> None:
        diff = IncidentDiffer.diff(base_report, base_report)
        assert diff.same_domain is True
        assert diff.same_severity is True
        assert diff.titles_match is True
        assert diff.title_similarity == 1.0

    def test_identical_reports_no_symptom_changes(self, base_report: IncidentReport) -> None:
        diff = IncidentDiffer.diff(base_report, base_report)
        assert diff.symptoms_added == ()
        assert diff.symptoms_removed == ()
        assert set(diff.symptoms_common) == set(base_report.symptoms)

    def test_identical_reports_no_cause_changes(self, base_report: IncidentReport) -> None:
        diff = IncidentDiffer.diff(base_report, base_report)
        assert diff.causes_added == ()
        assert diff.causes_removed == ()
        assert set(diff.causes_common) == set(base_report.suspected_causes)

    def test_identical_reports_zero_time_delta(self, base_report: IncidentReport) -> None:
        diff = IncidentDiffer.diff(base_report, base_report)
        assert diff.time_delta == timedelta()

    def test_identical_reports_no_outcome_improvement(self, base_report: IncidentReport) -> None:
        diff = IncidentDiffer.diff(base_report, base_report)
        assert diff.outcome_improved is False

    def test_identical_reports_perfect_fingerprint_similarity(self, base_report: IncidentReport) -> None:
        diff = IncidentDiffer.diff(base_report, base_report)
        assert diff.fingerprint_similarity == 1.0


class TestDiffDifferentReports:
    """Diffing two reports that differ in classification, symptoms, causes, etc."""

    def test_time_delta(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        assert diff.time_delta == timedelta(hours=3)

    def test_severity_change(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        assert diff.same_severity is False
        assert diff.severity1 == "warning"
        assert diff.severity2 == "error"

    def test_domain_unchanged(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        assert diff.same_domain is True

    def test_titles_differ(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        assert diff.titles_match is False
        assert diff.title1 == "DNS resolution slow"
        assert diff.title2 == "DNS resolution failing"
        # Titles share two words: "DNS" and "resolution"
        assert diff.title_similarity > 0.0
        assert diff.title_similarity < 1.0

    def test_symptoms_added_and_removed(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        assert "SERVFAIL responses" in diff.symptoms_added
        assert "timeout on resolve" in diff.symptoms_removed
        assert "slow DNS" in diff.symptoms_common

    def test_causes_added_and_common(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        assert "local resolver crash" in diff.causes_added
        assert "upstream DNS flap" in diff.causes_common
        assert diff.causes_removed == ()

    def test_root_cause_change(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        assert diff.root_cause1 is None
        assert diff.root_cause2 == "systemd-resolved crash"
        assert diff.root_cause_match is False

    def test_outcome_improved(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        assert diff.outcome1 == "unknown"
        assert diff.outcome2 == "improved"
        assert diff.outcome_improved is True

    def test_status_values_stored(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        assert diff.status1 == "open"
        assert diff.status2 == "mitigated"


class TestDiffDomainChange:
    """Verify diff detects a domain change."""

    def test_different_domains(self) -> None:
        r1 = _make_report(incident_id="a1", domain="net")
        r2 = _make_report(incident_id="a2", domain="disk")
        diff = IncidentDiffer.diff(r1, r2)
        assert diff.same_domain is False
        assert diff.domain1 == "net"
        assert diff.domain2 == "disk"


class TestDiffRootCauseMatch:
    """Verify root cause matching is case-insensitive."""

    def test_same_root_cause_different_case(self) -> None:
        r1 = _make_report(incident_id="a1", root_cause="OOM Kill")
        r2 = _make_report(incident_id="a2", root_cause="oom kill")
        diff = IncidentDiffer.diff(r1, r2)
        assert diff.root_cause_match is True

    def test_both_none_root_cause(self) -> None:
        r1 = _make_report(incident_id="a1", root_cause=None)
        r2 = _make_report(incident_id="a2", root_cause=None)
        diff = IncidentDiffer.diff(r1, r2)
        assert diff.root_cause_match is False

    def test_one_none_root_cause(self) -> None:
        r1 = _make_report(incident_id="a1", root_cause="disk full")
        r2 = _make_report(incident_id="a2", root_cause=None)
        diff = IncidentDiffer.diff(r1, r2)
        assert diff.root_cause_match is False


class TestDiffOutcomeImprovement:
    """Test outcome ranking logic: unknown < worse < no_change < partial < improved."""

    @pytest.mark.parametrize(
        "out1, out2, expected_improved",
        [
            ("unknown", "improved", True),
            ("worse", "no_change", True),
            ("no_change", "partial", True),
            ("partial", "improved", True),
            ("improved", "improved", False),  # same rank -> not improved
            ("improved", "unknown", False),   # regression
            ("partial", "no_change", False),
        ],
    )
    def test_outcome_rankings(self, out1: str, out2: str, expected_improved: bool) -> None:
        r1 = _make_report(incident_id="a1", outcome=out1)
        r2 = _make_report(incident_id="a2", outcome=out2)
        diff = IncidentDiffer.diff(r1, r2)
        assert diff.outcome_improved is expected_improved


# ---------------------------------------------------------------------------
# Decision comparison tests
# ---------------------------------------------------------------------------


class TestCompareDecisions:
    """Tests for _compare_decisions (via diff)."""

    def test_no_decision_records(self) -> None:
        """When both reports lack decision_record, diff falls back to decision dict."""
        r1 = _make_report(
            incident_id="d1",
            decision={"approach": "restart service"},
        )
        r2 = _make_report(
            incident_id="d2",
            decision={"approach": "upgrade package"},
        )
        diff = IncidentDiffer.diff(r1, r2)
        assert diff.decision_diff is not None
        assert diff.decision_diff.approach_changed is True
        assert diff.decision_diff.approach_1 == "restart service"
        assert diff.decision_diff.approach_2 == "upgrade package"

    def test_same_approach_case_insensitive(self) -> None:
        r1 = _make_report(
            incident_id="d1",
            decision={"approach": "Restart Service"},
        )
        r2 = _make_report(
            incident_id="d2",
            decision={"approach": "restart service"},
        )
        diff = IncidentDiffer.diff(r1, r2)
        assert diff.decision_diff is not None
        assert diff.decision_diff.approach_changed is False

    def test_with_decision_records_and_provenance(self) -> None:
        """Full decision records with man page and incident citations."""
        prov1 = Provenance(
            man_pages=(
                ManPageCitation(
                    name="systemctl",
                    section="1",
                    snippet="restart a service",
                ),
            ),
            prior_incidents=(
                IncidentCitation(
                    incident_id="prior-1111-aaaa-bbbb",
                    title="nginx restart",
                    outcome="improved",
                    similarity_score=0.8,
                    match_type="hybrid",
                ),
            ),
        )
        prov2 = Provenance(
            man_pages=(
                ManPageCitation(
                    name="journalctl",
                    section="1",
                    snippet="view logs",
                ),
            ),
        )

        confidence = ConfidenceBreakdown(overall=0.7, from_man_vault=0.2, from_llm=0.5)

        dr1 = DecisionRecord(
            chosen_approach="restart nginx",
            rationale="Prior art succeeded",
            confidence=confidence,
            provenance=prov1,
        )
        dr2 = DecisionRecord(
            chosen_approach="check logs first",
            rationale="Need more info",
            confidence=ConfidenceBreakdown(overall=0.5, from_llm=0.5),
            provenance=prov2,
        )

        r1 = _make_report(incident_id="d1", decision_record=dr1)
        r2 = _make_report(incident_id="d2", decision_record=dr2)
        diff = IncidentDiffer.diff(r1, r2)

        dd = diff.decision_diff
        assert dd is not None
        assert dd.approach_changed is True
        assert dd.approach_1 == "restart nginx"
        assert dd.approach_2 == "check logs first"

        # Sources: r1 has man:systemctl + incident:prior-11, r2 has man:journalctl
        assert "man:journalctl" in dd.sources_added
        assert "man:systemctl" in dd.sources_removed

    def test_empty_decision_dicts(self) -> None:
        """Both reports have empty decision dicts and no decision_record."""
        r1 = _make_report(incident_id="d1")
        r2 = _make_report(incident_id="d2")
        diff = IncidentDiffer.diff(r1, r2)
        dd = diff.decision_diff
        assert dd is not None
        assert dd.approach_changed is False
        assert dd.approach_1 == ""
        assert dd.approach_2 == ""


# ---------------------------------------------------------------------------
# Fingerprint comparison tests
# ---------------------------------------------------------------------------


class TestCompareFingerprints:
    """Tests for _compare_fingerprints."""

    def test_identical_fingerprints_perfect_similarity(self) -> None:
        fp = Fingerprint(disk_pressure=0.5, mem_pressure=0.5, cpu_pressure=2.0)
        sim = IncidentDiffer._compare_fingerprints(fp, fp)
        assert sim == 1.0

    def test_completely_different_fingerprints(self) -> None:
        fp1 = Fingerprint(disk_pressure=0.0, mem_pressure=0.0, swap_pressure=0.0, cpu_pressure=0.0)
        fp2 = Fingerprint(disk_pressure=1.0, mem_pressure=1.0, swap_pressure=1.0, cpu_pressure=5.0)
        sim = IncidentDiffer._compare_fingerprints(fp1, fp2)
        assert sim == 0.0

    def test_none_fingerprint_returns_zero(self) -> None:
        fp = Fingerprint()
        assert IncidentDiffer._compare_fingerprints(None, fp) == 0.0
        assert IncidentDiffer._compare_fingerprints(fp, None) == 0.0
        assert IncidentDiffer._compare_fingerprints(None, None) == 0.0

    def test_entity_overlap_affects_similarity(self) -> None:
        fp1 = Fingerprint(entities=("nginx", "postgres"))
        fp2 = Fingerprint(entities=("nginx", "redis"))
        sim = IncidentDiffer._compare_fingerprints(fp1, fp2)
        # Numeric fields are the same (all defaults), so numeric similarity = 1.0
        # Entity overlap = 1/3 (nginx shared, 3 total unique)
        # Final = (1.0 + 1/3) / 2 = ~0.667
        assert 0.5 < sim < 0.8

    def test_no_entities_pure_numeric_similarity(self) -> None:
        fp1 = Fingerprint(disk_pressure=0.5, mem_pressure=0.3)
        fp2 = Fingerprint(disk_pressure=0.5, mem_pressure=0.3)
        sim = IncidentDiffer._compare_fingerprints(fp1, fp2)
        assert sim == 1.0

    def test_partial_numeric_difference(self) -> None:
        fp1 = Fingerprint(disk_pressure=0.2)
        fp2 = Fingerprint(disk_pressure=0.8)
        sim = IncidentDiffer._compare_fingerprints(fp1, fp2)
        # disk_pressure diff = |0.2-0.8| / 1.0 = 0.6, others are 0.0
        # avg_diff = 0.6 / 4 = 0.15
        # similarity = 1.0 - 0.15 = 0.85
        assert 0.8 <= sim <= 0.9


class TestEntityDiff:
    """Verify entity add/remove/common is tracked in the top-level diff."""

    def test_entity_changes(self) -> None:
        fp1 = Fingerprint(entities=("nginx", "postgres"))
        fp2 = Fingerprint(entities=("nginx", "redis"))
        r1 = _make_report(incident_id="e1", fingerprint=fp1)
        r2 = _make_report(incident_id="e2", fingerprint=fp2)
        diff = IncidentDiffer.diff(r1, r2)
        assert "redis" in diff.entities_added
        assert "postgres" in diff.entities_removed
        assert "nginx" in diff.entities_common


# ---------------------------------------------------------------------------
# String similarity tests
# ---------------------------------------------------------------------------


class TestStringSimilarity:
    """Tests for _string_similarity."""

    def test_both_empty(self) -> None:
        assert IncidentDiffer._string_similarity("", "") == 1.0

    def test_one_empty(self) -> None:
        assert IncidentDiffer._string_similarity("hello", "") == 0.0
        assert IncidentDiffer._string_similarity("", "hello") == 0.0

    def test_identical_case_insensitive(self) -> None:
        assert IncidentDiffer._string_similarity("DNS Resolution Slow", "dns resolution slow") == 1.0

    def test_partial_overlap(self) -> None:
        sim = IncidentDiffer._string_similarity("DNS resolution slow", "DNS resolution failing")
        # words1 = {dns, resolution, slow}, words2 = {dns, resolution, failing}
        # overlap = 2, total = 4 -> 0.5
        assert sim == 0.5

    def test_no_overlap(self) -> None:
        sim = IncidentDiffer._string_similarity("hello world", "foo bar")
        assert sim == 0.0

    def test_complete_overlap(self) -> None:
        sim = IncidentDiffer._string_similarity("one two three", "three two one")
        assert sim == 1.0


# ---------------------------------------------------------------------------
# Render tests
# ---------------------------------------------------------------------------


class TestRenderIncidentDiff:
    """Tests for render_incident_diff output formatting."""

    def test_render_contains_header(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        text = render_incident_diff(diff)
        assert "INCIDENT DIFF:" in text
        assert "=" * 60 in text

    def test_render_shows_time_between(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        text = render_incident_diff(diff)
        assert "Time between:" in text
        assert "3h" in text

    def test_render_shows_severity_change(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        text = render_incident_diff(diff)
        assert "warning -> error" in text

    def test_render_shows_titles_differ(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        text = render_incident_diff(diff)
        assert "TITLES:" in text
        assert "DNS resolution slow" in text
        assert "DNS resolution failing" in text
        assert "Similarity:" in text

    def test_render_matching_titles(self) -> None:
        r1 = _make_report(incident_id="t1", title="same title")
        r2 = _make_report(incident_id="t2", title="same title")
        diff = IncidentDiffer.diff(r1, r2)
        text = render_incident_diff(diff)
        assert "TITLE: same title" in text
        # Should NOT show the two-line format
        assert "TITLES:" not in text

    def test_render_symptoms_changed_section(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        text = render_incident_diff(diff)
        assert "SYMPTOMS CHANGED:" in text
        assert "+ SERVFAIL responses" in text
        assert "- timeout on resolve" in text

    def test_render_no_symptoms_section_when_unchanged(self) -> None:
        r1 = _make_report(incident_id="u1", symptoms=("x",))
        r2 = _make_report(incident_id="u2", symptoms=("x",))
        diff = IncidentDiffer.diff(r1, r2)
        text = render_incident_diff(diff)
        assert "SYMPTOMS CHANGED:" not in text

    def test_render_causes_changed_section(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        text = render_incident_diff(diff)
        assert "SUSPECTED CAUSES CHANGED:" in text
        assert "+ local resolver crash" in text

    def test_render_root_cause_section(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        text = render_incident_diff(diff)
        assert "ROOT CAUSE:" in text
        assert "(not determined)" in text
        assert "systemd-resolved crash" in text

    def test_render_root_cause_omitted_when_both_none(self) -> None:
        r1 = _make_report(incident_id="n1")
        r2 = _make_report(incident_id="n2")
        diff = IncidentDiffer.diff(r1, r2)
        text = render_incident_diff(diff)
        assert "ROOT CAUSE:" not in text

    def test_render_outcome_section(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        text = render_incident_diff(diff)
        assert "OUTCOME:" in text
        assert "1: unknown" in text
        assert "2: improved" in text
        assert "[IMPROVED]" in text

    def test_render_no_improved_marker_when_same_outcome(self) -> None:
        r1 = _make_report(incident_id="s1", outcome="improved")
        r2 = _make_report(incident_id="s2", outcome="improved")
        diff = IncidentDiffer.diff(r1, r2)
        text = render_incident_diff(diff)
        assert "[IMPROVED]" not in text

    def test_render_fingerprint_similarity(
        self, base_report: IncidentReport, second_report: IncidentReport
    ) -> None:
        diff = IncidentDiffer.diff(base_report, second_report)
        text = render_incident_diff(diff)
        assert "FINGERPRINT SIMILARITY:" in text

    def test_render_entities_changed(self) -> None:
        fp1 = Fingerprint(entities=("nginx",))
        fp2 = Fingerprint(entities=("nginx", "redis"))
        r1 = _make_report(incident_id="ec1", fingerprint=fp1)
        r2 = _make_report(incident_id="ec2", fingerprint=fp2)
        diff = IncidentDiffer.diff(r1, r2)
        text = render_incident_diff(diff)
        assert "ENTITIES CHANGED:" in text
        assert "+ redis" in text

    def test_render_no_entities_section_when_unchanged(self) -> None:
        fp = Fingerprint(entities=("nginx",))
        r1 = _make_report(incident_id="eu1", fingerprint=fp)
        r2 = _make_report(incident_id="eu2", fingerprint=fp)
        diff = IncidentDiffer.diff(r1, r2)
        text = render_incident_diff(diff)
        assert "ENTITIES CHANGED:" not in text

    def test_render_decision_approach_changed(self) -> None:
        r1 = _make_report(incident_id="da1", decision={"approach": "approach A"})
        r2 = _make_report(incident_id="da2", decision={"approach": "approach B"})
        diff = IncidentDiffer.diff(r1, r2)
        text = render_incident_diff(diff)
        assert "APPROACH CHANGED:" in text
        assert "1: approach A" in text
        assert "2: approach B" in text

    def test_render_classification_same_domain(self) -> None:
        r1 = _make_report(incident_id="cd1", domain="disk")
        r2 = _make_report(incident_id="cd2", domain="disk")
        diff = IncidentDiffer.diff(r1, r2)
        text = render_incident_diff(diff)
        assert "Domain: disk" in text
        # Should NOT show "disk -> disk"
        assert "disk -> disk" not in text

    def test_render_classification_domain_change(self) -> None:
        r1 = _make_report(incident_id="dd1", domain="net")
        r2 = _make_report(incident_id="dd2", domain="disk")
        diff = IncidentDiffer.diff(r1, r2)
        text = render_incident_diff(diff)
        assert "net -> disk" in text


class TestRenderTimeDelta:
    """Test the time formatting logic in render_incident_diff."""

    def test_days_and_hours(self) -> None:
        r1 = _make_report(incident_id="t1", created_at=_BASE_TIME)
        r2 = _make_report(incident_id="t2", created_at=_BASE_TIME + timedelta(days=2, hours=5))
        diff = IncidentDiffer.diff(r1, r2)
        text = render_incident_diff(diff)
        assert "2d 5h" in text

    def test_only_hours(self) -> None:
        r1 = _make_report(incident_id="t1", created_at=_BASE_TIME)
        r2 = _make_report(incident_id="t2", created_at=_BASE_TIME + timedelta(hours=7))
        diff = IncidentDiffer.diff(r1, r2)
        text = render_incident_diff(diff)
        assert "7h" in text

    def test_only_minutes(self) -> None:
        r1 = _make_report(incident_id="t1", created_at=_BASE_TIME)
        r2 = _make_report(incident_id="t2", created_at=_BASE_TIME + timedelta(minutes=45))
        diff = IncidentDiffer.diff(r1, r2)
        text = render_incident_diff(diff)
        assert "45m" in text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case scenarios for the differ."""

    def test_empty_symptoms_both(self) -> None:
        r1 = _make_report(incident_id="e1", symptoms=())
        r2 = _make_report(incident_id="e2", symptoms=())
        diff = IncidentDiffer.diff(r1, r2)
        assert diff.symptoms_added == ()
        assert diff.symptoms_removed == ()
        assert diff.symptoms_common == ()

    def test_empty_causes_both(self) -> None:
        r1 = _make_report(incident_id="e1", suspected_causes=())
        r2 = _make_report(incident_id="e2", suspected_causes=())
        diff = IncidentDiffer.diff(r1, r2)
        assert diff.causes_added == ()
        assert diff.causes_removed == ()
        assert diff.causes_common == ()

    def test_empty_entities_both(self) -> None:
        fp = Fingerprint(entities=())
        r1 = _make_report(incident_id="e1", fingerprint=fp)
        r2 = _make_report(incident_id="e2", fingerprint=fp)
        diff = IncidentDiffer.diff(r1, r2)
        assert diff.entities_added == ()
        assert diff.entities_removed == ()
        assert diff.entities_common == ()

    def test_single_symptom_added(self) -> None:
        r1 = _make_report(incident_id="e1", symptoms=())
        r2 = _make_report(incident_id="e2", symptoms=("new symptom",))
        diff = IncidentDiffer.diff(r1, r2)
        assert "new symptom" in diff.symptoms_added

    def test_single_symptom_removed(self) -> None:
        r1 = _make_report(incident_id="e1", symptoms=("old symptom",))
        r2 = _make_report(incident_id="e2", symptoms=())
        diff = IncidentDiffer.diff(r1, r2)
        assert "old symptom" in diff.symptoms_removed

    def test_diff_preserves_incident_ids(self) -> None:
        r1 = _make_report(incident_id="aaaa-1111")
        r2 = _make_report(incident_id="bbbb-2222")
        diff = IncidentDiffer.diff(r1, r2)
        assert diff.incident1_id == "aaaa-1111"
        assert diff.incident2_id == "bbbb-2222"

    def test_diff_preserves_created_at(self) -> None:
        t1 = _BASE_TIME
        t2 = _BASE_TIME + timedelta(days=1)
        r1 = _make_report(incident_id="c1", created_at=t1)
        r2 = _make_report(incident_id="c2", created_at=t2)
        diff = IncidentDiffer.diff(r1, r2)
        assert diff.incident1_created == t1
        assert diff.incident2_created == t2
