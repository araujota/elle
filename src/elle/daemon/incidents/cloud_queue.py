"""Persistent queue for failed cloud incident submissions.

When a cloud submission fails, the incident is enqueued here for
automatic retry with exponential backoff. Uses PostgreSQL for durability
across daemon restarts.

Storage backend: PostgreSQL via psycopg (schema ``incidents``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from pydantic import BaseModel, ConfigDict

from elle.storage.engine import get_conn

logger = logging.getLogger(__name__)

PG_SCHEMA = "incidents"


# =============================================================================
# Models
# =============================================================================


@dataclass
class QueueEntry:
    """A single entry in the cloud submission queue."""

    id: int
    incident_id: str
    payload_json: str
    original_hash: str
    status: str  # pending, in_flight, succeeded, failed_permanent
    retry_count: int
    max_retries: int
    next_retry_at: str
    last_error: str | None
    enqueued_at: str
    last_attempted_at: str | None
    completed_at: str | None
    cloud_id: str | None


class QueueMetrics(BaseModel):
    """Aggregate metrics for the cloud submission queue."""

    model_config = ConfigDict(frozen=True)

    pending_count: int = 0
    in_flight_count: int = 0
    succeeded_count: int = 0
    failed_permanent_count: int = 0
    total_retries: int = 0
    oldest_pending_age_sec: float | None = None
    queue_size: int = 0


# =============================================================================
# Cloud Submission Queue
# =============================================================================


class CloudSubmissionQueue:
    """Persistent queue for retrying failed cloud incident submissions.

    Backed by PostgreSQL for durability. Entries are deduplicated by
    original_hash, retried with exponential backoff, and cleaned
    up after a configurable stale period.
    """

    def __init__(
        self,
        *,
        initial_delay_sec: float = 30.0,
        max_delay_sec: float = 3600.0,
        backoff_factor: float = 2.0,
        max_retries: int = 20,
        max_size: int = 1000,
    ) -> None:
        self._initial_delay_sec = initial_delay_sec
        self._max_delay_sec = max_delay_sec
        self._backoff_factor = backoff_factor
        self._max_retries = max_retries
        self._max_size = max_size

    def enqueue(
        self,
        incident_id: str,
        payload: dict[str, Any],
        original_hash: str,
    ) -> bool:
        """Add a failed submission to the retry queue.

        Uses INSERT ... ON CONFLICT DO NOTHING to deduplicate on original_hash.

        Args:
            incident_id: The incident ID.
            payload: The serialized incident payload.
            original_hash: Hash for deduplication.

        Returns:
            True if enqueued, False if duplicate or queue full.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()

            # Check queue size
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM cloud_submission_queue "
                "WHERE status IN ('pending', 'in_flight')"
            )
            row = cursor.fetchone()
            current_size = row["cnt"] if row else 0

            if current_size >= self._max_size:
                logger.warning(
                    f"Cloud submission queue full ({current_size}/{self._max_size}), "
                    f"rejecting incident {incident_id}"
                )
                return False

            now = datetime.now(timezone.utc).isoformat()
            next_retry = (
                datetime.now(timezone.utc) + timedelta(seconds=self._initial_delay_sec)
            ).isoformat()

            try:
                cursor.execute(
                    """INSERT INTO cloud_submission_queue
                       (incident_id, payload_json, original_hash, status,
                        retry_count, max_retries, next_retry_at, enqueued_at)
                       VALUES (%s, %s, %s, 'pending', 0, %s, %s, %s)
                       ON CONFLICT (original_hash) DO NOTHING""",
                    (
                        incident_id,
                        json.dumps(payload),
                        original_hash,
                        self._max_retries,
                        next_retry,
                        now,
                    ),
                )

                if cursor.rowcount > 0:
                    logger.info(
                        f"Enqueued incident {incident_id} for cloud retry "
                        f"(hash={original_hash[:12]}...)"
                    )
                    return True
                else:
                    logger.debug(
                        f"Incident {incident_id} already in queue (dedup)"
                    )
                    return False

            except psycopg.Error as e:
                logger.error(f"Failed to enqueue incident {incident_id}: {e}")
                return False

    def peek_batch(self, batch_size: int = 10) -> list[QueueEntry]:
        """Get the next batch of entries ready for retry.

        Returns pending entries where next_retry_at <= now, ordered
        by enqueue time (FIFO).

        Args:
            batch_size: Maximum entries to return.

        Returns:
            List of QueueEntry objects ready for retry.
        """
        now = datetime.now(timezone.utc).isoformat()

        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, incident_id, payload_json, original_hash, status,
                          retry_count, max_retries, next_retry_at, last_error,
                          enqueued_at, last_attempted_at, completed_at, cloud_id
                   FROM cloud_submission_queue
                   WHERE status = 'pending' AND next_retry_at <= %s
                   ORDER BY enqueued_at ASC
                   LIMIT %s""",
                (now, batch_size),
            )

            entries = []
            for row in cursor.fetchall():
                entries.append(QueueEntry(
                    id=row["id"],
                    incident_id=row["incident_id"],
                    payload_json=row["payload_json"],
                    original_hash=row["original_hash"],
                    status=row["status"],
                    retry_count=row["retry_count"],
                    max_retries=row["max_retries"],
                    next_retry_at=row["next_retry_at"],
                    last_error=row["last_error"],
                    enqueued_at=row["enqueued_at"],
                    last_attempted_at=row["last_attempted_at"],
                    completed_at=row["completed_at"],
                    cloud_id=row["cloud_id"],
                ))

            return entries

    def mark_in_flight(self, entry_id: int) -> None:
        """Mark an entry as in-flight (being submitted)."""
        now = datetime.now(timezone.utc).isoformat()
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE cloud_submission_queue
                   SET status = 'in_flight', last_attempted_at = %s
                   WHERE id = %s""",
                (now, entry_id),
            )

    def mark_succeeded(self, entry_id: int, cloud_id: str | None = None) -> None:
        """Mark an entry as successfully submitted."""
        now = datetime.now(timezone.utc).isoformat()
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE cloud_submission_queue
                   SET status = 'succeeded', completed_at = %s, cloud_id = %s
                   WHERE id = %s""",
                (now, cloud_id, entry_id),
            )
        logger.info(f"Cloud submission queue entry {entry_id} succeeded")

    def mark_failed(self, entry_id: int, error: str) -> None:
        """Mark an entry as failed, computing next retry backoff.

        If max retries exceeded, marks as failed_permanent.
        """
        now = datetime.now(timezone.utc).isoformat()

        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()

            # Get current retry count
            cursor.execute(
                "SELECT retry_count, max_retries FROM cloud_submission_queue WHERE id = %s",
                (entry_id,),
            )
            row = cursor.fetchone()
            if not row:
                return

            retry_count = row["retry_count"] + 1
            max_retries = row["max_retries"]

            if retry_count >= max_retries:
                # Permanently failed
                cursor.execute(
                    """UPDATE cloud_submission_queue
                       SET status = 'failed_permanent', retry_count = %s,
                           last_error = %s, last_attempted_at = %s, completed_at = %s
                       WHERE id = %s""",
                    (retry_count, error, now, now, entry_id),
                )
                logger.warning(
                    f"Cloud submission queue entry {entry_id} permanently failed "
                    f"after {retry_count} retries: {error}"
                )
            else:
                # Compute next backoff
                delay = min(
                    self._initial_delay_sec * (self._backoff_factor ** retry_count),
                    self._max_delay_sec,
                )
                next_retry = (
                    datetime.now(timezone.utc) + timedelta(seconds=delay)
                ).isoformat()

                cursor.execute(
                    """UPDATE cloud_submission_queue
                       SET status = 'pending', retry_count = %s,
                           next_retry_at = %s, last_error = %s,
                           last_attempted_at = %s
                       WHERE id = %s""",
                    (retry_count, next_retry, error, now, entry_id),
                )
                logger.debug(
                    f"Cloud submission entry {entry_id} retry {retry_count}/{max_retries}, "
                    f"next attempt in {delay:.0f}s"
                )

    def recover_in_flight(self) -> int:
        """Reset in-flight entries to pending on startup.

        Called during daemon startup to recover entries that were
        interrupted mid-submission.

        Returns:
            Number of entries recovered.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE cloud_submission_queue
                   SET status = 'pending'
                   WHERE status = 'in_flight'"""
            )
            count = cursor.rowcount
        if count > 0:
            logger.info(f"Recovered {count} in-flight cloud submission entries")
        return count

    def cleanup_stale(self, hours: int = 168) -> int:
        """Delete old succeeded/failed_permanent entries.

        Args:
            hours: Delete entries older than this many hours (default 7 days).

        Returns:
            Number of entries deleted.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat()

        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """DELETE FROM cloud_submission_queue
                   WHERE status IN ('succeeded', 'failed_permanent')
                   AND completed_at < %s""",
                (cutoff,),
            )
            count = cursor.rowcount
        if count > 0:
            logger.info(f"Cleaned up {count} stale cloud queue entries")
        return count

    def get_metrics(self) -> QueueMetrics:
        """Get aggregate queue metrics."""
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.cursor()

            # Counts by status
            cursor.execute(
                "SELECT status, COUNT(*) AS cnt FROM cloud_submission_queue GROUP BY status"
            )
            counts: dict[str, int] = {}
            for row in cursor.fetchall():
                counts[row["status"]] = row["cnt"]

            # Total retries
            cursor.execute(
                "SELECT COALESCE(SUM(retry_count), 0) AS total FROM cloud_submission_queue"
            )
            row = cursor.fetchone()
            total_retries = row["total"] if row else 0

            # Oldest pending age
            oldest_pending_age: float | None = None
            cursor.execute(
                "SELECT MIN(enqueued_at) AS oldest FROM cloud_submission_queue WHERE status = 'pending'"
            )
            row = cursor.fetchone()
            if row and row["oldest"]:
                try:
                    enqueued_val = row["oldest"]
                    if isinstance(enqueued_val, str):
                        enqueued = datetime.fromisoformat(enqueued_val)
                    else:
                        enqueued = enqueued_val
                    if enqueued.tzinfo is None:
                        enqueued = enqueued.replace(tzinfo=timezone.utc)
                    oldest_pending_age = (
                        datetime.now(timezone.utc) - enqueued
                    ).total_seconds()
                except (ValueError, TypeError):
                    pass

            # Total queue size
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM cloud_submission_queue"
            )
            row = cursor.fetchone()
            queue_size = row["cnt"] if row else 0

            return QueueMetrics(
                pending_count=counts.get("pending", 0),
                in_flight_count=counts.get("in_flight", 0),
                succeeded_count=counts.get("succeeded", 0),
                failed_permanent_count=counts.get("failed_permanent", 0),
                total_retries=total_retries,
                oldest_pending_age_sec=oldest_pending_age,
                queue_size=queue_size,
            )


