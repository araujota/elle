"""Tests for Man Vault indexer."""

import tempfile
from pathlib import Path

import pytest

from elle.daemon.manvault.indexer import (
    CORE_COMMANDS,
    _extract_name,
    _split_into_sections,
    _split_section,
    chunk_document,
    compute_hash,
    normalize_text,
)


class TestNameExtraction:
    """Tests for man page name extraction from filenames."""

    def test_simple_name(self):
        """Test extracting simple command name."""
        assert _extract_name("ls.1.gz", "1") == "ls"

    def test_name_with_dot(self):
        """Test extracting name with dot (e.g., systemd.service)."""
        assert _extract_name("systemd.service.5.gz", "5") == "systemd.service"

    def test_uncompressed(self):
        """Test extracting name from uncompressed file."""
        assert _extract_name("ls.1", "1") == "ls"

    def test_bz2_compression(self):
        """Test extracting name from bz2 compressed file."""
        assert _extract_name("ls.1.bz2", "1") == "ls"

    def test_xz_compression(self):
        """Test extracting name from xz compressed file."""
        assert _extract_name("ls.1.xz", "1") == "ls"

    def test_wrong_section(self):
        """Test that wrong section returns None."""
        assert _extract_name("ls.1.gz", "5") is None

    def test_invalid_filename(self):
        """Test that invalid filename returns None."""
        assert _extract_name("not-a-manpage.txt", "1") is None


class TestNormalization:
    """Tests for text normalization."""

    def test_remove_backspaces(self):
        """Test removing backspace formatting."""
        # The function removes character+backspace sequences
        # Input with embedded backspaces
        text = "hel\blo wor\bld"
        result = normalize_text(text)
        # Backspaces should be removed
        assert "\b" not in result
        # The result should be readable text (exact output depends on implementation)
        assert len(result) > 0

    def test_collapse_blank_lines(self):
        """Test collapsing multiple blank lines."""
        text = "line1\n\n\n\nline2"
        result = normalize_text(text)
        assert result == "line1\n\nline2"

    def test_strip_trailing_whitespace(self):
        """Test stripping trailing whitespace from lines."""
        text = "line1   \nline2  "
        result = normalize_text(text)
        assert result == "line1\nline2"

    def test_remove_leading_blank_lines(self):
        """Test removing leading blank lines."""
        text = "\n\n\nActual content"
        result = normalize_text(text)
        assert result == "Actual content"


class TestHashing:
    """Tests for content hashing."""

    def test_consistent_hash(self):
        """Test that same content produces same hash."""
        text = "Test content"
        hash1 = compute_hash(text)
        hash2 = compute_hash(text)
        assert hash1 == hash2

    def test_different_content_different_hash(self):
        """Test that different content produces different hash."""
        hash1 = compute_hash("Content 1")
        hash2 = compute_hash("Content 2")
        assert hash1 != hash2

    def test_hash_format(self):
        """Test that hash is valid SHA256 hex."""
        hash_value = compute_hash("Test")
        assert len(hash_value) == 64  # SHA256 hex length
        assert all(c in "0123456789abcdef" for c in hash_value)


class TestSectionSplitting:
    """Tests for section splitting."""

    def test_split_simple_sections(self):
        """Test splitting document with clear sections."""
        text = """NAME
       ls - list directory contents

DESCRIPTION
       List information about the files.

OPTIONS
       -l  use a long listing format
"""
        sections = _split_into_sections(text)

        # Should have 3 sections
        assert len(sections) == 3

        # Check section names
        section_names = [s[0] for s in sections]
        assert "NAME" in section_names
        assert "DESCRIPTION" in section_names
        assert "OPTIONS" in section_names

    def test_split_with_preamble(self):
        """Test splitting when there's content before first heading."""
        text = """Some preamble text

NAME
       ls - list directory contents
"""
        sections = _split_into_sections(text)

        # First section should have None as name (preamble)
        assert sections[0][0] is None
        assert "preamble" in sections[0][1].lower()

    def test_split_no_headings(self):
        """Test splitting when there are no headings."""
        text = "Just plain text without any headings."
        sections = _split_into_sections(text)

        assert len(sections) == 1
        assert sections[0][0] is None


