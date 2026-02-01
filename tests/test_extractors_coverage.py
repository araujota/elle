"""Additional coverage tests for extractors.py.

Targets uncovered lines:
- Lines 216, 221: FileManifest binary categorization and /etc/ config detection
- Line 238: Polkit action detection
- Lines 339-341: Bash completion parse failure handler
- Lines 507-509: Zsh completion parse failure handler
- Lines 518-564: Zsh completion result building and flag extraction
- Lines 610, 651-653: Man page extractor binary name lookup and error handler
- Line 815: Help output extractor no flags error
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elle.capabilities.autogen.intelligence.extractors import (
    BashCompletionExtractor,
    FileManifestExtractor,
    HelpOutputExtractor,
    ManPageExtractor,
    ZshCompletionExtractor,
)
from elle.capabilities.autogen.intelligence.models import (
    FileManifest,
)


# =========================================================================
# FileManifestExtractor: binary and config categorization (lines 216, 221, 238)
# =========================================================================


class TestFileManifestBinariesAndConfigs:
    """Cover binary detection (line 220-221) and /etc/ config detection (line 237-238)."""

    @pytest.fixture()
    def extractor(self) -> FileManifestExtractor:
        return FileManifestExtractor()

    def test_binary_categorized_when_is_file(self, extractor: FileManifestExtractor) -> None:
        """Binary in BINARY_DIRS is added when Path.is_file() returns True (line 220-221)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/usr/bin/mytool\n/usr/sbin/mydaemon\n"

        with patch("subprocess.run", return_value=mock_result):
            with patch.object(Path, "is_file", return_value=True):
                result = extractor.extract("mypkg")

        assert result.success is True
        assert result.manifest is not None
        assert "/usr/bin/mytool" in result.manifest.binaries
        assert "/usr/sbin/mydaemon" in result.manifest.binaries

    def test_binary_not_added_when_not_file(self, extractor: FileManifestExtractor) -> None:
        """Binary path that is not a file (directory) is skipped (line 220)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/usr/bin/\n"

        with patch("subprocess.run", return_value=mock_result):
            with patch.object(Path, "is_file", return_value=False):
                result = extractor.extract("mypkg")

        assert result.success is True
        assert result.manifest is not None
        assert len(result.manifest.binaries) == 0

    def test_etc_config_categorized_when_is_file(self, extractor: FileManifestExtractor) -> None:
        """Files under /etc/ are added as config_files when is_file() is True (line 237-238)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/etc/myapp/config.conf\n"

        with patch("subprocess.run", return_value=mock_result):
            with patch.object(Path, "is_file", return_value=True):
                result = extractor.extract("mypkg")

        assert result.success is True
        assert result.manifest is not None
        assert "/etc/myapp/config.conf" in result.manifest.config_files

    def test_polkit_action_detected(self, extractor: FileManifestExtractor) -> None:
        """Polkit action files are detected (line 240)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/usr/share/polkit-1/actions/org.example.policy\n"

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("mypkg")

        assert result.success is True
        assert result.manifest is not None
        assert len(result.manifest.polkit_actions) == 1


# =========================================================================
# BashCompletionExtractor: parse failure handler (lines 339-341)
# =========================================================================


class TestBashCompletionParseFailure:
    """Cover exception handling during bash completion file parsing."""

    @pytest.fixture()
    def extractor(self) -> BashCompletionExtractor:
        return BashCompletionExtractor()

    def test_parse_failure_continues_to_next_file(
        self, extractor: BashCompletionExtractor, tmp_path: Path
    ) -> None:
        """Exception reading one completion file should not block others (lines 339-341)."""
        # First file: will cause an exception
        bad_file = tmp_path / "bad_bash_comp"
        bad_file.write_text("valid content")

        # Second file: has valid flags
        good_file = tmp_path / "good_bash_comp"
        good_file.write_text('COMPREPLY=( $(compgen -W \'--help --version\' -- "$cur") )\n')

        manifest = FileManifest(completions=(str(bad_file), str(good_file)))

        # Make the first file's read_text raise an exception
        original_read_text = Path.read_text

        call_count = [0]

        def patched_read_text(self, *args, **kwargs):
            call_count[0] += 1
            if str(self) == str(bad_file) and call_count[0] <= 1:
                raise PermissionError("cannot read file")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", patched_read_text):
            result = extractor.extract("pkg", manifest=manifest)

        assert result.success is True
        flag_names = {f.flag for f in result.flags}
        assert "--help" in flag_names

    def test_all_files_fail_returns_error(
        self, extractor: BashCompletionExtractor, tmp_path: Path
    ) -> None:
        """When all completion files fail to parse, return error."""
        bad_file = tmp_path / "bad_bash_comp"
        bad_file.write_text("content")
        manifest = FileManifest(completions=(str(bad_file),))

        with patch.object(Path, "read_text", side_effect=OSError("fail")):
            result = extractor.extract("pkg", manifest=manifest)

        assert result.success is False


# =========================================================================
# ZshCompletionExtractor: parse failure and result building (lines 507-509, 518-564)
# =========================================================================


class TestZshCompletionParseCoverage:
    """Cover zsh completion parse failure and flag extraction paths."""

    @pytest.fixture()
    def extractor(self) -> ZshCompletionExtractor:
        return ZshCompletionExtractor()

    def test_parse_failure_continues(
        self, extractor: ZshCompletionExtractor, tmp_path: Path
    ) -> None:
        """Exception reading one zsh file continues to next (lines 507-509)."""
        bad_file = tmp_path / "bad_zsh_comp"
        bad_file.write_text("content")

        good_file = tmp_path / "good_zsh_comp"
        good_file.write_text(
            "_arguments \\\n"
            "    '--verbose[Enable verbose mode]' \\\n"
        )

        manifest = FileManifest(
            completions=(str(bad_file), str(good_file))
        )

        original_read_text = Path.read_text
        call_count = [0]

        def patched_read_text(self, *args, **kwargs):
            call_count[0] += 1
            if str(self) == str(bad_file) and call_count[0] <= 1:
                raise PermissionError("no access")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", patched_read_text):
            result = extractor.extract("pkg", manifest=manifest)

        # Should succeed from the good file
        assert result.source_name == "zsh_completion"

    def test_zsh_result_building_with_flags(
        self, extractor: ZshCompletionExtractor, tmp_path: Path
    ) -> None:
        """Cover ShellCompletions result construction (lines 518-530)."""
        comp_file = tmp_path / "zsh_pkg"
        comp_file.write_text(
            "_arguments \\\n"
            "    '--help[Show help message]' \\\n"
            "    '--output[Output file]:file:_files' \\\n"
        )

        # Use "zsh" in filename to pass the filter
        zsh_file = tmp_path / "zsh_complete"
        zsh_file.write_text(comp_file.read_text())

        manifest = FileManifest(completions=(str(zsh_file),))
        result = extractor.extract("pkg", manifest=manifest)

        if result.success:
            assert result.completions is not None
            assert result.completions.shell_type == "zsh"
            assert len(result.flags) >= 1
            flag_names = {f.flag for f in result.flags}
            assert "--help" in flag_names

    def test_zsh_flag_with_description_and_value(
        self, extractor: ZshCompletionExtractor, tmp_path: Path
    ) -> None:
        """Cover flag extraction with description and takes_value (lines 551-572)."""
        comp_file = tmp_path / "zsh_detailed"
        comp_file.write_text(
            "_arguments \\\n"
            "    '--config[Config file path]:config file:_files' \\\n"
            "    '--dry-run[Simulate only]' \\\n"
        )
        manifest = FileManifest(completions=(str(comp_file),))
        result = extractor.extract("pkg", manifest=manifest)

        if result.success:
            for flag in result.flags:
                if flag.flag == "--config":
                    assert flag.takes_value is True
                    assert "Config" in flag.description

    def test_zsh_no_flags_found(
        self, extractor: ZshCompletionExtractor, tmp_path: Path
    ) -> None:
        """Empty content with no flags returns error (line 511-516)."""
        comp_file = tmp_path / "zsh_empty"
        comp_file.write_text("# nothing here\n")
        manifest = FileManifest(completions=(str(comp_file),))
        result = extractor.extract("pkg", manifest=manifest)
        assert result.success is False
        assert "No flags" in (result.error or "")


# =========================================================================
# ManPageExtractor: binary name lookup and error (lines 610, 651-653)
# =========================================================================


class TestManPageExtractorCoverage:
    """Cover fallback binary name from manifest and exception handler."""

    @pytest.fixture()
    def extractor(self) -> ManPageExtractor:
        return ManPageExtractor()

    def test_uses_first_binary_when_no_name_match(
        self, extractor: ManPageExtractor
    ) -> None:
        """When no binary matches package name, use first binary (line 610)."""
        manifest = FileManifest(binaries=("/usr/bin/othertool",))

        with patch(
            "elle.capabilities.autogen.parser.parse_man_page",
            return_value=None,
        ) as mock_parse:
            result = extractor.extract("mypkg", manifest=manifest)
            # Should have called parse_man_page with "othertool" (first binary)
            mock_parse.assert_called_once_with("othertool")
            assert result.success is False
            assert "No man page" in (result.error or "")

    def test_uses_matching_binary_name(
        self, extractor: ManPageExtractor
    ) -> None:
        """When a binary matches the package name, it is used."""
        manifest = FileManifest(
            binaries=("/usr/bin/othertool", "/usr/bin/mypkg")
        )

        with patch(
            "elle.capabilities.autogen.parser.parse_man_page",
            return_value=None,
        ) as mock_parse:
            extractor.extract("mypkg", manifest=manifest)
            mock_parse.assert_called_once_with("mypkg")

    def test_generic_exception_handler(self, extractor: ManPageExtractor) -> None:
        """Generic exception is caught and returns error (lines 651-653)."""
        with patch(
            "elle.capabilities.autogen.parser.parse_man_page",
            side_effect=RuntimeError("unexpected crash"),
        ):
            result = extractor.extract("pkg")
            assert result.success is False
            assert "unexpected crash" in (result.error or "")

    def test_import_error_handler(self, extractor: ManPageExtractor) -> None:
        """ImportError returns 'not available' message (line 645-649)."""
        with patch.dict("sys.modules", {"elle.capabilities.autogen.parser": None}):
            result = extractor.extract("pkg")
            assert result.success is False
            assert "not available" in (result.error or "")


# =========================================================================
# HelpOutputExtractor: no flags error (line 814-819)
# =========================================================================


class TestHelpOutputNoFlagsCoverage:
    """Cover the path where help output exists but no flags are parsed."""

    @pytest.fixture()
    def extractor(self) -> HelpOutputExtractor:
        return HelpOutputExtractor()

    def test_help_text_with_no_parseable_flags(self, extractor: HelpOutputExtractor) -> None:
        """Help output with no matching flag patterns returns error (line 815)."""
        mock_result = MagicMock()
        mock_result.stdout = (
            "Usage: mytool [command]\n"
            "\n"
            "A tool that does things.\n"
            "Run 'mytool help' for more info.\n"
        )
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("mytool")
            assert result.success is False
            assert "No flags" in (result.error or "")

    def test_help_from_stderr_no_flags(self, extractor: HelpOutputExtractor) -> None:
        """Help from stderr with no flags returns error."""
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "Usage: tool\nNo options available.\n"

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("tool")
            assert result.success is False
            assert "No flags" in (result.error or "")
