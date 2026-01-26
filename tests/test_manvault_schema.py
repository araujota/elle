"""Tests for Man Vault schema and CRUD operations."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from elle.daemon.manvault.models import ManChunk, ManDoc
from elle.daemon.manvault.schema import (
    SCHEMA_VERSION,
    ensure_schema,
    get_connection,
    get_schema_version,
    init_manvault_schema,
    needs_migration,
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


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    init_manvault_schema(conn)
    yield conn

    conn.close()
    db_path.unlink(missing_ok=True)


class TestSchema:
    """Tests for schema creation and management."""

    def test_init_schema_creates_tables(self, temp_db):
        """Test that init_manvault_schema creates all required tables."""
        cursor = temp_db.cursor()

        # Check docs table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='docs'")
        assert cursor.fetchone() is not None

        # Check FTS table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='docs_fts'")
        assert cursor.fetchone() is not None

        # Check chunks table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chunks'")
        assert cursor.fetchone() is not None

        # Check embeddings table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='embeddings'")
        assert cursor.fetchone() is not None

        # Check meta table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'")
        assert cursor.fetchone() is not None

    def test_init_schema_creates_triggers(self, temp_db):
        """Test that triggers are created for FTS sync."""
        cursor = temp_db.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        triggers = {row[0] for row in cursor.fetchall()}

        assert "docs_ai" in triggers  # After insert
        assert "docs_ad" in triggers  # After delete
        assert "docs_au" in triggers  # After update

    def test_init_schema_creates_indexes(self, temp_db):
        """Test that indexes are created."""
        cursor = temp_db.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor.fetchall()}

        assert "idx_docs_name" in indexes
        assert "idx_docs_name_section" in indexes
        assert "idx_chunks_doc_id" in indexes

    def test_schema_version_is_set(self, temp_db):
        """Test that schema version is recorded."""
        version = get_schema_version(temp_db)
        assert version == SCHEMA_VERSION

    def test_needs_migration_false_when_current(self, temp_db):
        """Test that needs_migration returns False for current schema."""
        assert not needs_migration(temp_db)

    def test_ensure_schema_idempotent(self, temp_db):
        """Test that ensure_schema can be called multiple times."""
        # Should not raise
        ensure_schema(temp_db)
        ensure_schema(temp_db)

        # Schema should still be intact
        assert count_docs(temp_db) >= 0


class TestDocumentCRUD:
    """Tests for document CRUD operations."""

    def test_upsert_doc_insert(self, temp_db):
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

        doc_id = upsert_doc(temp_db, doc)
        assert doc_id > 0

        # Verify document was inserted
        retrieved = get_doc(temp_db, "ls", "1", "en")
        assert retrieved is not None
        assert retrieved.name == "ls"
        assert retrieved.section == "1"
        assert retrieved.sha256 == "abc123"

    def test_upsert_doc_update(self, temp_db):
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
        upsert_doc(temp_db, doc1)

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
        upsert_doc(temp_db, doc2)

        # Verify update
        retrieved = get_doc(temp_db, "ls", "1", "en")
        assert retrieved.sha256 == "xyz789"
        assert retrieved.text == "Updated text"

        # Should only be one document
        assert count_docs(temp_db) == 1

    def test_get_doc_not_found(self, temp_db):
        """Test getting a non-existent document."""
        result = get_doc(temp_db, "nonexistent", "1", "en")
        assert result is None

    def test_delete_doc(self, temp_db):
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
        doc_id = upsert_doc(temp_db, doc)

        # Delete
        assert delete_doc(temp_db, doc_id)

        # Verify deletion
        assert get_doc(temp_db, "ls", "1", "en") is None

    def test_get_all_hashes(self, temp_db):
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
            upsert_doc(temp_db, doc)

        hashes = get_all_hashes(temp_db)

        assert len(hashes) == 3
        assert hashes[("ls", "1", "en")] == "hash_ls"
        assert hashes[("grep", "1", "en")] == "hash_grep"

    def test_get_section_counts(self, temp_db):
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
            upsert_doc(temp_db, doc)

        counts = get_section_counts(temp_db)

        assert counts["1"] == 2
        assert counts["5"] == 1


class TestChunkCRUD:
    """Tests for chunk CRUD operations."""

    def test_upsert_chunk(self, temp_db):
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
        doc_id = upsert_doc(temp_db, doc)

        # Insert chunk
        chunk = ManChunk(
            doc_id=doc_id,
            chunk_index=0,
            text="Chunk text",
            heading="DESCRIPTION",
        )
        chunk_id = upsert_chunk(temp_db, chunk)

        assert chunk_id > 0

    def test_get_chunks_for_doc(self, temp_db):
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
        doc_id = upsert_doc(temp_db, doc)

        # Insert chunks
        chunks = [
            ManChunk(doc_id=doc_id, chunk_index=0, text="Chunk 0", heading="NAME"),
            ManChunk(doc_id=doc_id, chunk_index=1, text="Chunk 1", heading="DESCRIPTION"),
            ManChunk(doc_id=doc_id, chunk_index=2, text="Chunk 2", heading="OPTIONS"),
        ]
        upsert_chunks_batch(temp_db, chunks)

        # Retrieve chunks
        retrieved = get_chunks_for_doc(temp_db, doc_id)

        assert len(retrieved) == 3
        assert retrieved[0].chunk_index == 0
        assert retrieved[0].heading == "NAME"

    def test_chunks_cascade_delete(self, temp_db):
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
        doc_id = upsert_doc(temp_db, doc)

        chunk = ManChunk(doc_id=doc_id, chunk_index=0, text="Chunk text")
        upsert_chunk(temp_db, chunk)

        # Delete document
        delete_doc(temp_db, doc_id)

        # Chunks should be gone
        assert count_chunks(temp_db) == 0


class TestEmbeddingCRUD:
    """Tests for embedding CRUD operations."""

    def test_upsert_embedding(self, temp_db):
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
        doc_id = upsert_doc(temp_db, doc)

        chunk = ManChunk(doc_id=doc_id, chunk_index=0, text="Chunk text")
        chunk_id = upsert_chunk(temp_db, chunk)

        # Store embedding
        embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        upsert_embedding(temp_db, chunk_id, embedding, "test-model")

        # Retrieve embedding
        retrieved = get_embedding(temp_db, chunk_id)

        assert retrieved is not None
        assert len(retrieved) == 5
        assert abs(retrieved[0] - 0.1) < 0.001

    def test_embeddings_cascade_delete(self, temp_db):
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
        doc_id = upsert_doc(temp_db, doc)

        chunk = ManChunk(doc_id=doc_id, chunk_index=0, text="Chunk text")
        chunk_id = upsert_chunk(temp_db, chunk)

        embedding = [0.1, 0.2, 0.3]
        upsert_embedding(temp_db, chunk_id, embedding, "test-model")

        # Delete document (cascades to chunk and embedding)
        delete_doc(temp_db, doc_id)

        # Embedding should be gone
        assert count_embeddings(temp_db) == 0


class TestFTS:
    """Tests for FTS5 synchronization."""

    def test_fts_insert_trigger(self, temp_db):
        """Test that FTS index is updated on insert."""
        doc = ManDoc(
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="abc123",
            text="list directory contents",
            updated_at=datetime.now(),
        )
        upsert_doc(temp_db, doc)

        # Search FTS
        cursor = temp_db.cursor()
        cursor.execute("SELECT * FROM docs_fts WHERE docs_fts MATCH 'directory'")
        results = cursor.fetchall()

        assert len(results) == 1

    def test_fts_delete_trigger(self, temp_db):
        """Test that FTS index is updated on delete."""
        doc = ManDoc(
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="abc123",
            text="list directory contents",
            updated_at=datetime.now(),
        )
        doc_id = upsert_doc(temp_db, doc)

        # Delete document
        delete_doc(temp_db, doc_id)

        # FTS should be empty
        cursor = temp_db.cursor()
        cursor.execute("SELECT * FROM docs_fts WHERE docs_fts MATCH 'directory'")
        results = cursor.fetchall()

        assert len(results) == 0


class TestMeta:
    """Tests for metadata storage."""

    def test_set_and_get_meta(self, temp_db):
        """Test setting and getting metadata."""
        set_meta(temp_db, "test_key", "test_value")

        value = get_meta(temp_db, "test_key")
        assert value == "test_value"

    def test_get_meta_not_found(self, temp_db):
        """Test getting non-existent metadata."""
        value = get_meta(temp_db, "nonexistent")
        assert value is None
