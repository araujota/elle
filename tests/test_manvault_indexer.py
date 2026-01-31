from __future__ import annotations

import hashlib
import subprocess as real_sub
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from elle.daemon.manvault.indexer import (
    CORE_COMMANDS,
    DEFAULT_CHUNK_SIZE,
    HEADING_PATTERN,
    MAN_EXTENSIONS,
    MAN_PATHS,
    PG_SCHEMA,
    SECTION_HEADINGS,
    SUPPORTED_SECTIONS,
    _extract_name,
    _split_into_sections,
    _split_section,
    chunk_document,
    compute_hash,
    discover_man_pages,
    get_seeded_count,
    index_all,
    index_incremental,
    index_page,
    is_core_seeded,
    normalize_text,
    render_manpage,
    seed_core_commands,
)
from elle.daemon.manvault.models import ManDiscoveryItem, ManDoc

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_pg_schema(self):
        assert PG_SCHEMA == "manvault"

    def test_man_paths(self):
        assert [Path("/usr/share/man")] == MAN_PATHS

    def test_supported_sections(self):
        assert SUPPORTED_SECTIONS == ["1", "2", "3", "4", "5", "6", "7", "8"]

    def test_core_commands_nonempty(self):
        assert len(CORE_COMMANDS) > 0
        for name, section in CORE_COMMANDS:
            assert isinstance(name, str)
            assert section in SUPPORTED_SECTIONS

    def test_man_extensions(self):
        assert ".gz" in MAN_EXTENSIONS
        assert ".bz2" in MAN_EXTENSIONS
        assert ".xz" in MAN_EXTENSIONS
        assert "" in MAN_EXTENSIONS

    def test_section_headings_contains_name(self):
        assert "NAME" in SECTION_HEADINGS
        assert "DESCRIPTION" in SECTION_HEADINGS
        assert "SEE ALSO" in SECTION_HEADINGS

    def test_heading_pattern(self):
        assert HEADING_PATTERN.search("NAME") is not None
        assert HEADING_PATTERN.search("OPTIONS") is not None
        assert HEADING_PATTERN.search("lowercase") is None

    def test_default_chunk_size(self):
        assert DEFAULT_CHUNK_SIZE == 800


# ---------------------------------------------------------------------------
# _extract_name
# ---------------------------------------------------------------------------


class TestExtractName:
    def test_simple(self):
        assert _extract_name("ls.1.gz", "1") == "ls"

    def test_no_compression(self):
        assert _extract_name("ls.1", "1") == "ls"

    def test_bz2(self):
        assert _extract_name("ls.1.bz2", "1") == "ls"

    def test_xz(self):
        assert _extract_name("ls.1.xz", "1") == "ls"

    def test_compound_name(self):
        assert _extract_name("systemd.service.5.gz", "5") == "systemd.service"

    def test_wrong_section(self):
        assert _extract_name("ls.1.gz", "5") is None

    def test_empty_name(self):
        assert _extract_name(".1.gz", "1") is None

    def test_no_section_suffix(self):
        assert _extract_name("readme.txt", "1") is None

    def test_hyphenated_name(self):
        assert _extract_name("docker-compose.1.gz", "1") == "docker-compose"


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------


class TestNormalizeText:
    def test_backspace_removal(self):
        # Bold: X^HX (man page bold formatting)
        text = "B\bBo\bold\bld"
        result = normalize_text(text)
        assert "\b" not in result
        assert len(result) > 0

    def test_collapse_blank_lines(self):
        text = "line1\n\n\n\n\nline2"
        result = normalize_text(text)
        assert "\n\n\n" not in result

    def test_trailing_whitespace(self):
        text = "hello   \nworld   "
        result = normalize_text(text)
        assert result == "hello\nworld"

    def test_leading_trailing_blank_lines(self):
        text = "\n\n\nhello\n\n\n"
        result = normalize_text(text)
        assert result == "hello"

    def test_underline_removal(self):
        # Underline: _^HX
        text = "_\bH_\be_\bl_\bl_\bo"
        result = normalize_text(text)
        assert "\b" not in result
        assert len(result) > 0

    def test_empty_string(self):
        result = normalize_text("")
        assert result == ""

    def test_all_blank_lines(self):
        text = "\n\n\n"
        result = normalize_text(text)
        assert result == ""

    def test_multiple_backspace_passes(self):
        # Nested backspace: requires multiple passes
        text = "A\b\bBC"
        result = normalize_text(text)
        assert "\b" not in result

    def test_preserves_single_blank_line(self):
        text = "line1\n\nline2"
        result = normalize_text(text)
        assert result == "line1\n\nline2"


# ---------------------------------------------------------------------------
# compute_hash
# ---------------------------------------------------------------------------


