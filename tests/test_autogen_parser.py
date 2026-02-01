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


# =============================================================================
# Additional coverage tests (lines 42-56, 68-108, 158, 180, 229, 233, 296,
# 365, 367, 375, 410-411, 415, 420, 435, 440, 477)
# =============================================================================

from unittest.mock import MagicMock, mock_open

from elle.capabilities.autogen.parser import read_man_file, read_man_page


class TestReadManPage:
    """Tests for read_man_page covering lines 42-56."""

    @patch("elle.capabilities.autogen.parser.subprocess.run")
    def test_read_man_page_success(self, mock_run):
        """Test successful man page reading (lines 42-52)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NAME\n       ls - list directory contents\n",
        )
        result = read_man_page("ls")
        assert result is not None
        assert "ls" in result

    @patch("elle.capabilities.autogen.parser.subprocess.run")
    def test_read_man_page_failure_returncode(self, mock_run):
        """Test man page reading with non-zero return code (line 56)."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = read_man_page("nonexistent_cmd")
        assert result is None

    @patch("elle.capabilities.autogen.parser.subprocess.run")
    def test_read_man_page_empty_stdout(self, mock_run):
        """Test man page reading with empty stdout (line 51 false branch, line 56)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="   ")
        result = read_man_page("nonexistent_cmd")
        assert result is None

    @patch("elle.capabilities.autogen.parser.subprocess.run")
    def test_read_man_page_exception(self, mock_run):
        """Test man page reading with subprocess exception (lines 53-54)."""
        mock_run.side_effect = OSError("command not found")
        result = read_man_page("broken_cmd")
        assert result is None


class TestReadManFile:
    """Tests for read_man_file covering lines 68-108."""

    def test_read_man_file_not_exists(self):
        """Test reading non-existent file (lines 68-71)."""
        result = read_man_file("/tmp/nonexistent_man_file_12345.1")
        assert result is None

    @patch("builtins.open", mock_open(read_data="This is a plain text man page."))
    @patch("elle.capabilities.autogen.parser.Path")
    def test_read_man_file_plain_text(self, mock_path):
        """Test reading plain text man file (lines 78-80, 103)."""
        mock_path.return_value.exists.return_value = True
        result = read_man_file("/tmp/somefile.1")
        assert result == "This is a plain text man page."

    @patch("elle.capabilities.autogen.parser.Path")
    def test_read_man_file_gz(self, mock_path):
        """Test reading gzipped man file (lines 73-77)."""
        import gzip
        import tempfile

        mock_path.return_value.exists.return_value = True
        # Create a real gzip file
        with tempfile.NamedTemporaryFile(suffix=".gz", delete=False) as f:
            gz_path = f.name
        with gzip.open(gz_path, "wt") as f:
            f.write("This is compressed content.")
        result = read_man_file(gz_path)
        assert result == "This is compressed content."
        import os

        os.unlink(gz_path)

    @patch("elle.capabilities.autogen.parser.subprocess.run")
    @patch("builtins.open", mock_open(read_data=".TH TEST 1\n.SH NAME\ntest - a test"))
    @patch("elle.capabilities.autogen.parser.Path")
    def test_read_man_file_groff_source(self, mock_path, mock_run):
        """Test reading groff source man file (lines 83-101)."""
        mock_path.return_value.exists.return_value = True
        # First call: groff returns formatted output; second call: col -b returns cleaned output
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="TEST(1)  formatted output"),
            MagicMock(returncode=0, stdout="TEST(1)  cleaned output"),
        ]
        result = read_man_file("/tmp/test.1")
        assert result == "TEST(1)  cleaned output"

    @patch("elle.capabilities.autogen.parser.subprocess.run")
    @patch("builtins.open", mock_open(read_data=".TH TEST 1\n.SH NAME\ntest"))
    @patch("elle.capabilities.autogen.parser.Path")
    def test_read_man_file_groff_fail(self, mock_path, mock_run):
        """Test groff failure falls through to raw content (line 91 false, 103)."""
        mock_path.return_value.exists.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = read_man_file("/tmp/test.1")
        assert result == ".TH TEST 1\n.SH NAME\ntest"

    @patch("elle.capabilities.autogen.parser.subprocess.run")
    @patch("builtins.open", mock_open(read_data=".TH TEST 1\n.SH NAME\ntest"))
    @patch("elle.capabilities.autogen.parser.Path")
    def test_read_man_file_col_fail(self, mock_path, mock_run):
        """Test col -b failure falls through to raw content (line 100 false, 103)."""
        mock_path.return_value.exists.return_value = True
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="formatted"),
            MagicMock(returncode=1, stdout=""),
        ]
        result = read_man_file("/tmp/test.1")
        assert result == ".TH TEST 1\n.SH NAME\ntest"

    @patch("elle.capabilities.autogen.parser.Path")
    def test_read_man_file_exception(self, mock_path):
        """Test exception handling in read_man_file (lines 105-108)."""
        mock_path.return_value.exists.side_effect = PermissionError("denied")
        result = read_man_file("/tmp/test.1")
        assert result is None


class TestParseSynopsisEdgeCases:
    """Additional tests for parse_synopsis covering lines 158, 180, 181."""

    def test_parse_synopsis_empty_text(self):
        """Test empty synopsis returns None (line 158)."""
        result = parse_synopsis("", "cmd")
        assert result is None

    def test_parse_synopsis_subcommand_starts_with_dash(self):
        """Test synopsis where match starts with dash (line 180 false branch)."""
        text = "myapp -verbose"
        result = parse_synopsis(text, "myapp")
        assert result is not None
        # -verbose starts with dash, so not treated as subcommand
        assert len(result.subcommands) == 0

    def test_parse_synopsis_subcommand_is_uppercase_keyword(self):
        """Test synopsis where potential subcommand is a known keyword (line 181 false)."""
        text = "myapp FILE"
        result = parse_synopsis(text, "myapp")
        assert result is not None
        # FILE is uppercase so isupper() returns True, not a subcommand

    def test_parse_synopsis_subcommand_is_options_keyword(self):
        """Test synopsis where subcommand matches excluded keyword (line 181)."""
        text = "myapp OPTIONS something"
        result = parse_synopsis(text, "myapp")
        assert result is not None

    def test_parse_synopsis_no_options(self):
        """Test synopsis without OPTIONS marker."""
        text = "myapp subcommand <arg>"
        result = parse_synopsis(text, "myapp")
        assert result is not None
        assert result.has_options is False
        assert "subcommand" in result.subcommands

    def test_parse_synopsis_bracket_options(self):
        """Test synopsis with [-f] style options."""
        text = "myapp [-f] [-q] <input>"
        result = parse_synopsis(text, "myapp")
        assert result is not None
        assert result.has_options is True

    def test_parse_synopsis_angle_bracket_args(self):
        """Test synopsis extracting angle bracket positional args."""
        text = "myapp [OPTIONS] <input> <output>"
        result = parse_synopsis(text, "myapp")
        assert result is not None
        assert "input" in result.positional_args
        assert "output" in result.positional_args


class TestInferFlagTypeEdgeCases:
    """Additional tests for infer_flag_type covering lines 229, 233."""

    def test_infer_int_from_description(self):
        """Test inferring int from description keywords (line 229)."""
        result = infer_flag_type("The number of retries", "VALUE")
        assert result == FlagType.INT

    def test_infer_int_from_description_integer(self):
        """Test inferring int from 'integer' in description (line 229)."""
        result = infer_flag_type("Specify an integer limit", "VALUE")
        assert result == FlagType.INT

    def test_infer_int_from_description_count(self):
        """Test inferring int from 'count' in description (line 229)."""
        result = infer_flag_type("Set the count of items", "VALUE")
        assert result == FlagType.INT

    def test_infer_list_type_multiple(self):
        """Test inferring list type from 'multiple' keyword (line 233)."""
        result = infer_flag_type("Can be specified multiple times", "VALUE")
        assert result == FlagType.LIST

    def test_infer_list_type_repeat(self):
        """Test inferring list type from 'repeat' keyword (line 233)."""
        result = infer_flag_type("Repeat this flag for each item", "VALUE")
        assert result == FlagType.LIST


class TestParseOptionsEdgeCases:
    """Additional tests for parse_options covering line 296 (duplicate long skip)."""

    def test_parse_options_skips_duplicate_long(self):
        """Test that long-only options already found as short+long are skipped (line 296)."""
        text = """
       -v, --verbose          Enable verbose mode
       --verbose              Enable verbose mode
