"""Tests for autogen parser module."""

from __future__ import annotations

from unittest.mock import patch

from elle.capabilities.autogen.models import FlagType, ParsedFlag, ParsedManPage
from elle.capabilities.autogen.parser import (
    extract_sections,
    flag_exists,
    infer_flag_type,
    parse_examples,
    parse_man_page,
    parse_options,
    parse_synopsis,
)


class TestExtractSections:
    """Tests for extract_sections function."""

    def test_extract_basic_sections(self):
        """Test extracting basic sections."""
        text = """NAME
       ls - list directory contents

SYNOPSIS
       ls [OPTION]... [FILE]...

DESCRIPTION
       List information about the FILEs.

OPTIONS
       -a, --all
              do not ignore entries starting with .
"""
        sections = extract_sections(text)

        assert "NAME" in sections
        assert "SYNOPSIS" in sections
        assert "DESCRIPTION" in sections
        assert "OPTIONS" in sections

    def test_extract_empty_text(self):
        """Test with empty text."""
        sections = extract_sections("")
        assert sections == {}


class TestParseSynopsis:
    """Tests for parse_synopsis function."""

    def test_parse_simple_synopsis(self):
        """Test parsing simple synopsis."""
        text = "ls [OPTION]... [FILE]..."
        result = parse_synopsis(text, "ls")

        assert result is not None
        assert result.command == "ls"
        assert result.has_options is True

    def test_parse_synopsis_with_subcommand(self):
        """Test parsing synopsis with subcommand."""
        text = "systemctl [OPTIONS] restart UNIT..."
        result = parse_synopsis(text, "systemctl")

        assert result is not None
        assert result.command == "systemctl"

    def test_parse_synopsis_positional_args(self):
        """Test extracting positional arguments."""
        text = "cp [OPTION]... SOURCE DEST"
        result = parse_synopsis(text, "cp")

        assert result is not None
        assert "source" in result.positional_args or "dest" in result.positional_args


class TestInferFlagType:
    """Tests for infer_flag_type function."""

    def test_infer_bool(self):
        """Test inferring bool type."""
        result = infer_flag_type("Enable verbose mode", None)
        assert result == FlagType.BOOL

    def test_infer_path(self):
        """Test inferring path type."""
        result = infer_flag_type("Output file", "FILE")
        assert result == FlagType.PATH

        result = infer_flag_type("Output directory", "DIR")
        assert result == FlagType.PATH

    def test_infer_int(self):
        """Test inferring int type."""
        result = infer_flag_type("Number of lines", "NUM")
        assert result == FlagType.INT

        result = infer_flag_type("Set count", "COUNT")
        assert result == FlagType.INT

    def test_infer_string(self):
        """Test inferring string type."""
        # Use a metavar that won't match path patterns (file, path, dir)
        # or INT patterns (num, count, n, seconds, size, bytes)
        result = infer_flag_type("Set label", "TEXT")
        assert result == FlagType.STRING


class TestParseOptions:
    """Tests for parse_options function."""

    def test_parse_short_long_option(self):
        """Test parsing short and long options."""
        text = """
       -v, --verbose
              increase verbosity
"""
        flags = parse_options(text)

        assert len(flags) >= 1
        flag = flags[0]
        assert flag.short == "-v"
        assert flag.long == "--verbose"

    def test_parse_option_with_value(self):
        """Test parsing option with value."""
        text = """
       -o, --output=FILE
              write output to FILE
"""
        flags = parse_options(text)

        assert len(flags) >= 1
        flag = flags[0]
        assert flag.metavar == "FILE"
        assert flag.flag_type == FlagType.PATH

    def test_parse_long_only_option(self):
        """Test parsing long-only option."""
        text = """
       --no-preserve-root
              do not treat '/' specially
"""
        flags = parse_options(text)

        # Should find the long-only option
        assert any(f.long == "--no-preserve-root" for f in flags)


class TestParseExamples:
    """Tests for parse_examples function."""

    def test_parse_examples(self):
        """Test parsing examples."""
        text = """
       $ ls -la
       List all files in long format

       $ ls *.txt
       List text files
"""
        examples = parse_examples(text)

        assert len(examples) >= 2
        assert any("ls -la" in ex.command for ex in examples)


class TestFlagExists:
    """Tests for flag_exists function."""

    def test_flag_exists_by_name(self):
        """Test checking flag by name."""
        man_page = ParsedManPage(
            name="test",
            section=1,
            flags=(ParsedFlag(name="verbose", short="-v", long="--verbose", flag_type=FlagType.BOOL),),
        )

        assert flag_exists(man_page, "verbose") is True
        assert flag_exists(man_page, "nonexistent") is False

    def test_flag_exists_by_short(self):
        """Test checking flag by short form."""
        man_page = ParsedManPage(
            name="test",
            section=1,
            flags=(ParsedFlag(name="verbose", short="-v", long="--verbose", flag_type=FlagType.BOOL),),
        )

        assert flag_exists(man_page, "-v") is True

    def test_flag_exists_by_long(self):
        """Test checking flag by long form."""
        man_page = ParsedManPage(
            name="test",
            section=1,
            flags=(ParsedFlag(name="verbose", short="-v", long="--verbose", flag_type=FlagType.BOOL),),
        )

        assert flag_exists(man_page, "--verbose") is True


class TestParseManPage:
    """Tests for parse_man_page function."""

    @patch("elle.capabilities.autogen.parser.read_man_page")
    def test_parse_man_page(self, mock_read):
        """Test parsing complete man page."""
        mock_read.return_value = """LS(1)                          User Commands                         LS(1)

NAME
       ls - list directory contents

SYNOPSIS
       ls [OPTION]... [FILE]...

DESCRIPTION
       List information about the FILEs (the current directory by default).

OPTIONS
       -a, --all
              do not ignore entries starting with .

       -l     use a long listing format

EXAMPLES
       $ ls -la
       List all files

SEE ALSO
       dir(1), vdir(1)
"""

        result = parse_man_page("ls")

        assert result is not None
        assert result.name == "ls"
        assert result.section == 1
        assert len(result.flags) > 0
        assert "dir" in result.see_also

    @patch("elle.capabilities.autogen.parser.read_man_page")
    def test_parse_man_page_not_found(self, mock_read):
        """Test parsing nonexistent man page."""
        mock_read.return_value = None

        result = parse_man_page("nonexistent")
        assert result is None