class TestComputeHash:
    def test_deterministic(self):
        h1 = compute_hash("hello")
        h2 = compute_hash("hello")
        assert h1 == h2

    def test_different(self):
        h1 = compute_hash("hello")
        h2 = compute_hash("world")
        assert h1 != h2

    def test_hex_format(self):
        h = compute_hash("test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_matches_hashlib(self):
        text = "some test text"
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert compute_hash(text) == expected


# ---------------------------------------------------------------------------
# _split_into_sections
# ---------------------------------------------------------------------------


class TestSplitIntoSections:
    def test_no_headings(self):
        text = "Just some text without headings"
        sections = _split_into_sections(text)
        assert len(sections) == 1
        assert sections[0][0] is None

    def test_with_headings(self):
        text = "NAME\nfoo - a test\n\nDESCRIPTION\nSome description"
        sections = _split_into_sections(text)
        assert len(sections) == 2
        assert sections[0][0] == "NAME"
        assert sections[1][0] == "DESCRIPTION"

    def test_preamble_before_heading(self):
        text = "Some preamble\n\nNAME\ncontent"
        sections = _split_into_sections(text)
        assert len(sections) == 2
        assert sections[0][0] is None

    def test_multiple_headings(self):
        text = "NAME\nfoo\n\nSYNOPSIS\nfoo [opts]\n\nDESCRIPTION\nA tool\n\nOPTIONS\n-v"
        sections = _split_into_sections(text)
        headings = [s[0] for s in sections]
        assert "NAME" in headings
        assert "SYNOPSIS" in headings
        assert "DESCRIPTION" in headings
        assert "OPTIONS" in headings

    def test_empty_preamble_not_included(self):
        text = "NAME\ncontent here"
        sections = _split_into_sections(text)
        assert len(sections) == 1
        assert sections[0][0] == "NAME"

    def test_heading_content_correctly_split(self):
        text = "NAME\nfoo - bar\n\nDESCRIPTION\nbaz qux"
        sections = _split_into_sections(text)
        # Content after NAME heading should contain "foo - bar"
        assert "foo - bar" in sections[0][1]
        # Content after DESCRIPTION heading should contain "baz qux"
        assert "baz qux" in sections[1][1]


# ---------------------------------------------------------------------------
# _split_section
# ---------------------------------------------------------------------------


class TestSplitSection:
    def test_small_text(self):
        text = "Just a small paragraph."
        chunks = _split_section(text, 1000)
        assert len(chunks) == 1

    def test_large_text(self):
        paras = "\n\n".join([f"Paragraph {i} with some content to fill." for i in range(50)])
        chunks = _split_section(paras, 100)
        assert len(chunks) > 1

    def test_long_paragraph(self):
        text = "A" * 200 + ". " + "B" * 200 + ". " + "C" * 200
        chunks = _split_section(text, 150)
        assert len(chunks) > 1

    def test_empty_paragraphs_skipped(self):
        text = "para1\n\n\n\n\n\npara2"
        chunks = _split_section(text, 1000)
        assert len(chunks) == 1
        assert "para1" in chunks[0]
        assert "para2" in chunks[0]

    def test_paragraph_boundary_flush(self):
        # Two paragraphs that together exceed max_size
        text = "A" * 50 + "\n\n" + "B" * 50
        chunks = _split_section(text, 60)
        assert len(chunks) == 2

    def test_long_paragraph_sentence_split(self):
        # Create a paragraph longer than max that has multiple sentences
        text = "First sentence here. Second sentence here. Third sentence here. Fourth sentence here."
        chunks = _split_section(text, 40)
        assert len(chunks) >= 2

    def test_single_huge_paragraph_no_sentences(self):
        # Single chunk of text with no sentence boundaries
        text = "A" * 300
        chunks = _split_section(text, 100)
        # It should still produce at least one chunk
        assert len(chunks) >= 1

    def test_empty_text(self):
        chunks = _split_section("", 100)
        assert chunks == []


# ---------------------------------------------------------------------------
# chunk_document
# ---------------------------------------------------------------------------


class TestChunkDocument:
    def test_empty_sections(self):
        text = "NAME\n\nDESCRIPTION\n\nSome content here."
        chunks = chunk_document(text)
        assert len(chunks) >= 1

    def test_small_document(self):
        text = "NAME\nls - list directory contents"
        chunks = chunk_document(text)
        assert len(chunks) == 1
        assert chunks[0][1] == "NAME"

    def test_large_section(self):
        content = "\n\n".join([f"Option {i}: does something." for i in range(100)])
        text = f"OPTIONS\n{content}"
        chunks = chunk_document(text, max_chunk_size=200)
        assert len(chunks) > 1
        assert all(c[1] == "OPTIONS" for c in chunks)

    def test_multiple_sections(self):
        text = "NAME\nfoo - test\n\nDESCRIPTION\nA description\n\nOPTIONS\n-v verbose"
        chunks = chunk_document(text)
        headings = {c[1] for c in chunks}
        assert "NAME" in headings
        assert "DESCRIPTION" in headings
        assert "OPTIONS" in headings

    def test_empty_content_sections_skipped(self):
        text = "NAME\n\n\nDESCRIPTION\ncontent here"
        chunks = chunk_document(text)
        # NAME section has empty content after heading, should be skipped
        assert all("content" in c[0] or len(c[0].strip()) > 0 for c in chunks)

    def test_no_headings_document(self):
        text = "Just plain text without any section headings."
        chunks = chunk_document(text)
        assert len(chunks) == 1
        assert chunks[0][1] is None


# ---------------------------------------------------------------------------
# render_manpage
# ---------------------------------------------------------------------------


class TestRenderManpage:
    @patch("elle.daemon.manvault.indexer.subprocess.Popen")
    def test_render_success(self, mock_popen):
        mock_man = MagicMock()
        mock_man.stdout = MagicMock()
        mock_man.returncode = 0
        mock_man.wait.return_value = 0

        mock_col = MagicMock()
        mock_col.communicate.return_value = (
            b"LS(1)\n\nNAME\n ls - list\n",
            b"",
        )
        mock_col.returncode = 0

        mock_popen.side_effect = [mock_man, mock_col]
        result = render_manpage("ls", "1")
        assert result is not None
        assert "ls" in result

    @patch("elle.daemon.manvault.indexer.subprocess.Popen")
    def test_render_man_fails(self, mock_popen):
        mock_man = MagicMock()
        mock_man.stdout = MagicMock()
        mock_man.returncode = 1
        mock_man.wait.return_value = 1

        mock_col = MagicMock()
        mock_col.communicate.return_value = (b"", b"")
        mock_col.returncode = 0

        mock_popen.side_effect = [mock_man, mock_col]
        result = render_manpage("nonexistent", "1")
        assert result is None

    @patch("elle.daemon.manvault.indexer.subprocess.Popen")
    def test_render_timeout(self, mock_popen):
        mock_man = MagicMock()
        mock_man.stdout = MagicMock()
        mock_man.kill = MagicMock()

        mock_col = MagicMock()
        mock_col.communicate.side_effect = real_sub.TimeoutExpired(cmd="col", timeout=5)
        mock_col.kill = MagicMock()

        mock_popen.side_effect = [mock_man, mock_col]
        result = render_manpage("ls", "1")
        assert result is None
        mock_man.kill.assert_called_once()
        mock_col.kill.assert_called_once()

    @patch("elle.daemon.manvault.indexer.subprocess.Popen")
    def test_render_not_found(self, mock_popen):
        mock_popen.side_effect = FileNotFoundError("man not found")
        result = render_manpage("ls", "1")
        assert result is None

    @patch("elle.daemon.manvault.indexer.subprocess.Popen")
    def test_render_col_fails(self, mock_popen):
        mock_man = MagicMock()
        mock_man.stdout = MagicMock()
        mock_man.returncode = 0
        mock_man.wait.return_value = 0

        mock_col = MagicMock()
        mock_col.communicate.return_value = (b"", b"error")
        mock_col.returncode = 1

        mock_popen.side_effect = [mock_man, mock_col]
        result = render_manpage("ls", "1")
        assert result is None

    @patch("elle.daemon.manvault.indexer.subprocess.Popen")
    def test_render_generic_exception(self, mock_popen):
        mock_popen.side_effect = RuntimeError("unexpected error")
        result = render_manpage("ls", "1")
        assert result is None

    @patch("elle.daemon.manvault.indexer.subprocess.Popen")
    def test_render_non_english_lang(self, mock_popen):
        mock_man = MagicMock()
        mock_man.stdout = MagicMock()
        mock_man.returncode = 0
        mock_man.wait.return_value = 0

        mock_col = MagicMock()
        mock_col.communicate.return_value = (
            b"LS(1)\n\nNOM\n ls - lister\n",
            b"",
        )
        mock_col.returncode = 0

        mock_popen.side_effect = [mock_man, mock_col]
        result = render_manpage("ls", "1", lang="fr")
        assert result is not None

        # Verify env was set correctly for non-English
        first_popen_call = mock_popen.call_args_list[0]
        env_arg = first_popen_call[1]["env"]
        assert env_arg["LANG"] == "fr_UTF-8"
        assert env_arg["LC_ALL"] == "fr_UTF-8"

    @patch("elle.daemon.manvault.indexer.subprocess.Popen")
    def test_render_english_env(self, mock_popen):
        mock_man = MagicMock()
        mock_man.stdout = MagicMock()
        mock_man.returncode = 0
        mock_man.wait.return_value = 0

        mock_col = MagicMock()
        mock_col.communicate.return_value = (b"LS(1)\nNAME\nls - list\n", b"")
        mock_col.returncode = 0

        mock_popen.side_effect = [mock_man, mock_col]
        render_manpage("ls", "1", lang="en")

        first_popen_call = mock_popen.call_args_list[0]
        env_arg = first_popen_call[1]["env"]
        assert env_arg["LANG"] == "C"
        assert env_arg["LC_ALL"] == "C"
        assert env_arg["MANWIDTH"] == "80"
        assert env_arg["MAN_KEEP_FORMATTING"] == "1"

    @patch("elle.daemon.manvault.indexer.subprocess.Popen")
    def test_render_man_stdout_closed(self, mock_popen):
        """Verify man_proc.stdout.close() is called in parent."""
        mock_stdout = MagicMock()
        mock_man = MagicMock()
        mock_man.stdout = mock_stdout
        mock_man.returncode = 0
        mock_man.wait.return_value = 0

        mock_col = MagicMock()
        mock_col.communicate.return_value = (b"output", b"")
        mock_col.returncode = 0

        mock_popen.side_effect = [mock_man, mock_col]
        render_manpage("ls", "1")
        mock_stdout.close.assert_called_once()


# ---------------------------------------------------------------------------
# discover_man_pages
# ---------------------------------------------------------------------------


class TestDiscoverManPages:
    def test_discover_from_temp(self, tmp_path):
        man1_dir = tmp_path / "man1"
        man1_dir.mkdir()
        (man1_dir / "ls.1.gz").touch()
        (man1_dir / "cp.1.gz").touch()
        (man1_dir / "notaman").touch()  # should be skipped

        items = list(discover_man_pages([tmp_path]))
        names = [i.name for i in items]
        assert "ls" in names
        assert "cp" in names

    def test_discover_nonexistent(self, tmp_path):
        items = list(discover_man_pages([tmp_path / "nonexistent"]))
        assert items == []

    def test_discover_with_lang(self, tmp_path):
        fr_man1 = tmp_path / "fr" / "man1"
        fr_man1.mkdir(parents=True)
        (fr_man1 / "ls.1.gz").touch()
        items = list(discover_man_pages([tmp_path]))
        assert len(items) == 1
        assert items[0].lang == "fr"

    def test_discover_english_default(self, tmp_path):
        man1_dir = tmp_path / "man1"
        man1_dir.mkdir()
        (man1_dir / "ls.1.gz").touch()
        items = list(discover_man_pages([tmp_path]))
        assert len(items) == 1
        assert items[0].lang == "en"

    def test_discover_multiple_sections(self, tmp_path):
        for sec in ["1", "5", "8"]:
            d = tmp_path / f"man{sec}"
            d.mkdir()
            (d / f"test.{sec}.gz").touch()

        items = list(discover_man_pages([tmp_path]))
        sections = {i.section for i in items}
        assert sections == {"1", "5", "8"}

    def test_discover_unsupported_section_ignored(self, tmp_path):
        bad_dir = tmp_path / "man9"
        bad_dir.mkdir()
        (bad_dir / "foo.9.gz").touch()
        items = list(discover_man_pages([tmp_path]))
        assert items == []

    def test_discover_skips_non_files(self, tmp_path):
        man1_dir = tmp_path / "man1"
        man1_dir.mkdir()
        (man1_dir / "subdir.1.gz").mkdir()  # directory, not a file
        items = list(discover_man_pages([tmp_path]))
        assert items == []

    def test_discover_default_paths_used(self):
        # When paths is None, MAN_PATHS is used
        # On test machines /usr/share/man may or may not exist;
        # we just verify it doesn't crash
        items = list(discover_man_pages(None))
        assert isinstance(items, list)

    def test_discover_oserror_on_stat(self, tmp_path):
        man1_dir = tmp_path / "man1"
        man1_dir.mkdir()
        f = man1_dir / "ls.1.gz"
        f.touch()

        # The code calls man_file.is_file() (which internally uses stat),
        # then man_file.stat().st_mtime in a try block. We need the explicit
        # stat() call to raise OSError while allowing is_file() to work.
        # Track calls per file and raise only on the second call (the explicit one).
        original_stat = Path.stat
        stat_calls: dict[str, int] = {}

        def patched_stat(self_path, *args, **kwargs):
            name = str(self_path)
            stat_calls[name] = stat_calls.get(name, 0) + 1
            if self_path.name == "ls.1.gz" and stat_calls[name] > 1:
                raise OSError("permission denied")
            return original_stat(self_path, *args, **kwargs)

        with patch.object(Path, "stat", patched_stat):
            items = list(discover_man_pages([tmp_path]))
            assert items == []

    def test_discover_source_path_captured(self, tmp_path):
        man1_dir = tmp_path / "man1"
        man1_dir.mkdir()
        f = man1_dir / "ls.1.gz"
        f.touch()
        items = list(discover_man_pages([tmp_path]))
        assert len(items) == 1
        assert items[0].source_path == str(f)

    def test_discover_mtime_captured(self, tmp_path):
        man1_dir = tmp_path / "man1"
        man1_dir.mkdir()
        f = man1_dir / "ls.1.gz"
        f.touch()
        items = list(discover_man_pages([tmp_path]))
        assert len(items) == 1
        assert isinstance(items[0].mtime, float)
        assert items[0].mtime > 0


# ---------------------------------------------------------------------------
# index_page (fully mocked -- no DB required)
# ---------------------------------------------------------------------------


class TestIndexPage:
    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=42)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    @patch("elle.daemon.manvault.indexer.render_manpage")
    def test_index_page_success(self, mock_render, mock_get_doc, mock_upsert_doc, mock_upsert_chunks):
        mock_render.return_value = "NAME\nls - list contents\n\nDESCRIPTION\nLists directory."
        mock_conn = MagicMock()
        result = index_page("ls", "1", "en", conn=mock_conn)
        assert result is True
        mock_render.assert_called_once_with("ls", "1", "en")
        mock_upsert_doc.assert_called_once()
        mock_upsert_chunks.assert_called_once()

    @patch("elle.daemon.manvault.indexer.render_manpage", return_value=None)
    def test_index_page_no_text(self, mock_render):
        mock_conn = MagicMock()
        result = index_page("nope", "1", "en", conn=mock_conn)
        assert result is False

    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=42)
    @patch("elle.daemon.manvault.indexer.get_doc")
    @patch("elle.daemon.manvault.indexer.render_manpage")
    def test_index_page_skip_unchanged(self, mock_render, mock_get_doc, mock_upsert_doc, mock_upsert_chunks):
        text = "NAME\nls - list contents"
        normalized = normalize_text(text)
        sha = compute_hash(normalized)
        mock_render.return_value = text
        mock_get_doc.return_value = ManDoc(
            id=1,
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256=sha,
            text=normalized,
            updated_at=datetime.now(),
        )
        mock_conn = MagicMock()
        result = index_page("ls", "1", "en", conn=mock_conn)
        assert result is True
        # Should NOT upsert since hash matches
        mock_upsert_doc.assert_not_called()
        mock_upsert_chunks.assert_not_called()

    @patch("elle.daemon.manvault.indexer.delete_chunks_for_doc")
    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=42)
    @patch("elle.daemon.manvault.indexer.get_doc")
    @patch("elle.daemon.manvault.indexer.render_manpage")
    def test_index_page_update_existing(
        self,
        mock_render,
        mock_get_doc,
        mock_upsert_doc,
        mock_upsert_chunks,
        mock_delete_chunks,
    ):
        """When an existing doc has a different hash, it should be updated."""
        mock_render.return_value = "NAME\nls - new content"
        mock_get_doc.return_value = ManDoc(
            id=1,
            name="ls",
            section="1",
            lang="en",
            source_path="/usr/share/man/man1/ls.1.gz",
            sha256="old_hash_that_wont_match",
            text="old text",
            updated_at=datetime.now(),
        )
        mock_conn = MagicMock()
        result = index_page("ls", "1", "en", conn=mock_conn)
        assert result is True
        mock_upsert_doc.assert_called_once()
        mock_delete_chunks.assert_called_once_with(mock_conn, 42)
        mock_upsert_chunks.assert_called_once()

    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=10)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    @patch("elle.daemon.manvault.indexer.render_manpage")
    def test_index_page_default_source_path(self, mock_render, mock_get_doc, mock_upsert_doc, mock_upsert_chunks):
        """When source_path is None, a default path is generated."""
        mock_render.return_value = "NAME\nls - list"
        mock_conn = MagicMock()
        index_page("ls", "1", "en", source_path=None, conn=mock_conn)

        # Verify the ManDoc passed to upsert_doc has the default source_path
        doc_arg = mock_upsert_doc.call_args[0][1]
        assert doc_arg.source_path == "/usr/share/man/man1/ls.1.gz"

    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=10)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    @patch("elle.daemon.manvault.indexer.render_manpage")
    def test_index_page_custom_source_path(self, mock_render, mock_get_doc, mock_upsert_doc, mock_upsert_chunks):
        """When source_path is provided, it is used."""
        mock_render.return_value = "NAME\nls - list"
        mock_conn = MagicMock()
        index_page("ls", "1", "en", source_path="/custom/path.gz", conn=mock_conn)
        doc_arg = mock_upsert_doc.call_args[0][1]
        assert doc_arg.source_path == "/custom/path.gz"

    @patch("elle.daemon.manvault.indexer.get_conn")
    @patch("elle.daemon.manvault.indexer.render_manpage", return_value=None)
    def test_index_page_no_conn_uses_get_conn(self, mock_render, mock_get_conn):
        """When conn is None, get_conn is used to obtain a connection."""
        mock_ctx = MagicMock()
        mock_conn = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_ctx

        result = index_page("ls", "1", "en", conn=None)
        assert result is False
        mock_get_conn.assert_called_once_with(schema=PG_SCHEMA)

    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=5)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    @patch("elle.daemon.manvault.indexer.render_manpage")
    def test_index_page_chunks_created(self, mock_render, mock_get_doc, mock_upsert_doc, mock_upsert_chunks):
        """Verify chunks are correctly created and passed to upsert."""
        mock_render.return_value = "NAME\nfoo - bar\n\nDESCRIPTION\nbaz content"
        mock_conn = MagicMock()
        index_page("foo", "1", "en", conn=mock_conn)

        chunks = mock_upsert_chunks.call_args[0][1]
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.doc_id == 5
            assert isinstance(chunk.chunk_index, int)


