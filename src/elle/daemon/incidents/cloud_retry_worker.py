"""Background worker for retrying failed cloud incident submissions.

Periodically checks the cloud submission queue and retries failed
submissions with health checking and exponential backoff.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class WorkerStats:
    """Statistics for the cloud retry worker."""

    retries_attempted: int = 0
    retries_succeeded: int = 0
    retries_failed: int = 0
    cloud_healthy: bool = False
    last_health_check: float = 0.0
    started_at: float = field(default_factory=time.time)


class CloudRetryWorker:
    """Async background task that retries failed cloud submissions.

    Lifecycle:
    1. On start: recover_in_flight() to reset interrupted entries
    2. Periodically: health check → process batch → cleanup stale
    3. On stop: cancel task gracefully
    """

    def __init__(
        self,
        *,
        poll_interval_sec: float = 30.0,
        batch_size: int = 10,
        health_check_interval_sec: float = 60.0,
        stale_entry_hours: int = 168,
    ) -> None:
        self._poll_interval = poll_interval_sec
        self._batch_size = batch_size
        self._health_check_interval = health_check_interval_sec
        self._stale_entry_hours = stale_entry_hours

        self._task: asyncio.Task[None] | None = None
        self._shutdown = asyncio.Event()
        self.stats = WorkerStats()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start the retry worker background task."""
        if self._task is not None:
            logger.warning("Cloud retry worker already running")
            return

        # Recover in-flight entries from previous run
        try:
            from elle.daemon.incidents.cloud_queue import get_cloud_queue

            queue = get_cloud_queue()
            recovered = queue.recover_in_flight()
            if recovered:
                logger.info(f"Recovered {recovered} in-flight cloud submissions")
        except Exception as e:
            logger.warning(f"Failed to recover in-flight entries: {e}")

        self._shutdown.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="cloud-retry-worker",
        )
        logger.info("Cloud retry worker started")

    async def stop(self) -> None:
        """Stop the retry worker gracefully."""
        if self._task is None:
            return

        self._shutdown.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

        self._task = None
        logger.info(
            f"Cloud retry worker stopped "
            f"(attempted={self.stats.retries_attempted}, "
            f"succeeded={self.stats.retries_succeeded}, "
            f"failed={self.stats.retries_failed})"
        )

    async def _run_loop(self) -> None:
        """Main worker loop: health check → process → cleanup."""
        while not self._shutdown.is_set():
            try:
                # Wait for poll interval or shutdown
                try:
                    await asyncio.wait_for(
                        self._shutdown.wait(),
                        timeout=self._poll_interval,
                    )
                    break  # Shutdown was set
                except TimeoutError:
                    pass

                # Run health check if interval elapsed
                await self._maybe_health_check()

                # Only process if cloud is healthy
                if self.stats.cloud_healthy:
                    await self._process_batch()

                # Periodic cleanup
                await self._cleanup_stale()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cloud retry worker error: {e}")
                # Continue running despite errors

    async def _maybe_health_check(self) -> None:
        """Run a health check if the interval has elapsed."""
        now = time.time()
        if now - self.stats.last_health_check < self._health_check_interval:
            return

        try:
            from elle.daemon.incidents.cloud_sync import get_cloud_client

            client = get_cloud_client()
            healthy = await client.health_check()
            was_healthy = self.stats.cloud_healthy
            self.stats.cloud_healthy = healthy
            self.stats.last_health_check = now

            if healthy and not was_healthy:
                logger.info("Cloud endpoint recovered, will resume retries")
            elif not healthy and was_healthy:
                logger.warning("Cloud endpoint unreachable, pausing retries")

        except Exception as e:
            self.stats.cloud_healthy = False
            self.stats.last_health_check = now
            logger.debug(f"Cloud health check failed: {e}")

    async def _process_batch(self) -> None:
        """Process a batch of queued submissions."""
        try:
            from elle.daemon.incidents.cloud_queue import get_cloud_queue
            from elle.daemon.incidents.cloud_sync import get_cloud_client

            queue = get_cloud_queue()
            client = get_cloud_client()

            entries = queue.peek_batch(self._batch_size)
            if not entries:
                return

            for entry in entries:
                if self._shutdown.is_set():
                    break

                self.stats.retries_attempted += 1
                queue.mark_in_flight(entry.id)

                try:
                    # Deserialize payload
                    payload = json.loads(entry.payload_json)

                    # Rebuild anonymized report for submission
                    from elle.daemon.incidents.anonymize import AnonymizedIncidentReport

                    report = AnonymizedIncidentReport(**payload)

                    # Submit to cloud
                    result = await client.submit_incident(report)

                    # Mark as succeeded
                    queue.mark_succeeded(entry.id, result.cloud_id)
                    self.stats.retries_succeeded += 1
                    logger.info(
                        f"Cloud retry succeeded for incident {entry.incident_id} (attempt {entry.retry_count + 1})"
                    )

                except Exception as e:
                    error_str = str(e)
                    queue.mark_failed(entry.id, error_str)
                    self.stats.retries_failed += 1

                    # If this looks like a server-down error, stop batch early
                    if _is_server_down_error(e):
                        self.stats.cloud_healthy = False
                        logger.warning("Cloud appears down during batch, pausing retries")
                        break

        except RuntimeError:
            # Queue not configured
            pass
        except Exception as e:
            logger.error(f"Failed to process retry batch: {e}")

    async def _cleanup_stale(self) -> None:
        """Clean up old completed/failed entries."""
        try:
            from elle.daemon.incidents.cloud_queue import get_cloud_queue

            queue = get_cloud_queue()
            queue.cleanup_stale(self._stale_entry_hours)
        except RuntimeError:
            pass
        except Exception as e:
            logger.debug(f"Stale cleanup error: {e}")


def _is_server_down_error(exc: Exception) -> bool:
    """Check if an error indicates the server is unreachable."""
    import httpx

    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (502, 503, 504)
    return False


# =============================================================================
# Module-level singleton
# =============================================================================

_worker: CloudRetryWorker | None = None


def get_cloud_retry_worker() -> CloudRetryWorker:
    """Get the global cloud retry worker instance."""
    global _worker
    if _worker is None:
        _worker = CloudRetryWorker()
    return _worker


def reset_cloud_retry_worker() -> None:
    """Reset the global cloud retry worker (for testing)."""
    global _worker
    _worker = None
