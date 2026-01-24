"""Man Vault store - CRUD operations.

Provides database operations for the Man Vault:
- Document CRUD (upsert, get, delete)
- Chunk management
- Embedding storage
- Batch operations for efficient indexing
"""

import sqlite3
import struct
from datetime import datetime

from elle.daemon.manvault.models import ManChunk, ManDoc


def upsert_doc(conn: sqlite3.Connection, doc: ManDoc) -> int:
    """Insert or update a document.

    Uses UPSERT to handle both insert and update cases.

    Args:
        conn: SQLite connection.
        doc: Document to upsert.

    Returns:
        Document ID.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO docs (name, section, lang, source_path, sha256, text, updated_at, package_hint, priority)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name, section, lang) DO UPDATE SET
            source_path = excluded.source_path,
            sha256 = excluded.sha256,
            text = excluded.text,
            updated_at = excluded.updated_at,
            package_hint = excluded.package_hint,
            priority = excluded.priority
        RETURNING id
        """,
        (
            doc.name,
            doc.section,
            doc.lang,
            doc.source_path,
            doc.sha256,
            doc.text,
            doc.updated_at.isoformat(),
            doc.package_hint,
            doc.priority,
        ),
    )
    row = cursor.fetchone()
    conn.commit()
    return row[0]


def upsert_docs_batch(
    conn: sqlite3.Connection,
    docs: list[ManDoc],
    batch_size: int = 500,
) -> int:
    """Batch upsert documents for efficient indexing.

    Args:
        conn: SQLite connection.
        docs: Documents to upsert.
        batch_size: Number of documents per batch.

    Returns:
        Number of documents upserted.
    """
    count = 0
    cursor = conn.cursor()

    for i in range(0, len(docs), batch_size):
        batch = docs[i : i + batch_size]

        for doc in batch:
            cursor.execute(
                """
                INSERT INTO docs (name, section, lang, source_path, sha256, text, updated_at, package_hint, priority)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name, section, lang) DO UPDATE SET
                    source_path = excluded.source_path,
                    sha256 = excluded.sha256,
                    text = excluded.text,
                    updated_at = excluded.updated_at,
                    package_hint = excluded.package_hint,
                    priority = excluded.priority
                """,
                (
                    doc.name,
                    doc.section,
                    doc.lang,
                    doc.source_path,
                    doc.sha256,
                    doc.text,
                    doc.updated_at.isoformat(),
                    doc.package_hint,
                    doc.priority,
                ),
            )
            count += 1

        conn.commit()

    return count


def get_doc(
    conn: sqlite3.Connection,
    name: str,
    section: str,
    lang: str = "en",
) -> ManDoc | None:
    """Get a document by name, section, and language.

    Args:
        conn: SQLite connection.
        name: Document name (e.g., "ls").
        section: Man section (e.g., "1").
        lang: Language code (default "en").

    Returns:
        ManDoc if found, None otherwise.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, name, section, lang, source_path, sha256, text, updated_at, package_hint, priority
        FROM docs
        WHERE name = ? AND section = ? AND lang = ?
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
            updated_at=datetime.fromisoformat(row["updated_at"]),
            package_hint=row["package_hint"],
            priority=row["priority"],
        )
    return None


def get_doc_by_id(conn: sqlite3.Connection, doc_id: int) -> ManDoc | None:
    """Get a document by ID.

    Args:
        conn: SQLite connection.
        doc_id: Document ID.

    Returns:
        ManDoc if found, None otherwise.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, name, section, lang, source_path, sha256, text, updated_at, package_hint, priority
        FROM docs
        WHERE id = ?
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
            updated_at=datetime.fromisoformat(row["updated_at"]),
            package_hint=row["package_hint"],
            priority=row["priority"],
        )
    return None


