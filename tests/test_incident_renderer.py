"""Tests for incident rendering utilities."""

from datetime import UTC, datetime, timedelta

import pytest

from elle.cli.terminal.incident_renderer import (
    _format_duration,
    _time_ago,
    _truncate,
    render_incident_detail,
    render_incident_list,
    render_incident_markdown,
    render_search_results,
    render_snapshot_diff,
)
from elle.daemon.incidents.models import (
    Fingerprint,
    IncidentAction,
    IncidentReport,
    SystemSnapshot,
)

# =============================================================================
# Helper Function Tests
# =============================================================================


class TestTruncate:
    """Tests for text truncation."""

    def test_no_truncation_needed(self):
        assert _truncate("short", 10) == "short"

    def test_exact_length(self):
        assert _truncate("exact", 5) == "exact"

    def test_truncates_with_ellipsis(self):
        result = _truncate("this is a long string", 10)
        assert len(result) == 10
        assert result.endswith("\u2026")

    def test_custom_ellipsis(self):
        result = _truncate("long text", 7, ellipsis="...")
        assert result.endswith("...")


class TestTimeAgo:
    """Tests for time ago formatting."""

    def test_just_now(self):
        now = datetime.now(UTC)
        assert _time_ago(now) == "just now"

    def test_minutes_ago(self):
        past = datetime.now(UTC) - timedelta(minutes=5)
        assert _time_ago(past) == "5m ago"

    def test_hours_ago(self):
        past = datetime.now(UTC) - timedelta(hours=3)
        assert _time_ago(past) == "3h ago"

    def test_days_ago(self):
        past = datetime.now(UTC) - timedelta(days=2)
        assert _time_ago(past) == "2d ago"

    def test_older_shows_date(self):
        past = datetime.now(UTC) - timedelta(days=10)
        result = _time_ago(past)
        assert "-" in result  # ISO format date


class TestFormatDuration:
    """Tests for duration formatting."""

    def test_none_returns_dash(self):
        assert _format_duration(None) == "-"

    def test_seconds_only(self):
        assert _format_duration(45) == "45s"

    def test_minutes_and_seconds(self):
        assert _format_duration(125) == "2m 5s"

    def test_hours_and_minutes(self):
        assert _format_duration(3725) == "1h 2m"


# =============================================================================
# Incident List Rendering Tests
# =============================================================================


class TestRenderIncidentList:
    """Tests for incident list rendering."""

    @pytest.fixture
    def sample_incidents(self):
        """Create sample incidents for testing."""
        return [
            IncidentReport(
                incident_id="abc123def456",
                title="nginx.service failed to start",
                domain="service",
                severity="error",
                status="resolved",
                outcome="improved",
                created_at=datetime.now(UTC) - timedelta(hours=2),
                updated_at=datetime.now(UTC) - timedelta(hours=1),
            ),
            IncidentReport(
                incident_id="xyz789abc012",
                title="/dev/sda low space warning",
                domain="disk",
                severity="warning",
                status="open",
                outcome="unknown",
                created_at=datetime.now(UTC) - timedelta(days=1),
                updated_at=datetime.now(UTC) - timedelta(days=1),
            ),
        ]

    def test_renders_non_empty_list(self, sample_incidents):
        output = render_incident_list(sample_incidents)
        assert "Recent Incidents" in output
        assert "abc123def" in output
        assert "nginx" in output
        assert "service" in output

    def test_renders_empty_list(self):
        output = render_incident_list([])
        assert "No incidents found" in output

    def test_custom_title(self, sample_incidents):
        output = render_incident_list(sample_incidents, title="Open Incidents")
        assert "Open Incidents" in output

    def test_contains_outcome_indicators(self, sample_incidents):
        output = render_incident_list(sample_incidents)
        # Should contain outcome badge (improved)
        assert "improved" in output or "\u2714" in output

    def test_shows_domain_icons(self, sample_incidents):
        output = render_incident_list(sample_incidents)
        # Should contain domain abbreviations
        assert "svc" in output or "disk" in output


# =============================================================================
# Incident Detail Rendering Tests
# =============================================================================