"""
        flags = parse_options(text)
        # --verbose should appear only once (the short+long version)
        verbose_flags = [f for f in flags if f.name == "verbose"]
        assert len(verbose_flags) == 1
        assert verbose_flags[0].short == "-v"


class TestParseExamplesEdgeCases:
    """Additional tests for parse_examples covering lines 365, 367, 375."""

    def test_parse_examples_description_starts_with_dash(self):
        """Test example where description line starts with dash (line 365)."""
        text = """
       $ ls -la
       -this line starts with a dash
       This is continuation
"""
        examples = parse_examples(text)
        assert len(examples) >= 1
        # The dash line should break the description loop
        ex = examples[0]
        assert "ls -la" in ex.command
        assert "-this" not in ex.description

    def test_parse_examples_empty_command(self):
        """Test example line with just $ and nothing else (line 367->344)."""
        text = """
       $
       Some description

       $ actual-command
       Actual description
"""
        examples = parse_examples(text)
        # Empty $ should not produce an example
        valid = [e for e in examples if e.command]
        assert len(valid) >= 1
        assert any("actual-command" in e.command for e in valid)

    def test_parse_examples_non_command_line(self):
        """Test example parsing skips non-command lines (line 375)."""
        text = """
       This is just a description line
       Not a command at all

       $ real-command arg1
       Description of real command
