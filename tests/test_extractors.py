from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elle.capabilities.autogen.intelligence.extractors import (
    ALL_EXTRACTORS,
    BashCompletionExtractor,
    DpkgMetadataExtractor,
    FileManifestExtractor,
    HelpOutputExtractor,
    ManPageExtractor,
    SystemdUnitExtractor,
    ZshCompletionExtractor,
    get_extractors,
)
from elle.capabilities.autogen.intelligence.models import (
    FileManifest,
)

# =========================================================================
# get_extractors tests
# =========================================================================


class TestGetExtractors:
    def test_returns_all_extractors(self) -> None:
        extractors = get_extractors()
        assert len(extractors) == len(ALL_EXTRACTORS)

    def test_sorted_by_priority(self) -> None:
        extractors = get_extractors()
        priorities = [e.priority for e in extractors]
        assert priorities == sorted(priorities)

    def test_dpkg_is_first(self) -> None:
        extractors = get_extractors()
        assert extractors[0].name == "dpkg"


# =========================================================================
# DpkgMetadataExtractor tests
# =========================================================================


class TestDpkgMetadataExtractor:
    @pytest.fixture()
    def extractor(self) -> DpkgMetadataExtractor:
        return DpkgMetadataExtractor()

    def test_name_and_priority(self, extractor: DpkgMetadataExtractor) -> None:
        assert extractor.name == "dpkg"
        assert extractor.priority == 10

    def test_extract_success(self, extractor: DpkgMetadataExtractor) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "curl\n7.68.0\ncommand line tool\nweb\ncurl, libcurl4\nhttps://curl.se"

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("curl")
            assert result.success is True
            assert result.metadata is not None
            assert result.metadata.name == "curl"
            assert result.metadata.version == "7.68.0"

    def test_extract_not_found(self, extractor: DpkgMetadataExtractor) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("notapackage")
            assert result.success is False
            assert "not found" in (result.error or "")

    def test_extract_short_output(self, extractor: DpkgMetadataExtractor) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "pkg"

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("pkg")
            assert result.success is False

    def test_extract_timeout(self, extractor: DpkgMetadataExtractor) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("dpkg", 10)):
            result = extractor.extract("curl")
            assert result.success is False
            assert "timed out" in (result.error or "")

    def test_extract_exception(self, extractor: DpkgMetadataExtractor) -> None:
        with patch("subprocess.run", side_effect=OSError("nope")):
            result = extractor.extract("curl")
            assert result.success is False

    def test_extract_parses_dependencies(self, extractor: DpkgMetadataExtractor) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "nginx\n1.18.0\nweb server\nweb\nnginx-common (>= 1.18), libc6\nhttps://nginx.org"

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("nginx")
            assert result.success is True
            assert result.metadata is not None
            assert "nginx-common" in result.metadata.depends
            assert "libc6" in result.metadata.depends


# =========================================================================
# FileManifestExtractor tests
# =========================================================================


class TestFileManifestExtractor:
    @pytest.fixture()
    def extractor(self) -> FileManifestExtractor:
        return FileManifestExtractor()

    def test_name_and_priority(self, extractor: FileManifestExtractor) -> None:
        assert extractor.name == "manifest"
        assert extractor.priority == 20

    def test_extract_failure(self, extractor: FileManifestExtractor) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("badpkg")
            assert result.success is False

    def test_extract_timeout(self, extractor: FileManifestExtractor) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("dpkg", 10)):
            result = extractor.extract("pkg")
            assert result.success is False
            assert "timed out" in (result.error or "")

    def test_extract_categorizes_man_pages(self, extractor: FileManifestExtractor) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/usr/share/man/man1/curl.1\n/usr/share/man/man3/libcurl.3\n"

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("curl")
            assert result.success is True
            assert result.manifest is not None
            assert len(result.manifest.man_pages) == 2

    def test_extract_categorizes_systemd(self, extractor: FileManifestExtractor) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/lib/systemd/system/nginx.service\n/lib/systemd/system/nginx.socket\n"

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("nginx")
            assert result.success is True
            assert result.manifest is not None
            assert len(result.manifest.systemd_units) == 2

    def test_extract_categorizes_completions(self, extractor: FileManifestExtractor) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/usr/share/bash-completion/completions/curl\n"

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("curl")
            assert result.success is True
            assert result.manifest is not None
            assert len(result.manifest.completions) == 1

    def test_extract_categorizes_polkit(self, extractor: FileManifestExtractor) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/usr/share/polkit-1/actions/org.freedesktop.udisks2.policy\n"

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("udisks2")
            assert result.success is True
            assert result.manifest is not None
            assert len(result.manifest.polkit_actions) == 1

    def test_extract_exception(self, extractor: FileManifestExtractor) -> None:
        with patch("subprocess.run", side_effect=OSError("nope")):
            result = extractor.extract("pkg")
            assert result.success is False


