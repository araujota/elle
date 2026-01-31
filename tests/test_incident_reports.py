"""Tests for incident and efficacy report generation."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from elle.daemon.incidents.efficacy_tracker import record_outcome
from elle.daemon.incidents.models import Fingerprint
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
from elle.daemon.incidents.store import (
    append_action,
    create_incident_draft,
    finalize_outcome,
    update_incident,
)


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
            created_at=datetime.utcnow(),
            time_to_resolve_sec=300,
            action_count=5,
            confidence=0.85,
            has_narrative=True,
        )

        assert summary.incident_id == "inc-001"
        assert summary.outcome == "improved"
        assert summary.has_narrative is True


class TestReportGenerator:
    """Tests for ReportGenerator class."""

    def test_generator_context_manager(self, incidents_conn):
        """Test using generator as context manager."""
        conn = incidents_conn

        with ReportGenerator(conn) as gen:
            assert gen is not None

    def test_generate_incident_report_not_found(self, incidents_conn):
        """Test generating report for nonexistent incident."""
        conn = incidents_conn

        with ReportGenerator(conn) as gen, pytest.raises(ValueError, match="not found"):
            gen.generate_incident_report("nonexistent-id")

    def test_generate_incident_report_markdown(self, incidents_conn):
        """Test generating incident report in Markdown format."""
        conn = incidents_conn

        # Create an incident with some data
        incident = create_incident_draft(
            title="Test PostgreSQL Crash",
            domain="service",
            severity="error",
            conn=conn,
        )

        incident = update_incident(
            incident.incident_id,
            summary="PostgreSQL crashed due to disk full.",
            symptoms=("Connection refused", "WAL write errors"),
            root_cause="Disk full on /var partition",
            conn=conn,
        )

        # Add an action
        append_action(
            incident_id=incident.incident_id,
            kind="shell",
            command="df -h /var",
            success=True,
            conn=conn,
        )

        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert isinstance(report, IncidentFullReport)
        assert report.format == "markdown"
        assert "# Incident Report:" in report.report_text
        assert "PostgreSQL Crash" in report.report_text
        assert "Disk full" in report.report_text
        assert "Actions Taken" in report.report_text

    def test_generate_incident_report_json(self, incidents_conn):
        """Test generating incident report in JSON format."""
        conn = incidents_conn

        incident = create_incident_draft(
            title="Test Incident",
            domain="net",
            conn=conn,
        )
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_incident_report(incident.incident_id, format="json")

        assert report.format == "json"
        assert '"incident_id"' in report.report_text
        assert '"title"' in report.report_text

    def test_generate_incident_report_text(self, incidents_conn):
        """Test generating incident report in plain text format."""
        conn = incidents_conn

        incident = create_incident_draft(
            title="Plain Text Test",
            domain="disk",
            conn=conn,
        )
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_incident_report(incident.incident_id, format="text")

        assert report.format == "text"
        assert "INCIDENT REPORT:" in report.report_text
        assert "Plain Text Test" in report.report_text

    def test_generate_efficacy_report_empty(self, incidents_conn):
        """Test generating efficacy report with no data."""
        conn = incidents_conn

        with ReportGenerator(conn) as gen:
            report = gen.generate_efficacy_report(format="markdown")

        assert isinstance(report, EfficacyReport)
        assert "# ELLE Efficacy Report" in report.report_text

    def test_generate_efficacy_report_with_data(self, incidents_conn):
        """Test generating efficacy report with data."""
        conn = incidents_conn

        # Create some incidents with outcomes
        for domain in ["net", "disk", "service"]:
            for outcome in ["improved", "improved", "partial"]:
                incident = create_incident_draft(
                    title=f"Test {domain}",
                    domain=domain,
                    conn=conn,
                )
                incident = update_incident(
                    incident.incident_id,
                    fingerprint=Fingerprint(entities=(f"entity:{domain}",)),
                    conn=conn,
                )
                record_outcome(incident, outcome, conn=conn)

        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_efficacy_report(format="markdown")

        assert "Success Rates by Domain" in report.report_text
        assert "net" in report.report_text
        assert "disk" in report.report_text

    def test_generate_efficacy_report_filtered(self, incidents_conn):
        """Test generating efficacy report filtered by domain."""
        conn = incidents_conn

        # Create incidents in different domains
        for domain in ["net", "disk"]:
            incident = create_incident_draft(title=f"Test {domain}", domain=domain, conn=conn)
            record_outcome(incident, "improved", conn=conn)

        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_efficacy_report(domain="net", format="markdown")

        # Should include net but show filtered view
        assert "net" in report.report_text

    def test_generate_trend_report(self, incidents_conn):
        """Test generating trend report."""
        conn = incidents_conn

        # Create some incidents over time
        for i in range(5):
            incident = create_incident_draft(
                title=f"Trend test {i}",
                domain="net" if i % 2 == 0 else "disk",
                conn=conn,
            )
            # Finalize with outcome
            update_incident(
                incident.incident_id,
                status="resolved",
                outcome="improved",
                conn=conn,
            )

        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_trend_report(days=7, format="markdown")

        assert isinstance(report, TrendReport)
        assert report.total_incidents == 5
        assert "ELLE Trend Report" in report.report_text

    def test_generate_trend_report_text(self, incidents_conn):
        """Test generating trend report in text format."""
        conn = incidents_conn

        create_incident_draft(title="Test", domain="net", conn=conn)
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_trend_report(days=7, format="text")

        assert "ELLE TREND REPORT" in report.report_text


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_generate_incident_report_function(self, incidents_conn):
        """Test generate_incident_report convenience function."""
        conn = incidents_conn

        incident = create_incident_draft(title="Convenience test", conn=conn)
        conn.commit()

        report_text = generate_incident_report(incident.incident_id, conn=conn)
        assert "Convenience test" in report_text

    def test_generate_efficacy_report_function(self, incidents_conn):
        """Test generate_efficacy_report convenience function."""
        conn = incidents_conn

        report_text = generate_efficacy_report(conn=conn)
        assert "Efficacy Report" in report_text

    def test_generate_trend_report_function(self, incidents_conn):
        """Test generate_trend_report convenience function."""
        conn = incidents_conn

        report_text = generate_trend_report(days=7, conn=conn)
        assert "Trend Report" in report_text


class TestReportContent:
    """Tests for report content quality."""

    def test_markdown_report_has_sections(self, incidents_conn):
        """Test that Markdown reports have proper sections."""
        conn = incidents_conn

        incident = create_incident_draft(
            title="Complete Test",
            domain="service",
            severity="error",
            conn=conn,
        )
        update_incident(
            incident.incident_id,
            summary="Test summary",
            symptoms=("Symptom 1", "Symptom 2"),
            root_cause="Test root cause",
            verification_steps=("Step 1", "Step 2"),
            conn=conn,
        )
        append_action(
            incident_id=incident.incident_id,
            kind="shell",
            command="test command",
            success=True,
            conn=conn,
        )
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_incident_report(incident.incident_id, format="markdown")

        text = report.report_text
        assert "## Summary" in text
        assert "## Symptoms" in text
        assert "## Root Cause" in text
        assert "## Actions Taken" in text
        assert "## Verification Steps" in text

    def test_efficacy_report_shows_percentages(self, incidents_conn):
        """Test that efficacy reports show percentages correctly."""
        conn = incidents_conn

        # Create incidents with known outcomes
        for _ in range(8):
            incident = create_incident_draft(title="Good", domain="net", conn=conn)
            record_outcome(incident, "improved", conn=conn)

        for _ in range(2):
            incident = create_incident_draft(title="Bad", domain="net", conn=conn)
            record_outcome(incident, "no_change", conn=conn)

        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_efficacy_report(format="markdown")

        # Should show success rate percentage
        assert "%" in report.report_text

    def test_trend_report_aggregates_correctly(self, incidents_conn):
        """Test that trend reports aggregate data correctly."""
        conn = incidents_conn

        # Create 3 net incidents and 2 disk incidents
        for _ in range(3):
            create_incident_draft(title="Net", domain="net", conn=conn)
        for _ in range(2):
            create_incident_draft(title="Disk", domain="disk", conn=conn)

        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_trend_report(days=7)

        assert report.total_incidents == 5
        assert report.by_domain.get("net") == 3
        assert report.by_domain.get("disk") == 2


class TestReportFormatting:
    """Tests for report output formatting details."""

    def test_markdown_incident_report_has_header(self, incidents_conn):
        """Test that Markdown reports start with a proper header."""
        conn = incidents_conn

        incident = create_incident_draft(title="Header Test", conn=conn)
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert report.report_text.startswith("# Incident Report: Header Test")

    def test_markdown_report_includes_id(self, incidents_conn):
        """Test that Markdown reports include the incident ID."""
        conn = incidents_conn

        incident = create_incident_draft(title="ID Test", conn=conn)
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert f"`{incident.incident_id}`" in report.report_text

    def test_markdown_report_includes_domain_and_severity(self, incidents_conn):
        """Test that Markdown reports include classification info."""
        conn = incidents_conn

        incident = create_incident_draft(
            title="Classification Test",
            domain="disk",
            severity="critical",
            conn=conn,
        )
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert "disk" in report.report_text
        assert "critical" in report.report_text

    def test_text_report_has_separator_lines(self, incidents_conn):
        """Test that text reports use proper formatting separators."""
        conn = incidents_conn

        incident = create_incident_draft(title="Separator Test", conn=conn)
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_incident_report(incident.incident_id, format="text")

        assert "=" * 60 in report.report_text
        assert "INCIDENT REPORT:" in report.report_text

    def test_json_report_is_valid_json(self, incidents_conn):
        """Test that JSON reports produce valid JSON."""
        conn = incidents_conn

        incident = create_incident_draft(title="JSON Valid Test", conn=conn)
        update_incident(
            incident.incident_id,
            summary="Testing JSON output",
            symptoms=("Sym 1",),
            conn=conn,
        )
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_incident_report(incident.incident_id, format="json")

        # Should parse without error
        parsed = json.loads(report.report_text)
        assert "incident" in parsed
        assert parsed["incident"]["title"] == "JSON Valid Test"

    def test_json_report_includes_actions(self, incidents_conn):
        """Test that JSON reports include actions array."""
        conn = incidents_conn

        incident = create_incident_draft(title="JSON Actions", conn=conn)
        append_action(
            incident.incident_id,
            kind="shell",
            command="df -h",
            success=True,
            conn=conn,
        )
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_incident_report(incident.incident_id, format="json")

        parsed = json.loads(report.report_text)
        assert "actions" in parsed
        assert len(parsed["actions"]) == 1

    def test_markdown_report_shows_suspected_causes(self, incidents_conn):
        """Test that Markdown reports show suspected causes when no root cause."""
        conn = incidents_conn

        incident = create_incident_draft(title="Causes Test", conn=conn)
        update_incident(
            incident.incident_id,
            suspected_causes=("DNS misconfiguration", "Firewall rules"),
            conn=conn,
        )
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert "Suspected Causes" in report.report_text

    def test_markdown_report_resolved_outcome(self, incidents_conn):
        """Test that resolved incidents show outcome and verification steps."""
        conn = incidents_conn

        incident = create_incident_draft(title="Metrics Test", conn=conn)
        append_action(
            incident.incident_id,
            kind="shell",
            command="systemctl restart nginx",
            success=True,
            conn=conn,
        )
        finalize_outcome(
            incident.incident_id,
            outcome="improved",
            verification_steps=["nginx is running"],
            conn=conn,
        )
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_incident_report(incident.incident_id, format="markdown")

        assert "improved" in report.report_text
        assert "Verification Steps" in report.report_text
        assert "nginx is running" in report.report_text
        assert "Actions Taken" in report.report_text


class TestEfficacyReportFormats:
    """Tests for efficacy report format variations."""

    def test_efficacy_report_json_format(self, incidents_conn):
        """Test generating efficacy report in JSON format."""
        conn = incidents_conn

        incident = create_incident_draft(title="JSON eff", domain="net", conn=conn)
        record_outcome(incident, "improved", conn=conn)
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_efficacy_report(format="json")

        parsed = json.loads(report.report_text)
        assert "total_finalized_incidents" in parsed
        assert "overall_success_rate" in parsed

    def test_efficacy_report_text_format(self, incidents_conn):
        """Test generating efficacy report in text format."""
        conn = incidents_conn

        incident = create_incident_draft(title="Text eff", domain="disk", conn=conn)
        record_outcome(incident, "improved", conn=conn)
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_efficacy_report(format="text")

        assert "ELLE EFFICACY REPORT" in report.report_text
        assert "Total Incidents" in report.report_text


class TestTrendReportEdgeCases:
    """Tests for trend report edge cases."""

    def test_trend_report_empty_database(self, incidents_conn):
        """Test trend report on an empty database."""
        conn = incidents_conn

        with ReportGenerator(conn) as gen:
            report = gen.generate_trend_report(days=7)

        assert report.total_incidents == 0
        assert report.by_domain == {}
        assert report.by_outcome == {}

    def test_trend_report_day_range(self, incidents_conn):
        """Test trend report respects the day range."""
        conn = incidents_conn

        # All incidents are created 'now', so a 1-day range should include them
        for _ in range(3):
            create_incident_draft(title="Recent", domain="net", conn=conn)
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_trend_report(days=1)

        assert report.total_incidents == 3

    def test_trend_report_outcome_aggregation(self, incidents_conn):
        """Test that trend report aggregates outcomes correctly."""
        conn = incidents_conn

        for outcome in ["improved", "improved", "partial", "no_change"]:
            inc = create_incident_draft(title=f"Trend {outcome}", domain="net", conn=conn)
            update_incident(inc.incident_id, outcome=outcome, conn=conn)
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_trend_report(days=7)

        assert report.total_incidents == 4
        assert report.by_outcome.get("improved") == 2
        assert report.by_outcome.get("partial") == 1
        assert report.by_outcome.get("no_change") == 1


class TestSimilarIncidentsInReports:
    """Tests for similar incident discovery within reports."""

    def test_report_finds_similar_incidents(self, incidents_conn):
        """Test that reports identify similar incidents."""
        conn = incidents_conn

        # Create resolved incidents in same domain
        inc1 = create_incident_draft(title="Network timeout A", domain="net", conn=conn)
        update_incident(
            inc1.incident_id,
            fingerprint=Fingerprint(entities=("interface:eth0",)),
            conn=conn,
        )
        finalize_outcome(inc1.incident_id, "improved", conn=conn)

        inc2 = create_incident_draft(title="Network timeout B", domain="net", conn=conn)
        update_incident(
            inc2.incident_id,
            fingerprint=Fingerprint(entities=("interface:eth0",)),
            conn=conn,
        )
        finalize_outcome(inc2.incident_id, "improved", conn=conn)

        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_incident_report(inc2.incident_id, format="markdown")

        # The report should find inc1 as a similar incident
        assert len(report.similar_incidents) >= 1

    def test_report_similar_incidents_exclude_self(self, incidents_conn):
        """Test that similar incidents exclude the current incident."""
        conn = incidents_conn

        inc = create_incident_draft(title="Self test", domain="net", conn=conn)
        finalize_outcome(inc.incident_id, "improved", conn=conn)
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_incident_report(inc.incident_id, format="markdown")

        for similar in report.similar_incidents:
            assert similar.incident_id != inc.incident_id


class TestReportGeneratorLifecycle:
    """Tests for ReportGenerator lifecycle management."""

    def test_generator_closes_own_connection(self, incidents_conn):
        """Test that generator closes its own connection properly."""
        conn = incidents_conn

        # When passing an existing connection, generator should NOT close it
        gen = ReportGenerator(conn)
        gen.close()
        # conn should still be usable
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM incidents")
        assert cursor.fetchone()[0] == 0

    def test_generator_enter_returns_self(self, incidents_conn):
        """Test that __enter__ returns the generator itself."""
        conn = incidents_conn

        gen = ReportGenerator(conn)
        result = gen.__enter__()
        assert result is gen
        gen.__exit__(None, None, None)
