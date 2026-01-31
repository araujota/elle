from __future__ import annotations

from unittest.mock import MagicMock, patch

from elle.daemon.manvault.indexer import (
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
from elle.daemon.manvault.models import ManDiscoveryItem

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


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------


class TestNormalizeText:
    def test_backspace_removal(self):
        # Bold: X^HX  (man page bold formatting)
        text = "B\bBo\bold\bld"
        result = normalize_text(text)
        assert "\b" not in result
        # After removing .\b pairs: B\bB->B, o\bo->o, l\bl->l, d\bl->l
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
        mock_col.communicate.return_value = (b"LS(1)\n\nNAME\n ls - list\n", b"")
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
        import subprocess as real_sub

        mock_man = MagicMock()
        mock_man.stdout = MagicMock()
        mock_man.kill = MagicMock()

        mock_col = MagicMock()
        mock_col.communicate.side_effect = real_sub.TimeoutExpired(cmd="col", timeout=5)
        mock_col.kill = MagicMock()

        mock_popen.side_effect = [mock_man, mock_col]
        result = render_manpage("ls", "1")
        assert result is None

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


# ---------------------------------------------------------------------------
# index_page (with mocked render)
# ---------------------------------------------------------------------------


class TestIndexPage:
    @patch("elle.daemon.manvault.indexer.render_manpage")
    def test_index_page_success(self, mock_render, manvault_conn):
        mock_render.return_value = "NAME\nls - list contents\n\nDESCRIPTION\nLists directory."
        conn = manvault_conn
        result = index_page("ls", "1", "en", conn=conn)
        assert result is True

    @patch("elle.daemon.manvault.indexer.render_manpage")
    def test_index_page_no_text(self, mock_render, manvault_conn):
        mock_render.return_value = None
        conn = manvault_conn
        result = index_page("nope", "1", "en", conn=conn)
        assert result is False

    @patch("elle.daemon.manvault.indexer.render_manpage")
    def test_index_page_skip_unchanged(self, mock_render, manvault_conn):
        text = "NAME\nls - list contents"
        mock_render.return_value = text
        conn = manvault_conn
        assert index_page("ls", "1", "en", conn=conn) is True
        assert index_page("ls", "1", "en", conn=conn) is True


# ---------------------------------------------------------------------------
# index_all
# ---------------------------------------------------------------------------


class TestIndexAll:
    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    @patch("elle.daemon.manvault.indexer.index_page")
    def test_index_all(self, mock_index, mock_discover, manvault_conn):
        mock_discover.return_value = [
            ManDiscoveryItem(name="ls", section="1", lang="en", source_path="/usr/share/man/man1/ls.1.gz", mtime=1.0),
            ManDiscoveryItem(name="cp", section="1", lang="en", source_path="/usr/share/man/man1/cp.1.gz", mtime=1.0),
        ]
        mock_index.return_value = True
        conn = manvault_conn
        count = index_all(conn=conn)
        assert count == 2

    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    @patch("elle.daemon.manvault.indexer.index_page")
    def test_index_all_with_failure(self, mock_index, mock_discover, manvault_conn):
        mock_discover.return_value = [
            ManDiscoveryItem(name="ls", section="1", lang="en", source_path="/x", mtime=1.0),
        ]
        mock_index.side_effect = RuntimeError("fail")
        conn = manvault_conn
        count = index_all(conn=conn)
        assert count == 0


# ---------------------------------------------------------------------------
# index_incremental
# ---------------------------------------------------------------------------


class TestIndexIncremental:
    @patch("elle.daemon.manvault.indexer.discover_man_pages")
    @patch("elle.daemon.manvault.indexer.render_manpage")
    @patch("elle.daemon.manvault.indexer.index_page")
    def test_incremental_new_page(self, mock_idx, mock_render, mock_discover, manvault_conn):
        mock_discover.return_value = [
            ManDiscoveryItem(name="ls", section="1", lang="en", source_path="/x", mtime=1.0),
        ]
        mock_render.return_value = "NAME\nls - list"
        mock_idx.return_value = True

        conn = manvault_conn
        added, updated = index_incremental(conn=conn)
        assert added == 1
        assert updated == 0


# ---------------------------------------------------------------------------
# seed_core_commands / get_seeded_count / is_core_seeded
# ---------------------------------------------------------------------------


class TestCoreSeed:
    @patch("elle.daemon.manvault.indexer.render_manpage")
    def test_seed_core_commands(self, mock_render, manvault_conn):
        mock_render.return_value = None  # All fail = 0 seeded
        conn = manvault_conn
        count = seed_core_commands(conn=conn)
        assert count == 0

    @patch("elle.daemon.manvault.indexer.get_doc")
    def test_get_seeded_count(self, mock_get_doc, manvault_conn):
        mock_get_doc.return_value = None
        conn = manvault_conn
        count = get_seeded_count(conn)
        assert count == 0

    @patch("elle.daemon.manvault.indexer.get_seeded_count")
    def test_is_core_seeded_false(self, mock_count, manvault_conn):
        mock_count.return_value = 0
        conn = manvault_conn
        assert is_core_seeded(conn) is False

    @patch("elle.daemon.manvault.indexer.get_seeded_count")
    def test_is_core_seeded_true(self, mock_count, manvault_conn):
        mock_count.return_value = 999
        conn = manvault_conn
        assert is_core_seeded(conn) is True

    @patch("elle.daemon.manvault.indexer.render_manpage")
    def test_seed_with_progress(self, mock_render, manvault_conn):
        mock_render.return_value = None
        conn = manvault_conn
        progress_calls = []
        seed_core_commands(conn=conn, progress_callback=lambda c, t: progress_calls.append((c, t)))
        assert len(progress_calls) > 0