class TestRenderIncidentDetail:
    """Tests for incident detail rendering."""

    @pytest.fixture
    def detailed_incident(self):
        """Create a detailed incident for testing."""
        return IncidentReport(
            incident_id="detail123abc",
            title="Memory pressure critical",
            domain="oom",
            severity="critical",
            status="resolved",
            outcome="improved",
            summary="System experienced high memory pressure due to runaway process.",
            symptoms=(
                "High swap usage",
                "Slow system response",
                "OOM killer invoked",
            ),
            suspected_causes=(
                "Memory leak in application",
                "Insufficient RAM for workload",
            ),
            root_cause="Memory leak in custom application process",
            verification_steps=(
                "Verified memory usage returned to normal",
                "Confirmed OOM events stopped",
            ),
            time_to_mitigate_sec=300,
            time_to_resolve_sec=600,
            fingerprint=Fingerprint(
                disk_pressure=0.45,
                mem_pressure=0.92,
                swap_pressure=0.80,
                cpu_pressure=2.5,
            ),
            created_at=datetime.now(UTC) - timedelta(hours=6),
            updated_at=datetime.now(UTC) - timedelta(hours=5),
        )

    @pytest.fixture
    def sample_actions(self):
        """Create sample actions for testing."""
        return [
            IncidentAction(
                incident_id="detail123abc",
                step_index=0,
                kind="shell",
                command="pkill -f leaky_app",
                exit_code=0,
                success=True,
                duration_ms=100,
            ),
            IncidentAction(
                incident_id="detail123abc",
                step_index=1,
                kind="shell",
                command="systemctl restart leaky_app",
                exit_code=0,
                success=True,
                duration_ms=2500,
            ),
        ]

    def test_renders_title_and_status(self, detailed_incident):
        output = render_incident_detail(detailed_incident)
        assert "Memory pressure critical" in output
        assert "RESOLVED" in output or "resolved" in output.lower()

    def test_renders_symptoms(self, detailed_incident):
        output = render_incident_detail(detailed_incident)
        assert "High swap usage" in output
        assert "Slow system response" in output

    def test_renders_root_cause(self, detailed_incident):
        output = render_incident_detail(detailed_incident)
        assert "Memory leak" in output

    def test_renders_actions(self, detailed_incident, sample_actions):
        output = render_incident_detail(detailed_incident, actions=sample_actions)
        assert "pkill" in output
        assert "systemctl restart" in output

    def test_renders_outcome(self, detailed_incident):
        output = render_incident_detail(detailed_incident)
        assert "improved" in output

    def test_renders_verification(self, detailed_incident):
        output = render_incident_detail(detailed_incident)
        assert "memory usage returned to normal" in output

    def test_renders_fingerprint(self, detailed_incident):
        output = render_incident_detail(detailed_incident)
        # Should show pressure metrics
        assert "disk=" in output or "mem=" in output


# =============================================================================
# Markdown Export Tests
# =============================================================================