# ---------------------------------------------------------------------------
# index_all (fully mocked)
# ---------------------------------------------------------------------------


class TestIndexAll:
    @patch("elle.daemon.manvault.indexer.set_meta")
    @patch("elle.daemon.manvault.indexer.render_manpage")
    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=1)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    def test_index_all_success(
        self,
        mock_discover,
        mock_get_doc,
        mock_upsert_doc,
        mock_upsert_chunks,
        mock_render,
        mock_set_meta,
    ):
        mock_discover.return_value = [
            ManDiscoveryItem(
                name="ls",
                section="1",
                lang="en",
                source_path="/usr/share/man/man1/ls.1.gz",
                mtime=1.0,
            ),
            ManDiscoveryItem(
                name="cp",
                section="1",
                lang="en",
                source_path="/usr/share/man/man1/cp.1.gz",
                mtime=1.0,
            ),
        ]
        mock_render.return_value = "NAME\ntest - desc"
        mock_conn = MagicMock()
        count = index_all(conn=mock_conn)
        assert count == 2
        mock_set_meta.assert_called_once()

    @patch("elle.daemon.manvault.indexer.set_meta")
    @patch("elle.daemon.manvault.indexer.render_manpage")
    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=1)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    def test_index_all_with_failure(
        self,
        mock_discover,
        mock_get_doc,
        mock_upsert_doc,
        mock_upsert_chunks,
        mock_render,
        mock_set_meta,
    ):
        mock_discover.return_value = [
            ManDiscoveryItem(
                name="ls",
                section="1",
                lang="en",
                source_path="/x",
                mtime=1.0,
            ),
        ]
        mock_render.side_effect = RuntimeError("fail")
        mock_conn = MagicMock()
        count = index_all(conn=mock_conn)
        assert count == 0

    @patch("elle.daemon.manvault.indexer.set_meta")
    @patch("elle.daemon.manvault.indexer.render_manpage")
    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=1)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    def test_index_all_lang_filter(
        self,
        mock_discover,
        mock_get_doc,
        mock_upsert_doc,
        mock_upsert_chunks,
        mock_render,
        mock_set_meta,
    ):
        """Only pages matching lang_filter should be indexed."""
        mock_discover.return_value = [
            ManDiscoveryItem(
                name="ls",
                section="1",
                lang="en",
                source_path="/x",
                mtime=1.0,
            ),
            ManDiscoveryItem(
                name="ls",
                section="1",
                lang="fr",
                source_path="/y",
                mtime=1.0,
            ),
        ]
        mock_render.return_value = "NAME\nls - list"
        mock_conn = MagicMock()
        count = index_all(conn=mock_conn, lang_filter="en")
        assert count == 1

    @patch("elle.daemon.manvault.indexer.set_meta")
    @patch("elle.daemon.manvault.indexer.render_manpage")
    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=1)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    def test_index_all_progress_callback(
        self,
        mock_discover,
        mock_get_doc,
        mock_upsert_doc,
        mock_upsert_chunks,
        mock_render,
        mock_set_meta,
    ):
        """Progress callback is called every 100 pages."""
        # Create exactly 200 pages to trigger callback at i=99 and i=199
        pages = [
            ManDiscoveryItem(
                name=f"cmd{i}",
                section="1",
                lang="en",
                source_path=f"/x/{i}",
                mtime=1.0,
            )
            for i in range(200)
        ]
        mock_discover.return_value = pages
        mock_render.return_value = "NAME\ntest"
        mock_conn = MagicMock()
        progress_calls = []
        index_all(
            conn=mock_conn,
            progress_callback=lambda c, t: progress_calls.append((c, t)),
        )
        # Callback is called when (i+1) % 100 == 0, i.e. at i=99 and i=199
        assert len(progress_calls) == 2
        assert progress_calls[0] == (100, 200)
        assert progress_calls[1] == (200, 200)

    @patch("elle.daemon.manvault.indexer.set_meta")
    @patch("elle.daemon.manvault.indexer.discover_man_pages", return_value=[])
    def test_index_all_empty(self, mock_discover, mock_set_meta):
        """No pages discovered means zero indexed."""
        mock_conn = MagicMock()
        count = index_all(conn=mock_conn)
        assert count == 0
        mock_set_meta.assert_called_once()

    @patch("elle.daemon.manvault.indexer.get_conn")
    @patch("elle.daemon.manvault.indexer.set_meta")
    @patch("elle.daemon.manvault.indexer.discover_man_pages", return_value=[])
    def test_index_all_no_conn_uses_get_conn(self, mock_discover, mock_set_meta, mock_get_conn):
        """When conn is None, get_conn context manager is used."""
        mock_ctx = MagicMock()
        mock_inner_conn = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_inner_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_ctx

        count = index_all(conn=None)
        assert count == 0
        mock_get_conn.assert_called_once_with(schema=PG_SCHEMA)

    @patch("elle.daemon.manvault.indexer.set_meta")
    @patch("elle.daemon.manvault.indexer.render_manpage", return_value=None)
    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    def test_index_all_render_returns_none(self, mock_discover, mock_render, mock_set_meta):
        """Pages that fail to render are not counted."""
        mock_discover.return_value = [
            ManDiscoveryItem(
                name="ls",
                section="1",
                lang="en",
                source_path="/x",
                mtime=1.0,
            ),
        ]
        mock_conn = MagicMock()
        count = index_all(conn=mock_conn)
        assert count == 0

    @patch("elle.daemon.manvault.indexer.set_meta")
    @patch("elle.daemon.manvault.indexer.render_manpage")
    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=1)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    def test_index_all_no_lang_filter(
        self,
        mock_discover,
        mock_get_doc,
        mock_upsert_doc,
        mock_upsert_chunks,
        mock_render,
        mock_set_meta,
    ):
        """When lang_filter is empty string, all pages pass through."""
        mock_discover.return_value = [
            ManDiscoveryItem(
                name="ls",
                section="1",
                lang="en",
                source_path="/x",
                mtime=1.0,
            ),
            ManDiscoveryItem(
                name="ls",
                section="1",
                lang="fr",
                source_path="/y",
                mtime=1.0,
            ),
        ]
        mock_render.return_value = "NAME\ntest"
        mock_conn = MagicMock()
        count = index_all(conn=mock_conn, lang_filter="")
        assert count == 2


