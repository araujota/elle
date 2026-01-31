from __future__ import annotations

"""Comprehensive tests for elle.daemon.api.routes.

Covers all route handlers with mocked external dependencies:
- set_daemon / get_daemon
- GET /v1/status
- GET /v1/events (search + query paths)
- GET /v1/incidents (with severity, since filtering)
- GET /v1/incident/{id} (found, not found, snapshots, pressures)
- POST /v1/incident/{id}/execute-plan
- GET /v1/health
- GET /v1/metrics
- GET /v1/metrics/prometheus
"""

import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Pre-import mocks for heavy elle internals that are not needed here.
# ---------------------------------------------------------------------------

# Ensure structlog is available (some transitive imports need it)
if "structlog" not in sys.modules:
    sys.modules["structlog"] = MagicMock()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_queue_stats(name: str = "raw", size: int = 10, max_size: int = 10000) -> MagicMock:
    qs = MagicMock()
    qs.name = name
    qs.size = size
    qs.max_size = max_size
    qs.dropped = 0
    qs.processed = 100
    return qs


def _make_daemon_status(
    healthy: bool = True,
    uptime: int = 100,
    pid: int = 12345,
) -> MagicMock:
    status = MagicMock()
    status.started_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    status.uptime_sec = uptime
    status.pid = pid
    status.journal_active = True
    status.kernel_active = True
    status.probes_active = False
    status.api_active = True
    status.raw_queue = _make_queue_stats("raw")
    status.event_queue = _make_queue_stats("events", size=5, max_size=5000)
    status.events_total = 500
    status.incidents_total = 5
    status.healthy = healthy
    status.errors = ()
    return status


def _make_mock_daemon(healthy: bool = True) -> MagicMock:
    daemon = MagicMock()
    daemon.get_status.return_value = _make_daemon_status(healthy=healthy)
    return daemon


def _make_event(
    event_id: str = "evt-001",
    source: str = "journal",
    severity: str = "warning",
    category: str = "disk",
    message: str = "Disk usage high",
    entity: str | None = "disk:/dev/sda1",
    fingerprint: str | None = "abc123",
) -> MagicMock:
    ev = MagicMock()
    ev.event_id = event_id
    ev.ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    ev.source = source
    ev.severity = severity
    ev.category = category
    ev.message = message
    ev.entity = entity
    ev.fingerprint = fingerprint
    return ev


def _make_incident(
    incident_id: str = "inc-001",
    severity: str = "warning",
    status: str = "open",
    domain: str = "disk",
) -> MagicMock:
    inc = MagicMock()
    inc.incident_id = incident_id
    inc.created_at = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    inc.updated_at = datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
    inc.domain = domain
    inc.severity = severity
    inc.status = status
    inc.title = "Disk space warning"
    inc.summary = "Root partition usage above 90%"
    inc.outcome = "unknown"
    inc.event_ids = ("evt-001", "evt-002")
    inc.symptoms = ("high disk usage",)
    inc.suspected_causes = ("log accumulation",)
    inc.root_cause = None
    inc.log_snippets = ("Jan 1 10:00 disk full",)
    inc.decision = {}
    inc.verification_steps = ("check df -h",)
    inc.time_to_mitigate_sec = None
    inc.time_to_resolve_sec = None
    inc.trigger_source = "telemetry"
    inc.trigger_command = None
    return inc


def _make_action(step_index: int = 0) -> MagicMock:
    a = MagicMock()
    a.step_index = step_index
    a.kind = "shell"
    a.command = "du -sh /var/log"
    a.exit_code = 0
    a.success = True
    a.created_at = datetime(2025, 1, 1, 10, 5, 0, tzinfo=timezone.utc)
    return a


def _make_snapshot(
    which: str = "pre",
    mem_total_mb: int = 8000,
    mem_available_mb: int = 4000,
    disk_used_pct: int = 85,
    cpu_load: tuple[float, ...] = (1.5, 1.2, 0.9),
) -> MagicMock:
    snap = MagicMock()
    snap.which = which

    ss = MagicMock()
    ss.collected_at = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    ss.mem_total_mb = mem_total_mb
    ss.mem_available_mb = mem_available_mb
    ss.disks = ({"mount": "/", "used_pct": disk_used_pct, "avail_gb": 10},)
    ss.cpu_load = list(cpu_load)

    snap.snapshot = ss
    return snap


# ---------------------------------------------------------------------------
# Module-level reference management: set_daemon / get_daemon
# ---------------------------------------------------------------------------


