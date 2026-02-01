"""Coverage tests for elle.daemon.telemetry.store.

Targets 167 missing lines (10.3% -> 80%+) using mocked DB connections.
Covers: insert_event, insert_events_batch, get_event, get_event_by_id,
query_events, search_events, count_events, count_events_by_category,
get_recent_fingerprints, delete_old_events, _row_to_event,
insert_probe_result, get_latest_probe_result, delete_old_probe_results.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.telemetry.models import ProbeResult, TelemetryEvent
from elle.daemon.telemetry.store import (
    _row_to_event,
    count_events,
    count_events_by_category,
    delete_old_events,
    delete_old_probe_results,
    get_event,
    get_event_by_id,
    get_latest_probe_result,
    get_recent_fingerprints,
    insert_event,
    insert_events_batch,
    insert_probe_result,
    query_events,
    search_events,
)

PATCH_GET_CONN = "elle.daemon.telemetry.store.get_conn"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_conn():
    """Mock psycopg connection with cursor."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    cursor.rowcount = 0
    return conn


@pytest.fixture()
def sample_event():
    return TelemetryEvent(
        event_id="evt123",
        ts=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        source="journal",
        severity="error",
        category="oom",
        message="Out of memory: killed process 1234",
        entity="service:nginx",
        fingerprint="aabb1122",
        raw={"PRIORITY": "3"},
    )


@pytest.fixture()
def sample_row():
    return {
        "id": 1,
        "event_id": "evt123",
        "ts": "2025-06-01T12:00:00+00:00",
        "source": "journal",
        "severity": "error",
        "category": "oom",
        "message": "Out of memory: killed process 1234",
        "entity": "service:nginx",
        "fingerprint": "aabb1122",
        "raw": '{"PRIORITY": "3"}',
    }


# ---------------------------------------------------------------------------
# _row_to_event
# ---------------------------------------------------------------------------


class TestRowToEvent:
    def test_with_string_ts(self, sample_row):
        ev = _row_to_event(sample_row)
        assert ev.event_id == "evt123"
        assert ev.source == "journal"
        assert ev.severity == "error"
        assert ev.category == "oom"
        assert ev.entity == "service:nginx"
        assert ev.fingerprint == "aabb1122"
        assert ev.raw == {"PRIORITY": "3"}

    def test_with_datetime_ts(self, sample_row):
        sample_row["ts"] = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        ev = _row_to_event(sample_row)
        assert ev.ts == datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_with_null_entity(self, sample_row):
        sample_row["entity"] = None
        sample_row["fingerprint"] = None
        ev = _row_to_event(sample_row)
        assert ev.entity is None
        assert ev.fingerprint is None


# ---------------------------------------------------------------------------
# insert_event
# ---------------------------------------------------------------------------


class TestInsertEvent:
    def test_with_conn(self, mock_conn, sample_event):
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = {"id": 42}

        result = insert_event(sample_event, conn=mock_conn)

        assert result == 42
        cursor.execute.assert_called_once()
        sql = cursor.execute.call_args[0][0]
        assert "INSERT INTO events" in sql

    def test_with_conn_no_row(self, mock_conn, sample_event):
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = None

        result = insert_event(sample_event, conn=mock_conn)
        assert result == 0

    def test_without_conn(self, sample_event):
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.fetchone.return_value = {"id": 7}

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch(PATCH_GET_CONN, return_value=ctx) as mock_get:
            result = insert_event(sample_event)
            mock_get.assert_called_once_with(schema="telemetry")

        assert result == 7


# ---------------------------------------------------------------------------
# insert_events_batch
# ---------------------------------------------------------------------------


class TestInsertEventsBatch:
    def test_empty_list(self, mock_conn):
        result = insert_events_batch([], conn=mock_conn)
        assert result == 0

    def test_batch_with_conn(self, mock_conn, sample_event):
        cursor = mock_conn.cursor.return_value
        cursor.rowcount = 1

        events = [sample_event, sample_event]
        result = insert_events_batch(events, conn=mock_conn)

        assert result == 2
        assert cursor.execute.call_count == 2

    def test_batch_without_conn(self, sample_event):
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.rowcount = 1

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch(PATCH_GET_CONN, return_value=ctx):
            result = insert_events_batch([sample_event])

        assert result == 1

    def test_batch_conflict_skips(self, mock_conn, sample_event):
        cursor = mock_conn.cursor.return_value
        cursor.rowcount = 0  # ON CONFLICT DO NOTHING

        result = insert_events_batch([sample_event], conn=mock_conn)
        assert result == 0


# ---------------------------------------------------------------------------
# get_event
# ---------------------------------------------------------------------------


