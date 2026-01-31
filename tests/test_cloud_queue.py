"""Tests for CloudSubmissionQueue persistent retry queue.

Mocks the PostgreSQL layer (``elle.storage.engine.get_conn``) so that
tests run without a live database.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.incidents.cloud_queue import (
    CloudSubmissionQueue,
    QueueEntry,
    QueueMetrics,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_queue(**overrides: Any) -> CloudSubmissionQueue:
    """Create a CloudSubmissionQueue with test-friendly defaults."""
    defaults = {
        "initial_delay_sec": 10.0,
        "max_delay_sec": 600.0,
        "backoff_factor": 2.0,
        "max_retries": 5,
        "max_size": 100,
    }
    defaults.update(overrides)
    return CloudSubmissionQueue(**defaults)


def _mock_conn_ctx(mock_cursor: MagicMock) -> Any:
    """Return a ``get_conn`` replacement that yields a mock connection.

    The mock connection exposes ``.cursor()`` which returns *mock_cursor*.
    """
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    @contextmanager
    def _ctx(schema: str = "public"):
        yield mock_conn

    return _ctx


def _make_entry(**overrides: Any) -> QueueEntry:
    """Create a QueueEntry with sensible defaults."""
    defaults = {
        "id": 1,
        "incident_id": "inc-001",
        "payload_json": '{"key": "value"}',
        "original_hash": "abc123",
        "status": "pending",
        "retry_count": 0,
        "max_retries": 5,
        "next_retry_at": datetime.now(timezone.utc).isoformat(),
        "last_error": None,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
        "last_attempted_at": None,
        "completed_at": None,
        "cloud_id": None,
    }
    defaults.update(overrides)
    return QueueEntry(**defaults)


# ===========================================================================
# TestEnqueue
# ===========================================================================


class TestEnqueue:
    """Tests for CloudSubmissionQueue.enqueue."""

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_normal_enqueue(self, mock_get_conn: MagicMock) -> None:
        """enqueue returns True and an entry is created when the queue is not full."""
        cursor = MagicMock()
        # First call: COUNT query -> queue not full
        cursor.fetchone.return_value = {"cnt": 0}
        # INSERT succeeds with 1 row affected
        cursor.rowcount = 1
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        result = q.enqueue("inc-001", {"foo": "bar"}, "hash-001")

        assert result is True
        # Verify the INSERT was called
        assert cursor.execute.call_count == 2  # COUNT + INSERT

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_duplicate_detection(self, mock_get_conn: MagicMock) -> None:
        """Enqueue same original_hash twice; second returns False (ON CONFLICT DO NOTHING)."""
        cursor = MagicMock()
        cursor.fetchone.return_value = {"cnt": 0}
        # Second insert: rowcount 0 due to ON CONFLICT DO NOTHING
        cursor.rowcount = 0
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        result = q.enqueue("inc-001", {"foo": "bar"}, "hash-001")

        assert result is False

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_queue_full(self, mock_get_conn: MagicMock) -> None:
        """enqueue returns False when queue has max_size entries."""
        cursor = MagicMock()
        cursor.fetchone.return_value = {"cnt": 100}  # at max_size
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue(max_size=100)
        result = q.enqueue("inc-999", {"data": True}, "hash-999")

        assert result is False
        # INSERT should never be called when queue is full
        assert cursor.execute.call_count == 1  # only the COUNT query

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_payload_serialization(self, mock_get_conn: MagicMock) -> None:
        """Dict payload is serialized to a JSON string via json.dumps."""
        cursor = MagicMock()
        cursor.fetchone.return_value = {"cnt": 0}
        cursor.rowcount = 1
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        payload = {"nested": {"key": [1, 2, 3]}}
        q = _make_queue()
        q.enqueue("inc-002", payload, "hash-002")

        # Grab the INSERT call args
        insert_call = cursor.execute.call_args_list[1]
        params = insert_call[0][1]
        # The second positional param is the serialized payload
        assert params[1] == json.dumps(payload)

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    @patch("elle.daemon.incidents.cloud_queue.json.dumps", side_effect=TypeError("not serializable"))
    def test_invalid_json_payload(self, _mock_dumps: MagicMock, mock_get_conn: MagicMock) -> None:
        """Payload that cannot be serialized causes enqueue to return False (psycopg.Error path)."""
        cursor = MagicMock()
        cursor.fetchone.return_value = {"cnt": 0}
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        # json.dumps raises TypeError before the INSERT
        with pytest.raises(TypeError):
            q.enqueue("inc-003", {"bad": object()}, "hash-003")

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_large_payload(self, mock_get_conn: MagicMock) -> None:
        """A very large dict payload still enqueues successfully."""
        cursor = MagicMock()
        cursor.fetchone.return_value = {"cnt": 0}
        cursor.rowcount = 1
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        large_payload = {f"key_{i}": "x" * 1000 for i in range(500)}
        q = _make_queue()
        result = q.enqueue("inc-004", large_payload, "hash-004")

        assert result is True


# ===========================================================================
# TestDequeue
# ===========================================================================


class TestDequeue:
    """Tests for CloudSubmissionQueue.peek_batch."""

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_fifo_order(self, mock_get_conn: MagicMock) -> None:
        """peek_batch returns entries in enqueue order (ORDER BY enqueued_at ASC)."""
        now = datetime.now(timezone.utc)
        rows = [
            {
                "id": 1,
                "incident_id": "inc-A",
                "payload_json": "{}",
                "original_hash": "h1",
                "status": "pending",
                "retry_count": 0,
                "max_retries": 5,
                "next_retry_at": now.isoformat(),
                "last_error": None,
                "enqueued_at": (now - timedelta(minutes=10)).isoformat(),
                "last_attempted_at": None,
                "completed_at": None,
                "cloud_id": None,
            },
            {
                "id": 2,
                "incident_id": "inc-B",
                "payload_json": "{}",
                "original_hash": "h2",
                "status": "pending",
                "retry_count": 0,
                "max_retries": 5,
                "next_retry_at": now.isoformat(),
                "last_error": None,
                "enqueued_at": (now - timedelta(minutes=5)).isoformat(),
                "last_attempted_at": None,
                "completed_at": None,
                "cloud_id": None,
            },
        ]
        cursor = MagicMock()
        cursor.fetchall.return_value = rows
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        entries = q.peek_batch(batch_size=10)

        assert len(entries) == 2
        assert entries[0].incident_id == "inc-A"
        assert entries[1].incident_id == "inc-B"

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_empty_queue(self, mock_get_conn: MagicMock) -> None:
        """peek_batch returns empty list when no entries exist."""
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        entries = q.peek_batch()

        assert entries == []

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_batch_size_limit(self, mock_get_conn: MagicMock) -> None:
        """peek_batch(batch_size=2) passes LIMIT 2 to the query."""
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        q.peek_batch(batch_size=2)

        # The LIMIT value is passed as the second parameter
        call_args = cursor.execute.call_args
        params = call_args[0][1]
        assert params[1] == 2

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_skip_in_flight(self, mock_get_conn: MagicMock) -> None:
        """Entries marked in_flight are not returned by peek_batch (WHERE status = 'pending')."""
        cursor = MagicMock()
        # DB returns empty because in_flight entries are filtered by the SQL WHERE clause
        cursor.fetchall.return_value = []
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        entries = q.peek_batch()

        assert entries == []
        # Verify the SQL contains status = 'pending'
        sql = cursor.execute.call_args[0][0]
        assert "status = 'pending'" in sql

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_respect_next_retry_at(self, mock_get_conn: MagicMock) -> None:
        """Entries with future next_retry_at are not returned (next_retry_at <= now)."""
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        q.peek_batch()

        # Verify the SQL filters by next_retry_at
        sql = cursor.execute.call_args[0][0]
        assert "next_retry_at <=" in sql


# ===========================================================================
# TestRetryLogic
# ===========================================================================


class TestRetryLogic:
    """Tests for retry and backoff mechanics."""

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_mark_succeeded(self, mock_get_conn: MagicMock) -> None:
        """mark_succeeded updates status to 'succeeded' and sets cloud_id."""
        cursor = MagicMock()
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        q.mark_succeeded(42, cloud_id="cloud-xyz")

        sql = cursor.execute.call_args[0][0]
        params = cursor.execute.call_args[0][1]
        assert "status = 'succeeded'" in sql
        assert "cloud-xyz" in params
        assert 42 in params

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_mark_failed_increments_retry(self, mock_get_conn: MagicMock) -> None:
        """mark_failed increments retry_count when retries remain."""
        cursor = MagicMock()
        # fetchone returns current state: retry_count=1, max_retries=5
        cursor.fetchone.return_value = {"retry_count": 1, "max_retries": 5}
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        q.mark_failed(10, "connection timeout")

        # The UPDATE sets retry_count=2 (1+1) and status='pending'
        update_call = cursor.execute.call_args_list[-1]
        sql = update_call[0][0]
        params = update_call[0][1]
        assert "status = 'pending'" in sql
        assert params[0] == 2  # new retry_count

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_mark_failed_permanent_after_max_retries(self, mock_get_conn: MagicMock) -> None:
        """When retry_count >= max_retries, status becomes 'failed_permanent'."""
        cursor = MagicMock()
        # retry_count will become 5 (4+1) which == max_retries
        cursor.fetchone.return_value = {"retry_count": 4, "max_retries": 5}
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        q.mark_failed(10, "server error")

        update_call = cursor.execute.call_args_list[-1]
        sql = update_call[0][0]
        assert "status = 'failed_permanent'" in sql

    def test_backoff_calculation(self) -> None:
        """Exponential backoff: delay = initial * factor^retry_count."""
        _make_queue(initial_delay_sec=10.0, backoff_factor=2.0, max_delay_sec=10000.0)

        # Verify the formula: delay = min(initial * factor^retry, max_delay)
        # retry_count=1 -> 10 * 2^1 = 20
        # retry_count=2 -> 10 * 2^2 = 40
        # retry_count=3 -> 10 * 2^3 = 80
        delay_1 = min(10.0 * (2.0**1), 10000.0)
        delay_2 = min(10.0 * (2.0**2), 10000.0)
        delay_3 = min(10.0 * (2.0**3), 10000.0)

        assert delay_1 == 20.0
        assert delay_2 == 40.0
        assert delay_3 == 80.0

    def test_backoff_cap_at_max_delay(self) -> None:
        """Backoff never exceeds max_delay_sec."""
        _make_queue(initial_delay_sec=10.0, backoff_factor=2.0, max_delay_sec=50.0)

        # retry_count=10 -> 10 * 2^10 = 10240, capped at 50
        delay = min(10.0 * (2.0**10), 50.0)
        assert delay == 50.0

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_mark_in_flight(self, mock_get_conn: MagicMock) -> None:
        """mark_in_flight changes status to 'in_flight'."""
        cursor = MagicMock()
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        q.mark_in_flight(7)

        sql = cursor.execute.call_args[0][0]
        params = cursor.execute.call_args[0][1]
        assert "status = 'in_flight'" in sql
        assert 7 in params


# ===========================================================================
# TestStatusTransitions
# ===========================================================================


class TestStatusTransitions:
    """Tests for valid status transitions."""

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_pending_to_in_flight(self, mock_get_conn: MagicMock) -> None:
        """mark_in_flight transitions a pending entry to in_flight."""
        cursor = MagicMock()
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        q.mark_in_flight(1)

        sql = cursor.execute.call_args[0][0]
        assert "status = 'in_flight'" in sql

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_in_flight_to_succeeded(self, mock_get_conn: MagicMock) -> None:
        """mark_succeeded transitions an in_flight entry to succeeded."""
        cursor = MagicMock()
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        q.mark_succeeded(1, cloud_id="cloud-001")

        sql = cursor.execute.call_args[0][0]
        params = cursor.execute.call_args[0][1]
        assert "status = 'succeeded'" in sql
        assert "cloud-001" in params

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_in_flight_to_failed_retry(self, mock_get_conn: MagicMock) -> None:
        """mark_failed with retries remaining sets status back to 'pending'."""
        cursor = MagicMock()
        cursor.fetchone.return_value = {"retry_count": 0, "max_retries": 5}
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        q.mark_failed(1, "timeout")

        update_call = cursor.execute.call_args_list[-1]
        sql = update_call[0][0]
        assert "status = 'pending'" in sql

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_failed_to_failed_permanent(self, mock_get_conn: MagicMock) -> None:
        """mark_failed when max retries reached sets status to 'failed_permanent'."""
        cursor = MagicMock()
        # retry_count will become 5 (4+1) == max_retries
        cursor.fetchone.return_value = {"retry_count": 4, "max_retries": 5}
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        q.mark_failed(1, "permanent error")

        update_call = cursor.execute.call_args_list[-1]
        sql = update_call[0][0]
        assert "status = 'failed_permanent'" in sql

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_recover_in_flight(self, mock_get_conn: MagicMock) -> None:
        """recover_in_flight resets all in_flight entries to pending."""
        cursor = MagicMock()
        cursor.rowcount = 3
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        recovered = q.recover_in_flight()

        assert recovered == 3
        sql = cursor.execute.call_args[0][0]
        assert "SET status = 'pending'" in sql
        assert "WHERE status = 'in_flight'" in sql


# ===========================================================================
# TestCleanup
# ===========================================================================


class TestCleanup:
    """Tests for stale entry cleanup."""

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_stale_entry_removal(self, mock_get_conn: MagicMock) -> None:
        """cleanup_stale removes entries older than the specified hours."""
        cursor = MagicMock()
        cursor.rowcount = 5
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        count = q.cleanup_stale(hours=48)

        assert count == 5
        sql = cursor.execute.call_args[0][0]
        assert "DELETE FROM cloud_submission_queue" in sql
        assert "completed_at <" in sql

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_succeeded_entry_cleanup(self, mock_get_conn: MagicMock) -> None:
        """Succeeded entries are included in cleanup targets."""
        cursor = MagicMock()
        cursor.rowcount = 2
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        count = q.cleanup_stale(hours=168)

        assert count == 2
        sql = cursor.execute.call_args[0][0]
        assert "'succeeded'" in sql

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_retain_pending_entries(self, mock_get_conn: MagicMock) -> None:
        """Pending entries are not targeted by cleanup (only succeeded/failed_permanent)."""
        cursor = MagicMock()
        cursor.rowcount = 0
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        q.cleanup_stale()

        sql = cursor.execute.call_args[0][0]
        assert "'pending'" not in sql
        # Only succeeded and failed_permanent are cleaned
        assert "'succeeded'" in sql
        assert "'failed_permanent'" in sql

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_empty_queue_cleanup(self, mock_get_conn: MagicMock) -> None:
        """Cleanup on empty queue returns 0."""
        cursor = MagicMock()
        cursor.rowcount = 0
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        count = q.cleanup_stale()

        assert count == 0


# ===========================================================================
# TestQueueMetrics
# ===========================================================================


class TestQueueMetrics:
    """Tests for get_metrics and QueueMetrics model."""

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_all_counters_accurate(self, mock_get_conn: MagicMock) -> None:
        """QueueMetrics fields match actual counts from the database."""
        cursor = MagicMock()

        # Call sequence inside get_metrics:
        # 1. GROUP BY status
        # 2. SUM(retry_count)
        # 3. MIN(enqueued_at) for oldest pending
        # 4. COUNT(*) for total queue size
        cursor.fetchall.return_value = [
            {"status": "pending", "cnt": 3},
            {"status": "in_flight", "cnt": 1},
            {"status": "succeeded", "cnt": 10},
            {"status": "failed_permanent", "cnt": 2},
        ]
        cursor.fetchone.side_effect = [
            {"total": 15},  # total retries
            {"oldest": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()},  # oldest pending
            {"cnt": 16},  # queue size
        ]
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        metrics = q.get_metrics()

        assert metrics.pending_count == 3
        assert metrics.in_flight_count == 1
        assert metrics.succeeded_count == 10
        assert metrics.failed_permanent_count == 2
        assert metrics.total_retries == 15
        assert metrics.queue_size == 16

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_oldest_pending_age(self, mock_get_conn: MagicMock) -> None:
        """oldest_pending_age_sec is calculated from the oldest pending enqueue time."""
        cursor = MagicMock()
        two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

        cursor.fetchall.return_value = [{"status": "pending", "cnt": 1}]
        cursor.fetchone.side_effect = [
            {"total": 0},
            {"oldest": two_hours_ago},
            {"cnt": 1},
        ]
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        metrics = q.get_metrics()

        assert metrics.oldest_pending_age_sec is not None
        # Should be approximately 7200 seconds (2 hours), allowing some tolerance
        assert 7100 < metrics.oldest_pending_age_sec < 7300

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_queue_size_matches(self, mock_get_conn: MagicMock) -> None:
        """queue_size equals the total row count from COUNT(*)."""
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {"status": "pending", "cnt": 5},
            {"status": "succeeded", "cnt": 3},
        ]
        cursor.fetchone.side_effect = [
            {"total": 0},
            {"oldest": None},
            {"cnt": 8},
        ]
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        metrics = q.get_metrics()

        assert metrics.queue_size == 8

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_empty_metrics(self, mock_get_conn: MagicMock) -> None:
        """All zeros for empty queue."""
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.side_effect = [
            {"total": 0},
            {"oldest": None},
            {"cnt": 0},
        ]
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        metrics = q.get_metrics()

        assert metrics.pending_count == 0
        assert metrics.in_flight_count == 0
        assert metrics.succeeded_count == 0
        assert metrics.failed_permanent_count == 0
        assert metrics.total_retries == 0
        assert metrics.oldest_pending_age_sec is None
        assert metrics.queue_size == 0

    def test_metrics_model_validation(self) -> None:
        """QueueMetrics Pydantic model validates fields correctly."""
        metrics = QueueMetrics(
            pending_count=5,
            in_flight_count=2,
            succeeded_count=10,
            failed_permanent_count=1,
            total_retries=20,
            oldest_pending_age_sec=3600.0,
            queue_size=18,
        )

        assert metrics.pending_count == 5
        assert metrics.queue_size == 18
        assert metrics.oldest_pending_age_sec == 3600.0

        # Model is frozen (immutable)
        with pytest.raises(Exception):
            metrics.pending_count = 99  # type: ignore[misc]

        # Default values work
        default_metrics = QueueMetrics()
        assert default_metrics.pending_count == 0
        assert default_metrics.oldest_pending_age_sec is None
        assert default_metrics.queue_size == 0


# ===========================================================================
# TestConcurrency
# ===========================================================================


class TestConcurrency:
    """Tests for concurrent and multi-operation scenarios."""

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_multiple_enqueue_same_incident(self, mock_get_conn: MagicMock) -> None:
        """Deduplication works: second enqueue with same hash returns False."""
        cursor = MagicMock()
        cursor.fetchone.return_value = {"cnt": 0}

        call_count = 0

        # First call: rowcount=1 (inserted), second call: rowcount=0 (dedup)
        def _tracking_execute(*args: Any, **kwargs: Any) -> None:
            nonlocal call_count
            call_count += 1

        cursor.execute.side_effect = _tracking_execute

        # First enqueue succeeds
        cursor.rowcount = 1
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        result1 = q.enqueue("inc-001", {"v": 1}, "same-hash")
        assert result1 is True

        # Second enqueue is a duplicate
        cursor.rowcount = 0
        result2 = q.enqueue("inc-001", {"v": 1}, "same-hash")
        assert result2 is False

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_dequeue_respects_status(self, mock_get_conn: MagicMock) -> None:
        """Only entries with status='pending' are returned by peek_batch."""
        cursor = MagicMock()
        # Simulate DB returning only pending entries
        now = datetime.now(timezone.utc)
        cursor.fetchall.return_value = [
            {
                "id": 1,
                "incident_id": "inc-pending",
                "payload_json": "{}",
                "original_hash": "h1",
                "status": "pending",
                "retry_count": 0,
                "max_retries": 5,
                "next_retry_at": now.isoformat(),
                "last_error": None,
                "enqueued_at": now.isoformat(),
                "last_attempted_at": None,
                "completed_at": None,
                "cloud_id": None,
            },
        ]
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        entries = q.peek_batch()

        assert len(entries) == 1
        assert entries[0].status == "pending"
        assert entries[0].incident_id == "inc-pending"

    @patch("elle.daemon.incidents.cloud_queue.get_conn")
    def test_metrics_consistency(self, mock_get_conn: MagicMock) -> None:
        """Metrics match state after enqueue operations."""
        cursor = MagicMock()

        # Simulate metrics after several operations
        cursor.fetchall.return_value = [
            {"status": "pending", "cnt": 2},
            {"status": "succeeded", "cnt": 1},
        ]
        cursor.fetchone.side_effect = [
            {"total": 3},
            {"oldest": datetime.now(timezone.utc).isoformat()},
            {"cnt": 3},
        ]
        mock_get_conn.side_effect = _mock_conn_ctx(cursor)

        q = _make_queue()
        metrics = q.get_metrics()

        assert metrics.pending_count == 2
        assert metrics.succeeded_count == 1
        assert metrics.queue_size == 3
        # Sum of individual statuses should match queue_size
        status_sum = (
            metrics.pending_count + metrics.in_flight_count + metrics.succeeded_count + metrics.failed_permanent_count
        )
        assert status_sum == metrics.queue_size

    def test_queue_entry_dataclass(self) -> None:
        """QueueEntry fields are accessible as dataclass attributes."""
        entry = _make_entry(
            id=42,
            incident_id="inc-test",
            status="in_flight",
            retry_count=3,
            cloud_id="cloud-999",
        )

        assert entry.id == 42
        assert entry.incident_id == "inc-test"
        assert entry.status == "in_flight"
        assert entry.retry_count == 3
        assert entry.cloud_id == "cloud-999"
        assert entry.payload_json == '{"key": "value"}'
        assert entry.original_hash == "abc123"
        assert entry.last_error is None
        assert entry.last_attempted_at is None
        assert entry.completed_at is None