# ---------------------------------------------------------------------------
# index_incremental (fully mocked)
# ---------------------------------------------------------------------------


class TestIndexIncremental:
    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=1)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    @patch("elle.daemon.manvault.indexer.render_manpage")
    @patch("elle.daemon.manvault.indexer.get_all_hashes", return_value={})
    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    def test_incremental_new_page(
        self,
        mock_discover,
        mock_hashes,
        mock_render,
        mock_get_doc,
        mock_upsert_doc,
        mock_upsert_chunks,
    ):
        mock_discover.return_value = [
            ManDiscoveryItem(
                name="ls",
                section="1",
                lang="en",
                source_path="/x",
                mtime=1.0,
            ),
        ]
        mock_render.return_value = "NAME\nls - list"
        mock_conn = MagicMock()
        added, updated = index_incremental(conn=mock_conn)
        assert added == 1
        assert updated == 0

    @patch("elle.daemon.manvault.indexer.delete_chunks_for_doc")
    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=1)
    @patch("elle.daemon.manvault.indexer.get_doc")
    @patch("elle.daemon.manvault.indexer.render_manpage")
    @patch("elle.daemon.manvault.indexer.get_all_hashes")
    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    def test_incremental_updated_page(
        self,
        mock_discover,
        mock_hashes,
        mock_render,
        mock_get_doc,
        mock_upsert_doc,
        mock_upsert_chunks,
        mock_delete_chunks,
    ):
        """When hash differs, the page is counted as updated."""
        mock_discover.return_value = [
            ManDiscoveryItem(
                name="ls",
                section="1",
                lang="en",
                source_path="/x",
                mtime=1.0,
            ),
        ]
        mock_hashes.return_value = {("ls", "1", "en"): "old_hash"}
        mock_render.return_value = "NAME\nls - updated content"
        # get_doc returns existing doc with old hash (for _index_page_impl)
        mock_get_doc.return_value = ManDoc(
            id=1,
            name="ls",
            section="1",
            lang="en",
            source_path="/x",
            sha256="old_hash",
            text="old",
            updated_at=datetime.now(),
        )
        mock_conn = MagicMock()
        added, updated = index_incremental(conn=mock_conn)
        assert added == 0
        assert updated == 1

    @patch("elle.daemon.manvault.indexer.render_manpage")
    @patch("elle.daemon.manvault.indexer.get_all_hashes")
    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    def test_incremental_unchanged_page(self, mock_discover, mock_hashes, mock_render):
        """When hash matches, the page is skipped."""
        text = "NAME\nls - list"
        normalized = normalize_text(text)
        sha = compute_hash(normalized)
        mock_discover.return_value = [
            ManDiscoveryItem(
                name="ls",
                section="1",
                lang="en",
                source_path="/x",
                mtime=1.0,
            ),
        ]
        mock_hashes.return_value = {("ls", "1", "en"): sha}
        mock_render.return_value = text
        mock_conn = MagicMock()
        added, updated = index_incremental(conn=mock_conn)
        assert added == 0
        assert updated == 0

    @patch("elle.daemon.manvault.indexer.render_manpage", return_value=None)
    @patch("elle.daemon.manvault.indexer.get_all_hashes", return_value={})
    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    def test_incremental_render_fails(self, mock_discover, mock_hashes, mock_render):
        """If render returns None, page is skipped."""
        mock_discover.return_value = [
            ManDiscoveryItem(
                name="ls",
                section="1",
                lang="en",
                source_path="/x",
                mtime=1.0,
            ),
        ]
        mock_conn = MagicMock()
        added, updated = index_incremental(conn=mock_conn)
        assert added == 0
        assert updated == 0

    @patch("elle.daemon.manvault.indexer.render_manpage", return_value=None)
    @patch("elle.daemon.manvault.indexer.get_all_hashes", return_value={})
    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    def test_incremental_lang_filter(self, mock_discover, mock_hashes, mock_render):
        """Only pages matching lang_filter are processed."""
        mock_discover.return_value = [
            ManDiscoveryItem(
                name="ls",
                section="1",
                lang="en",
                source_path="/x",
                mtime=1.0,
            ),
            ManDiscoveryItem(
                name="ls",
                section="1",
                lang="fr",
                source_path="/y",
                mtime=1.0,
            ),
        ]
        mock_conn = MagicMock()
        added, updated = index_incremental(conn=mock_conn, lang_filter="en")
        # render returns None, so nothing indexed, but only 1 page processed
        assert added == 0
        assert updated == 0
        # render_manpage should only be called once for the 'en' page
        assert mock_render.call_count == 1

    @patch("elle.daemon.manvault.indexer.get_conn")
    @patch("elle.daemon.manvault.indexer.render_manpage", return_value=None)
    @patch("elle.daemon.manvault.indexer.get_all_hashes", return_value={})
    @patch("elle.daemon.manvault.indexer.discover_man_pages", return_value=[])
    def test_incremental_no_conn_uses_get_conn(self, mock_discover, mock_hashes, mock_render, mock_get_conn):
        mock_ctx = MagicMock()
        mock_inner_conn = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_inner_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_ctx

        added, updated = index_incremental(conn=None)
        assert added == 0
        assert updated == 0
        mock_get_conn.assert_called_once_with(schema=PG_SCHEMA)

    @patch("elle.daemon.manvault.indexer.render_manpage", return_value=None)
    @patch("elle.daemon.manvault.indexer.get_all_hashes", return_value={})
    @patch("elle.daemon.manvault.indexer.discover_man_pages", return_value=[])
    def test_incremental_empty_discovery(self, mock_discover, mock_hashes, mock_render):
        mock_conn = MagicMock()
        added, updated = index_incremental(conn=mock_conn)
        assert added == 0
        assert updated == 0

    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=1)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    @patch("elle.daemon.manvault.indexer.render_manpage")
    @patch("elle.daemon.manvault.indexer.get_all_hashes", return_value={})
    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    def test_incremental_index_page_impl_failure_not_counted(
        self,
        mock_discover,
        mock_hashes,
        mock_render,
        mock_get_doc,
        mock_upsert_doc,
        mock_upsert_chunks,
    ):
        """When _index_page_impl returns False (render fails inside), not counted."""
        mock_discover.return_value = [
            ManDiscoveryItem(
                name="ls",
                section="1",
                lang="en",
                source_path="/x",
                mtime=1.0,
            ),
        ]
        # First render call (for hash check in incremental) returns text
        # Second render call (inside _index_page_impl) returns None
        mock_render.side_effect = ["NAME\nls - list", None]
        mock_conn = MagicMock()
        added, updated = index_incremental(conn=mock_conn)
        assert added == 0


