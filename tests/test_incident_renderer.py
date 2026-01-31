"""Tests for incident rendering utilities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from elle.cli.terminal.incident_renderer import (
    _format_duration,
    _pad,
    _time_ago,
    _truncate,
    render_incident_detail,
    render_incident_list,
    render_incident_markdown,
    render_search_results,
    render_snapshot_diff,
    _snapshot_to_markdown,
    Color,
    DOMAIN_ICONS,
    OUTCOME_BADGES,
    SEVERITY_COLORS,
    STATUS_BADGES,
)
from elle.daemon.incidents.models import (
    Fingerprint,
    IncidentAction,
    IncidentReport,
    IncidentSnapshot,
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
        now = datetime.now(timezone.utc)
        assert _time_ago(now) == "just now"

    def test_minutes_ago(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert _time_ago(past) == "5m ago"

    def test_hours_ago(self):
        past = datetime.now(timezone.utc) - timedelta(hours=3)
        assert _time_ago(past) == "3h ago"

    def test_days_ago(self):
        past = datetime.now(timezone.utc) - timedelta(days=2)
        assert _time_ago(past) == "2d ago"

    def test_older_shows_date(self):
        past = datetime.now(timezone.utc) - timedelta(days=10)
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
                created_at=datetime.now(timezone.utc) - timedelta(hours=2),
                updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
            ),
            IncidentReport(
                incident_id="xyz789abc012",
                title="/dev/sda low space warning",
                domain="disk",
                severity="warning",
                status="open",
                outcome="unknown",
                created_at=datetime.now(timezone.utc) - timedelta(days=1),
                updated_at=datetime.now(timezone.utc) - timedelta(days=1),
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
            created_at=datetime.now(timezone.utc) - timedelta(hours=6),
            updated_at=datetime.now(timezone.utc) - timedelta(hours=5),
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
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
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
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        results = [(incident, 0.85)]
        output = render_search_results("disk", results)
        assert "search123" in output or "Disk space" in output
        assert "85%" in output


# =============================================================================
# Additional _pad Tests
# =============================================================================


class TestPad:
    """Tests for text padding with ANSI-aware length."""

    def test_left_align(self):
        result = _pad("abc", 10, align="left")
        assert result == "abc       "

    def test_right_align(self):
        result = _pad("abc", 10, align="right")
        assert result == "       abc"

    def test_center_align(self):
        result = _pad("abc", 10, align="center")
        assert len(result) == 10
        assert result.strip() == "abc"

    def test_text_longer_than_width(self):
        result = _pad("long text here", 5, align="left")
        assert result == "long text here"

    def test_ansi_codes_ignored_in_length(self):
        text = f"{Color.RED}abc{Color.RESET}"
        result = _pad(text, 10, align="left")
        # visible length is 3, so 7 spaces should be added
        assert result.endswith("       ")

    def test_exact_width(self):
        result = _pad("exact", 5, align="left")
        assert result == "exact"


# =============================================================================
# Additional _time_ago Edge Tests
# =============================================================================


class TestTimeAgoEdge:
    def test_naive_datetime_treated_as_utc(self):
        past = datetime.utcnow() - timedelta(minutes=10)
        result = _time_ago(past)
        assert "10m ago" in result

    def test_exactly_one_minute(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=1, seconds=1)
        result = _time_ago(past)
        assert "1m ago" in result

    def test_exactly_one_hour(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1, seconds=1)
        result = _time_ago(past)
        assert "1h ago" in result

    def test_six_days(self):
        past = datetime.now(timezone.utc) - timedelta(days=6)
        assert "6d ago" in _time_ago(past)

    def test_seven_days_shows_date(self):
        past = datetime.now(timezone.utc) - timedelta(days=7)
        result = _time_ago(past)
        assert "-" in result  # YYYY-MM-DD format


# =============================================================================
# Additional _format_duration Tests
# =============================================================================


class TestFormatDurationEdge:
    def test_zero_seconds(self):
        assert _format_duration(0) == "0s"

    def test_exactly_60_seconds(self):
        assert _format_duration(60) == "1m 0s"

    def test_exactly_3600_seconds(self):
        assert _format_duration(3600) == "1h 0m"

    def test_large_duration(self):
        result = _format_duration(7325)  # 2h 2m 5s
        assert "2h" in result
        assert "2m" in result


# =============================================================================
# Additional Incident List Rendering Tests
# =============================================================================


class TestRenderIncidentListAdditional:
    def test_no_header(self):
        output = render_incident_list([], show_header=False)
        assert "No incidents found" in output
        # Double-line box chars should not be present
        assert "\u2554" not in output

    def test_other_domain_uses_default_icon(self):
        incident = IncidentReport(
            incident_id="unknown-domain",
            title="Unknown domain test",
            domain="other",
            severity="info",
            status="open",
            outcome="unknown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        output = render_incident_list([incident])
        assert "other" in output or DOMAIN_ICONS["other"] in output

    def test_long_title_truncated(self):
        incident = IncidentReport(
            incident_id="long-title-test",
            title="x" * 100,
            domain="service",
            severity="info",
            status="open",
            outcome="unknown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        output = render_incident_list([incident])
        # Title should be truncated with ellipsis
        assert "\u2026" in output

    def test_footer_count(self):
        incidents = [
            IncidentReport(
                incident_id=f"inc-{i}",
                title=f"Incident {i}",
                domain="service",
                severity="info",
                status="open",
                outcome="unknown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            for i in range(3)
        ]
        output = render_incident_list(incidents)
        assert "3 incident(s)" in output


# =============================================================================
# Additional Incident Detail Rendering Tests
# =============================================================================


class TestRenderIncidentDetailAdditional:
    def test_no_summary(self):
        incident = IncidentReport(
            incident_id="no-summary",
            title="No Summary Test",
            domain="service",
            severity="info",
            status="open",
            outcome="unknown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        output = render_incident_detail(incident)
        assert "No Summary Test" in output

    def test_suspected_causes_rendered(self):
        incident = IncidentReport(
            incident_id="causes-test",
            title="With Causes",
            domain="service",
            severity="warning",
            status="open",
            outcome="unknown",
            suspected_causes=("Config error", "Resource limit"),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        output = render_incident_detail(incident)
        assert "Config error" in output
        assert "Resource limit" in output

    def test_time_to_mitigate_rendered(self):
        incident = IncidentReport(
            incident_id="ttm-test",
            title="TTM Test",
            domain="service",
            severity="info",
            status="mitigated",
            outcome="partial",
            time_to_mitigate_sec=120,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        output = render_incident_detail(incident)
        assert "2m 0s" in output

    def test_time_to_resolve_rendered(self):
        incident = IncidentReport(
            incident_id="ttr-test",
            title="TTR Test",
            domain="service",
            severity="info",
            status="resolved",
            outcome="improved",
            time_to_resolve_sec=3661,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        output = render_incident_detail(incident)
        assert "1h 1m" in output

    def test_action_with_nonzero_exit_code(self):
        incident = IncidentReport(
            incident_id="action-exit",
            title="Exit code test",
            domain="service",
            severity="info",
            status="open",
            outcome="unknown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        actions = [
            IncidentAction(
                incident_id="action-exit",
                step_index=0,
                kind="shell",
                command="failing_cmd",
                exit_code=127,
                success=False,
                duration_ms=0,
            ),
        ]
        output = render_incident_detail(incident, actions=actions)
        assert "127" in output

    def test_snapshot_diff_in_detail(self):
        incident = IncidentReport(
            incident_id="snap-test",
            title="Snapshot test",
            domain="service",
            severity="info",
            status="resolved",
            outcome="improved",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        pre_snapshot = SystemSnapshot(
            os="Ubuntu 24.04",
            kernel="6.8.0",
            uptime_sec=1000,
            hostname="test",
            cpu_load=(1.0, 1.0, 1.0),
            mem_total_mb=8000,
            mem_free_mb=1000,
            mem_available_mb=1000,  # 87.5% used
            disks=(),
            interfaces=(),
            services=(),
            docker_running=0,
            docker_exited=0,
            temps=(),
            smart=(),
        )
        post_snapshot = SystemSnapshot(
            os="Ubuntu 24.04",
            kernel="6.8.0",
            uptime_sec=1100,
            hostname="test",
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8000,
            mem_free_mb=5000,
            mem_available_mb=6000,  # 25% used
            disks=(),
            interfaces=(),
            services=(),
            docker_running=0,
            docker_exited=0,
            temps=(),
            smart=(),
        )
        snapshots = {
            "pre": IncidentSnapshot(
                incident_id="snap-test",
                which="pre",
                snapshot=pre_snapshot,
            ),
            "post": IncidentSnapshot(
                incident_id="snap-test",
                which="post",
                snapshot=post_snapshot,
            ),
        }
        output = render_incident_detail(incident, snapshots=snapshots)
        assert "STATE CHANGES" in output
        assert "Memory" in output


# =============================================================================
# Additional Snapshot Diff Tests
# =============================================================================


class TestRenderSnapshotDiffAdditional:
    def test_disk_increase(self):
        """Disk usage increase should show red indicator."""
        pre = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=1000, hostname="test",
            cpu_load=(1.0, 1.0, 1.0), mem_total_mb=8000, mem_free_mb=4000,
            mem_available_mb=4000, interfaces=(), services=(),
            docker_running=0, docker_exited=0, temps=(), smart=(),
            disks=({"mount": "/data", "used_pct": 50, "avail_gb": 50},),
        )
        post = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=1100, hostname="test",
            cpu_load=(1.0, 1.0, 1.0), mem_total_mb=8000, mem_free_mb=4000,
            mem_available_mb=4000, interfaces=(), services=(),
            docker_running=0, docker_exited=0, temps=(), smart=(),
            disks=({"mount": "/data", "used_pct": 60, "avail_gb": 40},),
        )
        diff = render_snapshot_diff(pre, post)
        assert "/data" in diff
        assert "\u2191" in diff  # Up arrow

    def test_interface_down(self):
        """Interface going down should show red."""
        pre = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=1000, hostname="test",
            cpu_load=(1.0, 1.0, 1.0), mem_total_mb=8000, mem_free_mb=4000,
            mem_available_mb=4000, disks=(), services=(),
            docker_running=0, docker_exited=0, temps=(), smart=(),
            interfaces=({"name": "wlan0", "state": "up"},),
        )
        post = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=1100, hostname="test",
            cpu_load=(1.0, 1.0, 1.0), mem_total_mb=8000, mem_free_mb=4000,
            mem_available_mb=4000, disks=(), services=(),
            docker_running=0, docker_exited=0, temps=(), smart=(),
            interfaces=({"name": "wlan0", "state": "down"},),
        )
        diff = render_snapshot_diff(pre, post)
        assert "wlan0" in diff
        assert "down" in diff

    def test_interface_unknown_state(self):
        """Interface changing to unknown state."""
        pre = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=1000, hostname="test",
            cpu_load=(1.0, 1.0, 1.0), mem_total_mb=8000, mem_free_mb=4000,
            mem_available_mb=4000, disks=(), services=(),
            docker_running=0, docker_exited=0, temps=(), smart=(),
            interfaces=({"name": "eth1", "state": "up"},),
        )
        post = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=1100, hostname="test",
            cpu_load=(1.0, 1.0, 1.0), mem_total_mb=8000, mem_free_mb=4000,
            mem_available_mb=4000, disks=(), services=(),
            docker_running=0, docker_exited=0, temps=(), smart=(),
            interfaces=({"name": "eth1", "state": "dormant"},),
        )
        diff = render_snapshot_diff(pre, post)
        assert "eth1" in diff
        assert "dormant" in diff

    def test_service_inactive_transition(self):
        """Service transitioning to inactive."""
        pre = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=1000, hostname="test",
            cpu_load=(1.0, 1.0, 1.0), mem_total_mb=8000, mem_free_mb=4000,
            mem_available_mb=4000, disks=(), interfaces=(),
            docker_running=0, docker_exited=0, temps=(), smart=(),
            services=({"name": "test-svc", "active": True, "failed": False},),
        )
        post = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=1100, hostname="test",
            cpu_load=(1.0, 1.0, 1.0), mem_total_mb=8000, mem_free_mb=4000,
            mem_available_mb=4000, disks=(), interfaces=(),
            docker_running=0, docker_exited=0, temps=(), smart=(),
            services=({"name": "test-svc", "active": False, "failed": False},),
        )
        diff = render_snapshot_diff(pre, post)
        assert "test-svc" in diff
        assert "inactive" in diff

    def test_memory_decrease(self):
        """Memory usage decrease should show green."""
        pre = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=1000, hostname="test",
            cpu_load=(1.0, 1.0, 1.0), mem_total_mb=10000, mem_free_mb=1000,
            mem_available_mb=1000, disks=(), interfaces=(), services=(),
            docker_running=0, docker_exited=0, temps=(), smart=(),
        )
        post = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=1100, hostname="test",
            cpu_load=(1.0, 1.0, 1.0), mem_total_mb=10000, mem_free_mb=5000,
            mem_available_mb=5000, disks=(), interfaces=(), services=(),
            docker_running=0, docker_exited=0, temps=(), smart=(),
        )
        diff = render_snapshot_diff(pre, post)
        assert "Memory" in diff
        assert "\u2193" in diff  # Down arrow

    def test_small_memory_change_not_shown(self):
        """Memory changes <= 5% should not be shown."""
        pre = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=1000, hostname="test",
            cpu_load=(1.0, 1.0, 1.0), mem_total_mb=10000, mem_free_mb=4900,
            mem_available_mb=4900, disks=(), interfaces=(), services=(),
            docker_running=0, docker_exited=0, temps=(), smart=(),
        )
        post = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=1100, hostname="test",
            cpu_load=(1.0, 1.0, 1.0), mem_total_mb=10000, mem_free_mb=4800,
            mem_available_mb=4800, disks=(), interfaces=(), services=(),
            docker_running=0, docker_exited=0, temps=(), smart=(),
        )
        diff = render_snapshot_diff(pre, post)
        assert "Memory" not in diff


# =============================================================================
# Additional Markdown Export Tests
# =============================================================================


class TestRenderIncidentMarkdownAdditional:
    def test_no_summary(self):
        incident = IncidentReport(
            incident_id="no-summary-md",
            title="No Summary",
            domain="service",
            severity="info",
            status="open",
            outcome="unknown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        md = render_incident_markdown(incident)
        assert "_No summary provided._" in md

    def test_actions_table(self):
        incident = IncidentReport(
            incident_id="actions-md",
            title="Actions Test",
            domain="service",
            severity="info",
            status="open",
            outcome="unknown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        actions = [
            IncidentAction(
                incident_id="actions-md",
                step_index=0,
                kind="shell",
                command="echo hello",
                exit_code=0,
                success=True,
            ),
            IncidentAction(
                incident_id="actions-md",
                step_index=1,
                kind="shell",
                command="bad|cmd",
                exit_code=1,
                success=False,
            ),
        ]
        md = render_incident_markdown(incident, actions=actions)
        assert "## Actions Taken" in md
        assert "echo hello" in md
        assert "bad\\|cmd" in md  # Pipe escaped

    def test_suspected_causes_section(self):
        incident = IncidentReport(
            incident_id="causes-md",
            title="Causes Test",
            domain="service",
            severity="info",
            status="open",
            outcome="unknown",
            suspected_causes=("Cause A", "Cause B"),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        md = render_incident_markdown(incident)
        assert "## Suspected Causes" in md
        assert "- Cause A" in md

    def test_timing_section(self):
        incident = IncidentReport(
            incident_id="timing-md",
            title="Timing Test",
            domain="service",
            severity="info",
            status="resolved",
            outcome="improved",
            time_to_mitigate_sec=120,
            time_to_resolve_sec=600,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        md = render_incident_markdown(incident)
        assert "## Timing" in md
        assert "2m 0s" in md
        assert "10m 0s" in md

    def test_fingerprint_section(self):
        incident = IncidentReport(
            incident_id="fp-md",
            title="Fingerprint Test",
            domain="oom",
            severity="critical",
            status="resolved",
            outcome="improved",
            fingerprint=Fingerprint(
                disk_pressure=0.3,
                mem_pressure=0.95,
                swap_pressure=0.7,
                cpu_pressure=1.5,
                entities=("nginx", "memcached"),
            ),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        md = render_incident_markdown(incident)
        assert "## Fingerprint" in md
        assert "0.95" in md
        assert "nginx, memcached" in md

    def test_snapshot_sections(self):
        snapshot = SystemSnapshot(
            os="Ubuntu 24.04",
            kernel="6.8.0",
            uptime_sec=3600,
            hostname="test",
            cpu_load=(0.5, 0.6, 0.7),
            mem_total_mb=16000,
            mem_free_mb=8000,
            mem_available_mb=10000,
            disks=({"mount": "/", "used_pct": 50, "avail_gb": 100},),
            interfaces=({"name": "eth0", "state": "up"},),
            services=(),
            docker_running=0,
            docker_exited=0,
            temps=(),
            smart=(),
        )
        incident = IncidentReport(
            incident_id="snap-md",
            title="Snapshot MD Test",
            domain="service",
            severity="info",
            status="resolved",
            outcome="improved",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        snapshots = {
            "pre": IncidentSnapshot(incident_id="snap-md", which="pre", snapshot=snapshot),
            "post": IncidentSnapshot(incident_id="snap-md", which="post", snapshot=snapshot),
        }
        md = render_incident_markdown(incident, snapshots=snapshots)
        assert "## Pre-Action State" in md
        assert "## Post-Action State" in md


# =============================================================================
# _snapshot_to_markdown Tests
# =============================================================================


class TestSnapshotToMarkdown:
    def test_basic_snapshot(self):
        snapshot = SystemSnapshot(
            os="Ubuntu 24.04",
            kernel="6.8.0",
            uptime_sec=7200,
            hostname="test",
            cpu_load=(1.5, 1.2, 0.8),
            mem_total_mb=16000,
            mem_free_mb=8000,
            mem_available_mb=12000,
            disks=({"mount": "/", "used_pct": 45, "avail_gb": 200},),
            interfaces=({"name": "eth0", "state": "up"},),
            services=(),
            docker_running=0,
            docker_exited=0,
            temps=(),
            smart=(),
        )
        md = _snapshot_to_markdown(snapshot)
        assert "Ubuntu 24.04" in md
        assert "6.8.0" in md
        assert "2h 0m" in md
        assert "1.50" in md
        assert "/" in md
        assert "eth0" in md

    def test_snapshot_no_disks(self):
        snapshot = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=100, hostname="test",
            cpu_load=(0.0, 0.0, 0.0), mem_total_mb=4000, mem_free_mb=2000,
            mem_available_mb=2000, disks=(), interfaces=(), services=(),
            docker_running=0, docker_exited=0, temps=(), smart=(),
        )
        md = _snapshot_to_markdown(snapshot)
        assert "Disks" not in md

    def test_snapshot_zero_mem(self):
        """Zero total memory should not cause division by zero."""
        snapshot = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=100, hostname="test",
            cpu_load=(0.0, 0.0, 0.0), mem_total_mb=0, mem_free_mb=0,
            mem_available_mb=0, disks=(), interfaces=(), services=(),
            docker_running=0, docker_exited=0, temps=(), smart=(),
        )
        md = _snapshot_to_markdown(snapshot)
        assert "Memory" in md
        assert "0%" in md


# =============================================================================
# Constants Sanity Tests
# =============================================================================


class TestConstants:
    def test_status_badges_keys(self):
        expected = {"open", "mitigated", "resolved", "false_positive"}
        assert set(STATUS_BADGES.keys()) == expected

    def test_outcome_badges_keys(self):
        expected = {"improved", "partial", "no_change", "worse", "unknown"}
        assert set(OUTCOME_BADGES.keys()) == expected

    def test_severity_colors_keys(self):
        expected = {"critical", "error", "warning", "info"}
        assert set(SEVERITY_COLORS.keys()) == expected

    def test_domain_icons_coverage(self):
        expected_domains = {"net", "disk", "oom", "docker", "auth", "pkg", "fs", "service", "thermal", "smart", "other"}
        assert set(DOMAIN_ICONS.keys()) == expected_domains


# =============================================================================
# Search Results Additional Tests
# =============================================================================


class TestRenderSearchResultsAdditional:
    def test_multiple_results_with_scores(self):
        incidents = []
        for i in range(3):
            incidents.append(
                IncidentReport(
                    incident_id=f"search-multi-{i}",
                    title=f"Search result {i}",
                    domain="service",
                    severity="info",
                    status="resolved",
                    outcome="improved",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
        results = [(inc, 0.9 - i * 0.2) for i, inc in enumerate(incidents)]
        output = render_search_results("service restart", results)
        assert "service restart" in output
        assert "90%" in output
        assert "incident" in output.lower()

    def test_low_score_result(self):
        incident = IncidentReport(
            incident_id="low-score",
            title="Barely relevant",
            domain="other",
            severity="info",
            status="open",
            outcome="unknown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        output = render_search_results("test", [(incident, 0.1)])
        assert "10%" in output