class TestRenderIncidentMarkdown:
    """Tests for markdown export."""

    @pytest.fixture
    def incident_for_export(self):
        """Create an incident for markdown export."""
        return IncidentReport(
            incident_id="export123abc",
            title="Test Incident for Export",
            domain="net",
            severity="warning",
            status="resolved",
            outcome="improved",
            summary="Network connectivity issue resolved.",
            symptoms=("Connection timeouts", "Packet loss"),
            root_cause="DNS resolver misconfiguration",
            verification_steps=("Ping test passed", "DNS resolution works"),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    def test_markdown_has_title(self, incident_for_export):
        md = render_incident_markdown(incident_for_export)
        assert "# Test Incident for Export" in md

    def test_markdown_has_metadata(self, incident_for_export):
        md = render_incident_markdown(incident_for_export)
        assert "**ID:**" in md
        assert "**Status:**" in md
        assert "**Severity:**" in md
        assert "**Domain:**" in md

    def test_markdown_has_symptoms(self, incident_for_export):
        md = render_incident_markdown(incident_for_export)
        assert "## Symptoms" in md
        assert "- Connection timeouts" in md

    def test_markdown_has_root_cause(self, incident_for_export):
        md = render_incident_markdown(incident_for_export)
        assert "## Root Cause" in md
        assert "DNS resolver misconfiguration" in md

    def test_markdown_has_verification(self, incident_for_export):
        md = render_incident_markdown(incident_for_export)
        assert "## Verification" in md
        assert "[x] Ping test passed" in md

    def test_markdown_has_footer(self, incident_for_export):
        md = render_incident_markdown(incident_for_export)
        assert "Generated by ELLE" in md


# =============================================================================
# Snapshot Diff Tests
# =============================================================================


class TestRenderSnapshotDiff:
    """Tests for snapshot diff rendering."""

    @pytest.fixture
    def pre_snapshot(self):
        """Create pre-action snapshot."""
        return SystemSnapshot(
            os="Ubuntu 24.04",
            kernel="6.8.0",
            uptime_sec=86400,
            hostname="testhost",
            cpu_load=(1.5, 1.2, 1.0),
            mem_total_mb=16000,
            mem_free_mb=1500,
            mem_available_mb=2000,  # 87.5% used
            disks=(
                {"mount": "/", "used_pct": 85, "avail_gb": 15},
                {"mount": "/home", "used_pct": 70, "avail_gb": 100},
            ),
            interfaces=({"name": "eth0", "state": "down", "errors": 0},),
            services=({"name": "nginx", "active": False, "failed": True},),
            docker_running=0,
            docker_exited=2,
            temps=(),
            smart=(),
        )

    @pytest.fixture
    def post_snapshot(self):
        """Create post-action snapshot."""
        return SystemSnapshot(
            os="Ubuntu 24.04",
            kernel="6.8.0",
            uptime_sec=86500,
            hostname="testhost",
            cpu_load=(0.5, 0.6, 0.8),
            mem_total_mb=16000,
            mem_free_mb=7500,
            mem_available_mb=8000,  # 50% used
            disks=(
                {"mount": "/", "used_pct": 75, "avail_gb": 25},
                {"mount": "/home", "used_pct": 70, "avail_gb": 100},
            ),
            interfaces=({"name": "eth0", "state": "up", "errors": 0},),
            services=({"name": "nginx", "active": True, "failed": False},),
            docker_running=2,
            docker_exited=0,
            temps=(),
            smart=(),
        )

    def test_shows_memory_change(self, pre_snapshot, post_snapshot):
        diff = render_snapshot_diff(pre_snapshot, post_snapshot)
        # Memory went from ~87% to 50%
        assert "Memory" in diff

    def test_shows_disk_change(self, pre_snapshot, post_snapshot):
        diff = render_snapshot_diff(pre_snapshot, post_snapshot)
        # Disk / went from 85% to 75%
        assert "Disk /" in diff

    def test_shows_interface_change(self, pre_snapshot, post_snapshot):
        diff = render_snapshot_diff(pre_snapshot, post_snapshot)
        # eth0 went from down to up
        assert "eth0" in diff
        assert "up" in diff

    def test_shows_service_change(self, pre_snapshot, post_snapshot):
        diff = render_snapshot_diff(pre_snapshot, post_snapshot)
        # nginx went from failed to active
        assert "nginx" in diff

    def test_no_changes_message(self):
        """Identical snapshots should show no changes."""
        snapshot = SystemSnapshot(
            os="Ubuntu 24.04",
            kernel="6.8.0",
            uptime_sec=1000,
            hostname="testhost",
            cpu_load=(1.0, 1.0, 1.0),
            mem_total_mb=8000,
            mem_free_mb=3500,
            mem_available_mb=4000,
            disks=(),
            interfaces=(),
            services=(),
            docker_running=0,
            docker_exited=0,
            temps=(),
            smart=(),
        )
        diff = render_snapshot_diff(snapshot, snapshot)
        assert "No significant" in diff


# =============================================================================
# Search Results Tests
# =============================================================================


class TestRenderSearchResults:
    """Tests for search results rendering."""

    def test_empty_results(self):
        output = render_search_results("test query", [])
        assert "No incidents found" in output

    def test_shows_query(self):
        output = render_search_results("disk full", [])
        assert "disk full" in output

    def test_shows_results_with_scores(self):
        incident = IncidentReport(
            incident_id="search123",
            title="Disk space warning",
            domain="disk",
            severity="warning",
            status="resolved",
            outcome="improved",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        results = [(incident, 0.85)]
        output = render_search_results("disk", results)
        assert "search123" in output or "Disk space" in output
        assert "85%" in output