# ---------------------------------------------------------------------------
# seed_core_commands (fully mocked)
# ---------------------------------------------------------------------------


class TestCoreSeed:
    @patch("elle.daemon.manvault.indexer.render_manpage", return_value=None)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    def test_seed_core_commands_all_fail(self, mock_get_doc, mock_render):
        """All render calls fail, so zero seeded."""
        mock_conn = MagicMock()
        count = seed_core_commands(conn=mock_conn)
        assert count == 0

    @patch("elle.daemon.manvault.indexer.upsert_chunks_batch")
    @patch("elle.daemon.manvault.indexer.upsert_doc", return_value=1)
    @patch("elle.daemon.manvault.indexer.render_manpage")
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    def test_seed_core_commands_success(self, mock_get_doc, mock_render, mock_upsert_doc, mock_upsert_chunks):
        """When render succeeds, commands are counted."""
        mock_render.return_value = "NAME\ncmd - desc"
        mock_conn = MagicMock()
        count = seed_core_commands(conn=mock_conn)
        assert count == len(CORE_COMMANDS)

    @patch("elle.daemon.manvault.indexer.render_manpage")
    @patch("elle.daemon.manvault.indexer.get_doc")
    def test_seed_core_commands_skip_existing(self, mock_get_doc, mock_render):
        """Existing docs are skipped, not re-indexed."""
        mock_get_doc.return_value = ManDoc(
            id=1,
            name="ls",
            section="1",
            lang="en",
            source_path="/x",
            sha256="abc",
            text="text",
            updated_at=datetime.now(),
        )
        mock_conn = MagicMock()
        count = seed_core_commands(conn=mock_conn)
        assert count == 0
        # render should never be called since all are skipped
        mock_render.assert_not_called()

    @patch("elle.daemon.manvault.indexer.render_manpage", return_value=None)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    def test_seed_with_progress(self, mock_get_doc, mock_render):
        mock_conn = MagicMock()
        progress_calls = []
        seed_core_commands(
            conn=mock_conn,
            progress_callback=lambda c, t: progress_calls.append((c, t)),
        )
        assert len(progress_calls) == len(CORE_COMMANDS)
        # Every iteration should call progress
        assert progress_calls[-1] == (len(CORE_COMMANDS), len(CORE_COMMANDS))

    @patch("elle.daemon.manvault.indexer.render_manpage")
    @patch("elle.daemon.manvault.indexer.get_doc")
    def test_seed_with_progress_skip_existing(self, mock_get_doc, mock_render):
        """Progress is reported even for skipped existing docs."""
        mock_get_doc.return_value = ManDoc(
            id=1,
            name="x",
            section="1",
            lang="en",
            source_path="/x",
            sha256="abc",
            text="text",
            updated_at=datetime.now(),
        )
        mock_conn = MagicMock()
        progress_calls = []
        seed_core_commands(
            conn=mock_conn,
            progress_callback=lambda c, t: progress_calls.append((c, t)),
        )
        # Progress called for every item including skipped
        assert len(progress_calls) == len(CORE_COMMANDS)

    @patch("elle.daemon.manvault.indexer._index_page_impl")
    @patch("elle.daemon.manvault.indexer.get_doc")
    def test_seed_handles_exception(self, mock_get_doc, mock_index_impl):
        """Exceptions during seeding are caught per-command."""
        mock_get_doc.return_value = None
        mock_index_impl.side_effect = RuntimeError("db error")
        mock_conn = MagicMock()
        # Should not raise
        count = seed_core_commands(conn=mock_conn)
        assert count == 0

    @patch("elle.daemon.manvault.indexer.get_conn")
    @patch("elle.daemon.manvault.indexer.render_manpage", return_value=None)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    def test_seed_no_conn_uses_get_conn(self, mock_get_doc, mock_render, mock_get_conn):
        mock_ctx = MagicMock()
        mock_inner_conn = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_inner_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_ctx

        seed_core_commands(conn=None)
        mock_get_conn.assert_called_once_with(schema=PG_SCHEMA)

    @patch("elle.daemon.manvault.indexer._index_page_impl", return_value=False)
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    def test_seed_index_page_returns_false(self, mock_get_doc, mock_index_impl):
        """When _index_page_impl returns False (e.g. man not available),
        it is not counted but no error occurs."""
        mock_conn = MagicMock()
        count = seed_core_commands(conn=mock_conn)
        assert count == 0