class TestGetEvent:
    def test_found(self, mock_conn, sample_row):
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = sample_row

        ev = get_event("evt123", conn=mock_conn)
        assert ev is not None
        assert ev.event_id == "evt123"

    def test_not_found(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = None

        ev = get_event("nonexistent", conn=mock_conn)
        assert ev is None

    def test_without_conn(self, sample_row):
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.fetchone.return_value = sample_row

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch(PATCH_GET_CONN, return_value=ctx):
            ev = get_event("evt123")

        assert ev is not None


# ---------------------------------------------------------------------------
# get_event_by_id
# ---------------------------------------------------------------------------


class TestGetEventById:
    def test_found(self, mock_conn, sample_row):
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = sample_row

        ev = get_event_by_id(1, conn=mock_conn)
        assert ev is not None
        assert ev.event_id == "evt123"

    def test_not_found(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = None

        ev = get_event_by_id(999, conn=mock_conn)
        assert ev is None

    def test_without_conn(self, sample_row):
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.fetchone.return_value = sample_row

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch(PATCH_GET_CONN, return_value=ctx):
            ev = get_event_by_id(1)

        assert ev is not None


# ---------------------------------------------------------------------------
# query_events
# ---------------------------------------------------------------------------


class TestQueryEvents:
    def test_no_filters(self, mock_conn, sample_row):
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = [sample_row]

        events = query_events(conn=mock_conn)
        assert len(events) == 1
        assert events[0].event_id == "evt123"

    def test_all_filters(self, mock_conn, sample_row):
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = [sample_row]

        events = query_events(
            since=datetime(2025, 1, 1, tzinfo=timezone.utc),
            until=datetime(2025, 12, 31, tzinfo=timezone.utc),
            category="oom",
            severity="error",
            entity="service:nginx",
            source="journal",
            limit=50,
            offset=10,
            conn=mock_conn,
        )
        assert len(events) == 1

        # Verify the SQL includes all filter clauses
        sql = cursor.execute.call_args[0][0]
        assert "ts >=" in sql
        assert "ts <=" in sql
        assert "category =" in sql
        assert "severity =" in sql
        assert "entity =" in sql
        assert "source =" in sql
        assert "LIMIT" in sql
        assert "OFFSET" in sql

    def test_empty_results(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = []

        events = query_events(conn=mock_conn)
        assert events == []

    def test_without_conn(self):
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.fetchall.return_value = []

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch(PATCH_GET_CONN, return_value=ctx):
            events = query_events()

        assert events == []


# ---------------------------------------------------------------------------
# search_events
# ---------------------------------------------------------------------------


class TestSearchEvents:
    def test_search(self, mock_conn, sample_row):
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = [sample_row]

        events = search_events("memory", conn=mock_conn)
        assert len(events) == 1

        sql = cursor.execute.call_args[0][0]
        assert "ILIKE" in sql

    def test_search_empty(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = []

        events = search_events("nonexistent", conn=mock_conn)
        assert events == []

    def test_without_conn(self):
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.fetchall.return_value = []

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch(PATCH_GET_CONN, return_value=ctx):
            events = search_events("test")

        assert events == []


# ---------------------------------------------------------------------------
# count_events
# ---------------------------------------------------------------------------


class TestCountEvents:
    def test_no_filters(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = {"cnt": 42}

        count = count_events(conn=mock_conn)
        assert count == 42

    def test_with_filters(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = {"cnt": 5}

        count = count_events(
            since=datetime(2025, 1, 1, tzinfo=timezone.utc),
            category="oom",
            severity="error",
            conn=mock_conn,
        )
        assert count == 5

        sql = cursor.execute.call_args[0][0]
        assert "ts >=" in sql
        assert "category =" in sql
        assert "severity =" in sql

    def test_fetchone_none(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = None

        count = count_events(conn=mock_conn)
        assert count == 0

    def test_without_conn(self):
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.fetchone.return_value = {"cnt": 10}

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch(PATCH_GET_CONN, return_value=ctx):
            count = count_events()

        assert count == 10


# ---------------------------------------------------------------------------
# count_events_by_category
# ---------------------------------------------------------------------------


class TestCountEventsByCategory:
    def test_with_results(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = [
            {"category": "oom", "cnt": 10},
            {"category": "disk", "cnt": 5},
        ]

        result = count_events_by_category(conn=mock_conn)
        assert result == {"oom": 10, "disk": 5}

    def test_with_since_filter(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = [{"category": "auth", "cnt": 3}]

        result = count_events_by_category(
            since=datetime(2025, 1, 1, tzinfo=timezone.utc),
            conn=mock_conn,
        )
        assert result == {"auth": 3}

        sql = cursor.execute.call_args[0][0]
        assert "WHERE ts >=" in sql

    def test_empty(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = []

        result = count_events_by_category(conn=mock_conn)
        assert result == {}

    def test_without_conn(self):
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.fetchall.return_value = []

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch(PATCH_GET_CONN, return_value=ctx):
            result = count_events_by_category()

        assert result == {}


# ---------------------------------------------------------------------------
# get_recent_fingerprints
# ---------------------------------------------------------------------------


class TestGetRecentFingerprints:
    def test_with_results(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = [
            {"fingerprint": "aabb"},
            {"fingerprint": "ccdd"},
        ]

        fps = get_recent_fingerprints(conn=mock_conn)
        assert fps == {"aabb", "ccdd"}

    def test_empty(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = []

        fps = get_recent_fingerprints(conn=mock_conn)
        assert fps == set()

    def test_custom_window(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = []

        fps = get_recent_fingerprints(window_sec=120, conn=mock_conn)
        assert fps == set()

    def test_without_conn(self):
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.fetchall.return_value = []

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch(PATCH_GET_CONN, return_value=ctx):
            fps = get_recent_fingerprints()

        assert fps == set()


# ---------------------------------------------------------------------------
# delete_old_events
# ---------------------------------------------------------------------------


class TestDeleteOldEvents:
    def test_deletes(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.rowcount = 15

        deleted = delete_old_events(older_than_days=30, conn=mock_conn)
        assert deleted == 15

    def test_nothing_to_delete(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.rowcount = 0

        deleted = delete_old_events(conn=mock_conn)
        assert deleted == 0

    def test_without_conn(self):
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.rowcount = 3

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch(PATCH_GET_CONN, return_value=ctx):
            deleted = delete_old_events()

        assert deleted == 3


# ---------------------------------------------------------------------------
# insert_probe_result
# ---------------------------------------------------------------------------


class TestInsertProbeResult:
    def test_with_conn(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = {"id": 10}

        pr = ProbeResult(
            probe_name="disk_check",
            ts=datetime(2025, 6, 1, tzinfo=timezone.utc),
            success=True,
            data={"disk_free": "50G"},
        )
        result = insert_probe_result(pr, conn=mock_conn)
        assert result == 10

    def test_with_conn_no_row(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = None

        pr = ProbeResult(probe_name="test")
        result = insert_probe_result(pr, conn=mock_conn)
        assert result == 0

    def test_without_conn(self):
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.fetchone.return_value = {"id": 5}

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        pr = ProbeResult(probe_name="test")
        with patch(PATCH_GET_CONN, return_value=ctx):
            result = insert_probe_result(pr)

        assert result == 5


# ---------------------------------------------------------------------------
# get_latest_probe_result
# ---------------------------------------------------------------------------


class TestGetLatestProbeResult:
    def test_found_string_ts(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = {
            "probe_name": "disk_check",
            "ts": "2025-06-01T12:00:00+00:00",
            "success": True,
            "data": '{"disk_free": "50G"}',
            "error": None,
        }

        pr = get_latest_probe_result("disk_check", conn=mock_conn)
        assert pr is not None
        assert pr.probe_name == "disk_check"
        assert pr.success is True
        assert pr.data == {"disk_free": "50G"}

    def test_found_datetime_ts(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = {
            "probe_name": "smart_check",
            "ts": datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            "success": False,
            "data": "{}",
            "error": "SMART read failed",
        }

        pr = get_latest_probe_result("smart_check", conn=mock_conn)
        assert pr is not None
        assert pr.success is False
        assert pr.error == "SMART read failed"

    def test_not_found(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = None

        pr = get_latest_probe_result("nonexistent", conn=mock_conn)
        assert pr is None

    def test_without_conn(self):
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.fetchone.return_value = None

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch(PATCH_GET_CONN, return_value=ctx):
            pr = get_latest_probe_result("test")

        assert pr is None


# ---------------------------------------------------------------------------
# delete_old_probe_results
# ---------------------------------------------------------------------------


class TestDeleteOldProbeResults:
    def test_deletes(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.rowcount = 8

        deleted = delete_old_probe_results(older_than_days=7, conn=mock_conn)
        assert deleted == 8

    def test_nothing_to_delete(self, mock_conn):
        cursor = mock_conn.cursor.return_value
        cursor.rowcount = 0

        deleted = delete_old_probe_results(conn=mock_conn)
        assert deleted == 0

    def test_without_conn(self):
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.rowcount = 2

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch(PATCH_GET_CONN, return_value=ctx):
            deleted = delete_old_probe_results()

        assert deleted == 2
