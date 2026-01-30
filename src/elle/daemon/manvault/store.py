"""Man Vault store - CRUD operations (PostgreSQL).

Provides database operations for the Man Vault:
- Document CRUD (upsert, get, delete)
- Chunk management
- Embedding storage (pgvector)
- Batch operations for efficient indexing
"""

from __future__ import annotations

import logging
from datetime import datetime

import psycopg

from elle.daemon.manvault.models import ManChunk, ManDoc
from elle.storage.engine import get_conn

logger = logging.getLogger(__name__)

PG_SCHEMA = "manvault"


def upsert_doc(conn: psycopg.Connection | None, doc: ManDoc) -> int:  # type: ignore[type-arg]
    """Insert or update a document.

    Uses INSERT ... ON CONFLICT ... DO UPDATE to handle both cases.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        doc: Document to upsert.

    Returns:
        Document ID.
    """
    if conn is not None:
        return _upsert_doc_impl(conn, doc)

    with get_conn(schema=PG_SCHEMA) as c:
        return _upsert_doc_impl(c, doc)


def _upsert_doc_impl(conn: psycopg.Connection, doc: ManDoc) -> int:  # type: ignore[type-arg]
    cursor = conn.execute(
        """
        INSERT INTO docs (name, section, lang, source_path, sha256, text, updated_at, package_hint, priority)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(name, section, lang) DO UPDATE SET
            source_path = EXCLUDED.source_path,
            sha256 = EXCLUDED.sha256,
            text = EXCLUDED.text,
            updated_at = EXCLUDED.updated_at,
            package_hint = EXCLUDED.package_hint,
            priority = EXCLUDED.priority
        RETURNING id
        """,
        (
            doc.name,
            doc.section,
            doc.lang,
            doc.source_path,
            doc.sha256,
            doc.text,
            doc.updated_at,
            doc.package_hint,
            doc.priority,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("RETURNING id failed - no row returned")
    return int(row["id"])


def upsert_docs_batch(
    conn: psycopg.Connection | None,  # type: ignore[type-arg]
    docs: list[ManDoc],
    batch_size: int = 500,
) -> int:
    """Batch upsert documents for efficient indexing.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        docs: Documents to upsert.
        batch_size: Number of documents per batch.

    Returns:
        Number of documents upserted.
    """
    if conn is not None:
        return _upsert_docs_batch_impl(conn, docs, batch_size)

    with get_conn(schema=PG_SCHEMA) as c:
        return _upsert_docs_batch_impl(c, docs, batch_size)


def _upsert_docs_batch_impl(
    conn: psycopg.Connection,  # type: ignore[type-arg]
    docs: list[ManDoc],
    batch_size: int,
) -> int:
    count = 0

    for i in range(0, len(docs), batch_size):
        batch = docs[i : i + batch_size]

        for doc in batch:
            conn.execute(
                """
                INSERT INTO docs (name, section, lang, source_path, sha256, text, updated_at, package_hint, priority)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(name, section, lang) DO UPDATE SET
                    source_path = EXCLUDED.source_path,
                    sha256 = EXCLUDED.sha256,
                    text = EXCLUDED.text,
                    updated_at = EXCLUDED.updated_at,
                    package_hint = EXCLUDED.package_hint,
                    priority = EXCLUDED.priority
                """,
                (
                    doc.name,
                    doc.section,
                    doc.lang,
                    doc.source_path,
                    doc.sha256,
                    doc.text,
                    doc.updated_at,
                    doc.package_hint,
                    doc.priority,
                ),
            )
            count += 1

    return count


def get_doc(
    conn: psycopg.Connection | None,  # type: ignore[type-arg]
    name: str,
    section: str,
    lang: str = "en",
) -> ManDoc | None:
    """Get a document by name, section, and language.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        name: Document name (e.g., "ls").
        section: Man section (e.g., "1").
        lang: Language code (default "en").

    Returns:
        ManDoc if found, None otherwise.
    """
    if conn is not None:
        return _get_doc_impl(conn, name, section, lang)

    with get_conn(schema=PG_SCHEMA) as c:
        return _get_doc_impl(c, name, section, lang)


def _get_doc_impl(
    conn: psycopg.Connection,  # type: ignore[type-arg]
    name: str,
    section: str,
    lang: str,
) -> ManDoc | None:
    cursor = conn.execute(
        """
        SELECT id, name, section, lang, source_path, sha256, text, updated_at, package_hint, priority
        FROM docs
        WHERE name = %s AND section = %s AND lang = %s
        """,
        (name, section, lang),
    )
    row = cursor.fetchone()
    if row:
        return ManDoc(
            id=row["id"],
            name=row["name"],
            section=row["section"],
            lang=row["lang"],
            source_path=row["source_path"],
            sha256=row["sha256"],
            text=row["text"],
            updated_at=row["updated_at"],
            package_hint=row["package_hint"],
            priority=row["priority"],
        )
    return None


def get_doc_by_id(conn: psycopg.Connection | None, doc_id: int) -> ManDoc | None:  # type: ignore[type-arg]
    """Get a document by ID.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        doc_id: Document ID.

    Returns:
        ManDoc if found, None otherwise.
    """
    if conn is not None:
        return _get_doc_by_id_impl(conn, doc_id)

    with get_conn(schema=PG_SCHEMA) as c:
        return _get_doc_by_id_impl(c, doc_id)


def _get_doc_by_id_impl(conn: psycopg.Connection, doc_id: int) -> ManDoc | None:  # type: ignore[type-arg]
    cursor = conn.execute(
        """
        SELECT id, name, section, lang, source_path, sha256, text, updated_at, package_hint, priority
        FROM docs
        WHERE id = %s
        """,
        (doc_id,),
    )
    row = cursor.fetchone()
    if row:
        return ManDoc(
            id=row["id"],
            name=row["name"],
            section=row["section"],
            lang=row["lang"],
            source_path=row["source_path"],
            sha256=row["sha256"],
            text=row["text"],
            updated_at=row["updated_at"],
            package_hint=row["package_hint"],
            priority=row["priority"],
        )
    return None


def get_all_hashes(conn: psycopg.Connection | None) -> dict[tuple[str, str, str], str]:  # type: ignore[type-arg]
    """Get all document hashes for change detection.

    Returns a mapping of (name, section, lang) -> sha256 hash.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).

    Returns:
        Dictionary mapping document keys to their SHA256 hashes.
    """
    if conn is not None:
        return _get_all_hashes_impl(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _get_all_hashes_impl(c)


def _get_all_hashes_impl(conn: psycopg.Connection) -> dict[tuple[str, str, str], str]:  # type: ignore[type-arg]
    cursor = conn.execute("SELECT name, section, lang, sha256 FROM docs")
    return {(row["name"], row["section"], row["lang"]): row["sha256"] for row in cursor}


def delete_doc(conn: psycopg.Connection | None, doc_id: int) -> bool:  # type: ignore[type-arg]
    """Delete a document and its chunks.

    Chunks and embeddings are deleted via CASCADE.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        doc_id: Document ID to delete.

    Returns:
        True if deleted, False if not found.
    """
    if conn is not None:
        return _delete_doc_impl(conn, doc_id)

    with get_conn(schema=PG_SCHEMA) as c:
        return _delete_doc_impl(c, doc_id)


def _delete_doc_impl(conn: psycopg.Connection, doc_id: int) -> bool:  # type: ignore[type-arg]
    cursor = conn.execute("DELETE FROM docs WHERE id = %s", (doc_id,))
    return cursor.rowcount > 0


def delete_doc_by_key(
    conn: psycopg.Connection | None,  # type: ignore[type-arg]
    name: str,
    section: str,
    lang: str = "en",
) -> bool:
    """Delete a document by name, section, and language.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        name: Document name.
        section: Man section.
        lang: Language code.

    Returns:
        True if deleted, False if not found.
    """
    if conn is not None:
        return _delete_doc_by_key_impl(conn, name, section, lang)

    with get_conn(schema=PG_SCHEMA) as c:
        return _delete_doc_by_key_impl(c, name, section, lang)


def _delete_doc_by_key_impl(
    conn: psycopg.Connection,  # type: ignore[type-arg]
    name: str,
    section: str,
    lang: str,
) -> bool:
    cursor = conn.execute(
        "DELETE FROM docs WHERE name = %s AND section = %s AND lang = %s",
        (name, section, lang),
    )
    return cursor.rowcount > 0


def upsert_chunk(conn: psycopg.Connection | None, chunk: ManChunk) -> int:  # type: ignore[type-arg]
    """Insert or update a chunk.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        chunk: Chunk to upsert.

    Returns:
        Chunk ID.
    """
    if conn is not None:
        return _upsert_chunk_impl(conn, chunk)

    with get_conn(schema=PG_SCHEMA) as c:
        return _upsert_chunk_impl(c, chunk)


def _upsert_chunk_impl(conn: psycopg.Connection, chunk: ManChunk) -> int:  # type: ignore[type-arg]
    cursor = conn.execute(
        """
        INSERT INTO chunks (doc_id, chunk_index, text, heading, start_line, end_line)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT(doc_id, chunk_index) DO UPDATE SET
            text = EXCLUDED.text,
            heading = EXCLUDED.heading,
            start_line = EXCLUDED.start_line,
            end_line = EXCLUDED.end_line
        RETURNING id
        """,
        (
            chunk.doc_id,
            chunk.chunk_index,
            chunk.text,
            chunk.heading,
            chunk.start_line,
            chunk.end_line,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("RETURNING id failed - no row returned")
    return int(row["id"])


def upsert_chunks_batch(conn: psycopg.Connection | None, chunks: list[ManChunk]) -> int:  # type: ignore[type-arg]
    """Batch upsert chunks.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        chunks: Chunks to upsert.

    Returns:
        Number of chunks upserted.
    """
    if not chunks:
        return 0

    if conn is not None:
        return _upsert_chunks_batch_impl(conn, chunks)

    with get_conn(schema=PG_SCHEMA) as c:
        return _upsert_chunks_batch_impl(c, chunks)


def _upsert_chunks_batch_impl(conn: psycopg.Connection, chunks: list[ManChunk]) -> int:  # type: ignore[type-arg]
    for chunk in chunks:
        conn.execute(
            """
            INSERT INTO chunks (doc_id, chunk_index, text, heading, start_line, end_line)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT(doc_id, chunk_index) DO UPDATE SET
                text = EXCLUDED.text,
                heading = EXCLUDED.heading,
                start_line = EXCLUDED.start_line,
                end_line = EXCLUDED.end_line
            """,
            (
                chunk.doc_id,
                chunk.chunk_index,
                chunk.text,
                chunk.heading,
                chunk.start_line,
                chunk.end_line,
            ),
        )

    return len(chunks)


def get_chunks_for_doc(conn: psycopg.Connection | None, doc_id: int) -> list[ManChunk]:  # type: ignore[type-arg]
    """Get all chunks for a document.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        doc_id: Document ID.

    Returns:
        List of chunks ordered by chunk_index.
    """
    if conn is not None:
        return _get_chunks_for_doc_impl(conn, doc_id)

    with get_conn(schema=PG_SCHEMA) as c:
        return _get_chunks_for_doc_impl(c, doc_id)


def _get_chunks_for_doc_impl(conn: psycopg.Connection, doc_id: int) -> list[ManChunk]:  # type: ignore[type-arg]
    cursor = conn.execute(
        """
        SELECT id, doc_id, chunk_index, text, heading, start_line, end_line
        FROM chunks
        WHERE doc_id = %s
        ORDER BY chunk_index
        """,
        (doc_id,),
    )
    return [
        ManChunk(
            id=row["id"],
            doc_id=row["doc_id"],
            chunk_index=row["chunk_index"],
            text=row["text"],
            heading=row["heading"],
            start_line=row["start_line"],
            end_line=row["end_line"],
        )
        for row in cursor
    ]


def delete_chunks_for_doc(conn: psycopg.Connection | None, doc_id: int) -> int:  # type: ignore[type-arg]
    """Delete all chunks for a document.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        doc_id: Document ID.

    Returns:
        Number of chunks deleted.
    """
    if conn is not None:
        return _delete_chunks_for_doc_impl(conn, doc_id)

    with get_conn(schema=PG_SCHEMA) as c:
        return _delete_chunks_for_doc_impl(c, doc_id)


def _delete_chunks_for_doc_impl(conn: psycopg.Connection, doc_id: int) -> int:  # type: ignore[type-arg]
    cursor = conn.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))
    return cursor.rowcount


def upsert_embedding(
    conn: psycopg.Connection | None,  # type: ignore[type-arg]
    chunk_id: int,
    embedding: list[float],
    model: str,
) -> None:
    """Store an embedding for a chunk.

    pgvector handles list[float] natively -- no struct.pack needed.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        chunk_id: Chunk ID.
        embedding: Embedding vector.
        model: Model name that generated the embedding.
    """
    if conn is not None:
        _upsert_embedding_impl(conn, chunk_id, embedding, model)
        return

    with get_conn(schema=PG_SCHEMA) as c:
        _upsert_embedding_impl(c, chunk_id, embedding, model)


def _upsert_embedding_impl(
    conn: psycopg.Connection,  # type: ignore[type-arg]
    chunk_id: int,
    embedding: list[float],
    model: str,
) -> None:
    conn.execute(
        """
        INSERT INTO embeddings (chunk_id, embedding, model, created_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT(chunk_id) DO UPDATE SET
            embedding = EXCLUDED.embedding,
            model = EXCLUDED.model,
            created_at = EXCLUDED.created_at
        """,
        (chunk_id, embedding, model),
    )


def upsert_embeddings_batch(
    conn: psycopg.Connection | None,  # type: ignore[type-arg]
    embeddings: list[tuple[int, list[float]]],
    model: str,
) -> int:
    """Batch store embeddings.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        embeddings: List of (chunk_id, embedding) tuples.
        model: Model name.

    Returns:
        Number of embeddings stored.
    """
    if not embeddings:
        return 0

    if conn is not None:
        return _upsert_embeddings_batch_impl(conn, embeddings, model)

    with get_conn(schema=PG_SCHEMA) as c:
        return _upsert_embeddings_batch_impl(c, embeddings, model)


def _upsert_embeddings_batch_impl(
    conn: psycopg.Connection,  # type: ignore[type-arg]
    embeddings: list[tuple[int, list[float]]],
    model: str,
) -> int:
    for chunk_id, embedding in embeddings:
        conn.execute(
            """
            INSERT INTO embeddings (chunk_id, embedding, model, created_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT(chunk_id) DO UPDATE SET
                embedding = EXCLUDED.embedding,
                model = EXCLUDED.model,
                created_at = EXCLUDED.created_at
            """,
            (chunk_id, embedding, model),
        )

    return len(embeddings)


def get_embedding(conn: psycopg.Connection | None, chunk_id: int) -> list[float] | None:  # type: ignore[type-arg]
    """Get embedding for a chunk.

    pgvector returns list[float] natively -- no struct.unpack needed.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        chunk_id: Chunk ID.

    Returns:
        Embedding vector, or None if not found.
    """
    if conn is not None:
        return _get_embedding_impl(conn, chunk_id)

    with get_conn(schema=PG_SCHEMA) as c:
        return _get_embedding_impl(c, chunk_id)


def _get_embedding_impl(conn: psycopg.Connection, chunk_id: int) -> list[float] | None:  # type: ignore[type-arg]
    cursor = conn.execute(
        "SELECT embedding FROM embeddings WHERE chunk_id = %s",
        (chunk_id,),
    )
    row = cursor.fetchone()
    if row:
        emb = row["embedding"]
        return list(emb) if not isinstance(emb, list) else emb
    return None


def get_embeddings_batch(
    conn: psycopg.Connection | None,  # type: ignore[type-arg]
    chunk_ids: list[int],
) -> dict[int, list[float]]:
    """Get embeddings for multiple chunks.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        chunk_ids: Chunk IDs.

    Returns:
        Dictionary mapping chunk_id to embedding vector.
    """
    if not chunk_ids:
        return {}

    if conn is not None:
        return _get_embeddings_batch_impl(conn, chunk_ids)

    with get_conn(schema=PG_SCHEMA) as c:
        return _get_embeddings_batch_impl(c, chunk_ids)


def _get_embeddings_batch_impl(
    conn: psycopg.Connection,  # type: ignore[type-arg]
    chunk_ids: list[int],
) -> dict[int, list[float]]:
    placeholders = ",".join(["%s"] * len(chunk_ids))
    cursor = conn.execute(
        f"SELECT chunk_id, embedding FROM embeddings WHERE chunk_id IN ({placeholders})",
        chunk_ids,
    )

    result: dict[int, list[float]] = {}
    for row in cursor:
        emb = row["embedding"]
        result[row["chunk_id"]] = list(emb) if not isinstance(emb, list) else emb
    return result


def get_all_embeddings(
    conn: psycopg.Connection | None,  # type: ignore[type-arg]
    model: str | None = None,
) -> list[tuple[int, list[float]]]:
    """Get all embeddings, optionally filtered by model.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        model: Filter by model name (optional).

    Returns:
        List of (chunk_id, embedding) tuples.
    """
    if conn is not None:
        return _get_all_embeddings_impl(conn, model)

    with get_conn(schema=PG_SCHEMA) as c:
        return _get_all_embeddings_impl(c, model)


def _get_all_embeddings_impl(
    conn: psycopg.Connection,  # type: ignore[type-arg]
    model: str | None,
) -> list[tuple[int, list[float]]]:
    if model:
        cursor = conn.execute(
            "SELECT chunk_id, embedding FROM embeddings WHERE model = %s",
            (model,),
        )
    else:
        cursor = conn.execute("SELECT chunk_id, embedding FROM embeddings")

    result: list[tuple[int, list[float]]] = []
    for row in cursor:
        emb = row["embedding"]
        result.append((row["chunk_id"], list(emb) if not isinstance(emb, list) else emb))
    return result


def get_chunks_without_embeddings(conn: psycopg.Connection | None, limit: int = 1000) -> list[ManChunk]:  # type: ignore[type-arg]
    """Get chunks that don't have embeddings yet.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        limit: Maximum number of chunks to return.

    Returns:
        List of chunks without embeddings.
    """
    if conn is not None:
        return _get_chunks_without_embeddings_impl(conn, limit)

    with get_conn(schema=PG_SCHEMA) as c:
        return _get_chunks_without_embeddings_impl(c, limit)


def _get_chunks_without_embeddings_impl(conn: psycopg.Connection, limit: int) -> list[ManChunk]:  # type: ignore[type-arg]
    cursor = conn.execute(
        """
        SELECT c.id, c.doc_id, c.chunk_index, c.text, c.heading, c.start_line, c.end_line
        FROM chunks c
        LEFT JOIN embeddings e ON c.id = e.chunk_id
        WHERE e.chunk_id IS NULL
        LIMIT %s
        """,
        (limit,),
    )
    return [
        ManChunk(
            id=row["id"],
            doc_id=row["doc_id"],
            chunk_index=row["chunk_index"],
            text=row["text"],
            heading=row["heading"],
            start_line=row["start_line"],
            end_line=row["end_line"],
        )
        for row in cursor
    ]


def count_docs(conn: psycopg.Connection | None) -> int:  # type: ignore[type-arg]
    """Count total documents.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).

    Returns:
        Number of documents.
    """
    if conn is not None:
        return _count_docs_impl(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _count_docs_impl(c)


def _count_docs_impl(conn: psycopg.Connection) -> int:  # type: ignore[type-arg]
    cursor = conn.execute("SELECT COUNT(*) AS cnt FROM docs")
    row = cursor.fetchone()
    if row is None:
        return 0
    return int(row["cnt"])


def count_chunks(conn: psycopg.Connection | None) -> int:  # type: ignore[type-arg]
    """Count total chunks.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).

    Returns:
        Number of chunks.
    """
    if conn is not None:
        return _count_chunks_impl(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _count_chunks_impl(c)


def _count_chunks_impl(conn: psycopg.Connection) -> int:  # type: ignore[type-arg]
    cursor = conn.execute("SELECT COUNT(*) AS cnt FROM chunks")
    row = cursor.fetchone()
    if row is None:
        return 0
    return int(row["cnt"])


def count_embeddings(conn: psycopg.Connection | None) -> int:  # type: ignore[type-arg]
    """Count total embeddings.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).

    Returns:
        Number of embeddings.
    """
    if conn is not None:
        return _count_embeddings_impl(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _count_embeddings_impl(c)


def _count_embeddings_impl(conn: psycopg.Connection) -> int:  # type: ignore[type-arg]
    cursor = conn.execute("SELECT COUNT(*) AS cnt FROM embeddings")
    row = cursor.fetchone()
    if row is None:
        return 0
    return int(row["cnt"])


def get_section_counts(conn: psycopg.Connection | None) -> dict[str, int]:  # type: ignore[type-arg]
    """Get document counts by section.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).

    Returns:
        Dictionary mapping section to document count.
    """
    if conn is not None:
        return _get_section_counts_impl(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _get_section_counts_impl(c)


def _get_section_counts_impl(conn: psycopg.Connection) -> dict[str, int]:  # type: ignore[type-arg]
    cursor = conn.execute("SELECT section, COUNT(*) AS count FROM docs GROUP BY section")
    return {row["section"]: row["count"] for row in cursor}


def set_meta(conn: psycopg.Connection | None, key: str, value: str) -> None:  # type: ignore[type-arg]
    """Set a metadata value.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        key: Metadata key.
        value: Metadata value.
    """
    if conn is not None:
        _set_meta_impl(conn, key, value)
        return

    with get_conn(schema=PG_SCHEMA) as c:
        _set_meta_impl(c, key, value)


def _set_meta_impl(conn: psycopg.Connection, key: str, value: str) -> None:  # type: ignore[type-arg]
    conn.execute(
        """
        INSERT INTO meta (key, value) VALUES (%s, %s)
        ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value
        """,
        (key, value),
    )


def get_meta(conn: psycopg.Connection | None, key: str) -> str | None:  # type: ignore[type-arg]
    """Get a metadata value.

    Args:
        conn: psycopg connection (optional, will obtain from pool if None).
        key: Metadata key.

    Returns:
        Value if found, None otherwise.
    """
    if conn is not None:
        return _get_meta_impl(conn, key)

    with get_conn(schema=PG_SCHEMA) as c:
        return _get_meta_impl(c, key)


def _get_meta_impl(conn: psycopg.Connection, key: str) -> str | None:  # type: ignore[type-arg]
    cursor = conn.execute("SELECT value FROM meta WHERE key = %s", (key,))
    row = cursor.fetchone()
    return row["value"] if row else None