# ---------------------------------------------------------------------------
# get_seeded_count (fully mocked)
# ---------------------------------------------------------------------------


class TestGetSeededCount:
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    def test_get_seeded_count_zero(self, mock_get_doc):
        mock_conn = MagicMock()
        count = get_seeded_count(mock_conn)
        assert count == 0
        assert mock_get_doc.call_count == len(CORE_COMMANDS)

    @patch("elle.daemon.manvault.indexer.get_doc")
    def test_get_seeded_count_all_present(self, mock_get_doc):
        mock_get_doc.return_value = ManDoc(
            id=1,
            name="x",
            section="1",
            lang="en",
            source_path="/x",
            sha256="abc",
            text="text",
            updated_at=datetime.now(),
        )
        mock_conn = MagicMock()
        count = get_seeded_count(mock_conn)
        assert count == len(CORE_COMMANDS)

    @patch("elle.daemon.manvault.indexer.get_doc")
    def test_get_seeded_count_partial(self, mock_get_doc):
        """Some docs present, some not."""
        call_count = [0]

        def side_effect(conn, name, section, lang):
            call_count[0] += 1
            if call_count[0] <= 5:
                return ManDoc(
                    id=call_count[0],
                    name=name,
                    section=section,
                    lang=lang,
                    source_path="/x",
                    sha256="abc",
                    text="text",
                    updated_at=datetime.now(),
                )
            return None

        mock_get_doc.side_effect = side_effect
        mock_conn = MagicMock()
        count = get_seeded_count(mock_conn)
        assert count == 5

    @patch("elle.daemon.manvault.indexer.get_conn")
    @patch("elle.daemon.manvault.indexer.get_doc", return_value=None)
    def test_get_seeded_count_no_conn(self, mock_get_doc, mock_get_conn):
        mock_ctx = MagicMock()
        mock_inner_conn = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_inner_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_ctx

        count = get_seeded_count(conn=None)
        assert count == 0
        mock_get_conn.assert_called_once_with(schema=PG_SCHEMA)