class TestSetDaemon:
    """Tests for set_daemon() and get_daemon()."""

    def test_set_daemon_stores_reference(self) -> None:
        """set_daemon should store the daemon reference in the module global."""
        import elle.daemon.api.routes as routes_mod

        mock_daemon = MagicMock()
        original = routes_mod._daemon
        try:
            routes_mod.set_daemon(mock_daemon)
            assert routes_mod._daemon is mock_daemon
        finally:
            routes_mod._daemon = original

    def test_get_daemon_returns_stored_daemon(self) -> None:
        """get_daemon should return the stored daemon reference."""
        import elle.daemon.api.routes as routes_mod

        mock_daemon = MagicMock()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = mock_daemon
            result = routes_mod.get_daemon()
            assert result is mock_daemon
        finally:
            routes_mod._daemon = original

    def test_get_daemon_raises_503_when_none(self) -> None:
        """get_daemon should raise HTTPException(503) when daemon is None."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        original = routes_mod._daemon
        try:
            routes_mod._daemon = None
            with pytest.raises(HTTPException) as exc_info:
                routes_mod.get_daemon()
            assert exc_info.value.status_code == 503
            assert "not initialized" in str(exc_info.value.detail)
        finally:
            routes_mod._daemon = original


# ---------------------------------------------------------------------------
# GET /v1/status
# ---------------------------------------------------------------------------


class TestGetStatus:
    """Tests for the get_status route handler."""

    @pytest.mark.asyncio
    async def test_status_success(self) -> None:
        """get_status should build a StatusResponse from daemon status."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon

            mock_auth = MagicMock()

            with (
                patch(
                    "elle.daemon.telemetry.store.count_events",
                    return_value=42,
                ),
                patch(
                    "elle.daemon.incidents.store.list_incidents",
                    return_value=[_make_incident(), _make_incident(incident_id="inc-002")],
                ),
            ):
                result = await routes_mod.get_status(auth=mock_auth)

            assert result.pid == 12345
            assert result.uptime_sec == 100
            assert result.journal_active is True
            assert result.kernel_active is True
            assert result.probes_active is False
            assert result.api_active is True
            assert result.healthy is True
            assert result.events_total == 500
            assert result.incidents_total == 5
            assert result.events_1h == 42
            assert result.incidents_open == 2
            assert result.raw_queue_size == 10
            assert result.event_queue_size == 5
            assert result.errors == []
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_status_fallback_on_store_error(self) -> None:
        """get_status should fall back to 0 counts when stores raise."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon

            mock_auth = MagicMock()

            with patch(
                "elle.daemon.telemetry.store.count_events",
                side_effect=RuntimeError("DB unavailable"),
            ):
                result = await routes_mod.get_status(auth=mock_auth)

            # Should still succeed with 0 fallback values
            assert result.events_1h == 0
            assert result.incidents_open == 0
            assert result.pid == 12345
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_status_daemon_not_initialized(self) -> None:
        """get_status should raise 503 when daemon is not set."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        original = routes_mod._daemon
        try:
            routes_mod._daemon = None
            mock_auth = MagicMock()

            with pytest.raises(HTTPException) as exc_info:
                await routes_mod.get_status(auth=mock_auth)
            assert exc_info.value.status_code == 503
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_status_unhealthy_daemon(self) -> None:
        """get_status should reflect unhealthy daemon state."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon(healthy=False)
        daemon.get_status.return_value.errors = ("journal connection lost",)
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            with (
                patch("elle.daemon.telemetry.store.count_events", return_value=0),
                patch("elle.daemon.incidents.store.list_incidents", return_value=[]),
            ):
                result = await routes_mod.get_status(auth=mock_auth)

            assert result.healthy is False
            assert "journal connection lost" in result.errors
        finally:
            routes_mod._daemon = original


# ---------------------------------------------------------------------------
# GET /v1/events
# ---------------------------------------------------------------------------


class TestGetEvents:
    """Tests for the get_events route handler."""

    @pytest.mark.asyncio
    async def test_events_query_mode(self) -> None:
        """get_events without search parameter uses query_events."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            events = [_make_event(), _make_event(event_id="evt-002")]

            with (
                patch("elle.daemon.telemetry.store.query_events", return_value=events) as mock_query,
                patch("elle.daemon.telemetry.store.count_events", return_value=2) as mock_count,
                patch("elle.daemon.telemetry.store.search_events"),
            ):
                result = await routes_mod.get_events(
                    since=None,
                    until=None,
                    category="disk",
                    severity="warning",
                    entity=None,
                    source=None,
                    search=None,
                    limit=50,
                    offset=0,
                    auth=mock_auth,
                )

            mock_query.assert_called_once_with(
                since=None,
                until=None,
                category="disk",
                severity="warning",
                entity=None,
                source=None,
                limit=50,
                offset=0,
            )
            mock_count.assert_called_once()
            assert result.total == 2
            assert result.limit == 50
            assert result.offset == 0
            assert len(result.events) == 2
            assert result.events[0].event_id == "evt-001"
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_events_search_mode(self) -> None:
        """get_events with search parameter uses search_events."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            events = [_make_event(event_id="evt-search-1")]

            with (
                patch("elle.daemon.telemetry.store.search_events", return_value=events) as mock_search,
                patch("elle.daemon.telemetry.store.query_events") as mock_query,
                patch("elle.daemon.telemetry.store.count_events") as mock_count,
            ):
                result = await routes_mod.get_events(
                    since=None,
                    until=None,
                    category=None,
                    severity=None,
                    entity=None,
                    source=None,
                    search="disk full",
                    limit=100,
                    offset=0,
                    auth=mock_auth,
                )

            mock_search.assert_called_once_with("disk full", limit=100)
            mock_query.assert_not_called()
            mock_count.assert_not_called()
            assert result.total == 1
            assert result.events[0].event_id == "evt-search-1"
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_events_empty_result(self) -> None:
        """get_events should handle empty result set."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            with (
                patch("elle.daemon.telemetry.store.query_events", return_value=[]),
                patch("elle.daemon.telemetry.store.count_events", return_value=0),
                patch("elle.daemon.telemetry.store.search_events"),
            ):
                result = await routes_mod.get_events(
                    since=None,
                    until=None,
                    category=None,
                    severity=None,
                    entity=None,
                    source=None,
                    search=None,
                    limit=100,
                    offset=0,
                    auth=mock_auth,
                )

            assert result.total == 0
            assert result.events == []
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_events_store_raises_500(self) -> None:
        """get_events should raise 500 on store exception."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            with patch(
                "elle.daemon.telemetry.store.query_events",
                side_effect=RuntimeError("database error"),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await routes_mod.get_events(
                        since=None,
                        until=None,
                        category=None,
                        severity=None,
                        entity=None,
                        source=None,
                        search=None,
                        limit=100,
                        offset=0,
                        auth=mock_auth,
                    )
                assert exc_info.value.status_code == 500
                assert "database error" in str(exc_info.value.detail)
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_events_daemon_not_initialized(self) -> None:
        """get_events should raise 503 when daemon is not set."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        original = routes_mod._daemon
        try:
            routes_mod._daemon = None
            mock_auth = MagicMock()

            with pytest.raises(HTTPException) as exc_info:
                await routes_mod.get_events(
                    since=None,
                    until=None,
                    category=None,
                    severity=None,
                    entity=None,
                    source=None,
                    search=None,
                    limit=100,
                    offset=0,
                    auth=mock_auth,
                )
            assert exc_info.value.status_code == 503
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_events_with_all_filters(self) -> None:
        """get_events should pass all filter params to query_events."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            since_dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
            until_dt = datetime(2025, 1, 2, tzinfo=timezone.utc)

            with (
                patch("elle.daemon.telemetry.store.query_events", return_value=[]) as mock_query,
                patch("elle.daemon.telemetry.store.count_events", return_value=0),
                patch("elle.daemon.telemetry.store.search_events"),
            ):
                await routes_mod.get_events(
                    since=since_dt,
                    until=until_dt,
                    category="oom",
                    severity="critical",
                    entity="service:nginx",
                    source="journal",
                    search=None,
                    limit=10,
                    offset=5,
                    auth=mock_auth,
                )

            mock_query.assert_called_once_with(
                since=since_dt,
                until=until_dt,
                category="oom",
                severity="critical",
                entity="service:nginx",
                source="journal",
                limit=10,
                offset=5,
            )
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_events_event_response_fields(self) -> None:
        """EventResponse objects should contain all expected fields."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            ev = _make_event(
                event_id="evt-fields",
                source="kernel",
                severity="error",
                category="oom",
                message="OOM killer invoked",
                entity="process:nginx",
                fingerprint="fp123",
            )

            with (
                patch("elle.daemon.telemetry.store.query_events", return_value=[ev]),
                patch("elle.daemon.telemetry.store.count_events", return_value=1),
                patch("elle.daemon.telemetry.store.search_events"),
            ):
                result = await routes_mod.get_events(
                    since=None,
                    until=None,
                    category=None,
                    severity=None,
                    entity=None,
                    source=None,
                    search=None,
                    limit=100,
                    offset=0,
                    auth=mock_auth,
                )

            e = result.events[0]
            assert e.event_id == "evt-fields"
            assert e.source == "kernel"
            assert e.severity == "error"
            assert e.category == "oom"
            assert e.message == "OOM killer invoked"
            assert e.entity == "process:nginx"
            assert e.fingerprint == "fp123"
        finally:
            routes_mod._daemon = original


