"""Tests for Man Vault schema and CRUD operations (PostgreSQL)."""

from __future__ import annotations

from datetime import datetime

import pytest

from elle.daemon.manvault.models import ManChunk, ManDoc
from elle.daemon.manvault.schema import (
    SCHEMA_VERSION,
    ensure_schema,
)
from elle.daemon.manvault.store import (
    count_chunks,
    count_docs,
    count_embeddings,
    delete_doc,
    get_all_hashes,
    get_chunks_for_doc,
    get_doc,
    get_embedding,
    get_meta,
    get_section_counts,
    set_meta,
    upsert_chunk,
    upsert_chunks_batch,
    upsert_doc,
    upsert_embedding,
)
from elle.storage.migrate import get_schema_version


@pytest.fixture
def conn(manvault_conn):
    """Use the manvault_conn fixture from conftest."""
    return manvault_conn


class TestSchema:
    """Tests for schema creation and management."""

    def test_init_schema_creates_tables(self, conn):
        """Test that the manvault schema has all required tables."""
        # Check docs table
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'manvault' AND table_name = %s",
            ("docs",),
        ).fetchone()
        assert row is not None

        # Check chunks table
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'manvault' AND table_name = %s",
            ("chunks",),
        ).fetchone()
        assert row is not None

        # Check embeddings table
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'manvault' AND table_name = %s",
            ("embeddings",),
        ).fetchone()
        assert row is not None

        # Check meta table
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'manvault' AND table_name = %s",
            ("meta",),
        ).fetchone()
        assert row is not None

    def test_init_schema_creates_tsvector_column(self, conn):
        """Test that the docs table has a doc_tsv tsvector column."""
        row = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'manvault' AND table_name = 'docs' "
            "AND column_name = 'doc_tsv'",
        ).fetchone()
        assert row is not None
        assert row["data_type"] == "tsvector"

    def test_init_schema_creates_indexes(self, conn):
        """Test that indexes are created."""
        rows = conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'manvault'").fetchall()
        indexes = {row["indexname"] for row in rows}

        assert "idx_docs_name" in indexes
        assert "idx_docs_name_section" in indexes
        assert "idx_chunks_doc_id" in indexes
        assert "idx_docs_tsv" in indexes

    def test_schema_version_is_set(self, conn):
        """Test that schema version is recorded."""
        version = get_schema_version(conn, "manvault")
        assert version == SCHEMA_VERSION

    def test_ensure_schema_idempotent(self, conn):
        """Test that ensure_schema can be called multiple times."""
        # Should not raise
        ensure_schema(conn)
        ensure_schema(conn)

        # Schema should still be intact
        assert count_docs(conn) >= 0


class TestDocumentCRUD:
    """Tests for document CRUD operations."""

    def test_upsert_doc_insert(self, conn):
        """Test inserting a new document."""
        doc = ManDoc(
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="abc123",
            text="ls - list directory contents",
            updated_at=datetime.now(),
        )

        doc_id = upsert_doc(conn, doc)
        assert doc_id > 0

        # Verify document was inserted
        retrieved = get_doc(conn, "ls", "1", "en")
        assert retrieved is not None
        assert retrieved.name == "ls"
        assert retrieved.section == "1"
        assert retrieved.sha256 == "abc123"

    def test_upsert_doc_update(self, conn):
        """Test updating an existing document."""
        # Insert initial document
        doc1 = ManDoc(
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="abc123",
            text="Original text",
            updated_at=datetime.now(),
        )
        upsert_doc(conn, doc1)

        # Update document
        doc2 = ManDoc(
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="xyz789",
            text="Updated text",
            updated_at=datetime.now(),
        )
        upsert_doc(conn, doc2)

        # Verify update
        retrieved = get_doc(conn, "ls", "1", "en")
        assert retrieved.sha256 == "xyz789"
        assert retrieved.text == "Updated text"

        # Should only be one document
        assert count_docs(conn) == 1

    def test_get_doc_not_found(self, conn):
        """Test getting a non-existent document."""
        result = get_doc(conn, "nonexistent", "1", "en")
        assert result is None

    def test_delete_doc(self, conn):
        """Test deleting a document."""
        doc = ManDoc(
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="abc123",
            text="ls - list directory contents",
            updated_at=datetime.now(),
        )
        doc_id = upsert_doc(conn, doc)

        # Delete
        assert delete_doc(conn, doc_id)

        # Verify deletion
        assert get_doc(conn, "ls", "1", "en") is None

    def test_get_all_hashes(self, conn):
        """Test getting all document hashes."""
        # Insert multiple documents
        for name, section in [("ls", "1"), ("grep", "1"), ("fstab", "5")]:
            doc = ManDoc(
                name=name,
                section=section,
                lang="en",
                source_path=f"/usr/share/man/man{section}/{name}.{section}.gz",
                sha256=f"hash_{name}",
                text=f"{name} documentation",
                updated_at=datetime.now(),
            )
            upsert_doc(conn, doc)

        hashes = get_all_hashes(conn)

        assert len(hashes) == 3
        assert hashes[("ls", "1", "en")] == "hash_ls"
        assert hashes[("grep", "1", "en")] == "hash_grep"

    def test_get_section_counts(self, conn):
        """Test getting section counts."""
        # Insert documents in different sections
        for name, section in [("ls", "1"), ("grep", "1"), ("fstab", "5")]:
            doc = ManDoc(
                name=name,
                section=section,
                lang="en",
                source_path=f"/usr/share/man/man{section}/{name}.{section}.gz",
                sha256=f"hash_{name}",
                text=f"{name} documentation",
                updated_at=datetime.now(),
            )
            upsert_doc(conn, doc)

        counts = get_section_counts(conn)

        assert counts["1"] == 2
        assert counts["5"] == 1


