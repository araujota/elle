"""Background service for Incident Vault.

Provides:
- Periodic embedding generation for incidents
- Incident status monitoring
- Automatic incident cleanup/archival
"""

import asyncio
import logging
from datetime import datetime, timedelta

from elle.daemon.incidents.models import IncidentVaultStatus
from elle.daemon.incidents.retriever import generate_case_text, get_status
from elle.daemon.incidents.schema import ensure_schema, get_connection
from elle.daemon.incidents.store import (
    get_incident,
    get_incidents_without_embeddings,
    upsert_embedding,
)

logger = logging.getLogger(__name__)

# Embedding model to use
EMBEDDING_MODEL = "nomic-embed-text"


class IncidentVaultService:
    """Background service for Incident Vault maintenance."""

    def __init__(self):
        self._embedding = False
        self._stop_requested = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background service."""
        logger.info("Incident Vault service starting")

        # Ensure schema exists
        conn = get_connection()
        try:
            ensure_schema(conn)
        finally:
            conn.close()

        # Start periodic tasks
        self._task = asyncio.create_task(self._run_periodic_tasks())

    async def stop(self) -> None:
        """Stop the service gracefully."""
        self._stop_requested = True
        logger.info("Incident Vault service stopping")

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_periodic_tasks(self) -> None:
        """Run periodic maintenance tasks."""
        while not self._stop_requested:
            try:
                # Embed pending incidents
                await self._do_embedding()

                # Check for stale open incidents
                await self._check_stale_incidents()

            except Exception as e:
                logger.error(f"Incident Vault periodic task error: {e}")

            # Sleep between cycles (every 5 minutes)
            await asyncio.sleep(300)

    async def _do_embedding(self) -> None:
        """Generate embeddings for incidents without them."""
        if self._embedding:
            return

        self._embedding = True
        try:
            count = await asyncio.to_thread(self._embed_pending)
            if count > 0:
                logger.info(f"Embedded {count} incidents")
        except Exception as e:
            logger.debug(f"Embedding error: {e}")
        finally:
            self._embedding = False

    def _embed_pending(self) -> int:
        """Embed incidents that don't have embeddings."""
        try:
            from elle.rag import get_client
        except ImportError:
            return 0

        client = get_client()
        if not client.is_available():
            return 0

        conn = get_connection()
        try:
            ensure_schema(conn)

            # Get incidents without embeddings
            incident_ids = get_incidents_without_embeddings(limit=50, conn=conn)
            if not incident_ids:
                return 0

            embedded = 0
            for incident_id in incident_ids:
                incident = get_incident(incident_id, conn=conn)
                if not incident:
                    continue

                # Generate case text
                case_text = generate_case_text(incident)

                # Generate embedding
                try:
                    embedding = client.generate_embedding(
                        model=EMBEDDING_MODEL,
                        text=case_text,
                    )
                    upsert_embedding(
                        incident_id,
                        embedding,
                        EMBEDDING_MODEL,
                        conn=conn,
                    )
                    embedded += 1
                except Exception as e:
                    logger.debug(f"Failed to embed incident {incident_id}: {e}")

            return embedded

        finally:
            conn.close()

    async def _check_stale_incidents(self) -> None:
        """Check for and handle stale open incidents."""
        await asyncio.to_thread(self._handle_stale_incidents)

    def _handle_stale_incidents(self) -> None:
        """Handle incidents that have been open too long."""
        conn = get_connection()
        try:
            ensure_schema(conn)

            # Get old open incidents (more than 24 hours)
            cutoff = datetime.utcnow() - timedelta(hours=24)

            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id FROM incidents
                WHERE status = 'open'
                AND updated_at < ?
                """,
                (cutoff.isoformat(),),
            )

            for row in cursor.fetchall():
                incident_id = row[0]
                # Don't auto-close, just log for now
                logger.debug(f"Stale open incident: {incident_id}")

        finally:
            conn.close()

    @property
    def is_embedding(self) -> bool:
        """Check if embedding is in progress."""
        return self._embedding

    def get_status(self) -> IncidentVaultStatus:
        """Get current service status."""
        status_dict = get_status()
        return IncidentVaultStatus(**status_dict)


# Module-level service instance
_service: IncidentVaultService | None = None


def get_service() -> IncidentVaultService:
    """Get the Incident Vault service singleton."""
    global _service
    if _service is None:
        _service = IncidentVaultService()
    return _service


def reset_service() -> None:
    """Reset the service (for testing)."""
    global _service
    _service = None