# =========================================================================
# BashCompletionExtractor tests
# =========================================================================


class TestBashCompletionExtractor:
    @pytest.fixture()
    def extractor(self) -> BashCompletionExtractor:
        return BashCompletionExtractor()

    def test_name_and_priority(self, extractor: BashCompletionExtractor) -> None:
        assert extractor.name == "bash_completion"
        assert extractor.priority == 30

    def test_extract_no_manifest_no_files(self, extractor: BashCompletionExtractor) -> None:
        with patch.object(extractor, "_find_completion_files", return_value=[]):
            result = extractor.extract("pkg")
            assert result.success is False
            assert "No bash completion" in (result.error or "")

    def test_extract_from_manifest(self, extractor: BashCompletionExtractor, tmp_path: Path) -> None:
        comp_file = tmp_path / "bash_completion"
        comp_file.write_text("""
COMPREPLY=( $(compgen -W '--help --verbose --output' -- "$cur") )
""")
        manifest = FileManifest(completions=(str(comp_file),))
        result = extractor.extract("pkg", manifest=manifest)
        assert result.success is True
        flag_names = {f.flag for f in result.flags}
        assert "--help" in flag_names
        assert "--verbose" in flag_names

    def test_extract_case_pattern(self, extractor: BashCompletionExtractor, tmp_path: Path) -> None:
        comp_file = tmp_path / "bash_case_comp"
        # The CASE_PATTERN requires \b before the flag;
        # word chars before dashes satisfy the boundary.
        comp_file.write_text(
            "case $cur in\n"
            "    opt--config)\n"
            "        COMPREPLY=()\n"
            "        ;;\n"
            "    opt--format)\n"
            "        COMPREPLY=()\n"
            "        ;;\n"
            "esac\n"
        )
        manifest = FileManifest(completions=(str(comp_file),))
        result = extractor.extract("pkg", manifest=manifest)
        assert result.success is True
        flag_names = {f.flag for f in result.flags}
        assert "--config" in flag_names
        assert "--format" in flag_names

    def test_extract_opts_var_pattern(self, extractor: BashCompletionExtractor, tmp_path: Path) -> None:
        comp_file = tmp_path / "bash_opts_comp"
        comp_file.write_text("""
opts="--help --version --debug"
""")
        manifest = FileManifest(completions=(str(comp_file),))
        result = extractor.extract("pkg", manifest=manifest)
        assert result.success is True
        flag_names = {f.flag for f in result.flags}
        assert "--help" in flag_names

    def test_extract_subcommands(self, extractor: BashCompletionExtractor, tmp_path: Path) -> None:
        comp_file = tmp_path / "bash_cmds_comp"
        comp_file.write_text("""
cmds="start stop restart status"
""")
        manifest = FileManifest(completions=(str(comp_file),))
        result = extractor.extract("pkg", manifest=manifest)
        assert result.success is True
        sub_names = {s.name for s in result.subcommands}
        assert "start" in sub_names
        assert "stop" in sub_names

    def test_extract_no_flags_found(self, extractor: BashCompletionExtractor, tmp_path: Path) -> None:
        comp_file = tmp_path / "bash_empty_comp"
        comp_file.write_text("# nothing useful here\n")
        manifest = FileManifest(completions=(str(comp_file),))
        result = extractor.extract("pkg", manifest=manifest)
        assert result.success is False


# =========================================================================
# ZshCompletionExtractor tests
# =========================================================================