class TestChunkCRUD:
    """Tests for chunk CRUD operations."""

    def test_upsert_chunk(self, conn):
        """Test inserting a chunk."""
        # First insert a document
        doc = ManDoc(
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="abc123",
            text="Full text",
            updated_at=datetime.now(),
        )
        doc_id = upsert_doc(conn, doc)

        # Insert chunk
        chunk = ManChunk(
            doc_id=doc_id,
            chunk_index=0,
            text="Chunk text",
            heading="DESCRIPTION",
        )
        chunk_id = upsert_chunk(conn, chunk)

        assert chunk_id > 0

    def test_get_chunks_for_doc(self, conn):
        """Test getting chunks for a document."""
        # Insert document
        doc = ManDoc(
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="abc123",
            text="Full text",
            updated_at=datetime.now(),
        )
        doc_id = upsert_doc(conn, doc)

        # Insert chunks
        chunks = [
            ManChunk(doc_id=doc_id, chunk_index=0, text="Chunk 0", heading="NAME"),
            ManChunk(doc_id=doc_id, chunk_index=1, text="Chunk 1", heading="DESCRIPTION"),
            ManChunk(doc_id=doc_id, chunk_index=2, text="Chunk 2", heading="OPTIONS"),
        ]
        upsert_chunks_batch(conn, chunks)

        # Retrieve chunks
        retrieved = get_chunks_for_doc(conn, doc_id)

        assert len(retrieved) == 3
        assert retrieved[0].chunk_index == 0
        assert retrieved[0].heading == "NAME"

    def test_chunks_cascade_delete(self, conn):
        """Test that chunks are deleted when document is deleted."""
        # Insert document and chunks
        doc = ManDoc(
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="abc123",
            text="Full text",
            updated_at=datetime.now(),
        )
        doc_id = upsert_doc(conn, doc)

        chunk = ManChunk(doc_id=doc_id, chunk_index=0, text="Chunk text")
        upsert_chunk(conn, chunk)

        # Delete document
        delete_doc(conn, doc_id)

        # Chunks should be gone
        assert count_chunks(conn) == 0


class TestEmbeddingCRUD:
    """Tests for embedding CRUD operations."""

    def test_upsert_embedding(self, conn):
        """Test storing an embedding."""
        # Insert document and chunk
        doc = ManDoc(
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="abc123",
            text="Full text",
            updated_at=datetime.now(),
        )
        doc_id = upsert_doc(conn, doc)

        chunk = ManChunk(doc_id=doc_id, chunk_index=0, text="Chunk text")
        chunk_id = upsert_chunk(conn, chunk)

        # Store embedding (must be 768 dimensions to match vector(768))
        embedding = [0.1] * 768
        embedding[0] = 0.1
        embedding[1] = 0.2
        embedding[2] = 0.3
        embedding[3] = 0.4
        embedding[4] = 0.5
        upsert_embedding(conn, chunk_id, embedding, "test-model")

        # Retrieve embedding
        retrieved = get_embedding(conn, chunk_id)

        assert retrieved is not None
        assert len(retrieved) == 768
        assert abs(retrieved[0] - 0.1) < 0.001

    def test_embeddings_cascade_delete(self, conn):
        """Test that embeddings are deleted when chunk is deleted."""
        # Insert document, chunk, and embedding
        doc = ManDoc(
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="abc123",
            text="Full text",
            updated_at=datetime.now(),
        )
        doc_id = upsert_doc(conn, doc)

        chunk = ManChunk(doc_id=doc_id, chunk_index=0, text="Chunk text")
        chunk_id = upsert_chunk(conn, chunk)

        # Must be 768 dimensions to match vector(768)
        embedding = [0.1] * 768
        upsert_embedding(conn, chunk_id, embedding, "test-model")

        # Delete document (cascades to chunk and embedding)
        delete_doc(conn, doc_id)

        # Embedding should be gone
        assert count_embeddings(conn) == 0


class TestFullTextSearch:
    """Tests for PostgreSQL tsvector full-text search."""

    def test_tsvector_insert(self, conn):
        """Test that tsvector column is populated on insert."""
        doc = ManDoc(
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="abc123",
            text="list directory contents",
            updated_at=datetime.now(),
        )
        upsert_doc(conn, doc)

        # Search via tsvector
        row = conn.execute(
            "SELECT * FROM docs WHERE doc_tsv @@ plainto_tsquery('english', %s)",
            ("directory",),
        ).fetchone()

        assert row is not None

    def test_tsvector_delete(self, conn):
        """Test that deleted docs no longer appear in tsvector search."""
        doc = ManDoc(
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="abc123",
            text="list directory contents",
            updated_at=datetime.now(),
        )
        doc_id = upsert_doc(conn, doc)

        # Delete document
        delete_doc(conn, doc_id)

        # tsvector search should return nothing
        row = conn.execute(
            "SELECT * FROM docs WHERE doc_tsv @@ plainto_tsquery('english', %s)",
            ("directory",),
        ).fetchone()

        assert row is None


class TestMeta:
    """Tests for metadata storage."""

    def test_set_and_get_meta(self, conn):
        """Test setting and getting metadata."""
        set_meta(conn, "test_key", "test_value")

        value = get_meta(conn, "test_key")
        assert value == "test_value"

    def test_get_meta_not_found(self, conn):
        """Test getting non-existent metadata."""
        value = get_meta(conn, "nonexistent")
        assert value is None