# =============================================================================
# Module-level singleton
# =============================================================================

_cloud_queue: CloudSubmissionQueue | None = None


def get_cloud_queue() -> CloudSubmissionQueue:
    """Get the global cloud submission queue.

    Raises:
        RuntimeError: If queue has not been configured.
    """
    if _cloud_queue is None:
        raise RuntimeError(
            "Cloud submission queue not configured. "
            "Call configure_cloud_queue() first."
        )
    return _cloud_queue


def configure_cloud_queue(
    *,
    initial_delay_sec: float = 30.0,
    max_delay_sec: float = 3600.0,
    backoff_factor: float = 2.0,
    max_retries: int = 20,
    max_size: int = 1000,
) -> CloudSubmissionQueue:
    """Configure the global cloud submission queue.

    Args:
        initial_delay_sec: Initial retry delay.
        max_delay_sec: Maximum retry delay cap.
        backoff_factor: Exponential backoff multiplier.
        max_retries: Max retries before permanent failure.
        max_size: Maximum queue size.

    Returns:
        The configured CloudSubmissionQueue.
    """
    global _cloud_queue
    _cloud_queue = CloudSubmissionQueue(
        initial_delay_sec=initial_delay_sec,
        max_delay_sec=max_delay_sec,
        backoff_factor=backoff_factor,
        max_retries=max_retries,
        max_size=max_size,
    )
    return _cloud_queue


def reset_cloud_queue() -> None:
    """Reset the global cloud queue (for testing)."""
    global _cloud_queue
    _cloud_queue = None