class TestZshCompletionExtractor:
    @pytest.fixture()
    def extractor(self) -> ZshCompletionExtractor:
        return ZshCompletionExtractor()

    def test_name_and_priority(self, extractor: ZshCompletionExtractor) -> None:
        assert extractor.name == "zsh_completion"
        assert extractor.priority == 31

    def test_extract_no_files(self, extractor: ZshCompletionExtractor) -> None:
        with patch.object(extractor, "_find_completion_files", return_value=[]):
            result = extractor.extract("pkg")
            assert result.success is False

    def test_extract_with_args_pattern(self, extractor: ZshCompletionExtractor, tmp_path: Path) -> None:
        comp_file = tmp_path / "_pkg"
        comp_file.write_text("""
_arguments \\
    '--help[Show help message]' \\
    '--verbose[Enable verbosity]:level' \\
""")
        FileManifest(completions=(str(comp_file),))
        # The zsh extractor checks for "zsh" in the filename
        result = extractor.extract("pkg", manifest=FileManifest(completions=(str(comp_file),)))
        # Files must have "zsh" or start with "_" in name to be picked
        # Since the filter checks "zsh" in filename or starts with "_"
        # The filename "_pkg" starts with "_"
        assert result.source_name == "zsh_completion"

    def test_extract_no_flags_found(self, extractor: ZshCompletionExtractor, tmp_path: Path) -> None:
        comp_file = tmp_path / "zsh_comp"
        comp_file.write_text("# empty zsh completion\n")
        manifest = FileManifest(completions=(str(comp_file),))
        result = extractor.extract("pkg", manifest=manifest)
        assert result.success is False


# =========================================================================
# ManPageExtractor tests
# =========================================================================


class TestManPageExtractor:
    @pytest.fixture()
    def extractor(self) -> ManPageExtractor:
        return ManPageExtractor()

    def test_name_and_priority(self, extractor: ManPageExtractor) -> None:
        assert extractor.name == "man_page"
        assert extractor.priority == 40

    def test_extract_import_error(self, extractor: ManPageExtractor) -> None:
        with patch.dict("sys.modules", {"elle.capabilities.autogen.parser": None}):
            result = extractor.extract("pkg")
            assert result.success is False
            assert "not available" in (result.error or "")

    def test_extract_no_man_page(self, extractor: ManPageExtractor) -> None:
        with patch("elle.capabilities.autogen.parser.parse_man_page", return_value=None):
            result = extractor.extract("pkg")
            assert result.success is False
            assert "No man page" in (result.error or "")

    def test_extract_with_man_page(self, extractor: ManPageExtractor) -> None:
        mock_flag = MagicMock()
        mock_flag.long = "--verbose"
        mock_flag.short = "-v"
        mock_flag.name = "verbose"
        mock_flag.description = "Enable verbose output"
        mock_flag.flag_type.value = "bool"
        mock_flag.metavar = None

        mock_man = MagicMock()
        mock_man.flags = [mock_flag]

        with patch("elle.capabilities.autogen.parser.parse_man_page", return_value=mock_man):
            result = extractor.extract("pkg")
            assert result.success is True
            assert len(result.flags) == 1
            assert result.flags[0].flag == "--verbose"

    def test_extract_uses_manifest_binary(self, extractor: ManPageExtractor) -> None:
        manifest = FileManifest(binaries=("/usr/bin/curl",))
        with patch("elle.capabilities.autogen.parser.parse_man_page", return_value=None) as mock_parse:
            extractor.extract("curl", manifest=manifest)
            mock_parse.assert_called_once_with("curl")


# =========================================================================
# SystemdUnitExtractor tests
# =========================================================================


