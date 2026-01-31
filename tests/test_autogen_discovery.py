"""Tests for autogen discovery module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from elle.capabilities.autogen.discovery import (
    BINARY_DIRS,
    SKIP_BINARIES,
    discover_binary,
    discover_specific_commands,
    find_binary,
    find_man_page,
    get_package_for_binary,
)


class TestFindBinary:
    """Tests for find_binary function."""

    @patch("subprocess.run")
    def test_find_binary_which(self, mock_run):
        """Test finding binary via which."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/usr/bin/ls\n",
        )

        result = find_binary("ls")
        assert result == "/usr/bin/ls"

    @patch("subprocess.run")
    def test_find_binary_not_found(self, mock_run):
        """Test binary not found."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        # Also patch Path.exists to return False
        with patch("pathlib.Path.exists", return_value=False):
            result = find_binary("nonexistent")
            assert result is None


class TestFindManPage:
    """Tests for find_man_page function."""

    @patch("subprocess.run")
    def test_find_man_page_exists(self, mock_run):
        """Test finding existing man page."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/usr/share/man/man1/ls.1.gz\n",
        )

        exists, section, path = find_man_page("ls")
        assert exists is True
        assert section == 1
        assert path == "/usr/share/man/man1/ls.1.gz"

    @patch("subprocess.run")
    def test_find_man_page_section_8(self, mock_run):
        """Test finding man page in section 8."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/usr/share/man/man8/systemctl.8.gz\n",
        )

        exists, section, path = find_man_page("systemctl")
        assert exists is True
        assert section == 8

    @patch("subprocess.run")
    def test_find_man_page_not_found(self, mock_run):
        """Test man page not found."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        exists, section, path = find_man_page("nonexistent")
        assert exists is False
        assert section is None


class TestGetPackageForBinary:
    """Tests for get_package_for_binary function."""

    @patch("subprocess.run")
    def test_get_package(self, mock_run):
        """Test getting package for binary."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="coreutils: /usr/bin/ls\n"),
            MagicMock(returncode=0, stdout="8.32-4ubuntu2"),
        ]

        package, version = get_package_for_binary("/usr/bin/ls")
        assert package == "coreutils"
        assert version == "8.32-4ubuntu2"

    @patch("subprocess.run")
    def test_get_package_not_found(self, mock_run):
        """Test binary not from package."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        package, version = get_package_for_binary("/custom/bin/tool")
        assert package is None
        assert version is None


class TestDiscoverBinary:
    """Tests for discover_binary function."""

    @patch("elle.capabilities.autogen.discovery.find_binary")
    @patch("elle.capabilities.autogen.discovery.get_package_for_binary")
    @patch("elle.capabilities.autogen.discovery.find_man_page")
    def test_discover_binary_full(self, mock_man, mock_pkg, mock_find):
        """Test full binary discovery."""
        mock_find.return_value = "/usr/bin/ls"
        mock_pkg.return_value = ("coreutils", "8.32")
        mock_man.return_value = (True, 1, "/usr/share/man/man1/ls.1.gz")

        binary = discover_binary("ls")

        assert binary is not None
        assert binary.name == "ls"
        assert binary.path == "/usr/bin/ls"
        assert binary.package == "coreutils"
        assert binary.man_available is True

    def test_discover_binary_skip(self):
        """Test skipped binaries."""
        result = discover_binary("rm")
        assert result is None

        result = discover_binary("bash")
        assert result is None


class TestDiscoverSpecificCommands:
    """Tests for discover_specific_commands function."""

    @patch("elle.capabilities.autogen.discovery.discover_binary")
    def test_discover_specific(self, mock_discover):
        """Test discovering specific commands."""
        from elle.capabilities.autogen.models import InstalledBinary

        mock_discover.side_effect = [
            InstalledBinary(
                name="ls",
                path="/usr/bin/ls",
                man_available=True,
            ),
            None,  # Second command not found
        ]

        result = discover_specific_commands(["ls", "nonexistent"])

        assert result.total_scanned == 2
        assert len(result.binaries) == 1


class TestSkipBinaries:
    """Tests for SKIP_BINARIES set."""

    def test_dangerous_commands_skipped(self):
        """Test dangerous commands are skipped."""
        assert "rm" in SKIP_BINARIES
        assert "dd" in SKIP_BINARIES
        assert "mkfs" in SKIP_BINARIES
        assert "fdisk" in SKIP_BINARIES

    def test_shells_skipped(self):
        """Test shells are skipped."""
        assert "bash" in SKIP_BINARIES
        assert "sh" in SKIP_BINARIES
        assert "zsh" in SKIP_BINARIES