# ---------------------------------------------------------------------------
# is_core_seeded (fully mocked)
# ---------------------------------------------------------------------------


class TestIsCoreSeedded:
    @patch("elle.daemon.manvault.indexer.get_seeded_count", return_value=0)
    def test_is_core_seeded_false(self, mock_count):
        assert is_core_seeded(MagicMock()) is False

    @patch("elle.daemon.manvault.indexer.get_seeded_count", return_value=999)
    def test_is_core_seeded_true(self, mock_count):
        assert is_core_seeded(MagicMock()) is True

    @patch("elle.daemon.manvault.indexer.get_seeded_count")
    def test_is_core_seeded_threshold(self, mock_count):
        """Test exact threshold boundary."""
        half = int(len(CORE_COMMANDS) * 0.5)
        mock_count.return_value = half
        # At exactly threshold should be True
        assert is_core_seeded(MagicMock(), threshold=0.5) is True

    @patch("elle.daemon.manvault.indexer.get_seeded_count")
    def test_is_core_seeded_below_threshold(self, mock_count):
        half = int(len(CORE_COMMANDS) * 0.5)
        mock_count.return_value = half - 1
        assert is_core_seeded(MagicMock(), threshold=0.5) is False

    @patch("elle.daemon.manvault.indexer.get_seeded_count")
    def test_is_core_seeded_custom_threshold(self, mock_count):
        mock_count.return_value = len(CORE_COMMANDS)
        assert is_core_seeded(MagicMock(), threshold=1.0) is True

    @patch("elle.daemon.manvault.indexer.get_seeded_count")
    def test_is_core_seeded_zero_threshold(self, mock_count):
        mock_count.return_value = 0
        assert is_core_seeded(MagicMock(), threshold=0.0) is True

    @patch("elle.daemon.manvault.indexer.get_seeded_count", return_value=0)
    def test_is_core_seeded_no_conn(self, mock_count):
        """When conn=None, get_seeded_count is called with None."""
        result = is_core_seeded(conn=None)
        mock_count.assert_called_once_with(None)
        assert result is False
