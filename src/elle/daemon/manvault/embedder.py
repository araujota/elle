"""Man Vault embedder (PostgreSQL + pgvector).

Generates and stores embeddings for man page chunks using Ollama.
Supports multiple embedding models with automatic fallback.

pgvector handles vector storage natively -- no struct.pack/unpack needed.
Cosine similarity is computed via the ``<=>`` operator in SQL queries
(see service.py search functions), so cosine_similarity() is removed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import psycopg

from elle.daemon.manvault.models import ManChunk
from elle.daemon.manvault.store import (
    get_chunks_for_doc,
    get_chunks_without_embeddings,
    get_embedding,
    upsert_embedding,
    upsert_embeddings_batch,
)
from elle.rag.ollama_client import (
    OllamaClient,
    OllamaError,
    get_client,
)
from elle.storage.engine import get_conn

logger = logging.getLogger(__name__)

PG_SCHEMA = "manvault"

# Default embedding model (768 dims, fast, good quality)
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"

# Fallback models in order of preference
FALLBACK_MODELS = ["all-minilm", "mxbai-embed-large"]

# Batch size for embedding generation
DEFAULT_BATCH_SIZE = 32


class ManVaultEmbedder:
    """Generates and stores embeddings for man page chunks.

    Uses Ollama's embedding API with automatic model detection
    and fallback support.

    Usage:
        embedder = ManVaultEmbedder()
        if embedder.is_available():
            count = embedder.embed_all_pending()
    """

    def __init__(
        self,
        client: OllamaClient | None = None,
        model: str | None = None,
    ) -> None:
        """Initialize the embedder.

        Args:
            client: Ollama client. Uses shared instance if not provided.
            model: Embedding model to use. Auto-detects if not provided.
        """
        self._client = client
        self._model = model
        self._detected_model: str | None = None

    @property
    def client(self) -> OllamaClient:
        """Get the Ollama client (lazy initialization)."""
        if self._client is None:
            self._client = get_client()
        return self._client

    @property
    def model(self) -> str:
        """Get the embedding model to use (auto-detect if needed)."""
        if self._model:
            return self._model

        if self._detected_model:
            return self._detected_model

        # Auto-detect available model
        if self.is_available():
            available = self.client.list_models()
            for candidate in [DEFAULT_EMBEDDING_MODEL] + FALLBACK_MODELS:
                if any(candidate in m for m in available):
                    self._detected_model = candidate
                    logger.info(f"Using embedding model: {candidate}")
                    return candidate

        # Fallback to default (will fail if not available)
        self._detected_model = DEFAULT_EMBEDDING_MODEL
        return self._detected_model

    def is_available(self) -> bool:
        """Check if the embedder is available.

        Returns:
            True if Ollama is running and has an embedding model.
        """
        return self.client.is_available()

    def embed_text(self, text: str) -> list[float]:
        """Generate embedding for a single text.

        Args:
            text: Text to embed.

        Returns:
            Embedding vector.

        Raises:
            OllamaUnavailableError: If Ollama is not available.
            OllamaError: If embedding fails.
        """
        return self.client.generate_embedding(self.model, text)

    def embed_texts_batch(
        self,
        texts: list[str],
        batch_size: int = DEFAULT_BATCH_SIZE,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[list[float]]:
        """Generate embeddings for multiple texts.

        Args:
            texts: Texts to embed.
            batch_size: Number of texts per batch (for progress reporting).
            progress_callback: Called with (current, total) for progress.

        Returns:
            List of embedding vectors.
        """
        embeddings: list[list[float]] = []
        total = len(texts)

        for i, text in enumerate(texts):
            embedding = self.embed_text(text)
            embeddings.append(embedding)

            if progress_callback and (i + 1) % batch_size == 0:
                progress_callback(i + 1, total)

        return embeddings

    def embed_chunk(
        self,
        chunk: ManChunk,
        conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
    ) -> bool:
        """Embed a single chunk and store the result.

        Args:
            chunk: Chunk to embed.
            conn: psycopg connection (optional, will obtain from pool if None).

        Returns:
            True if successful.
        """
        if chunk.id is None:
            logger.error("Cannot embed chunk without ID")
            return False

        if conn is not None:
            return self._embed_chunk_impl(conn, chunk)

        with get_conn(schema=PG_SCHEMA) as c:
            return self._embed_chunk_impl(c, chunk)

    def _embed_chunk_impl(
        self,
        conn: psycopg.Connection,  # type: ignore[type-arg]
        chunk: ManChunk,
    ) -> bool:
        try:
            # Check if already embedded
            existing = get_embedding(conn, chunk.id)  # type: ignore[arg-type]
            if existing:
                return True

            # Generate embedding
            embedding = self.embed_text(chunk.text)

            # Store embedding
            upsert_embedding(conn, chunk.id, embedding, self.model)  # type: ignore[arg-type]
            return True

        except OllamaError as e:
            logger.error(f"Failed to embed chunk {chunk.id}: {e}")
            return False

    def embed_document(
        self,
        doc_id: int,
        conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
    ) -> int:
        """Embed all chunks for a document.

        Args:
            doc_id: Document ID.
            conn: psycopg connection (optional, will obtain from pool if None).

        Returns:
            Number of chunks embedded.
        """
        if conn is not None:
            return self._embed_document_impl(conn, doc_id)

        with get_conn(schema=PG_SCHEMA) as c:
            return self._embed_document_impl(c, doc_id)

    def _embed_document_impl(
        self,
        conn: psycopg.Connection,  # type: ignore[type-arg]
        doc_id: int,
    ) -> int:
        # Get chunks for document
        chunks = get_chunks_for_doc(conn, doc_id)
        if not chunks:
            return 0

        count = 0
        embeddings_to_store: list[tuple[int, list[float]]] = []

        for chunk in chunks:
            if chunk.id is None:
                continue

            # Check if already embedded
            existing = get_embedding(conn, chunk.id)
            if existing:
                count += 1
                continue

            try:
                embedding = self.embed_text(chunk.text)
                embeddings_to_store.append((chunk.id, embedding))
                count += 1
            except OllamaError as e:
                logger.error(f"Failed to embed chunk {chunk.id}: {e}")

        # Batch store embeddings
        if embeddings_to_store:
            upsert_embeddings_batch(conn, embeddings_to_store, self.model)

        return count

    def embed_all_pending(
        self,
        conn: psycopg.Connection | None = None,  # type: ignore[type-arg]
        batch_size: int = DEFAULT_BATCH_SIZE,
        limit: int = 0,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> int:
        """Embed all chunks that don't have embeddings yet.

        Args:
            conn: psycopg connection (optional, will obtain from pool if None).
            batch_size: Number of chunks to process at a time.
            limit: Maximum chunks to embed (0 = no limit).
            progress_callback: Called with (current, total) for progress.

        Returns:
            Number of chunks embedded.
        """
        if conn is not None:
            return self._embed_all_pending_impl(conn, batch_size, limit, progress_callback)

        with get_conn(schema=PG_SCHEMA) as c:
            return self._embed_all_pending_impl(c, batch_size, limit, progress_callback)

    def _embed_all_pending_impl(
        self,
        conn: psycopg.Connection,  # type: ignore[type-arg]
        batch_size: int,
        limit: int,
        progress_callback: Callable[[int, int], None] | None,
    ) -> int:
        count = 0

        while True:
            # Get next batch of chunks without embeddings
            fetch_limit = min(batch_size, limit - count) if limit else batch_size
            chunks = get_chunks_without_embeddings(conn, fetch_limit)

            if not chunks:
                break

            embeddings_to_store: list[tuple[int, list[float]]] = []

            for chunk in chunks:
                if chunk.id is None:
                    continue

                try:
                    embedding = self.embed_text(chunk.text)
                    embeddings_to_store.append((chunk.id, embedding))
                    count += 1

                    if progress_callback:
                        progress_callback(count, limit or -1)

                except OllamaError as e:
                    logger.error(f"Failed to embed chunk {chunk.id}: {e}")
                    continue

                if limit and count >= limit:
                    break

            # Batch store embeddings
            if embeddings_to_store:
                upsert_embeddings_batch(conn, embeddings_to_store, self.model)

            if limit and count >= limit:
                break

        return count


# Module-level embedder instance
_embedder: ManVaultEmbedder | None = None


def get_embedder() -> ManVaultEmbedder:
    """Get the shared embedder instance.

    Returns:
        The ManVaultEmbedder singleton.
    """
    global _embedder
    if _embedder is None:
        _embedder = ManVaultEmbedder()
    return _embedder
