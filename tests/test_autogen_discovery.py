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


class TestBinaryDirs:
    """Tests for BINARY_DIRS list."""

    def test_standard_dirs_included(self):
        """Test standard directories are included."""
        assert "/usr/bin" in BINARY_DIRS
        assert "/usr/sbin" in BINARY_DIRS
        assert "/bin" in BINARY_DIRS
        assert "/sbin" in BINARY_DIRS