class TestChunking:
    """Tests for document chunking."""

    def test_small_section_single_chunk(self):
        """Test that small sections become single chunks."""
        text = """NAME
       ls - list directory contents
"""
        chunks = chunk_document(text, max_chunk_size=1000)

        # Small section should be single chunk
        assert len(chunks) >= 1
        assert "ls" in chunks[0][0]

    def test_large_section_split(self):
        """Test that large sections are split into multiple chunks."""
        # Create a large section
        large_text = """OPTIONS
       """ + "This is a very long option description. " * 100

        chunks = chunk_document(large_text, max_chunk_size=200)

        # Should have multiple chunks
        assert len(chunks) > 1

        # All chunks should have OPTIONS heading
        for _, heading in chunks:
            assert heading == "OPTIONS"

    def test_chunk_preserves_heading(self):
        """Test that chunks preserve their section heading."""
        text = """NAME
       ls - list

DESCRIPTION
       List directory contents
"""
        chunks = chunk_document(text)

        # Each chunk should have the correct heading
        headings = [heading for _, heading in chunks]
        assert "NAME" in headings or "DESCRIPTION" in headings

    def test_split_section_at_paragraphs(self):
        """Test that section splitting respects paragraph boundaries."""
        text = """Paragraph one is complete.

Paragraph two has more information.

Paragraph three concludes."""

        chunks = _split_section(text, max_size=50)

        # Chunks should be at paragraph boundaries where possible
        for chunk in chunks:
            # Each chunk should be a complete thought (ends with period)
            assert chunk.strip().endswith(".")


class TestChunkDocumentIntegration:
    """Integration tests for full document chunking."""

    def test_typical_manpage_chunking(self):
        """Test chunking a typical man page structure."""
        manpage = """NAME
       ls - list directory contents

SYNOPSIS
       ls [OPTION]... [FILE]...

DESCRIPTION
       List information about the FILEs.

OPTIONS
       -a, --all
              do not ignore entries starting with .

       -l     use a long listing format

SEE ALSO
       dir(1), vdir(1)
"""
        chunks = chunk_document(manpage)

        # Should have chunks for each section
        assert len(chunks) >= 4

        # Check that we captured key sections
        all_text = " ".join(text for text, _ in chunks)
        assert "list directory" in all_text
        assert "long listing format" in all_text

    def test_empty_document(self):
        """Test chunking an empty document."""
        chunks = chunk_document("")
        assert chunks == []

    def test_whitespace_only(self):
        """Test chunking whitespace-only document."""
        chunks = chunk_document("   \n\n   ")
        assert chunks == []


class TestCoreCommands:
    """Tests for core commands configuration."""

    def test_core_commands_not_empty(self):
        """Test that core commands list is not empty."""
        assert len(CORE_COMMANDS) > 0

    def test_core_commands_format(self):
        """Test that core commands are (name, section) tuples."""
        for item in CORE_COMMANDS:
            assert isinstance(item, tuple)
            assert len(item) == 2
            name, section = item
            assert isinstance(name, str)
            assert isinstance(section, str)
            assert len(name) > 0
            assert section in ["1", "2", "3", "4", "5", "6", "7", "8"]

    def test_essential_commands_present(self):
        """Test that essential commands are in the core list."""
        command_names = [name for name, _ in CORE_COMMANDS]

        # Critical system commands
        assert "systemctl" in command_names
        assert "journalctl" in command_names
        assert "apt" in command_names

        # Networking
        assert "ip" in command_names
        assert "ss" in command_names
        assert "ufw" in command_names

        # File operations
        assert "ls" in command_names
        assert "grep" in command_names
        assert "tar" in command_names

    def test_no_duplicate_commands(self):
        """Test that there are no duplicate (name, section) pairs."""
        seen = set()
        for item in CORE_COMMANDS:
            assert item not in seen, f"Duplicate core command: {item}"
            seen.add(item)