# ---------------------------------------------------------------------------
# GET /v1/incidents
# ---------------------------------------------------------------------------


class TestGetIncidents:
    """Tests for the get_incidents route handler."""

    @pytest.mark.asyncio
    async def test_incidents_basic_list(self) -> None:
        """get_incidents should return incident summaries."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            incidents = [_make_incident(), _make_incident(incident_id="inc-002")]

            with patch(
                "elle.daemon.incidents.store.list_incidents",
                return_value=incidents,
            ) as mock_list:
                result = await routes_mod.get_incidents(
                    since=None,
                    domain=None,
                    status=None,
                    severity=None,
                    search=None,
                    limit=100,
                    offset=0,
                    auth=mock_auth,
                )

            mock_list.assert_called_once_with(
                status=None,
                domain=None,
                limit=100,
                offset=0,
            )
            assert result.total == 2
            assert len(result.incidents) == 2
            assert result.incidents[0].incident_id == "inc-001"
            assert result.incidents[0].event_count == 2
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incidents_filter_by_severity(self) -> None:
        """get_incidents should filter by severity when specified."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            inc_warning = _make_incident(severity="warning")
            inc_error = _make_incident(incident_id="inc-err", severity="error")

            with patch(
                "elle.daemon.incidents.store.list_incidents",
                return_value=[inc_warning, inc_error],
            ):
                result = await routes_mod.get_incidents(
                    since=None,
                    domain=None,
                    status=None,
                    severity="error",
                    search=None,
                    limit=100,
                    offset=0,
                    auth=mock_auth,
                )

            assert result.total == 1
            assert result.incidents[0].severity == "error"
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incidents_filter_by_since(self) -> None:
        """get_incidents should filter by since datetime when specified."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            old_inc = _make_incident()
            old_inc.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)

            new_inc = _make_incident(incident_id="inc-new")
            new_inc.created_at = datetime(2025, 6, 1, tzinfo=timezone.utc)

            since_dt = datetime(2025, 1, 1, tzinfo=timezone.utc)

            with patch(
                "elle.daemon.incidents.store.list_incidents",
                return_value=[old_inc, new_inc],
            ):
                result = await routes_mod.get_incidents(
                    since=since_dt,
                    domain=None,
                    status=None,
                    severity=None,
                    search=None,
                    limit=100,
                    offset=0,
                    auth=mock_auth,
                )

            assert result.total == 1
            assert result.incidents[0].incident_id == "inc-new"
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incidents_empty_result(self) -> None:
        """get_incidents should return empty list when no incidents match."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            with patch(
                "elle.daemon.incidents.store.list_incidents",
                return_value=[],
            ):
                result = await routes_mod.get_incidents(
                    since=None,
                    domain=None,
                    status=None,
                    severity=None,
                    search=None,
                    limit=100,
                    offset=0,
                    auth=mock_auth,
                )

            assert result.total == 0
            assert result.incidents == []
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incidents_store_raises_500(self) -> None:
        """get_incidents should raise 500 on store exception."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            with patch(
                "elle.daemon.incidents.store.list_incidents",
                side_effect=RuntimeError("DB crash"),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await routes_mod.get_incidents(
                        since=None,
                        domain=None,
                        status=None,
                        severity=None,
                        search=None,
                        limit=100,
                        offset=0,
                        auth=mock_auth,
                    )
                assert exc_info.value.status_code == 500
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incidents_daemon_not_initialized(self) -> None:
        """get_incidents should raise 503 when daemon is not set."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        original = routes_mod._daemon
        try:
            routes_mod._daemon = None
            mock_auth = MagicMock()

            with pytest.raises(HTTPException) as exc_info:
                await routes_mod.get_incidents(
                    since=None,
                    domain=None,
                    status=None,
                    severity=None,
                    search=None,
                    limit=100,
                    offset=0,
                    auth=mock_auth,
                )
            assert exc_info.value.status_code == 503
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incidents_passes_status_and_domain(self) -> None:
        """get_incidents should forward status and domain to store."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            with patch(
                "elle.daemon.incidents.store.list_incidents",
                return_value=[],
            ) as mock_list:
                await routes_mod.get_incidents(
                    since=None,
                    domain="net",
                    status="open",
                    severity=None,
                    search=None,
                    limit=50,
                    offset=10,
                    auth=mock_auth,
                )

            mock_list.assert_called_once_with(
                status="open",
                domain="net",
                limit=50,
                offset=10,
            )
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incidents_summary_fields(self) -> None:
        """IncidentSummary should contain correct fields."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            inc = _make_incident()

            with patch(
                "elle.daemon.incidents.store.list_incidents",
                return_value=[inc],
            ):
                result = await routes_mod.get_incidents(
                    since=None,
                    domain=None,
                    status=None,
                    severity=None,
                    search=None,
                    limit=100,
                    offset=0,
                    auth=mock_auth,
                )

            s = result.incidents[0]
            assert s.incident_id == "inc-001"
            assert s.domain == "disk"
            assert s.severity == "warning"
            assert s.status == "open"
            assert s.title == "Disk space warning"
            assert s.outcome == "unknown"
            assert s.event_count == 2
        finally:
            routes_mod._daemon = original