class TestScanBinaryDirectory:
    """Tests for scan_binary_directory function."""

    def test_scan_existing_dir(self, tmp_path):
        """Test scanning a directory with executables."""
        from elle.capabilities.autogen.discovery import scan_binary_directory

        # Create executable file
        exe = tmp_path / "mycommand"
        exe.write_text("#!/bin/sh\necho ok")
        exe.chmod(0o755)

        # Create non-executable file
        nox = tmp_path / "not_exec"
        nox.write_text("data")
        nox.chmod(0o644)

        # Create hidden file
        hidden = tmp_path / ".hidden_cmd"
        hidden.write_text("#!/bin/sh")
        hidden.chmod(0o755)

        # Create backup file
        backup = tmp_path / "mycommand~"
        backup.write_text("old")
        backup.chmod(0o755)

        binaries = scan_binary_directory(str(tmp_path))
        assert "mycommand" in binaries
        assert "not_exec" not in binaries
        assert ".hidden_cmd" not in binaries
        assert "mycommand~" not in binaries

    def test_scan_nonexistent_dir(self):
        """Test scanning a directory that does not exist."""
        from elle.capabilities.autogen.discovery import scan_binary_directory

        result = scan_binary_directory("/nonexistent/path/123456")
        assert result == []

    def test_scan_dir_skip_list(self, tmp_path):
        """Test that skip-listed binaries are excluded."""
        from elle.capabilities.autogen.discovery import scan_binary_directory

        # Create a skip-listed binary
        rm = tmp_path / "rm"
        rm.write_text("#!/bin/sh")
        rm.chmod(0o755)

        bash = tmp_path / "bash"
        bash.write_text("#!/bin/sh")
        bash.chmod(0o755)

        ok = tmp_path / "mytool"
        ok.write_text("#!/bin/sh")
        ok.chmod(0o755)

        binaries = scan_binary_directory(str(tmp_path))
        assert "rm" not in binaries
        assert "bash" not in binaries
        assert "mytool" in binaries

    def test_scan_dir_permission_error(self, tmp_path):
        """Test scanning a directory with permission error."""
        from elle.capabilities.autogen.discovery import scan_binary_directory

        with patch("pathlib.Path.iterdir", side_effect=PermissionError("no")):
            result = scan_binary_directory(str(tmp_path))
        assert result == []

    def test_scan_dir_general_exception(self, tmp_path):
        """Test scanning a directory with a general exception."""
        from elle.capabilities.autogen.discovery import scan_binary_directory

        with patch("pathlib.Path.iterdir", side_effect=OSError("disk error")):
            result = scan_binary_directory(str(tmp_path))
        assert result == []


class TestFindBinaryFallback:
    """Tests for find_binary fallback path scanning."""

    @patch("subprocess.run")
    def test_find_binary_fallback_to_path_scan(self, mock_run, tmp_path):
        """Test fallback to path scanning when which fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        test_bin = tmp_path / "testcmd"
        test_bin.write_text("#!/bin/sh")
        test_bin.chmod(0o755)

        with patch(
            "elle.capabilities.autogen.discovery.BINARY_DIRS",
            [str(tmp_path)],
        ):
            result = find_binary("testcmd")
        assert result == str(test_bin)

    @patch("subprocess.run", side_effect=Exception("process error"))
    def test_find_binary_subprocess_exception(self, mock_run, tmp_path):
        """Test find_binary when subprocess raises an exception."""
        with patch("pathlib.Path.exists", return_value=False):
            result = find_binary("nonexistent")
        assert result is None


class TestGetPackageForBinaryEdgeCases:
    """Tests for get_package_for_binary edge cases."""

    @patch("subprocess.run")
    def test_version_not_found(self, mock_run):
        """Test when dpkg-query fails to get version."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="pkg: /usr/bin/tool\n"),
            MagicMock(returncode=1, stdout=""),  # version query fails
        ]
        package, version = get_package_for_binary("/usr/bin/tool")
        assert package == "pkg"
        assert version is None

    @patch("subprocess.run", side_effect=Exception("dpkg not found"))
    def test_dpkg_exception(self, mock_run):
        """Test when dpkg is not available."""
        package, version = get_package_for_binary("/usr/bin/tool")
        assert package is None
        assert version is None


