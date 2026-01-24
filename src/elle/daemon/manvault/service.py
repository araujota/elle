"""Man Vault background service.

Provides background indexing and embedding for the Man Vault.
Runs as part of the elled daemon.

Features:
- Initial full indexing on first run
- Periodic incremental indexing
- Background embedding generation
- Status reporting
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from elle.daemon.manvault.embedder import ManVaultEmbedder, get_embedder
from elle.daemon.manvault.indexer import (
    CORE_COMMANDS,
    index_all,
    index_incremental,
    is_core_seeded,
    seed_core_commands,
)
from elle.daemon.manvault.models import ManVaultStatus
from elle.daemon.manvault.schema import ensure_schema, get_connection, get_db_path
from elle.daemon.manvault.store import (
    count_chunks,
    count_docs,
    count_embeddings,
    get_meta,
    get_section_counts,
    set_meta,
)
from elle.rag.ollama_client import OllamaUnavailableError

logger = logging.getLogger(__name__)

# Service intervals
PERIODIC_CHECK_INTERVAL = 3600  # 1 hour
INCREMENTAL_INDEX_INTERVAL = 86400  # 24 hours
EMBEDDING_BATCH_SIZE = 100


class ManVaultService:
    """Background service for Man Vault indexing and embedding.

    Manages automatic indexing and embedding of man pages:
    - Full index on first run (when database is empty)
    - Incremental index every 24 hours
    - Embedding of pending chunks when Ollama is available

    Usage:
        service = ManVaultService()
        await service.start()
        # ... service runs in background ...
        await service.stop()
    """

    def __init__(self) -> None:
        """Initialize the service."""
        self._indexing = False
        self._embedding = False
        self._stop_requested = False
        self._task: asyncio.Task | None = None
        self._embedder: ManVaultEmbedder | None = None
        self._last_incremental: datetime | None = None

    @property
    def is_indexing(self) -> bool:
        """Check if indexing is in progress."""
        return self._indexing

    @property
    def is_embedding(self) -> bool:
        """Check if embedding is in progress."""
        return self._embedding

    @property
    def embedder(self) -> ManVaultEmbedder:
        """Get the embedder (lazy initialization)."""
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

    async def start(self) -> None:
        """Start the background service.

        Initializes the schema, seeds core commands, and starts the periodic task loop.
        Core commands are seeded first to ensure essential documentation is
        immediately available before full indexing completes.
        """
        logger.info("ManVault service starting")

        # Initialize schema
        conn = get_connection()
        try:
            ensure_schema(conn)
        finally:
            conn.close()

        # Seed core commands first (fast, ensures essentials are available)
        if not is_core_seeded():
            logger.info("Seeding core commands...")
            await self._do_core_seed()

        # Start background task
        self._stop_requested = False
        self._task = asyncio.create_task(self._run_periodic_tasks())

    async def stop(self) -> None:
        """Stop the service gracefully.

        Signals the background task to stop and waits for it to finish.
        """
        logger.info("ManVault service stopping")
        self._stop_requested = True

        if self._task:
            try:
                # Wait for task to finish with timeout
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("ManVault service stop timed out, cancelling")
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None

    async def _run_periodic_tasks(self) -> None:
        """Run periodic indexing and embedding tasks."""
        while not self._stop_requested:
            try:
                # Check if initial indexing is needed
                if self._needs_initial_index():
                    logger.info("Starting initial Man Vault index")
                    await self._do_full_index()

                # Check if incremental index is due
                if self._needs_incremental_index():
                    logger.info("Starting incremental Man Vault index")
                    await self._do_incremental_index()

                # Embed pending chunks if Ollama is available
                if self.embedder.is_available():
                    await self._do_embedding()

            except Exception as e:
                logger.error(f"ManVault periodic task error: {e}")

            # Sleep between cycles
            for _ in range(PERIODIC_CHECK_INTERVAL):
                if self._stop_requested:
                    return
                await asyncio.sleep(1)

    def _needs_initial_index(self) -> bool:
        """Check if initial indexing is needed.

        Returns:
            True if the database is empty.
        """
        conn = get_connection()
        try:
            return count_docs(conn) == 0
        finally:
            conn.close()

    def _needs_incremental_index(self) -> bool:
        """Check if incremental indexing is due.

        Returns:
            True if more than 24 hours since last incremental index.
        """
        conn = get_connection()
        try:
            last_indexed = get_meta(conn, "indexed_at")
            if not last_indexed:
                return True

            last_dt = datetime.fromisoformat(last_indexed)
            return datetime.now() - last_dt > timedelta(seconds=INCREMENTAL_INDEX_INTERVAL)
        finally:
            conn.close()

    async def _do_core_seed(self) -> int:
        """Seed core commands in background thread.

        This is called at startup to ensure essential man pages
        (systemctl, journalctl, apt, docker, etc.) are immediately available.

        Returns:
            Number of core commands indexed.
        """
        self._indexing = True
        try:
            count = await asyncio.to_thread(seed_core_commands)
            logger.info(f"Core seed complete: {count} commands")
            return count
        except Exception as e:
            logger.error(f"Core seed failed: {e}")
            return 0
        finally:
            self._indexing = False

    async def _do_full_index(self) -> int:
        """Run full indexing in background thread.

        Returns:
            Number of documents indexed.
        """
        self._indexing = True
        try:
            # Run indexing in thread pool to avoid blocking
            count = await asyncio.to_thread(index_all)
            logger.info(f"Full index complete: {count} docs")
            return count
        except Exception as e:
            logger.error(f"Full index failed: {e}")
            return 0
        finally:
            self._indexing = False

    async def _do_incremental_index(self) -> tuple[int, int]:
        """Run incremental indexing in background thread.

        Returns:
            Tuple of (added, updated) counts.
        """
        self._indexing = True
        try:
            added, updated = await asyncio.to_thread(index_incremental)
            if added or updated:
                logger.info(f"Incremental index: {added} added, {updated} updated")
            self._last_incremental = datetime.now()
            return added, updated
        except Exception as e:
            logger.error(f"Incremental index failed: {e}")
            return 0, 0
        finally:
            self._indexing = False

    async def _do_embedding(self) -> int:
        """Embed pending chunks in background thread.

        Returns:
            Number of chunks embedded.
        """
        self._embedding = True
        try:
            count = await asyncio.to_thread(
                self.embedder.embed_all_pending,
                None,  # conn
                EMBEDDING_BATCH_SIZE,  # batch_size
                EMBEDDING_BATCH_SIZE,  # limit per cycle
            )
            if count:
                logger.info(f"Embedded {count} chunks")
            return count
        except OllamaUnavailableError:
            logger.debug("Ollama not available, skipping embedding")
            return 0
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            return 0
        finally:
            self._embedding = False

    async def trigger_reindex(self) -> None:
        """Trigger a full reindex.

        Can be called to manually start reindexing.
        """
        if self._indexing:
            logger.warning("Indexing already in progress")
            return

        asyncio.create_task(self._do_full_index())

    def get_status(self) -> ManVaultStatus:
        """Get current service status.

        Returns:
            ManVaultStatus with current state.
        """
        conn = get_connection()
        try:
            # Get counts
            total_docs = count_docs(conn)
            total_chunks = count_chunks(conn)
            embedded_chunks = count_embeddings(conn)

            # Get metadata
            indexed_at_str = get_meta(conn, "indexed_at")
            indexed_at = datetime.fromisoformat(indexed_at_str) if indexed_at_str else None

            # Get section counts
            sections = get_section_counts(conn)

            # Get database size
            db_path = get_db_path()
            db_size = db_path.stat().st_size if db_path.exists() else 0

            # Get embedding model
            embedding_model = self.embedder.model if self.embedder.is_available() else None

            return ManVaultStatus(
                total_docs=total_docs,
                total_chunks=total_chunks,
                embedded_chunks=embedded_chunks,
                indexed_at=indexed_at,
                embedding_model=embedding_model,
                sections=sections,
                db_size_bytes=db_size,
                is_indexing=self._indexing,
                is_embedding=self._embedding,
            )
        finally:
            conn.close()


# Module-level service instance
_service: ManVaultService | None = None


def get_service() -> ManVaultService:
    """Get the ManVault service singleton.

    Returns:
        The ManVaultService instance.
    """
    global _service
    if _service is None:
        _service = ManVaultService()
    return _service


def get_status() -> ManVaultStatus:
    """Get current Man Vault status.

    Convenience function that gets status without requiring
    the full service to be running.

    Returns:
        ManVaultStatus with current state.
    """
    conn = get_connection()
    try:
        ensure_schema(conn)

        # Get counts
        total_docs = count_docs(conn)
        total_chunks = count_chunks(conn)
        embedded_chunks = count_embeddings(conn)

        # Get metadata
        indexed_at_str = get_meta(conn, "indexed_at")
        indexed_at = datetime.fromisoformat(indexed_at_str) if indexed_at_str else None

        # Get section counts
        sections = get_section_counts(conn)

        # Get database size
        db_path = get_db_path()
        db_size = db_path.stat().st_size if db_path.exists() else 0

        # Try to get embedding model
        try:
            embedder = get_embedder()
            embedding_model = embedder.model if embedder.is_available() else None
        except Exception:
            embedding_model = None

        return ManVaultStatus(
            total_docs=total_docs,
            total_chunks=total_chunks,
            embedded_chunks=embedded_chunks,
            indexed_at=indexed_at,
            embedding_model=embedding_model,
            sections=sections,
            db_size_bytes=db_size,
            is_indexing=False,  # Can't determine without service
            is_embedding=False,
        )
    finally:
        conn.close()