# ---------------------------------------------------------------------------
# GET /v1/incident/{id}
# ---------------------------------------------------------------------------


class TestGetIncidentDetail:
    """Tests for the get_incident route handler."""

    @pytest.mark.asyncio
    async def test_incident_found(self) -> None:
        """get_incident should return full detail for existing incident."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()
            inc = _make_incident()
            actions = [_make_action(0), _make_action(1)]

            pre_snap = _make_snapshot("pre")
            post_snap = _make_snapshot("post", mem_available_mb=6000)

            with (
                patch(
                    "elle.daemon.incidents.store.get_incident",
                    return_value=inc,
                ),
                patch(
                    "elle.daemon.incidents.store.get_actions",
                    return_value=actions,
                ),
                patch(
                    "elle.daemon.incidents.store.get_snapshots",
                    return_value={"pre": pre_snap, "post": post_snap},
                ),
            ):
                result = await routes_mod.get_incident(
                    incident_id="inc-001",
                    auth=mock_auth,
                )

            assert result.incident_id == "inc-001"
            assert result.domain == "disk"
            assert result.severity == "warning"
            assert result.title == "Disk space warning"
            assert result.summary == "Root partition usage above 90%"
            assert len(result.actions) == 2
            assert result.actions[0].step_index == 0
            assert result.actions[0].kind == "shell"
            assert result.actions[0].command == "du -sh /var/log"
            assert result.actions[0].exit_code == 0
            assert result.actions[0].success is True
            assert "pre" in result.snapshots
            assert "post" in result.snapshots
            assert result.trigger_source == "telemetry"
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incident_not_found(self) -> None:
        """get_incident should raise 404 for missing incident."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            with (
                patch(
                    "elle.daemon.incidents.store.get_incident",
                    return_value=None,
                ),
                patch("elle.daemon.incidents.store.get_actions"),
                patch("elle.daemon.incidents.store.get_snapshots"),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await routes_mod.get_incident(
                        incident_id="nonexistent",
                        auth=mock_auth,
                    )
                assert exc_info.value.status_code == 404
                assert "not found" in str(exc_info.value.detail).lower()
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incident_store_raises_500(self) -> None:
        """get_incident should raise 500 on unexpected store exception."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            with patch(
                "elle.daemon.incidents.store.get_incident",
                side_effect=RuntimeError("connection lost"),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await routes_mod.get_incident(
                        incident_id="inc-crash",
                        auth=mock_auth,
                    )
                assert exc_info.value.status_code == 500
                assert "connection lost" in str(exc_info.value.detail)
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incident_daemon_not_initialized(self) -> None:
        """get_incident should raise 503 when daemon is not set."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        original = routes_mod._daemon
        try:
            routes_mod._daemon = None
            mock_auth = MagicMock()

            with pytest.raises(HTTPException) as exc_info:
                await routes_mod.get_incident(
                    incident_id="inc-001",
                    auth=mock_auth,
                )
            assert exc_info.value.status_code == 503
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incident_snapshot_mem_pressure(self) -> None:
        """Snapshot mem_pressure should be computed from mem_total/available."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()
            inc = _make_incident()

            # 8000 total, 2000 available => pressure = 1 - (2000/8000) = 0.75
            snap = _make_snapshot("pre", mem_total_mb=8000, mem_available_mb=2000)

            with (
                patch("elle.daemon.incidents.store.get_incident", return_value=inc),
                patch("elle.daemon.incidents.store.get_actions", return_value=[]),
                patch("elle.daemon.incidents.store.get_snapshots", return_value={"pre": snap}),
            ):
                result = await routes_mod.get_incident(
                    incident_id="inc-001",
                    auth=mock_auth,
                )

            assert result.snapshots["pre"].mem_pressure == 0.75
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incident_snapshot_zero_mem_total(self) -> None:
        """Snapshot mem_pressure should be 0 when mem_total is 0."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()
            inc = _make_incident()

            snap = _make_snapshot("pre", mem_total_mb=0, mem_available_mb=0)

            with (
                patch("elle.daemon.incidents.store.get_incident", return_value=inc),
                patch("elle.daemon.incidents.store.get_actions", return_value=[]),
                patch("elle.daemon.incidents.store.get_snapshots", return_value={"pre": snap}),
            ):
                result = await routes_mod.get_incident(
                    incident_id="inc-001",
                    auth=mock_auth,
                )

            assert result.snapshots["pre"].mem_pressure == 0.0
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incident_snapshot_disk_pressure(self) -> None:
        """Snapshot disk_pressure should be the max used_pct across disks."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()
            inc = _make_incident()

            snap = _make_snapshot("pre", disk_used_pct=92)

            with (
                patch("elle.daemon.incidents.store.get_incident", return_value=inc),
                patch("elle.daemon.incidents.store.get_actions", return_value=[]),
                patch("elle.daemon.incidents.store.get_snapshots", return_value={"pre": snap}),
            ):
                result = await routes_mod.get_incident(
                    incident_id="inc-001",
                    auth=mock_auth,
                )

            # 92 / 100 = 0.92
            assert result.snapshots["pre"].disk_pressure == 0.92
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incident_snapshot_multiple_disks(self) -> None:
        """disk_pressure should pick the highest used_pct across disks."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()
            inc = _make_incident()

            snap = _make_snapshot("pre")
            snap.snapshot.disks = (
                {"mount": "/", "used_pct": 60},
                {"mount": "/home", "used_pct": 95},
                {"mount": "/var", "used_pct": 80},
            )

            with (
                patch("elle.daemon.incidents.store.get_incident", return_value=inc),
                patch("elle.daemon.incidents.store.get_actions", return_value=[]),
                patch("elle.daemon.incidents.store.get_snapshots", return_value={"pre": snap}),
            ):
                result = await routes_mod.get_incident(
                    incident_id="inc-001",
                    auth=mock_auth,
                )

            assert result.snapshots["pre"].disk_pressure == 0.95
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incident_snapshot_no_disks(self) -> None:
        """disk_pressure should be 0 when there are no disks."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()
            inc = _make_incident()

            snap = _make_snapshot("pre")
            snap.snapshot.disks = ()

            with (
                patch("elle.daemon.incidents.store.get_incident", return_value=inc),
                patch("elle.daemon.incidents.store.get_actions", return_value=[]),
                patch("elle.daemon.incidents.store.get_snapshots", return_value={"pre": snap}),
            ):
                result = await routes_mod.get_incident(
                    incident_id="inc-001",
                    auth=mock_auth,
                )

            assert result.snapshots["pre"].disk_pressure == 0.0
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incident_snapshot_cpu_load(self) -> None:
        """Snapshot cpu_load should be the first element of cpu_load list."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()
            inc = _make_incident()

            snap = _make_snapshot("pre", cpu_load=(3.5, 2.1, 1.0))

            with (
                patch("elle.daemon.incidents.store.get_incident", return_value=inc),
                patch("elle.daemon.incidents.store.get_actions", return_value=[]),
                patch("elle.daemon.incidents.store.get_snapshots", return_value={"pre": snap}),
            ):
                result = await routes_mod.get_incident(
                    incident_id="inc-001",
                    auth=mock_auth,
                )

            assert result.snapshots["pre"].cpu_load == 3.5
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incident_snapshot_empty_cpu_load(self) -> None:
        """Snapshot cpu_load should be 0 when cpu_load list is empty."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()
            inc = _make_incident()

            snap = _make_snapshot("pre")
            snap.snapshot.cpu_load = []

            with (
                patch("elle.daemon.incidents.store.get_incident", return_value=inc),
                patch("elle.daemon.incidents.store.get_actions", return_value=[]),
                patch("elle.daemon.incidents.store.get_snapshots", return_value={"pre": snap}),
            ):
                result = await routes_mod.get_incident(
                    incident_id="inc-001",
                    auth=mock_auth,
                )

            assert result.snapshots["pre"].cpu_load == 0
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incident_no_snapshots(self) -> None:
        """get_incident should handle incidents with no snapshots."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()
            inc = _make_incident()

            with (
                patch("elle.daemon.incidents.store.get_incident", return_value=inc),
                patch("elle.daemon.incidents.store.get_actions", return_value=[]),
                patch("elle.daemon.incidents.store.get_snapshots", return_value={}),
            ):
                result = await routes_mod.get_incident(
                    incident_id="inc-001",
                    auth=mock_auth,
                )

            assert result.snapshots == {}
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incident_no_actions(self) -> None:
        """get_incident should handle incidents with no actions."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()
            inc = _make_incident()

            with (
                patch("elle.daemon.incidents.store.get_incident", return_value=inc),
                patch("elle.daemon.incidents.store.get_actions", return_value=[]),
                patch("elle.daemon.incidents.store.get_snapshots", return_value={}),
            ):
                result = await routes_mod.get_incident(
                    incident_id="inc-001",
                    auth=mock_auth,
                )

            assert result.actions == []
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_incident_http_exception_reraise(self) -> None:
        """HTTPException from within (e.g. 404) should be re-raised, not wrapped in 500."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            # Simulate get_incident returning None to trigger 404
            with (
                patch("elle.daemon.incidents.store.get_incident", return_value=None),
                patch("elle.daemon.incidents.store.get_actions"),
                patch("elle.daemon.incidents.store.get_snapshots"),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await routes_mod.get_incident(
                        incident_id="inc-missing",
                        auth=mock_auth,
                    )
                # Should be 404, not 500
                assert exc_info.value.status_code == 404
        finally:
            routes_mod._daemon = original


# ---------------------------------------------------------------------------
# POST /v1/incident/{id}/execute-plan
# ---------------------------------------------------------------------------


class TestExecuteIncidentPlan:
    """Tests for the execute_incident_plan route handler."""

    @pytest.mark.asyncio
    async def test_execute_plan_success(self) -> None:
        """execute_incident_plan should return success dict on good execution."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            mock_result = MagicMock()
            mock_result.incident_id = "FORECAST-123"
            mock_result.executed = True
            mock_result.success = True
            mock_result.error = None

            with patch(
                "elle.daemon.incidents.forecast_handler.execute_prepared_plan",
                new_callable=AsyncMock,
                return_value=mock_result,
            ):
                result = await routes_mod.execute_incident_plan(
                    incident_id="FORECAST-123",
                    auth=mock_auth,
                )

            assert result["incident_id"] == "FORECAST-123"
            assert result["executed"] is True
            assert result["success"] is True
            assert result["error"] is None
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_execute_plan_no_prepared_plan(self) -> None:
        """execute_incident_plan should raise 404 when no prepared plan exists."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            mock_result = MagicMock()
            mock_result.incident_id = "FORECAST-NOPE"
            mock_result.executed = False
            mock_result.success = False
            mock_result.error = "No prepared plan found for FORECAST-NOPE"

            with patch(
                "elle.daemon.incidents.forecast_handler.execute_prepared_plan",
                new_callable=AsyncMock,
                return_value=mock_result,
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await routes_mod.execute_incident_plan(
                        incident_id="FORECAST-NOPE",
                        auth=mock_auth,
                    )
                assert exc_info.value.status_code == 404
                assert "No prepared plan" in str(exc_info.value.detail)
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_execute_plan_failure_not_404(self) -> None:
        """execute_incident_plan should return failure dict when error is not 'No prepared plan'."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            mock_result = MagicMock()
            mock_result.incident_id = "FORECAST-FAIL"
            mock_result.executed = False
            mock_result.success = False
            mock_result.error = "Capability execution failed"

            with patch(
                "elle.daemon.incidents.forecast_handler.execute_prepared_plan",
                new_callable=AsyncMock,
                return_value=mock_result,
            ):
                result = await routes_mod.execute_incident_plan(
                    incident_id="FORECAST-FAIL",
                    auth=mock_auth,
                )

            assert result["success"] is False
            assert result["error"] == "Capability execution failed"
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_execute_plan_handler_raises_500(self) -> None:
        """execute_incident_plan should raise 500 on unexpected exception."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            with patch(
                "elle.daemon.incidents.forecast_handler.execute_prepared_plan",
                new_callable=AsyncMock,
                side_effect=RuntimeError("something broke"),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await routes_mod.execute_incident_plan(
                        incident_id="FORECAST-ERR",
                        auth=mock_auth,
                    )
                assert exc_info.value.status_code == 500
                assert "something broke" in str(exc_info.value.detail)
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_execute_plan_daemon_not_initialized(self) -> None:
        """execute_incident_plan should raise 503 when daemon is not set."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        original = routes_mod._daemon
        try:
            routes_mod._daemon = None
            mock_auth = MagicMock()

            with pytest.raises(HTTPException) as exc_info:
                await routes_mod.execute_incident_plan(
                    incident_id="FORECAST-123",
                    auth=mock_auth,
                )
            assert exc_info.value.status_code == 503
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_execute_plan_success_false_no_error(self) -> None:
        """execute_incident_plan with success=False and error=None should return dict (not 404)."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            mock_result = MagicMock()
            mock_result.incident_id = "FORECAST-EDGE"
            mock_result.executed = False
            mock_result.success = False
            mock_result.error = None

            with patch(
                "elle.daemon.incidents.forecast_handler.execute_prepared_plan",
                new_callable=AsyncMock,
                return_value=mock_result,
            ):
                result = await routes_mod.execute_incident_plan(
                    incident_id="FORECAST-EDGE",
                    auth=mock_auth,
                )

            assert result["success"] is False
            assert result["error"] is None
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_execute_plan_http_exception_reraise(self) -> None:
        """HTTPException from within should be re-raised, not wrapped in 500."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            # Simulate a 404 arising from the "No prepared plan" path
            mock_result = MagicMock()
            mock_result.success = False
            mock_result.error = "No prepared plan for this incident"

            with patch(
                "elle.daemon.incidents.forecast_handler.execute_prepared_plan",
                new_callable=AsyncMock,
                return_value=mock_result,
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await routes_mod.execute_incident_plan(
                        incident_id="FORECAST-404",
                        auth=mock_auth,
                    )
                assert exc_info.value.status_code == 404
        finally:
            routes_mod._daemon = original


# ---------------------------------------------------------------------------
# GET /v1/health
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """Tests for the health_check route handler."""

    @pytest.mark.asyncio
    async def test_health_healthy(self) -> None:
        """health_check should return 'healthy' when daemon is healthy."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon(healthy=True)
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            result = await routes_mod.health_check()
            assert result["status"] == "healthy"
            assert result["uptime_sec"] == 100
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_health_unhealthy(self) -> None:
        """health_check should return 'unhealthy' when daemon is not healthy."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon(healthy=False)
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            result = await routes_mod.health_check()
            assert result["status"] == "unhealthy"
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_health_daemon_not_initialized(self) -> None:
        """health_check should raise 503 when daemon is not set."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        original = routes_mod._daemon
        try:
            routes_mod._daemon = None
            with pytest.raises(HTTPException) as exc_info:
                await routes_mod.health_check()
            assert exc_info.value.status_code == 503
        finally:
            routes_mod._daemon = original


# ---------------------------------------------------------------------------
# GET /v1/metrics
# ---------------------------------------------------------------------------


class TestGetMetrics:
    """Tests for the get_metrics route handler."""

    @pytest.mark.asyncio
    async def test_metrics_success(self) -> None:
        """get_metrics should return JSON metrics from observability system."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            mock_metrics = MagicMock()
            mock_metrics.model_dump.return_value = {
                "events_total": 500,
                "incidents_total": 5,
                "healthy": True,
            }

            with patch(
                "elle.daemon.observability.metrics.collect_observability_metrics",
                new_callable=AsyncMock,
                return_value=mock_metrics,
            ):
                result = await routes_mod.get_metrics(auth=mock_auth)

            assert result["events_total"] == 500
            assert result["incidents_total"] == 5
            assert result["healthy"] is True
            mock_metrics.model_dump.assert_called_once_with(mode="json")
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_metrics_collector_raises_500(self) -> None:
        """get_metrics should raise 500 when the collector fails."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            with patch(
                "elle.daemon.observability.metrics.collect_observability_metrics",
                new_callable=AsyncMock,
                side_effect=RuntimeError("metrics broken"),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await routes_mod.get_metrics(auth=mock_auth)
                assert exc_info.value.status_code == 500
                assert "metrics broken" in str(exc_info.value.detail)
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_metrics_daemon_not_initialized(self) -> None:
        """get_metrics should raise 503 when daemon is not set."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        original = routes_mod._daemon
        try:
            routes_mod._daemon = None
            mock_auth = MagicMock()
            with pytest.raises(HTTPException) as exc_info:
                await routes_mod.get_metrics(auth=mock_auth)
            assert exc_info.value.status_code == 503
        finally:
            routes_mod._daemon = original


# ---------------------------------------------------------------------------
# GET /v1/metrics/prometheus
# ---------------------------------------------------------------------------


class TestGetPrometheusMetrics:
    """Tests for the get_prometheus_metrics route handler."""

    @pytest.mark.asyncio
    async def test_prometheus_metrics_success(self) -> None:
        """get_prometheus_metrics should return PlainTextResponse with Prometheus format."""
        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            mock_metrics = MagicMock()
            prom_output = "# HELP elle_events_total Total events\nelle_events_total 500\n"

            with (
                patch(
                    "elle.daemon.observability.metrics.collect_observability_metrics",
                    new_callable=AsyncMock,
                    return_value=mock_metrics,
                ) as mock_collect,
                patch(
                    "elle.daemon.observability.metrics.format_prometheus",
                    return_value=prom_output,
                ) as mock_format,
            ):
                result = await routes_mod.get_prometheus_metrics(auth=mock_auth)

            mock_collect.assert_called_once()
            mock_format.assert_called_once_with(mock_metrics)

            # Result is a PlainTextResponse
            from fastapi.responses import PlainTextResponse

            assert isinstance(result, PlainTextResponse)
            assert result.body == prom_output.encode()
            assert "text/plain" in result.media_type
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_prometheus_metrics_collector_raises_500(self) -> None:
        """get_prometheus_metrics should raise 500 when collector fails."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            with patch(
                "elle.daemon.observability.metrics.collect_observability_metrics",
                new_callable=AsyncMock,
                side_effect=RuntimeError("collect failed"),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await routes_mod.get_prometheus_metrics(auth=mock_auth)
                assert exc_info.value.status_code == 500
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_prometheus_metrics_format_raises_500(self) -> None:
        """get_prometheus_metrics should raise 500 when formatter fails."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        daemon = _make_mock_daemon()
        original = routes_mod._daemon
        try:
            routes_mod._daemon = daemon
            mock_auth = MagicMock()

            mock_metrics = MagicMock()

            with (
                patch(
                    "elle.daemon.observability.metrics.collect_observability_metrics",
                    new_callable=AsyncMock,
                    return_value=mock_metrics,
                ),
                patch(
                    "elle.daemon.observability.metrics.format_prometheus",
                    side_effect=RuntimeError("format failed"),
                ),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await routes_mod.get_prometheus_metrics(auth=mock_auth)
                assert exc_info.value.status_code == 500
        finally:
            routes_mod._daemon = original

    @pytest.mark.asyncio
    async def test_prometheus_daemon_not_initialized(self) -> None:
        """get_prometheus_metrics should raise 503 when daemon is not set."""
        from fastapi import HTTPException

        import elle.daemon.api.routes as routes_mod

        original = routes_mod._daemon
        try:
            routes_mod._daemon = None
            mock_auth = MagicMock()
            with pytest.raises(HTTPException) as exc_info:
                await routes_mod.get_prometheus_metrics(auth=mock_auth)
            assert exc_info.value.status_code == 503
        finally:
            routes_mod._daemon = original


# ---------------------------------------------------------------------------
# Router metadata
# ---------------------------------------------------------------------------


class TestRouterConfiguration:
    """Tests for the module-level router configuration."""

    def test_router_prefix(self) -> None:
        """Router should have /v1 prefix."""
        from elle.daemon.api.routes import router

        assert router.prefix == "/v1"

    def test_router_tags(self) -> None:
        """Router should have v1 tag."""
        from elle.daemon.api.routes import router

        assert "v1" in router.tags

    def test_router_has_expected_routes(self) -> None:
        """Router should include all expected endpoints."""
        from elle.daemon.api.routes import router

        paths = [route.path for route in router.routes]
        assert "/v1/status" in paths
        assert "/v1/events" in paths
        assert "/v1/incidents" in paths
        assert "/v1/incident/{incident_id}" in paths
        assert "/v1/incident/{incident_id}/execute-plan" in paths
        assert "/v1/health" in paths
        assert "/v1/metrics" in paths
        assert "/v1/metrics/prometheus" in paths