class TestFindManPageEdgeCases:
    """Tests for find_man_page edge cases."""

    @patch("subprocess.run")
    def test_empty_path(self, mock_run):
        """Test when man returns empty path."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        exists, section, path = find_man_page("empty")
        assert exists is False

    @patch("subprocess.run")
    def test_no_numeric_section(self, mock_run):
        """Test when section cannot be parsed from path."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/usr/share/man/man-custom/tool.gz\n",
        )
        exists, section, path = find_man_page("tool")
        assert exists is True
        assert section is None
        assert path == "/usr/share/man/man-custom/tool.gz"

    @patch("subprocess.run", side_effect=Exception("man not found"))
    def test_man_exception(self, mock_run):
        """Test when man command raises exception."""
        exists, section, path = find_man_page("tool")
        assert exists is False
        assert section is None
        assert path is None


class TestDiscoverBinaryEdgeCases:
    """Tests for discover_binary edge cases."""

    @patch("elle.capabilities.autogen.discovery.find_binary", return_value=None)
    def test_binary_not_found(self, mock_find):
        """Test when binary is not found on system."""
        result = discover_binary("notinstalled")
        assert result is None

    @patch("elle.capabilities.autogen.discovery.find_binary", return_value="/usr/bin/tool")
    @patch("elle.capabilities.autogen.discovery.get_package_for_binary", return_value=(None, None))
    @patch("elle.capabilities.autogen.discovery.find_man_page", return_value=(False, None, None))
    def test_binary_no_package_no_man(self, mock_man, mock_pkg, mock_find):
        """Test binary found but no package or man page."""
        result = discover_binary("tool")
        assert result is not None
        assert result.name == "tool"
        assert result.package is None
        assert result.man_available is False


class TestDiscoverAllBinaries:
    """Tests for discover_all_binaries function."""

    @patch("elle.capabilities.autogen.discovery.scan_binary_directory")
    @patch("elle.capabilities.autogen.discovery.discover_binary")
    def test_basic_discovery(self, mock_discover, mock_scan):
        """Test basic binary discovery with man page requirement."""
        from elle.capabilities.autogen.discovery import discover_all_binaries
        from elle.capabilities.autogen.models import InstalledBinary

        mock_scan.return_value = ["ls", "cat"]
        mock_discover.side_effect = [
            InstalledBinary(name="ls", path="/usr/bin/ls", man_available=True),
            InstalledBinary(name="cat", path="/usr/bin/cat", man_available=False),
        ]

        result = discover_all_binaries(directories=["/usr/bin"], require_man=True)
        assert len(result.binaries) == 1
        assert result.binaries[0].name == "ls"
        assert result.total_scanned == 2
        assert result.with_man_pages == 1

    @patch("elle.capabilities.autogen.discovery.scan_binary_directory")
    @patch("elle.capabilities.autogen.discovery.discover_binary")
    def test_no_man_required(self, mock_discover, mock_scan):
        """Test discovery without requiring man pages."""
        from elle.capabilities.autogen.discovery import discover_all_binaries
        from elle.capabilities.autogen.models import InstalledBinary

        mock_scan.return_value = ["tool"]
        mock_discover.return_value = InstalledBinary(
            name="tool", path="/usr/bin/tool", man_available=False,
        )

        result = discover_all_binaries(
            directories=["/usr/bin"], require_man=False,
        )
        assert len(result.binaries) == 1

    @patch("elle.capabilities.autogen.discovery.scan_binary_directory")
    @patch("elle.capabilities.autogen.discovery.discover_binary")
    def test_limit_respected(self, mock_discover, mock_scan):
        """Test that limit parameter caps results."""
        from elle.capabilities.autogen.discovery import discover_all_binaries
        from elle.capabilities.autogen.models import InstalledBinary

        mock_scan.return_value = ["a", "b", "c", "d"]
        mock_discover.side_effect = [
            InstalledBinary(name=n, path=f"/usr/bin/{n}", man_available=True)
            for n in ["a", "b", "c", "d"]
        ]

        result = discover_all_binaries(
            directories=["/usr/bin"], require_man=True, limit=2,
        )
        assert len(result.binaries) == 2

    @patch("elle.capabilities.autogen.discovery.scan_binary_directory")
    @patch("elle.capabilities.autogen.discovery.discover_binary")
    def test_deduplication(self, mock_discover, mock_scan):
        """Test that same binary in multiple dirs is scanned once."""
        from elle.capabilities.autogen.discovery import discover_all_binaries
        from elle.capabilities.autogen.models import InstalledBinary

        # Both dirs have "ls"
        mock_scan.side_effect = [["ls"], ["ls"]]
        mock_discover.return_value = InstalledBinary(
            name="ls", path="/usr/bin/ls", man_available=True,
        )

        result = discover_all_binaries(
            directories=["/usr/bin", "/bin"], require_man=True,
        )
        assert len(result.binaries) == 1
        # discover_binary called once due to dedup
        assert mock_discover.call_count == 1

    @patch("elle.capabilities.autogen.discovery.scan_binary_directory")
    @patch("elle.capabilities.autogen.discovery.discover_binary")
    def test_discover_binary_returns_none(self, mock_discover, mock_scan):
        """Test when discover_binary returns None (skip-listed)."""
        from elle.capabilities.autogen.discovery import discover_all_binaries

        mock_scan.return_value = ["somebin"]
        mock_discover.return_value = None

        result = discover_all_binaries(
            directories=["/usr/bin"], require_man=False,
        )
        assert len(result.binaries) == 0
        assert result.total_scanned == 1