def get_all_hashes(conn: sqlite3.Connection) -> dict[tuple[str, str, str], str]:
    """Get all document hashes for change detection.

    Returns a mapping of (name, section, lang) -> sha256 hash.

    Args:
        conn: SQLite connection.

    Returns:
        Dictionary mapping document keys to their SHA256 hashes.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT name, section, lang, sha256 FROM docs")
    return {(row["name"], row["section"], row["lang"]): row["sha256"] for row in cursor}


def delete_doc(conn: sqlite3.Connection, doc_id: int) -> bool:
    """Delete a document and its chunks.

    Chunks and embeddings are deleted via CASCADE.

    Args:
        conn: SQLite connection.
        doc_id: Document ID to delete.

    Returns:
        True if deleted, False if not found.
    """
    cursor = conn.cursor()
    cursor.execute("DELETE FROM docs WHERE id = ?", (doc_id,))
    conn.commit()
    return cursor.rowcount > 0


def delete_doc_by_key(
    conn: sqlite3.Connection,
    name: str,
    section: str,
    lang: str = "en",
) -> bool:
    """Delete a document by name, section, and language.

    Args:
        conn: SQLite connection.
        name: Document name.
        section: Man section.
        lang: Language code.

    Returns:
        True if deleted, False if not found.
    """
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM docs WHERE name = ? AND section = ? AND lang = ?",
        (name, section, lang),
    )
    conn.commit()
    return cursor.rowcount > 0


def upsert_chunk(conn: sqlite3.Connection, chunk: ManChunk) -> int:
    """Insert or update a chunk.

    Args:
        conn: SQLite connection.
        chunk: Chunk to upsert.

    Returns:
        Chunk ID.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO chunks (doc_id, chunk_index, text, heading, start_line, end_line)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(doc_id, chunk_index) DO UPDATE SET
            text = excluded.text,
            heading = excluded.heading,
            start_line = excluded.start_line,
            end_line = excluded.end_line
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
    conn.commit()
    return row[0]