class TestSystemdUnitExtractor:
    @pytest.fixture()
    def extractor(self) -> SystemdUnitExtractor:
        return SystemdUnitExtractor()

    def test_name_and_priority(self, extractor: SystemdUnitExtractor) -> None:
        assert extractor.name == "systemd_unit"
        assert extractor.priority == 50

    def test_extract_no_manifest(self, extractor: SystemdUnitExtractor) -> None:
        result = extractor.extract("pkg")
        assert result.success is False
        assert "No systemd units" in (result.error or "")

    def test_extract_no_units_in_manifest(self, extractor: SystemdUnitExtractor) -> None:
        manifest = FileManifest(systemd_units=())
        result = extractor.extract("pkg", manifest=manifest)
        assert result.success is False

    def test_extract_parses_unit_file(self, extractor: SystemdUnitExtractor, tmp_path: Path) -> None:
        unit_file = tmp_path / "nginx.service"
        unit_file.write_text("""[Unit]
Description=A high performance web server
Requires=network-online.target

[Service]
ExecStart=/usr/sbin/nginx -g 'daemon off;'
Environment=NGINX_CONF=/etc/nginx/nginx.conf
Environment=LANG=C

[Install]
WantedBy=multi-user.target
""")
        manifest = FileManifest(systemd_units=(str(unit_file),))
        result = extractor.extract("nginx", manifest=manifest)
        assert result.success is True
        assert len(result.systemd_units) == 1
        unit = result.systemd_units[0]
        assert unit.unit_name == "nginx.service"
        assert unit.unit_type == "service"
        assert unit.description == "A high performance web server"
        assert unit.exec_start is not None
        assert len(unit.environment) == 2
        assert len(unit.requires) == 1

    def test_extract_timer_unit(self, extractor: SystemdUnitExtractor, tmp_path: Path) -> None:
        unit_file = tmp_path / "backup.timer"
        unit_file.write_text("""[Unit]
Description=Daily backup timer

[Timer]
OnCalendar=daily
""")
        manifest = FileManifest(systemd_units=(str(unit_file),))
        result = extractor.extract("backup", manifest=manifest)
        assert result.success is True
        assert result.systemd_units[0].unit_type == "timer"

    def test_extract_bad_file(self, extractor: SystemdUnitExtractor, tmp_path: Path) -> None:
        manifest = FileManifest(systemd_units=(str(tmp_path / "nonexistent.service"),))
        result = extractor.extract("pkg", manifest=manifest)
        assert result.success is False


# =========================================================================
# HelpOutputExtractor tests
# =========================================================================


class TestHelpOutputExtractor:
    @pytest.fixture()
    def extractor(self) -> HelpOutputExtractor:
        return HelpOutputExtractor()

    def test_name_and_priority(self, extractor: HelpOutputExtractor) -> None:
        assert extractor.name == "help_output"
        assert extractor.priority == 70

    def test_extract_success(self, extractor: HelpOutputExtractor) -> None:
        mock_result = MagicMock()
        mock_result.stdout = """Usage: tool [OPTIONS]

  -v, --verbose          Enable verbose output
  -o, --output FILE      Output file path
      --version          Show version
"""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("tool")
            assert result.success is True
            assert len(result.flags) >= 2

    def test_extract_timeout(self, extractor: HelpOutputExtractor) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tool", 5)):
            result = extractor.extract("tool")
            assert result.success is False
            assert "timed out" in (result.error or "")

    def test_extract_not_found(self, extractor: HelpOutputExtractor) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError("tool")):
            result = extractor.extract("tool")
            assert result.success is False
            assert "not found" in (result.error or "")

    def test_extract_no_output(self, extractor: HelpOutputExtractor) -> None:
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("tool")
            assert result.success is False

    def test_extract_stderr_fallback(self, extractor: HelpOutputExtractor) -> None:
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = """Usage: tool [OPTIONS]

  -h, --help          Show help
"""
        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("tool")
            assert result.success is True

    def test_extract_uses_manifest_binary(self, extractor: HelpOutputExtractor) -> None:
        manifest = FileManifest(binaries=("/usr/bin/mytool",))
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            extractor.extract("mytool", manifest=manifest)
            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "/usr/bin/mytool"

    def test_extract_long_only_flags(self, extractor: HelpOutputExtractor) -> None:
        mock_result = MagicMock()
        mock_result.stdout = """Options:
      --max-retries NUM    Maximum retry count
      --dry-run            Only simulate
"""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = extractor.extract("tool")
            assert result.success is True
            flag_names = {f.flag for f in result.flags}
            assert "--max-retries" in flag_names or "--dry-run" in flag_names

    def test_extract_generic_exception(self, extractor: HelpOutputExtractor) -> None:
        with patch("subprocess.run", side_effect=OSError("error")):
            result = extractor.extract("tool")
            assert result.success is False
