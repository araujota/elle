"""Tests for incident and efficacy report generation."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

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
from elle.daemon.incidents.schema import ensure_schema, get_connection
from elle.daemon.incidents.store import (
    append_action,
    create_incident_draft,
    finalize_outcome,
    update_incident,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    ensure_schema(conn)
    yield conn, db_path

    conn.close()
    db_path.unlink()


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

    def test_generator_context_manager(self, temp_db):
        """Test using generator as context manager."""
        conn, _ = temp_db

        with ReportGenerator(conn) as gen:
            assert gen is not None

    def test_generate_incident_report_not_found(self, temp_db):
        """Test generating report for nonexistent incident."""
        conn, _ = temp_db

        with ReportGenerator(conn) as gen:
            with pytest.raises(ValueError, match="not found"):
                gen.generate_incident_report("nonexistent-id")

    def test_generate_incident_report_markdown(self, temp_db):
        """Test generating incident report in Markdown format."""
        conn, _ = temp_db

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

    def test_generate_incident_report_json(self, temp_db):
        """Test generating incident report in JSON format."""
        conn, _ = temp_db

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

    def test_generate_incident_report_text(self, temp_db):
        """Test generating incident report in plain text format."""
        conn, _ = temp_db

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

    def test_generate_efficacy_report_empty(self, temp_db):
        """Test generating efficacy report with no data."""
        conn, _ = temp_db

        with ReportGenerator(conn) as gen:
            report = gen.generate_efficacy_report(format="markdown")

        assert isinstance(report, EfficacyReport)
        assert "# ELLE Efficacy Report" in report.report_text

    def test_generate_efficacy_report_with_data(self, temp_db):
        """Test generating efficacy report with data."""
        conn, _ = temp_db

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

    def test_generate_efficacy_report_filtered(self, temp_db):
        """Test generating efficacy report filtered by domain."""
        conn, _ = temp_db

        # Create incidents in different domains
        for domain in ["net", "disk"]:
            incident = create_incident_draft(title=f"Test {domain}", domain=domain, conn=conn)
            record_outcome(incident, "improved", conn=conn)

        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_efficacy_report(domain="net", format="markdown")

        # Should include net but show filtered view
        assert "net" in report.report_text

    def test_generate_trend_report(self, temp_db):
        """Test generating trend report."""
        conn, _ = temp_db

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

    def test_generate_trend_report_text(self, temp_db):
        """Test generating trend report in text format."""
        conn, _ = temp_db

        incident = create_incident_draft(title="Test", domain="net", conn=conn)
        conn.commit()

        with ReportGenerator(conn) as gen:
            report = gen.generate_trend_report(days=7, format="text")

        assert "ELLE TREND REPORT" in report.report_text


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_generate_incident_report_function(self, temp_db):
        """Test generate_incident_report convenience function."""
        conn, _ = temp_db

        incident = create_incident_draft(title="Convenience test", conn=conn)
        conn.commit()

        report_text = generate_incident_report(incident.incident_id, conn=conn)
        assert "Convenience test" in report_text

    def test_generate_efficacy_report_function(self, temp_db):
        """Test generate_efficacy_report convenience function."""
        conn, _ = temp_db

        report_text = generate_efficacy_report(conn=conn)
        assert "Efficacy Report" in report_text

    def test_generate_trend_report_function(self, temp_db):
        """Test generate_trend_report convenience function."""
        conn, _ = temp_db

        report_text = generate_trend_report(days=7, conn=conn)
        assert "Trend Report" in report_text


class TestReportContent:
    """Tests for report content quality."""

    def test_markdown_report_has_sections(self, temp_db):
        """Test that Markdown reports have proper sections."""
        conn, _ = temp_db

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

    def test_efficacy_report_shows_percentages(self, temp_db):
        """Test that efficacy reports show percentages correctly."""
        conn, _ = temp_db

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

    def test_trend_report_aggregates_correctly(self, temp_db):
        """Test that trend reports aggregate data correctly."""
        conn, _ = temp_db

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