def upsert_chunks_batch(conn: sqlite3.Connection, chunks: list[ManChunk]) -> int:
    """Batch upsert chunks.

    Args:
        conn: SQLite connection.
        chunks: Chunks to upsert.

    Returns:
        Number of chunks upserted.
    """
    if not chunks:
        return 0

    cursor = conn.cursor()

    for chunk in chunks:
        cursor.execute(
            """
            INSERT INTO chunks (doc_id, chunk_index, text, heading, start_line, end_line)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id, chunk_index) DO UPDATE SET
                text = excluded.text,
                heading = excluded.heading,
                start_line = excluded.start_line,
                end_line = excluded.end_line
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

    conn.commit()
    return len(chunks)


def get_chunks_for_doc(conn: sqlite3.Connection, doc_id: int) -> list[ManChunk]:
    """Get all chunks for a document.

    Args:
        conn: SQLite connection.
        doc_id: Document ID.

    Returns:
        List of chunks ordered by chunk_index.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, doc_id, chunk_index, text, heading, start_line, end_line
        FROM chunks
        WHERE doc_id = ?
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


def delete_chunks_for_doc(conn: sqlite3.Connection, doc_id: int) -> int:
    """Delete all chunks for a document.

    Args:
        conn: SQLite connection.
        doc_id: Document ID.

    Returns:
        Number of chunks deleted.
    """
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    conn.commit()
    return cursor.rowcount


def upsert_embedding(
    conn: sqlite3.Connection,
    chunk_id: int,
    embedding: list[float],
    model: str,
) -> None:
    """Store an embedding for a chunk.

    Embeddings are stored as BLOBs (packed float32 arrays).

    Args:
        conn: SQLite connection.
        chunk_id: Chunk ID.
        embedding: Embedding vector.
        model: Model name that generated the embedding.
    """
    cursor = conn.cursor()

    # Pack embedding as float32 array
    embedding_blob = struct.pack(f"{len(embedding)}f", *embedding)
    dim = len(embedding)

    cursor.execute(
        """
        INSERT INTO embeddings (chunk_id, embedding, model, dim, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(chunk_id) DO UPDATE SET
            embedding = excluded.embedding,
            model = excluded.model,
            dim = excluded.dim,
            created_at = excluded.created_at
        """,
        (chunk_id, embedding_blob, model, dim, datetime.now().isoformat()),
    )
    conn.commit()


def upsert_embeddings_batch(
    conn: sqlite3.Connection,
    embeddings: list[tuple[int, list[float]]],
    model: str,
) -> int:
    """Batch store embeddings.

    Args:
        conn: SQLite connection.
        embeddings: List of (chunk_id, embedding) tuples.
        model: Model name.

    Returns:
        Number of embeddings stored.
    """
    if not embeddings:
        return 0

    cursor = conn.cursor()
    now = datetime.now().isoformat()

    for chunk_id, embedding in embeddings:
        embedding_blob = struct.pack(f"{len(embedding)}f", *embedding)
        dim = len(embedding)

        cursor.execute(
            """
            INSERT INTO embeddings (chunk_id, embedding, model, dim, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                embedding = excluded.embedding,
                model = excluded.model,
                dim = excluded.dim,
                created_at = excluded.created_at
            """,
            (chunk_id, embedding_blob, model, dim, now),
        )

    conn.commit()
    return len(embeddings)


def get_embedding(conn: sqlite3.Connection, chunk_id: int) -> list[float] | None:
    """Get embedding for a chunk.

    Args:
        conn: SQLite connection.
        chunk_id: Chunk ID.

    Returns:
        Embedding vector, or None if not found.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT embedding, dim FROM embeddings WHERE chunk_id = ?",
        (chunk_id,),
    )
    row = cursor.fetchone()
    if row:
        return list(struct.unpack(f"{row['dim']}f", row["embedding"]))
    return None


def get_embeddings_batch(
    conn: sqlite3.Connection,
    chunk_ids: list[int],
) -> dict[int, list[float]]:
    """Get embeddings for multiple chunks.

    Args:
        conn: SQLite connection.
        chunk_ids: Chunk IDs.

    Returns:
        Dictionary mapping chunk_id to embedding vector.
    """
    if not chunk_ids:
        return {}

    cursor = conn.cursor()
    placeholders = ",".join("?" * len(chunk_ids))
    cursor.execute(
        f"SELECT chunk_id, embedding, dim FROM embeddings WHERE chunk_id IN ({placeholders})",
        chunk_ids,
    )

    result = {}
    for row in cursor:
        result[row["chunk_id"]] = list(struct.unpack(f"{row['dim']}f", row["embedding"]))
    return result


def get_all_embeddings(
    conn: sqlite3.Connection,
    model: str | None = None,
) -> list[tuple[int, list[float]]]:
    """Get all embeddings, optionally filtered by model.

    Args:
        conn: SQLite connection.
        model: Filter by model name (optional).

    Returns:
        List of (chunk_id, embedding) tuples.
    """
    cursor = conn.cursor()

    if model:
        cursor.execute(
            "SELECT chunk_id, embedding, dim FROM embeddings WHERE model = ?",
            (model,),
        )
    else:
        cursor.execute("SELECT chunk_id, embedding, dim FROM embeddings")

    result = []
    for row in cursor:
        embedding = list(struct.unpack(f"{row['dim']}f", row["embedding"]))
        result.append((row["chunk_id"], embedding))
    return result


def get_chunks_without_embeddings(conn: sqlite3.Connection, limit: int = 1000) -> list[ManChunk]:
    """Get chunks that don't have embeddings yet.

    Args:
        conn: SQLite connection.
        limit: Maximum number of chunks to return.

    Returns:
        List of chunks without embeddings.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT c.id, c.doc_id, c.chunk_index, c.text, c.heading, c.start_line, c.end_line
        FROM chunks c
        LEFT JOIN embeddings e ON c.id = e.chunk_id
        WHERE e.chunk_id IS NULL
        LIMIT ?
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


def count_docs(conn: sqlite3.Connection) -> int:
    """Count total documents.

    Args:
        conn: SQLite connection.

    Returns:
        Number of documents.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM docs")
    return cursor.fetchone()[0]


def count_chunks(conn: sqlite3.Connection) -> int:
    """Count total chunks.

    Args:
        conn: SQLite connection.

    Returns:
        Number of chunks.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM chunks")
    return cursor.fetchone()[0]


def count_embeddings(conn: sqlite3.Connection) -> int:
    """Count total embeddings.

    Args:
        conn: SQLite connection.

    Returns:
        Number of embeddings.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM embeddings")
    return cursor.fetchone()[0]


def get_section_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Get document counts by section.

    Args:
        conn: SQLite connection.

    Returns:
        Dictionary mapping section to document count.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT section, COUNT(*) as count FROM docs GROUP BY section")
    return {row["section"]: row["count"] for row in cursor}


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Set a metadata value.

    Args:
        conn: SQLite connection.
        key: Metadata key.
        value: Metadata value.
    """
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Get a metadata value.

    Args:
        conn: SQLite connection.
        key: Metadata key.

    Returns:
        Value if found, None otherwise.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM meta WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row["value"] if row else None
