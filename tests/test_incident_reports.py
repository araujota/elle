from __future__ import annotations

"""Tests for incident and efficacy report generation.

All database dependencies are mocked so no PostgreSQL instance is required.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from elle.daemon.incidents.efficacy import (
    DomainEfficacy,
    EfficacyStats,
    EntityEfficacy,
    SolutionApproachEfficacy,
)
from elle.daemon.incidents.models import (
    ConfidenceBreakdown,
    DecisionRecord,
    Fingerprint,
    IncidentAction,
    IncidentCitation,
    IncidentReport,
    ManPageCitation,
    Provenance,
)
from elle.daemon.incidents.narrative import CausalChain, Narrative
from elle.daemon.incidents.reports import (
    EfficacyReport,
    IncidentFullReport,
    IncidentReportSummary,
    ReportGenerator,
    TrendReport,
    generate_efficacy_report,
    generate_incident_report,
    generate_trend_report,
)

# =============================================================================
# Helpers: build fake domain objects
# =============================================================================

NOW = datetime.now(timezone.utc)


def _make_incident(
    incident_id: str = "inc-001",
    title: str = "Test Incident",
    domain: str = "service",
    severity: str = "warning",
    status: str = "open",
    outcome: str = "unknown",
    summary: str = "",
    symptoms: tuple[str, ...] = (),
    root_cause: str | None = None,
    suspected_causes: tuple[str, ...] = (),
    verification_steps: tuple[str, ...] = (),
    time_to_resolve_sec: int | None = None,
    confidence: float = 0.0,
    fingerprint: Fingerprint | None = None,
    decision_record: DecisionRecord | None = None,
) -> IncidentReport:
    return IncidentReport(
        incident_id=incident_id,
        title=title,
        domain=domain,
        severity=severity,
        status=status,
        outcome=outcome,
        summary=summary,
        symptoms=symptoms,
        root_cause=root_cause,
        suspected_causes=suspected_causes,
        verification_steps=verification_steps,
        time_to_resolve_sec=time_to_resolve_sec,
        confidence=confidence,
        fingerprint=fingerprint or Fingerprint(),
        decision_record=decision_record,
        created_at=NOW,
        updated_at=NOW,
    )


def _make_action(
    incident_id: str = "inc-001",
    step_index: int = 0,
    kind: str = "shell",
    command: str = "df -h /var",
    success: bool = True,
) -> IncidentAction:
    return IncidentAction(
        id=1,
        incident_id=incident_id,
        step_index=step_index,
        kind=kind,
        command=command,
        success=success,
        created_at=NOW,
    )


def _make_narrative() -> Narrative:
    return Narrative(
        chain=CausalChain(chain_id="chain-001"),
        summary="Disk filled up causing PostgreSQL to crash.",
        timeline=("10:00 Disk hit 95%", "10:05 PostgreSQL WAL write failed"),
        explanation="The /var partition filled up due to log rotation failure.",
        keywords=("disk", "postgresql", "wal"),
        generated_at=NOW,
        model_used="qwen2.5:7b-instruct-q8_0",
    )


def _make_domain_efficacy(
    domain: str = "net",
    total: int = 10,
    improved: int = 7,
    partial: int = 1,
    no_change: int = 1,
    worse: int = 1,
    success_rate: float = 0.8,
) -> DomainEfficacy:
    return DomainEfficacy(
        domain=domain,
        total_incidents=total,
        improved_count=improved,
        partial_count=partial,
        no_change_count=no_change,
        worse_count=worse,
        success_rate=success_rate,
        last_updated=NOW,
    )


def _make_efficacy_stats(
    domain_stats: tuple[DomainEfficacy, ...] = (),
    total: int = 0,
    overall_rate: float = 0.5,
    top_entities: tuple[EntityEfficacy, ...] = (),
    top_approaches: tuple[SolutionApproachEfficacy, ...] = (),
) -> EfficacyStats:
    return EfficacyStats(
        total_finalized_incidents=total,
        overall_success_rate=overall_rate,
        domain_stats=domain_stats,
        top_entities=top_entities,
        top_approaches=top_approaches,
        computed_at=NOW,
    )


def _make_provenance() -> Provenance:
    return Provenance(
        man_pages=(
            ManPageCitation(
                name="systemctl",
                section="1",
                snippet="systemctl restart restarts a service unit...",
                relevance_score=0.9,
            ),
        ),
        prior_incidents=(
            IncidentCitation(
                incident_id="inc-prev-001",
                title="Previous network timeout",
                outcome="improved",
                similarity_score=0.85,
                match_type="fingerprint",
                successful_actions=("systemctl restart nginx",),
            ),
        ),
        primary_source="man_vault",
    )


def _make_decision_record() -> DecisionRecord:
    return DecisionRecord(
        chosen_approach="Restart the service",
        rationale="Service was in a failed state",
        confidence=ConfidenceBreakdown(
            overall=0.85,
            from_man_vault=0.3,
            from_incident_vault=0.3,
            from_llm=0.25,
        ),
        provenance=_make_provenance(),
        planned_commands=("systemctl restart nginx",),
    )


# =============================================================================
# Test: Report Models
# =============================================================================


class TestReportModels:
    """Tests for report data models."""

    def test_incident_report_summary(self):
        """Test IncidentReportSummary creation."""
        summary = IncidentReportSummary(
            incident_id="inc-001",
            title="PostgreSQL crash",
            domain="service",
            severity="error",
            status="resolved",
            outcome="improved",
            created_at=NOW,
            time_to_resolve_sec=300,
            action_count=5,
            confidence=0.85,
            has_narrative=True,
        )

        assert summary.incident_id == "inc-001"
        assert summary.title == "PostgreSQL crash"
        assert summary.outcome == "improved"
        assert summary.has_narrative is True
        assert summary.time_to_resolve_sec == 300
        assert summary.action_count == 5
        assert summary.confidence == 0.85

    def test_incident_report_summary_frozen(self):
        """Test that IncidentReportSummary is frozen."""
        summary = IncidentReportSummary(
            incident_id="inc-001",
            title="Test",
            domain="net",
            severity="warning",
            status="open",
            outcome="unknown",
            created_at=NOW,
            time_to_resolve_sec=None,
            action_count=0,
            confidence=0.0,
            has_narrative=False,
        )
        with pytest.raises(Exception):
            summary.title = "Changed"  # type: ignore[misc]

    def test_incident_full_report_defaults(self):
        """Test IncidentFullReport with defaults."""
        incident = _make_incident()
        report = IncidentFullReport(
            incident=incident,
            actions=(),
            narrative=None,
            similar_incidents=(),
            report_text="# Report",
        )
        assert report.format == "markdown"
        assert report.narrative is None
        assert report.report_text == "# Report"

    def test_efficacy_report_model(self):
        """Test EfficacyReport model creation."""
        stats = _make_efficacy_stats()
        report = EfficacyReport(
            stats=stats,
            report_text="# Efficacy",
        )
        assert report.format == "markdown"
        assert "Efficacy" in report.report_text

    def test_trend_report_model(self):
        """Test TrendReport model creation."""
        report = TrendReport(
            from_time=NOW - timedelta(days=7),
            to_time=NOW,
            total_incidents=10,
            by_domain={"net": 5, "disk": 5},
            by_outcome={"improved": 8, "no_change": 2},
            avg_time_to_resolve_sec=120.0,
            domain_trends=(),
            report_text="# Trends",
        )
        assert report.total_incidents == 10
        assert report.by_domain["net"] == 5


# =============================================================================
# Test: ReportGenerator -- incident reports
# =============================================================================


class TestReportGenerator:
    """Tests for ReportGenerator class."""

    def test_generator_context_manager(self):
        """Test using generator as context manager."""
        with ReportGenerator() as gen:
            assert gen is not None

    def test_generator_close_is_noop(self):
        """Test that close() is a safe no-op."""
        gen = ReportGenerator()
        gen.close()  # should not raise

    def test_generator_enter_returns_self(self):
        """Test that __enter__ returns the generator itself."""
        gen = ReportGenerator()
        result = gen.__enter__()
        assert result is gen
        gen.__exit__(None, None, None)

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_generate_incident_report_not_found(self, _mock_sim, _mock_narr):
        """Test generating report for nonexistent incident."""
        with patch("elle.daemon.incidents.store.get_incident", return_value=None):
            with ReportGenerator() as gen, pytest.raises(ValueError, match="not found"):
                gen.generate_incident_report("nonexistent-id")

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_generate_incident_report_markdown(self, _mock_sim, _mock_narr):
        """Test generating incident report in Markdown format."""
        incident = _make_incident(
            title="Test PostgreSQL Crash",
            domain="service",
            severity="error",
            summary="PostgreSQL crashed due to disk full.",
            symptoms=("Connection refused", "WAL write errors"),
            root_cause="Disk full on /var partition",
        )
        actions = [_make_action(command="df -h /var")]

        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=actions),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert isinstance(report, IncidentFullReport)
        assert report.format == "markdown"
        assert "# Incident Report:" in report.report_text
        assert "PostgreSQL Crash" in report.report_text
        assert "Disk full" in report.report_text
        assert "Actions Taken" in report.report_text
        assert "Connection refused" in report.report_text

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_generate_incident_report_json(self, _mock_sim, _mock_narr):
        """Test generating incident report in JSON format."""
        incident = _make_incident(incident_id="inc-json", title="Test Incident", domain="net")
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="json")

        assert report.format == "json"
        parsed = json.loads(report.report_text)
        assert "incident" in parsed
        assert parsed["incident"]["title"] == "Test Incident"
        assert "actions" in parsed
        assert "generated_at" in parsed

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_generate_incident_report_text(self, _mock_sim, _mock_narr):
        """Test generating incident report in plain text format."""
        incident = _make_incident(title="Plain Text Test", domain="disk")
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="text")

        assert report.format == "text"
        assert "INCIDENT REPORT:" in report.report_text
        assert "Plain Text Test" in report.report_text
        assert "=" * 60 in report.report_text

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative")
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_generate_incident_report_with_narrative(self, _mock_sim, mock_narr):
        """Test report includes narrative when available."""
        narrative = _make_narrative()
        mock_narr.return_value = narrative
        incident = _make_incident(title="Narrative Test")
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert "Causal Narrative" in report.report_text
        assert "Disk filled up" in report.report_text
        assert "Timeline" in report.report_text
        assert report.narrative is not None

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative")
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_generate_incident_report_narrative_text_format(self, _mock_sim, mock_narr):
        """Test text format includes narrative summary."""
        narrative = _make_narrative()
        mock_narr.return_value = narrative
        incident = _make_incident(title="NarrText Test")
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="text")

        assert "CAUSAL NARRATIVE" in report.report_text
        assert "Disk filled up" in report.report_text


# =============================================================================
# Test: _get_narrative (database interaction)
# =============================================================================


class TestGetNarrative:
    """Tests for the _get_narrative private method with mocked database."""

    def test_get_narrative_returns_narrative(self):
        """Test that _get_narrative returns a Narrative when row exists."""
        mock_cursor = MagicMock()
        chain_json = json.dumps({"chain_id": "c1"})
        timeline_json = json.dumps(["step1", "step2"])
        keywords_json = json.dumps(["disk", "oom"])
        mock_cursor.fetchone.return_value = {
            "chain_json": chain_json,
            "summary": "Something happened",
            "timeline_json": timeline_json,
            "explanation": "Detailed explanation",
            "keywords_json": keywords_json,
            "generated_at": "2025-06-15T12:00:00",
            "model_used": "test-model",
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("elle.daemon.incidents.reports.get_conn") as mock_get_conn:
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            gen = ReportGenerator()
            result = gen._get_narrative("inc-001")

        assert result is not None
        assert result.summary == "Something happened"
        assert result.explanation == "Detailed explanation"
        assert len(result.timeline) == 2
        assert len(result.keywords) == 2
        assert result.model_used == "test-model"

    def test_get_narrative_no_row(self):
        """Test that _get_narrative returns None when no row found."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("elle.daemon.incidents.reports.get_conn") as mock_get_conn:
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            gen = ReportGenerator()
            result = gen._get_narrative("inc-missing")

        assert result is None

    def test_get_narrative_undefined_table(self):
        """Test _get_narrative returns None when table doesn't exist."""
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = psycopg.errors.UndefinedTable("narratives")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("elle.daemon.incidents.reports.get_conn") as mock_get_conn:
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            gen = ReportGenerator()
            result = gen._get_narrative("inc-001")

        assert result is None

    def test_get_narrative_generic_exception(self):
        """Test _get_narrative returns None on generic exception."""
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = RuntimeError("connection lost")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("elle.daemon.incidents.reports.get_conn") as mock_get_conn:
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            gen = ReportGenerator()
            result = gen._get_narrative("inc-001")

        assert result is None

    def test_get_narrative_null_fields(self):
        """Test _get_narrative handles null/empty JSON fields gracefully."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            "chain_json": None,
            "summary": None,
            "timeline_json": None,
            "explanation": None,
            "keywords_json": None,
            "generated_at": None,
            "model_used": None,
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("elle.daemon.incidents.reports.get_conn") as mock_get_conn:
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            gen = ReportGenerator()
            result = gen._get_narrative("inc-001")

        assert result is not None
        assert result.summary == ""
        assert result.timeline == ()
        assert result.keywords == ()
        assert result.explanation == ""
        assert result.model_used == ""


# =============================================================================
# Test: _get_similar_incidents
# =============================================================================


class TestGetSimilarIncidents:
    """Tests for the _get_similar_incidents private method."""

    def test_finds_similar_incidents_same_domain(self):
        """Test finding similar incidents in same domain."""
        target = _make_incident(
            incident_id="inc-target",
            domain="net",
            fingerprint=Fingerprint(entities=("interface:eth0",)),
        )
        similar1 = _make_incident(
            incident_id="inc-sim1",
            domain="net",
            status="resolved",
            fingerprint=Fingerprint(entities=("interface:eth0",)),
        )
        similar2 = _make_incident(
            incident_id="inc-sim2",
            domain="net",
            status="resolved",
            fingerprint=Fingerprint(entities=("interface:eth1",)),
        )

        with patch("elle.daemon.incidents.store.list_incidents", return_value=[similar1, similar2]):
            gen = ReportGenerator()
            result = gen._get_similar_incidents(target, limit=5)

        # Both should be included (same domain = always matches)
        assert len(result) == 2
        ids = {s.incident_id for s in result}
        assert "inc-sim1" in ids
        assert "inc-sim2" in ids

    def test_excludes_self_from_similar(self):
        """Test that similar incidents exclude the current incident."""
        target = _make_incident(incident_id="inc-self", domain="net")
        same = _make_incident(incident_id="inc-self", domain="net", status="resolved")

        with patch("elle.daemon.incidents.store.list_incidents", return_value=[same]):
            gen = ReportGenerator()
            result = gen._get_similar_incidents(target, limit=5)

        assert len(result) == 0

    def test_respects_limit(self):
        """Test that similar incidents respects the limit parameter."""
        target = _make_incident(incident_id="inc-target", domain="net")
        incidents = [
            _make_incident(
                incident_id=f"inc-{i}",
                domain="net",
                status="resolved",
            )
            for i in range(10)
        ]

        with patch("elle.daemon.incidents.store.list_incidents", return_value=incidents):
            gen = ReportGenerator()
            result = gen._get_similar_incidents(target, limit=3)

        assert len(result) == 3

    def test_empty_domain_results(self):
        """Test _get_similar_incidents returns empty tuple with no matches."""
        target = _make_incident(incident_id="inc-lonely", domain="disk")
        with patch("elle.daemon.incidents.store.list_incidents", return_value=[]):
            gen = ReportGenerator()
            result = gen._get_similar_incidents(target)

        assert result == ()


# =============================================================================
# Test: Efficacy Reports
# =============================================================================


class TestEfficacyReports:
    """Tests for efficacy report generation."""

    @patch("elle.daemon.incidents.reports.get_efficacy_stats")
    def test_generate_efficacy_report_empty(self, mock_stats):
        """Test generating efficacy report with no data."""
        mock_stats.return_value = _make_efficacy_stats()
        with ReportGenerator() as gen:
            report = gen.generate_efficacy_report(format="markdown")

        assert isinstance(report, EfficacyReport)
        assert "# ELLE Efficacy Report" in report.report_text

    @patch("elle.daemon.incidents.reports.get_efficacy_stats")
    def test_generate_efficacy_report_with_data(self, mock_stats):
        """Test generating efficacy report with domain data."""
        domain_stats = (
            _make_domain_efficacy("net", 10, 7, 1, 1, 1, 0.8),
            _make_domain_efficacy("disk", 5, 3, 1, 1, 0, 0.7),
            _make_domain_efficacy("service", 8, 6, 1, 1, 0, 0.75),
        )
        mock_stats.return_value = _make_efficacy_stats(
            domain_stats=domain_stats,
            total=23,
            overall_rate=0.75,
        )
        with ReportGenerator() as gen:
            report = gen.generate_efficacy_report(format="markdown")

        assert "Success Rates by Domain" in report.report_text
        assert "net" in report.report_text
        assert "disk" in report.report_text
        assert "service" in report.report_text

    @patch("elle.daemon.incidents.reports.get_efficacy_stats")
    def test_generate_efficacy_report_filtered(self, mock_stats):
        """Test generating efficacy report filtered by domain."""
        domain_stats = (
            _make_domain_efficacy("net", 10, 7, 1, 1, 1, 0.8),
            _make_domain_efficacy("disk", 5, 3, 1, 1, 0, 0.7),
        )
        mock_stats.return_value = _make_efficacy_stats(
            domain_stats=domain_stats,
            total=15,
            overall_rate=0.75,
        )
        with ReportGenerator() as gen:
            report = gen.generate_efficacy_report(domain="net", format="markdown")

        assert "net" in report.report_text

    @patch("elle.daemon.incidents.reports.get_efficacy_stats")
    def test_generate_efficacy_report_json(self, mock_stats):
        """Test generating efficacy report in JSON format."""
        domain_stats = (_make_domain_efficacy("net", 5, 4, 1, 0, 0, 0.9),)
        mock_stats.return_value = _make_efficacy_stats(
            domain_stats=domain_stats,
            total=5,
            overall_rate=0.9,
        )
        with ReportGenerator() as gen:
            report = gen.generate_efficacy_report(format="json")

        parsed = json.loads(report.report_text)
        assert "total_finalized_incidents" in parsed
        assert "overall_success_rate" in parsed

    @patch("elle.daemon.incidents.reports.get_efficacy_stats")
    def test_generate_efficacy_report_json_filtered(self, mock_stats):
        """Test JSON efficacy report filtered by domain."""
        domain_stats = (
            _make_domain_efficacy("net", 5, 4, 1, 0, 0, 0.9),
            _make_domain_efficacy("disk", 3, 2, 1, 0, 0, 0.7),
        )
        mock_stats.return_value = _make_efficacy_stats(
            domain_stats=domain_stats,
            total=8,
            overall_rate=0.8,
        )
        with ReportGenerator() as gen:
            report = gen.generate_efficacy_report(domain="net", format="json")

        parsed = json.loads(report.report_text)
        # Should only contain net domain stats
        if "domain_stats" in parsed:
            for ds in parsed["domain_stats"]:
                assert ds["domain"] == "net"

    @patch("elle.daemon.incidents.reports.get_efficacy_stats")
    def test_generate_efficacy_report_text(self, mock_stats):
        """Test generating efficacy report in text format."""
        domain_stats = (_make_domain_efficacy("disk", 5, 4, 1, 0, 0, 0.8),)
        mock_stats.return_value = _make_efficacy_stats(
            domain_stats=domain_stats,
            total=5,
            overall_rate=0.8,
        )
        with ReportGenerator() as gen:
            report = gen.generate_efficacy_report(format="text")

        assert "ELLE EFFICACY REPORT" in report.report_text
        assert "Total Incidents" in report.report_text
        assert "disk" in report.report_text

    @patch("elle.daemon.incidents.reports.get_efficacy_stats")
    def test_generate_efficacy_report_text_filtered(self, mock_stats):
        """Test text efficacy report filtered by domain."""
        domain_stats = (
            _make_domain_efficacy("net", 5, 4, 1, 0, 0, 0.9),
            _make_domain_efficacy("disk", 3, 2, 1, 0, 0, 0.7),
        )
        mock_stats.return_value = _make_efficacy_stats(
            domain_stats=domain_stats,
            total=8,
        )
        with ReportGenerator() as gen:
            report = gen.generate_efficacy_report(domain="net", format="text")

        assert "ELLE EFFICACY REPORT" in report.report_text
        assert "net" in report.report_text

    @patch("elle.daemon.incidents.reports.get_efficacy_stats")
    def test_efficacy_report_shows_percentages(self, mock_stats):
        """Test that efficacy reports show percentages correctly."""
        domain_stats = (_make_domain_efficacy("net", 10, 8, 0, 2, 0, 0.8),)
        mock_stats.return_value = _make_efficacy_stats(
            domain_stats=domain_stats,
            total=10,
            overall_rate=0.8,
        )
        with ReportGenerator() as gen:
            report = gen.generate_efficacy_report(format="markdown")

        assert "%" in report.report_text

    @patch("elle.daemon.incidents.reports.get_efficacy_stats")
    def test_efficacy_insights_best_domain(self, mock_stats):
        """Test efficacy report Insights section highlights best domain."""
        domain_stats = (
            _make_domain_efficacy("net", 10, 8, 1, 1, 0, 0.85),
            _make_domain_efficacy("disk", 5, 1, 0, 3, 1, 0.3),
        )
        mock_stats.return_value = _make_efficacy_stats(
            domain_stats=domain_stats,
            total=15,
            overall_rate=0.6,
        )
        with ReportGenerator() as gen:
            report = gen.generate_efficacy_report(format="markdown")

        assert "Best performing domain" in report.report_text
        assert "net" in report.report_text
        assert "Needs attention" in report.report_text
        assert "disk" in report.report_text

    @patch("elle.daemon.incidents.reports.get_efficacy_stats")
    def test_efficacy_top_entities(self, mock_stats):
        """Test that efficacy report includes top entities."""
        entity = EntityEfficacy(
            entity="service:nginx",
            total_incidents=5,
            improved_count=4,
            success_rate=0.8,
        )
        mock_stats.return_value = _make_efficacy_stats(
            total=5,
            overall_rate=0.8,
            top_entities=(entity,),
        )
        with ReportGenerator() as gen:
            report = gen.generate_efficacy_report(format="markdown")

        assert "Top Entities" in report.report_text
        assert "service:nginx" in report.report_text

    @patch("elle.daemon.incidents.reports.get_efficacy_stats")
    def test_efficacy_top_approaches(self, mock_stats):
        """Test that efficacy report includes top approaches."""
        approach = SolutionApproachEfficacy(
            approach_signature="abc123",
            domain="net",
            key_commands=("systemctl restart nginx", "nginx -t", "extra-cmd"),
            total_uses=10,
            improved_count=8,
            success_rate=0.8,
            avg_time_to_resolve_sec=120,
        )
        mock_stats.return_value = _make_efficacy_stats(
            total=10,
            overall_rate=0.8,
            top_approaches=(approach,),
        )
        with ReportGenerator() as gen:
            report = gen.generate_efficacy_report(format="markdown")

        assert "Most Effective Approaches" in report.report_text
        assert "systemctl restart nginx" in report.report_text
        # More than 2 commands => should show "+1" suffix
        assert "+1" in report.report_text


# =============================================================================
# Test: Trend Reports
# =============================================================================


class TestTrendReports:
    """Tests for trend report generation."""

    @patch("elle.daemon.incidents.reports.get_all_domain_efficacy", return_value=[])
    @patch("elle.daemon.incidents.store.list_incidents", return_value=[])
    def test_generate_trend_report_empty(self, _mock_list, _mock_eff):
        """Test trend report on empty data."""
        with ReportGenerator() as gen:
            report = gen.generate_trend_report(days=7)

        assert isinstance(report, TrendReport)
        assert report.total_incidents == 0
        assert report.by_domain == {}
        assert report.by_outcome == {}
        assert report.avg_time_to_resolve_sec is None

    @patch("elle.daemon.incidents.reports.get_all_domain_efficacy", return_value=[])
    def test_generate_trend_report_markdown(self, _mock_eff):
        """Test trend report in Markdown format."""
        incidents = [
            _make_incident(
                incident_id=f"inc-{i}", domain="net" if i % 2 == 0 else "disk", status="resolved", outcome="improved"
            )
            for i in range(5)
        ]
        with patch("elle.daemon.incidents.store.list_incidents", return_value=incidents):
            with ReportGenerator() as gen:
                report = gen.generate_trend_report(days=7, format="markdown")

        assert isinstance(report, TrendReport)
        assert report.total_incidents == 5
        assert "ELLE Trend Report" in report.report_text
        assert "net" in report.report_text
        assert "disk" in report.report_text

    @patch("elle.daemon.incidents.reports.get_all_domain_efficacy", return_value=[])
    def test_generate_trend_report_text(self, _mock_eff):
        """Test trend report in text format."""
        incidents = [_make_incident(incident_id="inc-1", domain="net")]
        with patch("elle.daemon.incidents.store.list_incidents", return_value=incidents):
            with ReportGenerator() as gen:
                report = gen.generate_trend_report(days=7, format="text")

        assert "ELLE TREND REPORT" in report.report_text

    @patch("elle.daemon.incidents.reports.get_all_domain_efficacy", return_value=[])
    def test_trend_report_day_range(self, _mock_eff):
        """Test trend report respects the day range -- old incidents excluded."""
        recent = _make_incident(incident_id="inc-recent", domain="net")
        # create an old incident (created 30 days ago -- outside a 7-day window)
        old = IncidentReport(
            incident_id="inc-old",
            title="Old Incident",
            domain="disk",
            created_at=NOW - timedelta(days=30),
            updated_at=NOW - timedelta(days=30),
        )
        with patch("elle.daemon.incidents.store.list_incidents", return_value=[recent, old]):
            with ReportGenerator() as gen:
                report = gen.generate_trend_report(days=7)

        assert report.total_incidents == 1  # only the recent one

    @patch("elle.daemon.incidents.reports.get_all_domain_efficacy", return_value=[])
    def test_trend_report_aggregates_correctly(self, _mock_eff):
        """Test trend report aggregates domains and outcomes correctly."""
        incidents = []
        for i in range(3):
            incidents.append(
                _make_incident(
                    incident_id=f"inc-net-{i}",
                    domain="net",
                    outcome="improved",
                )
            )
        for i in range(2):
            incidents.append(
                _make_incident(
                    incident_id=f"inc-disk-{i}",
                    domain="disk",
                    outcome="partial",
                )
            )

        with patch("elle.daemon.incidents.store.list_incidents", return_value=incidents):
            with ReportGenerator() as gen:
                report = gen.generate_trend_report(days=7)

        assert report.total_incidents == 5
        assert report.by_domain.get("net") == 3
        assert report.by_domain.get("disk") == 2
        assert report.by_outcome.get("improved") == 3
        assert report.by_outcome.get("partial") == 2

    @patch("elle.daemon.incidents.reports.get_all_domain_efficacy", return_value=[])
    def test_trend_report_avg_resolve_time(self, _mock_eff):
        """Test trend report calculates average resolution time."""
        incidents = [
            _make_incident(incident_id="inc-1", time_to_resolve_sec=120),
            _make_incident(incident_id="inc-2", time_to_resolve_sec=180),
            _make_incident(incident_id="inc-3", time_to_resolve_sec=None),
        ]
        with patch("elle.daemon.incidents.store.list_incidents", return_value=incidents):
            with ReportGenerator() as gen:
                report = gen.generate_trend_report(days=7)

        assert report.avg_time_to_resolve_sec == 150.0

    def test_trend_report_with_domain_trends(self):
        """Test trend report includes domain trends in markdown."""
        domain_trends = [
            _make_domain_efficacy("net", 10, 8, 1, 1, 0, 0.8),
        ]
        with (
            patch("elle.daemon.incidents.reports.get_all_domain_efficacy", return_value=domain_trends),
            patch("elle.daemon.incidents.store.list_incidents", return_value=[]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_trend_report(days=7, format="markdown")

        assert "Domain Performance" in report.report_text
        assert "net" in report.report_text

    def test_trend_report_text_with_avg_resolve(self):
        """Test text trend report includes avg resolution time."""
        incidents = [
            _make_incident(incident_id="inc-1", time_to_resolve_sec=120, domain="net"),
        ]
        with (
            patch("elle.daemon.incidents.reports.get_all_domain_efficacy", return_value=[]),
            patch("elle.daemon.incidents.store.list_incidents", return_value=incidents),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_trend_report(days=7, format="text")

        assert "Avg Resolution" in report.report_text


# =============================================================================
# Test: Convenience Functions
# =============================================================================


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_generate_incident_report_function(self, _mock_sim, _mock_narr):
        """Test generate_incident_report convenience function."""
        incident = _make_incident(title="Convenience test")
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
        ):
            report_text = generate_incident_report(incident.incident_id)

        assert "Convenience test" in report_text

    @patch("elle.daemon.incidents.reports.get_efficacy_stats")
    def test_generate_efficacy_report_function(self, mock_stats):
        """Test generate_efficacy_report convenience function."""
        mock_stats.return_value = _make_efficacy_stats()
        report_text = generate_efficacy_report()
        assert "Efficacy Report" in report_text

    @patch("elle.daemon.incidents.reports.get_all_domain_efficacy", return_value=[])
    @patch("elle.daemon.incidents.store.list_incidents", return_value=[])
    def test_generate_trend_report_function(self, _mock_list, _mock_eff):
        """Test generate_trend_report convenience function."""
        report_text = generate_trend_report(days=7)
        assert "Trend Report" in report_text


# =============================================================================
# Test: Report Content Quality
# =============================================================================


class TestReportContent:
    """Tests for report content quality."""

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_markdown_report_has_sections(self, _mock_sim, _mock_narr):
        """Test that Markdown reports have proper sections."""
        incident = _make_incident(
            title="Complete Test",
            domain="service",
            severity="error",
            summary="Test summary",
            symptoms=("Symptom 1", "Symptom 2"),
            root_cause="Test root cause",
            verification_steps=("Step 1", "Step 2"),
        )
        actions = [_make_action(command="test command")]
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=actions),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="markdown")

        text = report.report_text
        assert "## Summary" in text
        assert "## Symptoms" in text
        assert "## Root Cause" in text
        assert "## Actions Taken" in text
        assert "## Verification Steps" in text

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_markdown_report_shows_suspected_causes(self, _mock_sim, _mock_narr):
        """Test that Markdown reports show suspected causes when no root cause."""
        incident = _make_incident(
            title="Causes Test",
            suspected_causes=("DNS misconfiguration", "Firewall rules"),
        )
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert "Suspected Causes" in report.report_text
        assert "DNS misconfiguration" in report.report_text
        assert "Firewall rules" in report.report_text

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_markdown_report_resolution_metrics(self, _mock_sim, _mock_narr):
        """Test that resolution metrics appear in markdown reports."""
        incident = _make_incident(
            title="Metrics Test",
            time_to_resolve_sec=185,
            confidence=0.87,
        )
        actions = [_make_action()]
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=actions),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert "Resolution Metrics" in report.report_text
        assert "3m 5s" in report.report_text  # 185 sec = 3m 5s
        assert "87%" in report.report_text

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_markdown_report_long_command_truncation(self, _mock_sim, _mock_narr):
        """Test that long commands are truncated in action tables."""
        long_cmd = "a" * 100
        incident = _make_incident(title="Long Cmd Test")
        actions = [_make_action(command=long_cmd)]
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=actions),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="markdown")

        # Should be truncated with "..."
        assert "..." in report.report_text
        # The action table should not contain the full 100-char command
        assert long_cmd not in report.report_text

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_markdown_report_action_no_command(self, _mock_sim, _mock_narr):
        """Test action display when command is None."""
        incident = _make_incident(title="No Cmd Test")
        action = IncidentAction(
            id=1,
            incident_id=incident.incident_id,
            step_index=0,
            kind="verify",
            command=None,
            success=True,
        )
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[action]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="markdown")

        # None command should show as "-"
        assert "| `-` |" in report.report_text

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_text_report_action_status(self, _mock_sim, _mock_narr):
        """Test text format shows action success/failure correctly."""
        incident = _make_incident(
            title="Action Status",
            root_cause="Something broke",
        )
        actions = [
            _make_action(command="systemctl restart nginx", success=True),
            _make_action(step_index=1, command="nginx -t", success=False),
        ]
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=actions),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="text")

        assert "[OK]" in report.report_text
        assert "[FAIL]" in report.report_text
        assert "ROOT CAUSE" in report.report_text


# =============================================================================
# Test: Report Formatting
# =============================================================================


class TestReportFormatting:
    """Tests for report output formatting details."""

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_markdown_incident_report_has_header(self, _mock_sim, _mock_narr):
        """Test that Markdown reports start with a proper header."""
        incident = _make_incident(title="Header Test")
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert report.report_text.startswith("# Incident Report: Header Test")

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_markdown_report_includes_id(self, _mock_sim, _mock_narr):
        """Test that Markdown reports include the incident ID."""
        incident = _make_incident(incident_id="inc-id-test", title="ID Test")
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert "`inc-id-test`" in report.report_text

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_markdown_report_includes_domain_and_severity(self, _mock_sim, _mock_narr):
        """Test that Markdown reports include classification info."""
        incident = _make_incident(title="Classification Test", domain="disk", severity="critical")
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert "disk" in report.report_text
        assert "critical" in report.report_text

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_text_report_has_separator_lines(self, _mock_sim, _mock_narr):
        """Test that text reports use proper formatting separators."""
        incident = _make_incident(title="Separator Test")
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="text")

        assert "=" * 60 in report.report_text
        assert "INCIDENT REPORT:" in report.report_text

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_json_report_is_valid_json(self, _mock_sim, _mock_narr):
        """Test that JSON reports produce valid JSON."""
        incident = _make_incident(
            title="JSON Valid Test",
            summary="Testing JSON output",
            symptoms=("Sym 1",),
        )
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="json")

        parsed = json.loads(report.report_text)
        assert "incident" in parsed
        assert parsed["incident"]["title"] == "JSON Valid Test"

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_json_report_includes_actions(self, _mock_sim, _mock_narr):
        """Test that JSON reports include actions array."""
        incident = _make_incident(title="JSON Actions")
        actions = [_make_action(command="df -h")]
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=actions),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="json")

        parsed = json.loads(report.report_text)
        assert "actions" in parsed
        assert len(parsed["actions"]) == 1

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_json_report_includes_narrative(self, _mock_sim, _mock_narr):
        """Test that JSON reports show null narrative when absent."""
        incident = _make_incident(title="JSON No Narrative")
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="json")

        parsed = json.loads(report.report_text)
        assert parsed["narrative"] is None

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    def test_markdown_report_similar_incidents_section(self, _mock_narr):
        """Test that similar incidents appear in markdown report."""
        incident = _make_incident(incident_id="inc-main", title="Main Incident", domain="net")
        similar = _make_incident(
            incident_id="inc-sim",
            title="Similar Problem",
            domain="net",
            status="resolved",
        )
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
            patch("elle.daemon.incidents.store.list_incidents", return_value=[similar]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert "Similar Past Incidents" in report.report_text
        assert "Similar Problem" in report.report_text


# =============================================================================
# Test: Provenance in Reports
# =============================================================================


class TestProvenanceInReports:
    """Tests for provenance rendering in reports."""

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_markdown_report_with_provenance(self, _mock_sim, _mock_narr):
        """Test markdown report includes decision provenance."""
        incident = _make_incident(
            title="Provenance Test",
            decision_record=_make_decision_record(),
        )
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="markdown")

        text = report.report_text
        assert "Decision Provenance" in text
        assert "Man Page References" in text
        assert "systemctl(1)" in text
        assert "Prior Incidents Referenced" in text
        assert "Previous network timeout" in text

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_markdown_report_provenance_summary(self, _mock_sim, _mock_narr):
        """Test provenance summary line in markdown."""
        incident = _make_incident(
            title="Summary Test",
            decision_record=_make_decision_record(),
        )
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert "Suggested because" in report.report_text


# =============================================================================
# Test: Resolved outcome details
# =============================================================================


class TestResolvedOutcome:
    """Tests for resolved/finalized incident report details."""

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    @patch("elle.daemon.incidents.reports.ReportGenerator._get_similar_incidents", return_value=())
    def test_markdown_report_resolved_outcome(self, _mock_sim, _mock_narr):
        """Test that resolved incidents show outcome and verification steps."""
        incident = _make_incident(
            title="Metrics Test",
            outcome="improved",
            status="resolved",
            verification_steps=("nginx is running",),
            time_to_resolve_sec=120,
            confidence=0.9,
        )
        actions = [_make_action(command="systemctl restart nginx")]
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=incident),
            patch("elle.daemon.incidents.store.get_actions", return_value=actions),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert "improved" in report.report_text
        assert "Verification Steps" in report.report_text
        assert "nginx is running" in report.report_text
        assert "Actions Taken" in report.report_text
        assert "Resolution Metrics" in report.report_text


# =============================================================================
# Test: Similar Incidents in Reports
# =============================================================================


class TestSimilarIncidentsInReports:
    """Tests for similar incident discovery within reports."""

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    def test_report_finds_similar_incidents(self, _mock_narr):
        """Test that reports identify similar incidents."""
        target = _make_incident(
            incident_id="inc-target",
            title="Network timeout B",
            domain="net",
            fingerprint=Fingerprint(entities=("interface:eth0",)),
        )
        sim1 = _make_incident(
            incident_id="inc-sim1",
            title="Network timeout A",
            domain="net",
            status="resolved",
            fingerprint=Fingerprint(entities=("interface:eth0",)),
        )
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=target),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
            patch("elle.daemon.incidents.store.list_incidents", return_value=[sim1]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(target.incident_id, format="markdown")

        assert len(report.similar_incidents) >= 1

    @patch("elle.daemon.incidents.reports.ReportGenerator._get_narrative", return_value=None)
    def test_report_similar_incidents_exclude_self(self, _mock_narr):
        """Test that similar incidents exclude the current incident."""
        target = _make_incident(
            incident_id="inc-self",
            domain="net",
            status="resolved",
        )
        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=target),
            patch("elle.daemon.incidents.store.get_actions", return_value=[]),
            patch("elle.daemon.incidents.store.list_incidents", return_value=[target]),
        ):
            with ReportGenerator() as gen:
                report = gen.generate_incident_report(target.incident_id, format="markdown")

        for similar in report.similar_incidents:
            assert similar.incident_id != target.incident_id


# =============================================================================
# Test: ReportGenerator Lifecycle
# =============================================================================


class TestReportGeneratorLifecycle:
    """Tests for ReportGenerator lifecycle management."""

    def test_generator_context_manager_exits_cleanly(self):
        """Test context manager __exit__ path."""
        gen = ReportGenerator()
        gen.__enter__()
        gen.__exit__(None, None, None)
        # close() should be idempotent
        gen.close()

    def test_generator_enter_returns_self(self):
        """Test that __enter__ returns the generator itself."""
        gen = ReportGenerator()
        result = gen.__enter__()
        assert result is gen
        gen.__exit__(None, None, None)

    def test_generator_exit_with_exception(self):
        """Test __exit__ when exception info is passed."""
        gen = ReportGenerator()
        gen.__enter__()
        result = gen.__exit__(ValueError, ValueError("boom"), None)
        # Should not suppress exceptions (returns None/falsy)
        assert not result