"""
        examples = parse_examples(text)
        assert len(examples) >= 1
        assert any("real-command" in e.command for e in examples)

    def test_parse_examples_hash_prefix(self):
        """Test example with # prefix."""
        text = """
       # sudo apt install foo
       Install the foo package
"""
        examples = parse_examples(text)
        assert len(examples) >= 1
        assert any("apt install foo" in e.command for e in examples)

    def test_parse_examples_consecutive_commands(self):
        """Test consecutive commands without descriptions (line 367->344 loop)."""
        text = """
       $ cmd1
       $ cmd2
"""
        examples = parse_examples(text)
        assert len(examples) >= 2


class TestParseManPageEdgeCases:
    """Additional tests for parse_man_page covering lines 410-411, 415, 420, 435, 440, 477."""

    @patch("elle.capabilities.autogen.parser.read_man_page")
    def test_parse_man_page_section_from_name(self, mock_read):
        """Test man section detection from NAME section (lines 410-411)."""
        mock_read.return_value = """NAME
       mycmd(8) - some daemon utility

DESCRIPTION
       Does things.
"""
        result = parse_man_page("mycmd")
        assert result is not None
        assert result.section == 8

    @patch("elle.capabilities.autogen.parser.read_man_page")
    def test_parse_man_page_no_synopsis(self, mock_read):
        """Test man page without SYNOPSIS section (line 415->419)."""
        mock_read.return_value = """NAME
       nocmd - a command without synopsis

DESCRIPTION
       This command has no synopsis section.
"""
        result = parse_man_page("nocmd")
        assert result is not None
        assert result.synopsis is None

    @patch("elle.capabilities.autogen.parser.read_man_page")
    def test_parse_man_page_no_description(self, mock_read):
        """Test man page without DESCRIPTION section (line 420->428)."""
        mock_read.return_value = """NAME
       nodesc - a command without description

SYNOPSIS
       nodesc [OPTIONS]
"""
        result = parse_man_page("nodesc")
        assert result is not None
        assert result.description == ""

    @patch("elle.capabilities.autogen.parser.read_man_page")
    def test_parse_man_page_no_examples(self, mock_read):
        """Test man page without EXAMPLES section (line 435->439)."""
        mock_read.return_value = """NAME
       noex - a command

SYNOPSIS
       noex [OPTIONS]

DESCRIPTION
       No examples here.
"""
        result = parse_man_page("noex")
        assert result is not None
        assert len(result.examples) == 0

    @patch("elle.capabilities.autogen.parser.read_man_page")
    def test_parse_man_page_no_see_also(self, mock_read):
        """Test man page without SEE ALSO section (line 440->446)."""
        mock_read.return_value = """NAME
       nosa - a command

SYNOPSIS
       nosa [OPTIONS]

DESCRIPTION
       No see also here.
"""
        result = parse_man_page("nosa")
        assert result is not None
        assert len(result.see_also) == 0

    @patch("elle.capabilities.autogen.parser.read_man_file")
    def test_parse_man_page_with_man_path(self, mock_read_file):
        """Test parse_man_page with man_path calls read_man_file (line 396)."""
        mock_read_file.return_value = """NAME
       pathcmd - command from file

SYNOPSIS
       pathcmd [OPTIONS]

DESCRIPTION
       Read from file path.
"""
        result = parse_man_page("pathcmd", man_path="/usr/share/man/man1/pathcmd.1")
        assert result is not None
        assert result.name == "pathcmd"
        mock_read_file.assert_called_once_with("/usr/share/man/man1/pathcmd.1")

    @patch("elle.capabilities.autogen.parser.read_man_page")
    def test_parse_man_page_description_with_double_newline(self, mock_read):
        """Test description truncation at double newline (line 423)."""
        mock_read.return_value = """NAME
       desctest - testing description

DESCRIPTION
       First paragraph of description.

       Second paragraph should not appear.

SYNOPSIS
       desctest [OPTIONS]
"""
        result = parse_man_page("desctest")
        assert result is not None
        assert "First paragraph" in result.description
        assert "Second paragraph" not in result.description

    @patch("elle.capabilities.autogen.parser.read_man_page")
    def test_parse_man_page_with_flags_section(self, mock_read):
        """Test parsing FLAGS section (line 429-431)."""
        mock_read.return_value = """NAME
       flagtest - testing flags

SYNOPSIS
       flagtest [OPTIONS]

FLAGS
       --debug              Enable debug mode
"""
        result = parse_man_page("flagtest")
        assert result is not None
        assert any(f.name == "debug" for f in result.flags)


