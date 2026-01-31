"""Tests for CloudRetryWorker background task.

Covers worker lifecycle, batch processing, health checks,
retry behavior, error handling, and stats tracking.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.incidents.cloud_queue import QueueEntry
from elle.daemon.incidents.cloud_retry_worker import (
    CloudRetryWorker,
    WorkerStats,
    _is_server_down_error,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Patch targets -- the lazy imports resolve to these module-level functions.
_PATCH_GET_QUEUE = "elle.daemon.incidents.cloud_queue.get_cloud_queue"
_PATCH_GET_CLIENT = "elle.daemon.incidents.cloud_sync.get_cloud_client"
_PATCH_ANON_REPORT = "elle.daemon.incidents.anonymize.AnonymizedIncidentReport"


def _make_entry(
    entry_id: int = 1,
    incident_id: str = "inc-001",
    retry_count: int = 0,
    max_retries: int = 20,
    payload: dict | None = None,
) -> QueueEntry:
    """Build a minimal QueueEntry for testing."""
    if payload is None:
        payload = {"incident_id": incident_id, "created_at_hour": "2025-01-01T00:00:00"}
    return QueueEntry(
        id=entry_id,
        incident_id=incident_id,
        payload_json=json.dumps(payload),
        original_hash="abc123",
        status="pending",
        retry_count=retry_count,
        max_retries=max_retries,
        next_retry_at="2025-01-01T00:00:00",
        last_error=None,
        enqueued_at="2025-01-01T00:00:00",
        last_attempted_at=None,
        completed_at=None,
        cloud_id=None,
    )


@pytest.fixture
def mock_queue():
    """Return a MagicMock with the CloudSubmissionQueue interface."""
    q = MagicMock()
    q.peek_batch.return_value = []
    q.recover_in_flight.return_value = 0
    q.mark_in_flight.return_value = None
    q.mark_succeeded.return_value = None
    q.mark_failed.return_value = None
    q.cleanup_stale.return_value = 0
    q.get_metrics.return_value = MagicMock()
    return q


@pytest.fixture
def mock_client():
    """Return an AsyncMock with the CloudSyncClient interface."""
    c = AsyncMock()
    c.health_check.return_value = True
    c.submit_incident.return_value = MagicMock(cloud_id="cloud-001")
    return c


@pytest.fixture
def worker():
    """Create a CloudRetryWorker with fast intervals for testing."""
    return CloudRetryWorker(
        poll_interval_sec=0.05,
        batch_size=5,
        health_check_interval_sec=0.0,
        stale_entry_hours=168,
    )


# ===================================================================
# TestWorkerLifecycle
# ===================================================================


class TestWorkerLifecycle:
    """Worker start / stop / guard / shutdown / recovery tests."""

    async def test_start(self, worker: CloudRetryWorker, mock_queue: MagicMock):
        """worker.start() sets running=True and creates an asyncio task."""
        with patch(_PATCH_GET_QUEUE, return_value=mock_queue):
            await worker.start()
            try:
                assert worker.running is True
                assert worker._task is not None
                assert not worker._task.done()
            finally:
                await worker.stop()

    async def test_stop(self, worker: CloudRetryWorker, mock_queue: MagicMock):
        """worker.stop() sets running=False and cancels task."""
        with patch(_PATCH_GET_QUEUE, return_value=mock_queue):
            await worker.start()
            assert worker.running is True

            await worker.stop()
            assert worker.running is False
            assert worker._task is None

    async def test_double_start_guard(self, worker: CloudRetryWorker, mock_queue: MagicMock):
        """Calling start() twice does not create duplicate tasks."""
        with patch(_PATCH_GET_QUEUE, return_value=mock_queue):
            await worker.start()
            first_task = worker._task

            # Second start should be a no-op
            await worker.start()
            assert worker._task is first_task

            await worker.stop()

    async def test_graceful_shutdown(
        self,
        worker: CloudRetryWorker,
        mock_queue: MagicMock,
        mock_client: AsyncMock,
    ):
        """stop() waits for the current batch to finish (no crash)."""
        with (
            patch(_PATCH_GET_QUEUE, return_value=mock_queue),
            patch(_PATCH_GET_CLIENT, return_value=mock_client),
        ):
            await worker.start()
            # Let the loop tick at least once
            await asyncio.sleep(0.1)
            # stop should complete cleanly
            await worker.stop()
            assert worker.running is False

    async def test_recover_in_flight_on_start(self, worker: CloudRetryWorker, mock_queue: MagicMock):
        """start() calls queue.recover_in_flight to reset interrupted entries."""
        mock_queue.recover_in_flight.return_value = 3

        with patch(_PATCH_GET_QUEUE, return_value=mock_queue):
            await worker.start()
            try:
                mock_queue.recover_in_flight.assert_called_once()
            finally:
                await worker.stop()


# ===================================================================
# TestBatchProcessing
# ===================================================================


class TestBatchProcessing:
    """Tests for _process_batch via the running worker loop."""

    async def test_process_batch_of_entries(
        self,
        worker: CloudRetryWorker,
        mock_queue: MagicMock,
        mock_client: AsyncMock,
    ):
        """Multiple queue entries are processed successfully."""
        entries = [_make_entry(entry_id=i, incident_id=f"inc-{i}") for i in range(3)]
        # peek_batch returns our entries
        mock_queue.peek_batch.return_value = entries

        with (
            patch(_PATCH_GET_QUEUE, return_value=mock_queue),
            patch(_PATCH_GET_CLIENT, return_value=mock_client),
            patch(_PATCH_ANON_REPORT) as MockReport,
        ):
            MockReport.side_effect = lambda **kw: MagicMock()
            worker.stats.cloud_healthy = True
            # Drive _process_batch directly to avoid timing issues
            await worker._process_batch()

            assert mock_queue.mark_in_flight.call_count == 3
            assert mock_queue.mark_succeeded.call_count == 3
            assert worker.stats.retries_attempted == 3
            assert worker.stats.retries_succeeded == 3

    async def test_partial_batch_success(
        self,
        worker: CloudRetryWorker,
        mock_queue: MagicMock,
        mock_client: AsyncMock,
    ):
        """Some entries succeed and some fail within a batch."""
        entries = [_make_entry(entry_id=i, incident_id=f"inc-{i}") for i in range(3)]
        mock_queue.peek_batch.return_value = entries
        # Second submission raises
        mock_client.submit_incident.side_effect = [
            MagicMock(cloud_id="c1"),
            ValueError("bad payload"),
            MagicMock(cloud_id="c3"),
        ]

        with (
            patch(_PATCH_GET_QUEUE, return_value=mock_queue),
            patch(_PATCH_GET_CLIENT, return_value=mock_client),
            patch(_PATCH_ANON_REPORT) as MockReport,
        ):
            MockReport.side_effect = lambda **kw: MagicMock()
            worker.stats.cloud_healthy = True
            await worker._process_batch()

            assert worker.stats.retries_attempted == 3
            assert worker.stats.retries_succeeded == 2
            assert worker.stats.retries_failed == 1
            assert mock_queue.mark_failed.call_count == 1

    async def test_all_fail(
        self,
        worker: CloudRetryWorker,
        mock_queue: MagicMock,
        mock_client: AsyncMock,
    ):
        """All entries in batch fail and stats are updated."""
        entries = [_make_entry(entry_id=i) for i in range(2)]
        mock_queue.peek_batch.return_value = entries
        mock_client.submit_incident.side_effect = ValueError("fail")

        with (
            patch(_PATCH_GET_QUEUE, return_value=mock_queue),
            patch(_PATCH_GET_CLIENT, return_value=mock_client),
            patch(_PATCH_ANON_REPORT) as MockReport,
        ):
            MockReport.side_effect = lambda **kw: MagicMock()
            worker.stats.cloud_healthy = True
            await worker._process_batch()

            assert worker.stats.retries_attempted == 2
            assert worker.stats.retries_succeeded == 0
            assert worker.stats.retries_failed == 2
            assert mock_queue.mark_failed.call_count == 2

    async def test_empty_queue(
        self,
        worker: CloudRetryWorker,
        mock_queue: MagicMock,
        mock_client: AsyncMock,
    ):
        """peek_batch returns empty list so no processing happens."""
        mock_queue.peek_batch.return_value = []

        with (
            patch(_PATCH_GET_QUEUE, return_value=mock_queue),
            patch(_PATCH_GET_CLIENT, return_value=mock_client),
        ):
            worker.stats.cloud_healthy = True
            await worker._process_batch()

            mock_queue.mark_in_flight.assert_not_called()
            mock_queue.mark_succeeded.assert_not_called()
            assert worker.stats.retries_attempted == 0

    async def test_batch_with_mixed_results(
        self,
        worker: CloudRetryWorker,
        mock_queue: MagicMock,
        mock_client: AsyncMock,
    ):
        """Mix of success, transient failure, and server-down error."""
        import httpx

        entries = [_make_entry(entry_id=i, incident_id=f"inc-{i}") for i in range(3)]
        mock_queue.peek_batch.return_value = entries
        mock_client.submit_incident.side_effect = [
            MagicMock(cloud_id="c1"),
            ValueError("transient"),
            httpx.ConnectError("connection refused"),
        ]

        with (
            patch(_PATCH_GET_QUEUE, return_value=mock_queue),
            patch(_PATCH_GET_CLIENT, return_value=mock_client),
            patch(_PATCH_ANON_REPORT) as MockReport,
        ):
            MockReport.side_effect = lambda **kw: MagicMock()
            worker.stats.cloud_healthy = True
            await worker._process_batch()

            # First succeeds, second fails (non-server), third fails (server-down -> breaks batch)
            assert worker.stats.retries_succeeded == 1
            assert worker.stats.retries_failed == 2
            # Server-down error should mark cloud as unhealthy
            assert worker.stats.cloud_healthy is False


# ===================================================================
# TestHealthChecks
# ===================================================================


class TestHealthChecks:
    """Tests for _maybe_health_check and its interaction with batch processing."""

    async def test_cloud_healthy_processes(
        self,
        mock_queue: MagicMock,
        mock_client: AsyncMock,
    ):
        """When health_check returns True, batch is processed."""
        worker = CloudRetryWorker(
            poll_interval_sec=0.05,
            batch_size=5,
            health_check_interval_sec=0.0,
        )
        mock_client.health_check.return_value = True
        entries = [_make_entry()]
        mock_queue.peek_batch.return_value = entries

        with (
            patch(_PATCH_GET_QUEUE, return_value=mock_queue),
            patch(_PATCH_GET_CLIENT, return_value=mock_client),
            patch(_PATCH_ANON_REPORT) as MockReport,
        ):
            MockReport.side_effect = lambda **kw: MagicMock()
            # Run health check first
            await worker._maybe_health_check()
            assert worker.stats.cloud_healthy is True

            # Now process batch
            await worker._process_batch()
            assert worker.stats.retries_attempted == 1

    async def test_cloud_unhealthy_skips(
        self,
        mock_queue: MagicMock,
        mock_client: AsyncMock,
    ):
        """When health_check returns False, batch is skipped in the loop."""
        worker = CloudRetryWorker(
            poll_interval_sec=0.05,
            batch_size=5,
            health_check_interval_sec=0.0,
        )
        mock_client.health_check.return_value = False

        with (
            patch(_PATCH_GET_QUEUE, return_value=mock_queue),
            patch(_PATCH_GET_CLIENT, return_value=mock_client),
        ):
            # Run health check: sets cloud_healthy = False
            await worker._maybe_health_check()
            assert worker.stats.cloud_healthy is False

            # _run_loop checks cloud_healthy before calling _process_batch.
            # Verify the guard: if not healthy, peek_batch should not be called.
            # Start the loop briefly.
            await worker.start()
            await asyncio.sleep(0.15)
            await worker.stop()

            # peek_batch should not have been called because cloud is unhealthy
            mock_queue.peek_batch.assert_not_called()

    async def test_health_check_interval(
        self,
        mock_client: AsyncMock,
    ):
        """Health check only runs when health_check_interval_sec has elapsed."""
        worker = CloudRetryWorker(
            poll_interval_sec=0.05,
            batch_size=5,
            health_check_interval_sec=1000.0,  # very large
        )
        # Pretend we just checked
        worker.stats.last_health_check = time.time()

        with patch(_PATCH_GET_CLIENT, return_value=mock_client):
            await worker._maybe_health_check()
            # Should not have called health_check because interval has not elapsed
            mock_client.health_check.assert_not_called()

    async def test_health_transition_logging(
        self,
        mock_client: AsyncMock,
    ):
        """stats.cloud_healthy is updated when health transitions."""
        worker = CloudRetryWorker(
            poll_interval_sec=0.05,
            batch_size=5,
            health_check_interval_sec=0.0,
        )

        with patch(_PATCH_GET_CLIENT, return_value=mock_client):
            # Transition: unhealthy -> healthy
            worker.stats.cloud_healthy = False
            mock_client.health_check.return_value = True
            await worker._maybe_health_check()
            assert worker.stats.cloud_healthy is True

            # Reset interval so check runs again
            worker.stats.last_health_check = 0.0

            # Transition: healthy -> unhealthy
            mock_client.health_check.return_value = False
            await worker._maybe_health_check()
            assert worker.stats.cloud_healthy is False


# ===================================================================
# TestRetryBehavior
# ===================================================================


class TestRetryBehavior:
    """Tests for retry logic, permanent failure, stale cleanup, and stats."""

    async def test_successful_retry(
        self,
        worker: CloudRetryWorker,
        mock_queue: MagicMock,
        mock_client: AsyncMock,
    ):
        """A previously-failed entry is retried and succeeds."""
        entry = _make_entry(entry_id=10, retry_count=3)
        mock_queue.peek_batch.return_value = [entry]

        with (
            patch(_PATCH_GET_QUEUE, return_value=mock_queue),
            patch(_PATCH_GET_CLIENT, return_value=mock_client),
            patch(_PATCH_ANON_REPORT) as MockReport,
        ):
            MockReport.side_effect = lambda **kw: MagicMock()
            worker.stats.cloud_healthy = True
            await worker._process_batch()

            mock_queue.mark_succeeded.assert_called_once_with(10, "cloud-001")
            assert worker.stats.retries_succeeded == 1

    async def test_permanent_failure(
        self,
        worker: CloudRetryWorker,
        mock_queue: MagicMock,
        mock_client: AsyncMock,
    ):
        """Entry exceeding max retries is marked as failed (mark_failed called)."""
        # Entry that has already been retried max_retries - 1 times
        entry = _make_entry(entry_id=20, retry_count=19, max_retries=20)
        mock_queue.peek_batch.return_value = [entry]
        mock_client.submit_incident.side_effect = ValueError("still broken")

        with (
            patch(_PATCH_GET_QUEUE, return_value=mock_queue),
            patch(_PATCH_GET_CLIENT, return_value=mock_client),
            patch(_PATCH_ANON_REPORT) as MockReport,
        ):
            MockReport.side_effect = lambda **kw: MagicMock()
            worker.stats.cloud_healthy = True
            await worker._process_batch()

            # The worker calls queue.mark_failed with the entry id and error string
            mock_queue.mark_failed.assert_called_once()
            call_args = mock_queue.mark_failed.call_args
            assert call_args[0][0] == 20
            assert "still broken" in call_args[0][1]
            assert worker.stats.retries_failed == 1

    async def test_stale_entry_cleanup(
        self,
        worker: CloudRetryWorker,
        mock_queue: MagicMock,
    ):
        """cleanup_stale is called during _cleanup_stale."""
        with patch(_PATCH_GET_QUEUE, return_value=mock_queue):
            await worker._cleanup_stale()
            mock_queue.cleanup_stale.assert_called_once_with(168)

    async def test_worker_stats_accuracy(
        self,
        worker: CloudRetryWorker,
        mock_queue: MagicMock,
        mock_client: AsyncMock,
    ):
        """stats.retries_attempted/succeeded/failed are accurate across calls."""
        with (
            patch(_PATCH_GET_QUEUE, return_value=mock_queue),
            patch(_PATCH_GET_CLIENT, return_value=mock_client),
            patch(_PATCH_ANON_REPORT) as MockReport,
        ):
            MockReport.side_effect = lambda **kw: MagicMock()
            worker.stats.cloud_healthy = True

            # Batch 1: 2 successes
            mock_queue.peek_batch.return_value = [
                _make_entry(entry_id=1),
                _make_entry(entry_id=2),
            ]
            await worker._process_batch()

            # Batch 2: 1 failure
            mock_queue.peek_batch.return_value = [_make_entry(entry_id=3)]
            mock_client.submit_incident.side_effect = ValueError("fail")
            await worker._process_batch()

            assert worker.stats.retries_attempted == 3
            assert worker.stats.retries_succeeded == 2
            assert worker.stats.retries_failed == 1

    async def test_is_server_down_error(self):
        """_is_server_down_error detects connection and timeout errors."""
        import httpx

        # ConnectError
        assert _is_server_down_error(httpx.ConnectError("refused")) is True

        # TimeoutException
        assert _is_server_down_error(httpx.ReadTimeout("timed out")) is True

        # HTTP 503
        mock_response = MagicMock()
        mock_response.status_code = 503
        err_503 = httpx.HTTPStatusError(
            "Service Unavailable",
            request=MagicMock(),
            response=mock_response,
        )
        assert _is_server_down_error(err_503) is True

        # HTTP 502
        mock_response_502 = MagicMock()
        mock_response_502.status_code = 502
        err_502 = httpx.HTTPStatusError(
            "Bad Gateway",
            request=MagicMock(),
            response=mock_response_502,
        )
        assert _is_server_down_error(err_502) is True

        # HTTP 504
        mock_response_504 = MagicMock()
        mock_response_504.status_code = 504
        err_504 = httpx.HTTPStatusError(
            "Gateway Timeout",
            request=MagicMock(),
            response=mock_response_504,
        )
        assert _is_server_down_error(err_504) is True

        # Non-server error (e.g., 400)
        mock_response_400 = MagicMock()
        mock_response_400.status_code = 400
        err_400 = httpx.HTTPStatusError(
            "Bad Request",
            request=MagicMock(),
            response=mock_response_400,
        )
        assert _is_server_down_error(err_400) is False

        # Generic ValueError is NOT a server-down error
        assert _is_server_down_error(ValueError("nope")) is False


# ===================================================================
# TestErrorHandling
# ===================================================================


class TestErrorHandling:
    """Tests for graceful error handling in various failure modes."""

    async def test_queue_access_failure(
        self,
        worker: CloudRetryWorker,
    ):
        """Exception from queue.peek_batch is handled gracefully."""
        bad_queue = MagicMock()
        bad_queue.peek_batch.side_effect = Exception("db locked")

        with (
            patch(_PATCH_GET_QUEUE, return_value=bad_queue),
            patch(_PATCH_GET_CLIENT, return_value=AsyncMock()),
        ):
            worker.stats.cloud_healthy = True
            # Should NOT raise
            await worker._process_batch()
            # Stats should be untouched since we never got to process entries
            assert worker.stats.retries_attempted == 0

    async def test_cloud_client_exception(
        self,
        worker: CloudRetryWorker,
        mock_queue: MagicMock,
    ):
        """Exception from cloud client.submit_incident is handled."""
        entries = [_make_entry(entry_id=1)]
        mock_queue.peek_batch.return_value = entries

        bad_client = AsyncMock()
        bad_client.submit_incident.side_effect = RuntimeError("TLS handshake failed")

        with (
            patch(_PATCH_GET_QUEUE, return_value=mock_queue),
            patch(_PATCH_GET_CLIENT, return_value=bad_client),
            patch(_PATCH_ANON_REPORT) as MockReport,
        ):
            MockReport.side_effect = lambda **kw: MagicMock()
            worker.stats.cloud_healthy = True
            await worker._process_batch()

            assert worker.stats.retries_failed == 1
            mock_queue.mark_failed.assert_called_once()

    async def test_asyncio_cancellation(
        self,
        worker: CloudRetryWorker,
        mock_queue: MagicMock,
    ):
        """CancelledError during processing is handled cleanly."""
        with patch(_PATCH_GET_QUEUE, return_value=mock_queue):
            await worker.start()
            # Simulate cancellation by cancelling the task directly
            worker._task.cancel()
            try:
                await worker._task
            except asyncio.CancelledError:
                pass
            # Worker should no longer be running
            assert worker._task.done()

    async def test_unexpected_exception_recovery(
        self,
        mock_queue: MagicMock,
        mock_client: AsyncMock,
    ):
        """A random Exception inside the loop does not crash the worker."""
        worker = CloudRetryWorker(
            poll_interval_sec=0.05,
            batch_size=5,
            health_check_interval_sec=0.0,
        )

        call_count = 0

        async def flaky_process_batch():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Unexpected DB error")
            return

        with (
            patch(_PATCH_GET_QUEUE, return_value=mock_queue),
            patch(_PATCH_GET_CLIENT, return_value=mock_client),
        ):
            mock_client.health_check.return_value = True
            worker._process_batch = flaky_process_batch  # type: ignore[assignment]
            await worker.start()
            # Let the loop run through multiple iterations
            await asyncio.sleep(0.25)
            # Worker should still be running despite the first-iteration exception
            assert worker.running is True
            await worker.stop()
            # Confirm the flaky function was called more than once (recovered)
            assert call_count >= 2


# ===================================================================
# TestWorkerStats
# ===================================================================


class TestWorkerStats:
    """Tests for the WorkerStats dataclass."""

    async def test_stats_initialization(self):
        """Fresh WorkerStats has zeros and reasonable defaults."""
        stats = WorkerStats()
        assert stats.retries_attempted == 0
        assert stats.retries_succeeded == 0
        assert stats.retries_failed == 0
        assert stats.cloud_healthy is False
        assert stats.last_health_check == 0.0
        assert stats.started_at > 0

    async def test_stats_accumulation(
        self,
        mock_queue: MagicMock,
        mock_client: AsyncMock,
    ):
        """Stats accumulate correctly across multiple batches."""
        worker = CloudRetryWorker(
            poll_interval_sec=0.05,
            batch_size=5,
            health_check_interval_sec=0.0,
        )

        with (
            patch(_PATCH_GET_QUEUE, return_value=mock_queue),
            patch(_PATCH_GET_CLIENT, return_value=mock_client),
            patch(_PATCH_ANON_REPORT) as MockReport,
        ):
            MockReport.side_effect = lambda **kw: MagicMock()
            worker.stats.cloud_healthy = True

            # Batch 1: 2 entries, both succeed
            mock_queue.peek_batch.return_value = [
                _make_entry(entry_id=1),
                _make_entry(entry_id=2),
            ]
            mock_client.submit_incident.return_value = MagicMock(cloud_id="c-x")
            await worker._process_batch()

            assert worker.stats.retries_attempted == 2
            assert worker.stats.retries_succeeded == 2
            assert worker.stats.retries_failed == 0

            # Batch 2: 3 entries, first two succeed, third fails
            mock_queue.peek_batch.return_value = [
                _make_entry(entry_id=3),
                _make_entry(entry_id=4),
                _make_entry(entry_id=5),
            ]
            mock_client.submit_incident.side_effect = [
                MagicMock(cloud_id="c-a"),
                MagicMock(cloud_id="c-b"),
                ValueError("oops"),
            ]
            await worker._process_batch()

            # Accumulated totals
            assert worker.stats.retries_attempted == 5
            assert worker.stats.retries_succeeded == 4
            assert worker.stats.retries_failed == 1
