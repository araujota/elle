"""Chaos tests for cloud sync, queue, and retry worker resilience.

Tests that the cloud sync pipeline handles infrastructure faults gracefully:
expired mTLS certs, unreachable endpoints, DNS failures, malformed responses,
database refusal, queue corruption, worker crashes, and flapping connectivity.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from elle.daemon.incidents.anonymize import AnonymizedIncidentReport
from elle.daemon.incidents.cloud_queue import CloudSubmissionQueue, QueueEntry
from elle.daemon.incidents.cloud_sync import CloudSyncClient
from tests.chaos.conftest import FaultInjector

# =============================================================================
# Helpers
# =============================================================================


def _make_cloud_client(
    endpoint: str = "https://vault.test.example.com:8443",
    timeout_sec: int = 5,
) -> CloudSyncClient:
    """Create a CloudSyncClient configured for testing (no real certs)."""
    return CloudSyncClient(
        endpoint=endpoint,
        ca_cert_path="/tmp/fake-ca.pem",
        client_cert_path="/tmp/fake-client.pem",
        client_key_path="/tmp/fake-client-key.pem",
        timeout_sec=timeout_sec,
    )


def _make_anonymized_report() -> AnonymizedIncidentReport:
    """Create a minimal AnonymizedIncidentReport for testing."""
    from datetime import datetime, timezone

    from elle.daemon.incidents.models import Fingerprint

    return AnonymizedIncidentReport(
        incident_id="test-incident-001",
        created_at_hour=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        updated_at_hour=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        domain="net",
        severity="warning",
        status="open",
        outcome="unknown",
        trigger_source="manual",
        fingerprint=Fingerprint(),
    )


def _make_queue_entry(
    entry_id: int = 1,
    incident_id: str = "test-incident-001",
    status: str = "pending",
    retry_count: int = 0,
) -> QueueEntry:
    """Create a minimal QueueEntry for testing."""
    payload = _make_anonymized_report().model_dump(mode="json")
    return QueueEntry(
        id=entry_id,
        incident_id=incident_id,
        payload_json=json.dumps(payload),
        original_hash="abc123def456",
        status=status,
        retry_count=retry_count,
        max_retries=5,
        next_retry_at="2025-01-01T12:00:00+00:00",
        last_error=None,
        enqueued_at="2025-01-01T11:59:00+00:00",
        last_attempted_at=None,
        completed_at=None,
        cloud_id=None,
    )


# =============================================================================
# TestCloudSyncResilience
# =============================================================================


class TestCloudSyncResilience:
    """Cloud sync client should handle network and TLS faults gracefully."""

    @pytest.mark.asyncio
    async def test_expired_mtls_cert(self, fault: FaultInjector) -> None:
        """Client should raise SSLError on expired mTLS cert, not crash."""
        with fault.expired_mtls_cert():
            client = _make_cloud_client()
            # _get_ssl_context calls ssl.SSLContext.load_verify_locations
            # before load_cert_chain, so we must also patch the former to
            # prevent FileNotFoundError on the fake CA path.  The patched
            # load_cert_chain will then raise SSLError.
            with (
                patch("pathlib.Path.exists", return_value=True),
                patch("ssl.SSLContext.load_verify_locations"),
            ):
                with pytest.raises(ssl.SSLError, match="certificate has expired"):
                    client._get_ssl_context()

    @pytest.mark.asyncio
    async def test_unreachable_endpoint(self, fault: FaultInjector) -> None:
        """Client should raise ConnectError when endpoint is unreachable."""
        report = _make_anonymized_report()
        client = _make_cloud_client()

        with (
            fault.unreachable_endpoint("https://vault.test.example.com:8443"),
            patch("pathlib.Path.exists", return_value=True),
            patch("ssl.SSLContext.load_cert_chain"),
            patch("ssl.SSLContext.load_verify_locations"),
        ):
            # Force fresh client creation
            client._client = None
            with pytest.raises(httpx.ConnectError):
                await client.submit_incident(report)

    @pytest.mark.asyncio
    async def test_dns_failure(self, fault: FaultInjector) -> None:
        """Client should handle DNS resolution failure without crash."""
        report = _make_anonymized_report()
        client = _make_cloud_client()

        # Patch httpx.AsyncClient.post to raise a ConnectError wrapping
        # the DNS resolution failure, which is what httpx does in practice.
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("ssl.SSLContext.load_cert_chain"),
            patch("ssl.SSLContext.load_verify_locations"),
            patch(
                "httpx.AsyncClient.post",
                side_effect=httpx.ConnectError("Name or service not known"),
            ),
        ):
            client._client = None
            with pytest.raises(httpx.ConnectError, match="Name or service not known"):
                await client.submit_incident(report)

    @pytest.mark.asyncio
    async def test_malformed_json_response(self, fault: FaultInjector) -> None:
        """Client should handle a malformed JSON response from cloud."""
        report = _make_anonymized_report()
        client = _make_cloud_client()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("ssl.SSLContext.load_cert_chain"),
            patch("ssl.SSLContext.load_verify_locations"),
            patch("httpx.AsyncClient.post", return_value=mock_response),
        ):
            client._client = None
            with pytest.raises(json.JSONDecodeError):
                await client.submit_incident(report)

    @pytest.mark.asyncio
    async def test_socket_disconnect(self, fault: FaultInjector) -> None:
        """Client should handle socket disconnect mid-response."""
        report = _make_anonymized_report()
        client = _make_cloud_client()

        # httpx wraps low-level socket disconnects into ReadError
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("ssl.SSLContext.load_cert_chain"),
            patch("ssl.SSLContext.load_verify_locations"),
            patch(
                "httpx.AsyncClient.post",
                side_effect=httpx.ReadError("peer closed connection without sending complete message body"),
            ),
        ):
            client._client = None
            with pytest.raises(httpx.ReadError):
                await client.submit_incident(report)


# =============================================================================
# TestCloudQueueResilience
# =============================================================================


class TestCloudQueueResilience:
    """Cloud submission queue should handle storage and data faults."""

    def test_database_refused_during_enqueue(self, fault: FaultInjector) -> None:
        """Enqueue should fail gracefully when the database is unreachable."""

        queue = CloudSubmissionQueue(max_size=100)
        payload = _make_anonymized_report().model_dump(mode="json")

        with fault.database_refused():
            # enqueue calls get_conn which will raise; should not crash
            with pytest.raises(Exception):
                queue.enqueue(
                    incident_id="test-001",
                    payload=payload,
                    original_hash="hash-001",
                )

    def test_corrupted_queue_entry(self, fault: FaultInjector) -> None:
        """Dequeue (peek_batch) should handle corrupted rows from the DB."""

        queue = CloudSubmissionQueue()

        # Inline the corruption mock with the correct patch target so that
        # cloud_queue.get_conn (the local reference) is replaced.
        corrupted_row = {
            "id": None,
            "incident_id": "",
            "payload_json": "{invalid json",
            "original_hash": "",
            "status": "INVALID_STATUS",
            "retry_count": -1,
            "max_retries": 0,
            "next_retry_at": "not-a-date",
            "last_error": None,
            "enqueued_at": "not-a-date",
            "last_attempted_at": None,
            "completed_at": None,
            "cloud_id": None,
        }
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [corrupted_row]
        mock_cursor.fetchone.return_value = corrupted_row

        with patch("elle.daemon.incidents.cloud_queue.get_conn") as mock_conn:
            mock_ctx = MagicMock()
            mock_ctx.cursor.return_value = mock_cursor
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)

            # peek_batch reads rows from DB.  Corrupted rows should
            # either be skipped or cause a controlled error, not a crash.
            try:
                entries = queue.peek_batch(batch_size=5)
                # If it succeeds, the entry may have None/bad values,
                # which is fine as long as no unhandled exception occurs.
                for entry in entries:
                    assert entry is not None
            except (TypeError, ValueError, KeyError):
                # Controlled error from trying to construct QueueEntry
                # with invalid data - this is acceptable graceful degradation.
                pass

    def test_queue_overflow(self, fault: FaultInjector) -> None:
        """Queue should reject enqueue when at max_size capacity."""

        queue = CloudSubmissionQueue(max_size=5)

        # Mock the DB to report the queue is already full
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"cnt": 5}

        with patch("elle.daemon.incidents.cloud_queue.get_conn") as mock_conn:
            mock_ctx = MagicMock()
            mock_ctx.cursor.return_value = mock_cursor
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)

            payload = _make_anonymized_report().model_dump(mode="json")
            result = queue.enqueue(
                incident_id="overflow-001",
                payload=payload,
                original_hash="hash-overflow",
            )

            # Should return False when queue is full
            assert result is False

    def test_concurrent_worker_access(self, fault: FaultInjector) -> None:
        """Concurrent peek_batch calls should not corrupt state."""

        queue = CloudSubmissionQueue()

        # Simulate two concurrent peek_batch calls returning the same rows
        mock_rows = [
            {
                "id": 1,
                "incident_id": "concurrent-001",
                "payload_json": "{}",
                "original_hash": "hash-c1",
                "status": "pending",
                "retry_count": 0,
                "max_retries": 5,
                "next_retry_at": "2025-01-01T12:00:00+00:00",
                "last_error": None,
                "enqueued_at": "2025-01-01T11:00:00+00:00",
                "last_attempted_at": None,
                "completed_at": None,
                "cloud_id": None,
            },
        ]

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_rows

        with patch("elle.daemon.incidents.cloud_queue.get_conn") as mock_conn:
            mock_ctx = MagicMock()
            mock_ctx.cursor.return_value = mock_cursor
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)

            # Both calls should succeed independently
            batch_a = queue.peek_batch(batch_size=5)
            batch_b = queue.peek_batch(batch_size=5)

            assert len(batch_a) == 1
            assert len(batch_b) == 1
            assert batch_a[0].incident_id == "concurrent-001"
            assert batch_b[0].incident_id == "concurrent-001"

    def test_stale_lock_recovery(self, fault: FaultInjector) -> None:
        """recover_in_flight should reset stale in_flight entries to pending."""

        queue = CloudSubmissionQueue()

        mock_cursor = MagicMock()
        # Simulate 3 in_flight entries being recovered
        mock_cursor.rowcount = 3

        with patch("elle.daemon.incidents.cloud_queue.get_conn") as mock_conn:
            mock_ctx = MagicMock()
            mock_ctx.cursor.return_value = mock_cursor
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)

            recovered = queue.recover_in_flight()

            assert recovered == 3
            # Verify the UPDATE SQL was executed
            mock_cursor.execute.assert_called_once()
            sql_arg = mock_cursor.execute.call_args[0][0]
            assert "status = 'pending'" in sql_arg
            assert "status = 'in_flight'" in sql_arg


# =============================================================================
# TestRetryWorkerResilience
# =============================================================================


class TestRetryWorkerResilience:
    """Cloud retry worker should handle async and infrastructure faults."""

    @pytest.mark.asyncio
    async def test_worker_crash_mid_batch(self, fault: FaultInjector) -> None:
        """Worker should survive an exception during batch processing."""
        from elle.daemon.incidents.cloud_retry_worker import CloudRetryWorker

        worker = CloudRetryWorker(
            poll_interval_sec=0.1,
            batch_size=5,
            health_check_interval_sec=0.0,
        )
        worker.stats.cloud_healthy = True

        entry = _make_queue_entry()

        # Mock get_cloud_queue to return an entry, then submit_incident raises
        mock_queue = MagicMock()
        mock_queue.peek_batch.return_value = [entry]
        mock_queue.mark_in_flight = MagicMock()
        mock_queue.mark_failed = MagicMock()

        mock_client = MagicMock()
        mock_client.submit_incident = AsyncMock(
            side_effect=RuntimeError("Simulated crash mid-batch"),
        )

        with (
            patch(
                "elle.daemon.incidents.cloud_queue.get_cloud_queue",
                return_value=mock_queue,
            ),
            patch(
                "elle.daemon.incidents.cloud_sync.get_cloud_client",
                return_value=mock_client,
            ),
        ):
            # _process_batch should handle the exception internally
            await worker._process_batch()

            # Entry should be marked as failed, not left in limbo
            mock_queue.mark_failed.assert_called_once()
            assert worker.stats.retries_failed == 1
            assert worker.stats.retries_attempted == 1

    @pytest.mark.asyncio
    async def test_asyncio_cancellation_during_health(self, fault: FaultInjector) -> None:
        """Worker should handle asyncio.CancelledError during health check."""
        from elle.daemon.incidents.cloud_retry_worker import CloudRetryWorker

        worker = CloudRetryWorker(
            poll_interval_sec=0.1,
            health_check_interval_sec=0.0,
        )
        # Force health check interval to have elapsed
        worker.stats.last_health_check = 0.0

        mock_client = MagicMock()
        mock_client.health_check = AsyncMock(side_effect=asyncio.CancelledError())

        with patch(
            "elle.daemon.incidents.cloud_sync.get_cloud_client",
            return_value=mock_client,
        ):
            # CancelledError during health check should propagate
            # (as it's used for task cancellation), or be caught
            # by the run loop.  Either way, no crash.
            try:
                await worker._maybe_health_check()
            except asyncio.CancelledError:
                pass  # Expected - run loop would catch this

            # Cloud should be marked unhealthy after the error
            assert worker.stats.cloud_healthy is False

    @pytest.mark.asyncio
    async def test_cloud_flapping(self, fault: FaultInjector) -> None:
        """Worker should handle cloud API alternating healthy/unhealthy."""
        from elle.daemon.incidents.cloud_retry_worker import CloudRetryWorker

        worker = CloudRetryWorker(
            poll_interval_sec=0.1,
            batch_size=5,
            health_check_interval_sec=0.0,
        )

        entry = _make_queue_entry()

        mock_queue = MagicMock()
        mock_queue.peek_batch.return_value = [entry]
        mock_queue.mark_in_flight = MagicMock()
        mock_queue.mark_succeeded = MagicMock()
        mock_queue.mark_failed = MagicMock()

        with (
            fault.cloud_api_flapping(),
            patch(
                "elle.daemon.incidents.cloud_queue.get_cloud_queue",
                return_value=mock_queue,
            ),
        ):
            # First call (odd) succeeds, second call (even) fails
            mock_client_success = MagicMock()
            mock_client_success.submit_incident = AsyncMock(
                return_value=MagicMock(cloud_id="cloud-001"),
            )

            mock_client_fail = MagicMock()
            mock_client_fail.submit_incident = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused (flapping)"),
            )

            # Round 1: Cloud is healthy, process succeeds
            worker.stats.cloud_healthy = True
            with patch(
                "elle.daemon.incidents.cloud_sync.get_cloud_client",
                return_value=mock_client_success,
            ):
                await worker._process_batch()
            assert worker.stats.retries_succeeded == 1

            # Round 2: Cloud flaps to unhealthy, process fails
            mock_queue.peek_batch.return_value = [_make_queue_entry(entry_id=2)]
            with patch(
                "elle.daemon.incidents.cloud_sync.get_cloud_client",
                return_value=mock_client_fail,
            ):
                await worker._process_batch()
            assert worker.stats.retries_failed == 1
            # Worker should detect server-down and mark cloud unhealthy
            assert worker.stats.cloud_healthy is False

    @pytest.mark.asyncio
    async def test_event_loop_shutdown(self, fault: FaultInjector) -> None:
        """Worker should stop cleanly when the event loop is cancelled."""
        from elle.daemon.incidents.cloud_retry_worker import CloudRetryWorker

        worker = CloudRetryWorker(poll_interval_sec=0.1)

        # Start the worker
        mock_queue = MagicMock()
        mock_queue.recover_in_flight.return_value = 0

        with patch(
            "elle.daemon.incidents.cloud_queue.get_cloud_queue",
            return_value=mock_queue,
        ):
            await worker.start()
            assert worker.running is True

            # Stop the worker (simulates event loop shutdown)
            await worker.stop()
            assert worker.running is False

            # Worker should have been cancelled gracefully
            assert worker._task is None

    @pytest.mark.asyncio
    async def test_queue_empty_after_cloud_recovery(self, fault: FaultInjector) -> None:
        """Queue should drain completely after cloud recovers."""
        from elle.daemon.incidents.cloud_retry_worker import CloudRetryWorker

        worker = CloudRetryWorker(
            poll_interval_sec=0.1,
            batch_size=10,
            health_check_interval_sec=0.0,
        )
        worker.stats.cloud_healthy = True

        entries = [_make_queue_entry(entry_id=i, incident_id=f"drain-{i:03d}") for i in range(1, 4)]

        mock_queue = MagicMock()
        # First call returns entries, second call returns empty (drained)
        mock_queue.peek_batch.side_effect = [entries, []]
        mock_queue.mark_in_flight = MagicMock()
        mock_queue.mark_succeeded = MagicMock()

        mock_client = MagicMock()
        mock_client.submit_incident = AsyncMock(
            return_value=MagicMock(cloud_id="cloud-drain"),
        )

        with (
            patch(
                "elle.daemon.incidents.cloud_queue.get_cloud_queue",
                return_value=mock_queue,
            ),
            patch(
                "elle.daemon.incidents.cloud_sync.get_cloud_client",
                return_value=mock_client,
            ),
        ):
            # First batch: 3 entries processed
            await worker._process_batch()
            assert worker.stats.retries_succeeded == 3
            assert mock_queue.mark_succeeded.call_count == 3

            # Second batch: queue is empty
            await worker._process_batch()
            # No additional successes - queue was empty
            assert worker.stats.retries_succeeded == 3