class TestListInstalledPackages:
    """Tests for list_installed_packages function."""

    @patch("subprocess.run")
    def test_success(self, mock_run):
        """Test listing packages successfully."""
        from elle.capabilities.autogen.discovery import list_installed_packages

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="pkg1 1.0\npkg2 2.0.1\n",
        )

        result = list_installed_packages()
        assert len(result) == 2
        assert result[0] == ("pkg1", "1.0")
        assert result[1] == ("pkg2", "2.0.1")

    @patch("subprocess.run")
    def test_failure(self, mock_run):
        """Test listing packages when dpkg-query fails."""
        from elle.capabilities.autogen.discovery import list_installed_packages

        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = list_installed_packages()
        assert result == []

    @patch("subprocess.run", side_effect=Exception("no dpkg"))
    def test_exception(self, mock_run):
        """Test listing packages when subprocess raises."""
        from elle.capabilities.autogen.discovery import list_installed_packages

        result = list_installed_packages()
        assert result == []

    @patch("subprocess.run")
    def test_malformed_lines(self, mock_run):
        """Test listing packages with malformed output."""
        from elle.capabilities.autogen.discovery import list_installed_packages

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="validpkg 1.0\nbadline\n",
        )

        result = list_installed_packages()
        assert len(result) == 1
        assert result[0] == ("validpkg", "1.0")


class TestDiscoverSpecificCommandsEdgeCases:
    """Additional edge case tests for discover_specific_commands."""

    @patch("elle.capabilities.autogen.discovery.discover_binary")
    def test_all_found(self, mock_discover):
        """Test when all commands are found."""
        from elle.capabilities.autogen.models import InstalledBinary

        mock_discover.side_effect = [
            InstalledBinary(name="ls", path="/usr/bin/ls", man_available=True),
            InstalledBinary(name="cat", path="/usr/bin/cat", man_available=True),
        ]

        result = discover_specific_commands(["ls", "cat"])
        assert result.total_scanned == 2
        assert len(result.binaries) == 2
        assert result.with_man_pages == 2

    @patch("elle.capabilities.autogen.discovery.discover_binary")
    def test_mixed_man_pages(self, mock_discover):
        """Test counting with mixed man page availability."""
        from elle.capabilities.autogen.models import InstalledBinary

        mock_discover.side_effect = [
            InstalledBinary(name="ls", path="/usr/bin/ls", man_available=True),
            InstalledBinary(name="tool", path="/usr/bin/tool", man_available=False),
        ]

        result = discover_specific_commands(["ls", "tool"])
        assert result.with_man_pages == 1
        assert len(result.binaries) == 2

    @patch("elle.capabilities.autogen.discovery.discover_binary", return_value=None)
    def test_none_found(self, mock_discover):
        """Test when no commands are found."""
        result = discover_specific_commands(["x", "y", "z"])
        assert len(result.binaries) == 0
        assert result.total_scanned == 3

    def test_empty_list(self):
        """Test with empty command list."""
        result = discover_specific_commands([])
        assert len(result.binaries) == 0
        assert result.total_scanned == 0


class TestBinaryDirs:
    """Tests for BINARY_DIRS list."""

    def test_standard_dirs_included(self):
        """Test standard directories are included."""
        assert "/usr/bin" in BINARY_DIRS
        assert "/usr/sbin" in BINARY_DIRS
        assert "/bin" in BINARY_DIRS
        assert "/sbin" in BINARY_DIRS
