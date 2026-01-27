"""Man Vault embedder.

Generates and stores embeddings for man page chunks using Ollama.
Supports multiple embedding models with automatic fallback.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Callable

from elle.daemon.manvault.models import ManChunk
from elle.daemon.manvault.schema import ensure_schema, get_connection
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

logger = logging.getLogger(__name__)

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
        embeddings = []
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
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        """Embed a single chunk and store the result.

        Args:
            chunk: Chunk to embed.
            conn: SQLite connection.

        Returns:
            True if successful.
        """
        if chunk.id is None:
            logger.error("Cannot embed chunk without ID")
            return False

        # Get or create connection
        own_conn = conn is None
        if own_conn:
            conn = get_connection()
            ensure_schema(conn)

        try:
            # Check if already embedded
            existing = get_embedding(conn, chunk.id)
            if existing:
                return True

            # Generate embedding
            embedding = self.embed_text(chunk.text)

            # Store embedding
            upsert_embedding(conn, chunk.id, embedding, self.model)
            return True

        except OllamaError as e:
            logger.error(f"Failed to embed chunk {chunk.id}: {e}")
            return False
        finally:
            if own_conn and conn is not None:
                conn.close()

    def embed_document(
        self,
        doc_id: int,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """Embed all chunks for a document.

        Args:
            doc_id: Document ID.
            conn: SQLite connection.

        Returns:
            Number of chunks embedded.
        """
        # Get or create connection
        own_conn = conn is None
        if own_conn:
            conn = get_connection()
            ensure_schema(conn)

        try:
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

        finally:
            if own_conn and conn is not None:
                conn.close()

    def embed_all_pending(
        self,
        conn: sqlite3.Connection | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        limit: int = 0,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> int:
        """Embed all chunks that don't have embeddings yet.

        Args:
            conn: SQLite connection.
            batch_size: Number of chunks to process at a time.
            limit: Maximum chunks to embed (0 = no limit).
            progress_callback: Called with (current, total) for progress.

        Returns:
            Number of chunks embedded.
        """
        # Get or create connection
        own_conn = conn is None
        if own_conn:
            conn = get_connection()
            ensure_schema(conn)

        try:
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

        finally:
            if own_conn and conn is not None:
                conn.close()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Cosine similarity score between -1 and 1.
    """
    if len(a) != len(b):
        raise ValueError("Vectors must have same dimension")

    dot_product = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


def euclidean_distance(a: list[float], b: list[float]) -> float:
    """Compute Euclidean distance between two vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Euclidean distance (lower = more similar).
    """
    if len(a) != len(b):
        raise ValueError("Vectors must have same dimension")

    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=False)))


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