class TestFlagExistsEdgeCases:
    """Additional tests for flag_exists covering line 477."""

    def test_flag_exists_by_long_with_dashes(self):
        """Test flag_exists matching long form with dashes (line 477)."""
        man_page = ParsedManPage(
            name="test",
            section=1,
            flags=(
                ParsedFlag(
                    name="no_preserve_root",
                    short=None,
                    long="--no-preserve-root",
                    flag_type=FlagType.BOOL,
                ),
            ),
        )
        assert flag_exists(man_page, "--no-preserve-root") is True

    def test_flag_exists_no_match_any_form(self):
        """Test flag_exists returns False when no match on any form (line 477 false)."""
        man_page = ParsedManPage(
            name="test",
            section=1,
            flags=(
                ParsedFlag(
                    name="verbose",
                    short="-v",
                    long="--verbose",
                    flag_type=FlagType.BOOL,
                ),
            ),
        )
        assert flag_exists(man_page, "--debug") is False

    def test_flag_exists_none_short_none_long(self):
        """Test flag_exists with flag that has no short or long form."""
        man_page = ParsedManPage(
            name="test",
            section=1,
            flags=(
                ParsedFlag(
                    name="special",
                    short=None,
                    long=None,
                    flag_type=FlagType.BOOL,
                ),
            ),
        )
        assert flag_exists(man_page, "special") is True
        assert flag_exists(man_page, "--other") is False

    def test_flag_exists_match_only_via_long_form(self):
        """Test flag_exists where only long form matches, not name or short (line 477 True)."""
        man_page = ParsedManPage(
            name="test",
            section=1,
            flags=(
                ParsedFlag(
                    name="alias_name",
                    short="-a",
                    long="--real-flag",
                    flag_type=FlagType.BOOL,
                ),
            ),
        )
        # Search for --real-flag: normalized is "real_flag"
        # flag.name is "alias_name" (no match at line 472)
        # flag.short is "-a", search is "--real-flag", lstrip("-") gives "a" vs "real-flag" (no match at 474)
        # flag.long is "--real-flag", lstrip("-").replace("-","_") is "real_flag" == "real_flag" (match at 477)
        assert flag_exists(man_page, "--real-flag") is True


class TestParseSynopsisExcludedKeywords:
    """Tests for parse_synopsis excluded keyword branch (line 181->185)."""

    def test_parse_synopsis_lowercase_dir_subcommand(self):
        """Test synopsis where subcommand after OPTIONS could be excluded but lowercase."""
        # Pattern matches the word after "myapp [OPTIONS] ", looking for lowercase
        # matches that happen to be in the exclusion list
        # Since the exclusion list uses uppercase ("FILE", "DIR", "NAME"),
        # lowercase variants won't match, but we test to ensure the branch is exercised
        text = "myapp [OPTIONS] dir <path>"
        result = parse_synopsis(text, "myapp")
        assert result is not None
        # "dir" is lowercase, not uppercase, so it passes isupper() is False
        # But "dir" != "DIR" in the exclusion list, so it becomes a subcommand
        assert "dir" in result.subcommands
