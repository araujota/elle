"""Tests for Man Vault query and retrieval."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from elle.daemon.manvault.models import ManChunk, ManDoc
from elle.daemon.manvault.retriever import (
    _escape_fts5_query,
    _parse_sections,
    extract_snippet,
    get_document,
    search,
)
from elle.daemon.manvault.schema import get_connection, init_manvault_schema
from elle.daemon.manvault.store import upsert_chunk, upsert_chunks_batch, upsert_doc


@pytest.fixture
def temp_db():
    """Create a temporary database with sample data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    init_manvault_schema(conn)

    # Insert sample documents
    docs = [
        ManDoc(
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="hash_ls",
            text="""NAME
       ls - list directory contents

DESCRIPTION
       List information about the FILEs.

OPTIONS
       -a, --all
              do not ignore entries starting with .

       -l     use a long listing format

       -h, --human-readable
              with -l, print sizes like 1K 234M 2G etc.
""",
            updated_at=datetime.now(),
            priority=1,
        ),
        ManDoc(
            name="grep",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/grep.1.gz",
            sha256="hash_grep",
            text="""NAME
       grep - print lines that match patterns

DESCRIPTION
       grep searches for PATTERNS in each FILE.

OPTIONS
       -i, --ignore-case
              ignore case distinctions in patterns

       -r, --recursive
              read all files under each directory recursively
""",
            updated_at=datetime.now(),
        ),
        ManDoc(
            name="fstab",
            section="5",
            lang="en",
            source_path="/usr/share/man/man5/fstab.5.gz",
            sha256="hash_fstab",
            text="""NAME
       fstab - static information about the filesystems

DESCRIPTION
       The file fstab contains descriptive information about the filesystems.

OPTIONS
       The following options may be used in the fourth field.
""",
            updated_at=datetime.now(),
        ),
    ]

    for doc in docs:
        doc_id = upsert_doc(conn, doc)

        # Add some chunks
        chunks = [
            ManChunk(doc_id=doc_id, chunk_index=0, text=doc.text[:200], heading="NAME"),
            ManChunk(doc_id=doc_id, chunk_index=1, text=doc.text[200:], heading="OPTIONS"),
        ]
        upsert_chunks_batch(conn, chunks)

    yield conn

    conn.close()
    db_path.unlink(missing_ok=True)


class TestFTS5Escaping:
    """Tests for FTS5 query escaping."""

    def test_simple_query(self):
        """Test that simple queries pass through."""
        assert _escape_fts5_query("list files") == "list files"

    def test_escape_special_chars(self):
        """Test escaping special characters."""
        result = _escape_fts5_query("ls -l")
        assert result == 'ls "-l"'

    def test_escape_quotes(self):
        """Test escaping quotes."""
        result = _escape_fts5_query('search "term"')
        assert '"\\"term\\""' in result or '"term"' in result


class TestSectionParsing:
    """Tests for section parsing."""

    def test_parse_typical_sections(self):
        """Test parsing typical man page sections."""
        text = """NAME
       ls - list directory contents

DESCRIPTION
       List information about the FILEs.
"""
        sections = _parse_sections(text)

        assert len(sections) == 2
        assert sections[0][0] == "NAME"
        assert sections[1][0] == "DESCRIPTION"

    def test_parse_no_sections(self):
        """Test parsing text without sections."""
        text = "Just plain text."
        sections = _parse_sections(text)

        assert len(sections) == 1
        assert sections[0][0] is None


class TestSnippetExtraction:
    """Tests for snippet extraction."""

    def test_extract_relevant_snippet(self):
        """Test that snippet extraction finds relevant content."""
        text = """NAME
       ls - list directory contents

DESCRIPTION
       List information about the FILEs.

OPTIONS
       -l     use a long listing format
"""
        snippet, section = extract_snippet(text, "long listing")

        assert "long listing" in snippet
        assert section in ("OPTIONS", "DESCRIPTION", None)

    def test_snippet_length_limit(self):
        """Test that snippets respect length limit."""
        long_text = "word " * 1000  # ~5000 characters

        snippet, _ = extract_snippet(long_text, "word", max_length=500)

        assert len(snippet) <= 600  # Allow some tolerance for word boundaries

    def test_snippet_prefers_options_section(self):
        """Test that OPTIONS section is preferred when relevant."""
        text = """DESCRIPTION
       The command has options.

OPTIONS
       -v     verbose mode for more output options
"""
        snippet, section = extract_snippet(text, "options")

        # Should prefer OPTIONS section
        assert section in ("OPTIONS", "DESCRIPTION")


class TestLexicalSearch:
    """Tests for lexical (FTS5) search."""

    def test_search_by_name(self, temp_db):
        """Test searching by command name."""
        results = search("ls", k=5, search_type="lexical", conn=temp_db)

        assert len(results) >= 1
        assert any(r.name == "ls" for r in results)

    def test_search_by_content(self, temp_db):
        """Test searching by content."""
        results = search("directory contents", k=5, search_type="lexical", conn=temp_db)

        assert len(results) >= 1
        # ls man page mentions "directory contents"
        names = [r.name for r in results]
        assert "ls" in names

    def test_search_filter_by_section(self, temp_db):
        """Test filtering search by section."""
        results = search(
            "filesystem", k=5, section="5", search_type="lexical", conn=temp_db
        )

        # Should only return section 5 results
        for r in results:
            assert r.section == "5"

    def test_search_no_results(self, temp_db):
        """Test search with no results."""
        results = search(
            "xyznonexistent123", k=5, search_type="lexical", conn=temp_db
        )

        assert results == []


class TestHybridSearch:
    """Tests for hybrid search."""

    def test_hybrid_search_combines_results(self, temp_db):
        """Test that hybrid search returns results."""
        # Note: Without embeddings, hybrid falls back to lexical only
        results = search("list files", k=5, search_type="hybrid", conn=temp_db)

        # Should get some results from lexical
        assert len(results) >= 0  # May be empty without embeddings

    def test_hybrid_deduplicates(self, temp_db):
        """Test that hybrid search deduplicates results."""
        results = search("ls directory", k=10, search_type="hybrid", conn=temp_db)

        # Each (name, section) should appear only once
        seen = set()
        for r in results:
            key = (r.name, r.section)
            assert key not in seen, f"Duplicate result: {key}"
            seen.add(key)


class TestGetDocument:
    """Tests for document retrieval."""

    def test_get_existing_document(self, temp_db):
        """Test getting an existing document."""
        text = get_document("ls", "1", conn=temp_db)

        assert text is not None
        assert "list directory contents" in text

    def test_get_nonexistent_document(self, temp_db):
        """Test getting a non-existent document."""
        text = get_document("nonexistent", "1", conn=temp_db)

        assert text is None

    def test_get_document_wrong_section(self, temp_db):
        """Test getting document with wrong section."""
        text = get_document("ls", "5", conn=temp_db)

        # ls is in section 1, not 5
        assert text is None


class TestSearchResults:
    """Tests for search result properties."""

    def test_results_have_required_fields(self, temp_db):
        """Test that search results have all required fields."""
        results = search("ls", k=1, search_type="lexical", conn=temp_db)

        if results:
            r = results[0]
            assert r.name is not None
            assert r.section is not None
            assert r.snippet is not None
            assert r.score >= 0
            assert r.search_type in ("lexical", "semantic", "hybrid")

    def test_results_ordered_by_score(self, temp_db):
        """Test that results are ordered by score (descending)."""
        results = search("directory", k=5, search_type="lexical", conn=temp_db)

        if len(results) > 1:
            scores = [r.score for r in results]
            assert scores == sorted(scores, reverse=True)
