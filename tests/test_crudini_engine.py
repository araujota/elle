"""Tests for the crudini engine."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from elle.ops.crudini.models import (
    CrudiniEditRequest,
    CrudiniOperation,
    CrudiniRequest,
    CrudiniUnavailableError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_INI = """\
[database]
host = localhost
port = 5432
name = mydb

[server]
port = 8080
workers = 4
"""


def _make_completed(stdout: str = "", stderr: str = "", rc: int = 0):
    """Build a fake subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(args=["crudini"], returncode=rc, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


class TestCrudiniAvailability:
    """Tests for module-level availability helpers."""

    def test_is_available_when_found(self) -> None:
        """is_available returns True when shutil.which finds crudini."""
        import elle.ops.crudini.engine as mod

        # Reset the cache so our mock takes effect
        mod._crudini_checked = False
        mod._crudini_path = None

        with patch("elle.ops.crudini.engine.shutil.which", return_value="/usr/bin/crudini"):
            result = mod.is_available()

        assert result is True

        # Restore cache
        mod._crudini_checked = False
        mod._crudini_path = None

    def test_is_available_when_not_found(self) -> None:
        """is_available returns False when crudini is absent."""
        import elle.ops.crudini.engine as mod

        mod._crudini_checked = False
        mod._crudini_path = None

        with patch("elle.ops.crudini.engine.shutil.which", return_value=None):
            result = mod.is_available()

        assert result is False

        mod._crudini_checked = False
        mod._crudini_path = None

    def test_get_version_returns_version_string(self) -> None:
        """get_version returns the stdout of crudini --version."""
        import elle.ops.crudini.engine as mod

        mod._crudini_checked = False
        mod._crudini_path = None
        mod._crudini_version = None

        with (
            patch("elle.ops.crudini.engine.shutil.which", return_value="/usr/bin/crudini"),
            patch(
                "elle.ops.crudini.engine.subprocess.run",
                return_value=_make_completed(stdout="0.9.4\n"),
            ),
        ):
            version = mod.get_version()

        assert version == "0.9.4"

        mod._crudini_checked = False
        mod._crudini_path = None
        mod._crudini_version = None

    def test_get_version_returns_none_when_unavailable(self) -> None:
        """get_version returns None when crudini is not installed."""
        import elle.ops.crudini.engine as mod

        mod._crudini_checked = False
        mod._crudini_path = None
        mod._crudini_version = None

        with patch("elle.ops.crudini.engine.shutil.which", return_value=None):
            version = mod.get_version()

        assert version is None

        mod._crudini_checked = False
        mod._crudini_path = None
        mod._crudini_version = None

    def test_get_status_available(self) -> None:
        """get_status returns a populated CrudiniStatus when available."""
        import elle.ops.crudini.engine as mod

        mod._crudini_checked = False
        mod._crudini_path = None
        mod._crudini_version = None

        with (
            patch("elle.ops.crudini.engine.shutil.which", return_value="/usr/bin/crudini"),
            patch(
                "elle.ops.crudini.engine.subprocess.run",
                return_value=_make_completed(stdout="0.9.4\n"),
            ),
        ):
            status = mod.get_status()

        assert status.available is True
        assert status.path == "/usr/bin/crudini"
        assert status.version == "0.9.4"

        mod._crudini_checked = False
        mod._crudini_path = None
        mod._crudini_version = None

    def test_get_status_unavailable(self) -> None:
        """get_status shows unavailable when crudini is missing."""
        import elle.ops.crudini.engine as mod

        mod._crudini_checked = False
        mod._crudini_path = None
        mod._crudini_version = None

        with patch("elle.ops.crudini.engine.shutil.which", return_value=None):
            status = mod.get_status()

        assert status.available is False
        assert status.path is None

        mod._crudini_checked = False
        mod._crudini_path = None
        mod._crudini_version = None


# ---------------------------------------------------------------------------
# Engine construction
# ---------------------------------------------------------------------------


class TestCrudiniEngineInit:
    """Tests for CrudiniEngine initialization."""

    def test_init_raises_when_unavailable(self) -> None:
        """CrudiniEngine() raises CrudiniUnavailableError when crudini is absent."""
        import elle.ops.crudini.engine as mod

        mod._crudini_checked = False
        mod._crudini_path = None

        with (
            patch("elle.ops.crudini.engine.shutil.which", return_value=None),
            pytest.raises(CrudiniUnavailableError),
        ):
            mod.CrudiniEngine()

        mod._crudini_checked = False
        mod._crudini_path = None

    def test_init_succeeds_when_available(self) -> None:
        """CrudiniEngine() succeeds when crudini is found."""
        import elle.ops.crudini.engine as mod

        mod._crudini_checked = False
        mod._crudini_path = None

        with patch("elle.ops.crudini.engine.shutil.which", return_value="/usr/bin/crudini"):
            engine = mod.CrudiniEngine()

        assert engine._crudini_path == "/usr/bin/crudini"

        mod._crudini_checked = False
        mod._crudini_path = None

    def test_context_manager(self) -> None:
        """CrudiniEngine works as a context manager."""
        import elle.ops.crudini.engine as mod

        mod._crudini_checked = False
        mod._crudini_path = None

        with patch("elle.ops.crudini.engine.shutil.which", return_value="/usr/bin/crudini"):
            with mod.CrudiniEngine() as engine:
                assert engine is not None
                assert engine._crudini_path == "/usr/bin/crudini"

        mod._crudini_checked = False
        mod._crudini_path = None


# ---------------------------------------------------------------------------
# Fixture: engine with mocked crudini binary
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    """Create a CrudiniEngine with a mocked crudini binary path."""
    import elle.ops.crudini.engine as mod

    mod._crudini_checked = False
    mod._crudini_path = None

    with patch("elle.ops.crudini.engine.shutil.which", return_value="/usr/bin/crudini"):
        eng = mod.CrudiniEngine()

    yield eng

    mod._crudini_checked = False
    mod._crudini_path = None


# ---------------------------------------------------------------------------
# Get operations
# ---------------------------------------------------------------------------


class TestCrudiniGet:
    """Tests for get, get_section, list_sections, list_parameters."""

    def test_get_returns_value(self, engine) -> None:
        """get() returns the trimmed stdout on success."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stdout="localhost\n"),
        ):
            value = engine.get("/etc/app.conf", "database", "host")

        assert value == "localhost"

    def test_get_returns_none_on_failure(self, engine) -> None:
        """get() returns None when crudini exits non-zero."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stderr="not found", rc=1),
        ):
            value = engine.get("/etc/app.conf", "missing", "key")

        assert value is None

    def test_get_section_parses_sh_format(self, engine) -> None:
        """get_section() parses 'key=value' lines from --format=sh output."""
        sh_output = "host='localhost'\nport='5432'\n"
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stdout=sh_output),
        ):
            section = engine.get_section("/etc/app.conf", "database")

        assert section == {"host": "localhost", "port": "5432"}

    def test_get_section_returns_empty_on_failure(self, engine) -> None:
        """get_section() returns {} when the section does not exist."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stderr="no such section", rc=1),
        ):
            section = engine.get_section("/etc/app.conf", "nonexistent")

        assert section == {}

    def test_list_sections(self, engine) -> None:
        """list_sections() returns a list of section names."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stdout="database\nserver\n"),
        ):
            sections = engine.list_sections("/etc/app.conf")

        assert sections == ["database", "server"]

    def test_list_sections_returns_empty_on_failure(self, engine) -> None:
        """list_sections() returns [] on error."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stderr="bad file", rc=1),
        ):
            sections = engine.list_sections("/etc/bad.conf")

        assert sections == []

    def test_list_parameters(self, engine) -> None:
        """list_parameters() returns parameter names from a section."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stdout="host\nport\nname\n"),
        ):
            params = engine.list_parameters("/etc/app.conf", "database")

        assert params == ["host", "port", "name"]

    def test_list_parameters_returns_empty_on_failure(self, engine) -> None:
        """list_parameters() returns [] when the section is missing."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(rc=1),
        ):
            params = engine.list_parameters("/etc/app.conf", "nonexistent")

        assert params == []


# ---------------------------------------------------------------------------
# get_file_content
# ---------------------------------------------------------------------------


class TestCrudiniFileContent:
    """Tests for get_file_content."""

    def test_get_file_content_assembles_sections(self, engine) -> None:
        """get_file_content() builds INIFileContent from sections."""
        # First call: list_sections  -> "database\nserver\n"
        # Second call: get_section(database)  -> "host='localhost'\n"
        # Third call: get_section(server) -> "port='8080'\n"
        call_count = 0
        responses = [
            _make_completed(stdout="database\nserver\n"),  # list_sections
            _make_completed(stdout="host='localhost'\n"),  # get_section database
            _make_completed(stdout="port='8080'\n"),  # get_section server
        ]

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            side_effect=_side_effect,
        ):
            content = engine.get_file_content("/etc/app.conf")

        assert content.file_path == "/etc/app.conf"
        assert len(content.sections) == 2
        assert content.sections[0].name == "database"
        assert content.sections[0].parameters == {"host": "localhost"}
        assert content.sections[1].name == "server"
        assert content.sections[1].parameters == {"port": "8080"}


# ---------------------------------------------------------------------------
# Set operations
# ---------------------------------------------------------------------------


class TestCrudiniSet:
    """Tests for set and set_section."""

    def test_set_returns_success(self, engine) -> None:
        """set() returns CrudiniResult with success=True."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(),
        ):
            result = engine.set("/etc/app.conf", "database", "host", "db.local")

        assert result.success is True
        assert result.error is None

    def test_set_with_existing_keep(self, engine) -> None:
        """set(existing='keep') passes --existing=keep to crudini."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(),
        ) as mock_run:
            engine.set("/etc/app.conf", "database", "host", "db.local", existing="keep")

        args = mock_run.call_args[0][0]
        assert "--existing=keep" in args

    def test_set_with_existing_error(self, engine) -> None:
        """set(existing='error') passes --existing=error to crudini."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(),
        ) as mock_run:
            engine.set("/etc/app.conf", "database", "host", "db.local", existing="error")

        args = mock_run.call_args[0][0]
        assert "--existing=error" in args

    def test_set_returns_failure(self, engine) -> None:
        """set() reports errors when crudini fails."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stderr="permission denied", rc=1),
        ):
            result = engine.set("/etc/app.conf", "database", "host", "db.local")

        assert result.success is False
        assert result.error == "permission denied"

    def test_set_section_sets_multiple(self, engine) -> None:
        """set_section() sets all parameters in the dict."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(),
        ) as mock_run:
            result = engine.set_section(
                "/etc/app.conf",
                "database",
                {"host": "db.local", "port": "5433"},
            )

        assert result.success is True
        assert mock_run.call_count == 2

    def test_set_section_stops_on_first_failure(self, engine) -> None:
        """set_section() stops and returns error on first failure."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stderr="fail", rc=1),
        ):
            result = engine.set_section(
                "/etc/app.conf",
                "database",
                {"host": "x", "port": "y"},
            )

        assert result.success is False


# ---------------------------------------------------------------------------
# Delete operations
# ---------------------------------------------------------------------------


class TestCrudiniDelete:
    """Tests for delete."""

    def test_delete_parameter(self, engine) -> None:
        """delete() with parameter deletes a single key."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(),
        ) as mock_run:
            result = engine.delete("/etc/app.conf", "database", "deprecated_key")

        assert result.success is True
        args = mock_run.call_args[0][0]
        assert "--del" in args
        assert "deprecated_key" in args

    def test_delete_section(self, engine) -> None:
        """delete() without parameter deletes the entire section."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(),
        ) as mock_run:
            result = engine.delete("/etc/app.conf", "old_section")

        assert result.success is True
        args = mock_run.call_args[0][0]
        assert "old_section" in args

    def test_delete_failure(self, engine) -> None:
        """delete() reports error when section is missing."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stderr="section not found", rc=1),
        ):
            result = engine.delete("/etc/app.conf", "nonexistent")

        assert result.success is False
        assert result.error == "section not found"


# ---------------------------------------------------------------------------
# Merge operations
# ---------------------------------------------------------------------------


class TestCrudiniMerge:
    """Tests for merge."""

    def test_merge_success(self, engine) -> None:
        """merge() passes content via stdin to crudini --merge."""
        merge_content = "[new_section]\nkey = value\n"
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(),
        ) as mock_run:
            result = engine.merge("/etc/app.conf", merge_content)

        assert result.success is True
        # Verify --merge was passed
        args = mock_run.call_args[0][0]
        assert "--merge" in args
        # Verify stdin was used
        assert mock_run.call_args[1]["input"] == merge_content

    def test_merge_failure(self, engine) -> None:
        """merge() reports error on failure."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stderr="invalid content", rc=1),
        ):
            result = engine.merge("/etc/app.conf", "bad content")

        assert result.success is False
        assert result.error == "invalid content"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestCrudiniValidation:
    """Tests for validate and validate_content."""

    def test_validate_valid_file(self, engine) -> None:
        """validate() returns success for a valid INI file."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stdout="database\nserver\n"),
        ):
            result = engine.validate("/etc/app.conf")

        assert result.success is True
        assert result.output == "Valid INI file"

    def test_validate_invalid_file(self, engine) -> None:
        """validate() returns failure for an invalid file."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stderr="parse error", rc=1),
        ):
            result = engine.validate("/etc/bad.conf")

        assert result.success is False
        assert result.error == "parse error"

    def test_validate_content_valid(self, engine) -> None:
        """validate_content() returns True for valid INI content."""
        assert engine.validate_content(SAMPLE_INI) is True

    def test_validate_content_invalid(self, engine) -> None:
        """validate_content() returns False for broken INI content."""
        # Missing section header
        assert engine.validate_content("this is not = valid ini without [section]") is False


# ---------------------------------------------------------------------------
# Process (request dispatch)
# ---------------------------------------------------------------------------


class TestCrudiniProcess:
    """Tests for process() dispatching to correct operations."""

    def test_process_get_parameter(self, engine) -> None:
        """process() dispatches get with parameter to engine.get()."""
        request = CrudiniRequest(
            file_path="/etc/app.conf",
            operation=CrudiniOperation(kind="get", section="database", parameter="host"),
        )
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stdout="localhost\n"),
        ):
            result = engine.process(request)

        assert result.success is True
        assert result.output == "localhost"

    def test_process_get_section(self, engine) -> None:
        """process() dispatches get without parameter to get_section()."""
        request = CrudiniRequest(
            file_path="/etc/app.conf",
            operation=CrudiniOperation(kind="get", section="database"),
        )
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stdout="host='localhost'\nport='5432'\n"),
        ):
            result = engine.process(request)

        assert result.success is True
        assert "host=localhost" in result.output

    def test_process_get_parameter_not_found(self, engine) -> None:
        """process() returns error when parameter is missing."""
        request = CrudiniRequest(
            file_path="/etc/app.conf",
            operation=CrudiniOperation(kind="get", section="database", parameter="missing"),
        )
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stderr="not found", rc=1),
        ):
            result = engine.process(request)

        assert result.success is False
        assert result.error == "Parameter not found"

    def test_process_set(self, engine) -> None:
        """process() dispatches set operations."""
        request = CrudiniRequest(
            file_path="/etc/app.conf",
            operation=CrudiniOperation(kind="set", section="database", parameter="host", value="db.local"),
        )
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(),
        ):
            result = engine.process(request)

        assert result.success is True

    def test_process_set_missing_parameter(self, engine) -> None:
        """process() rejects set without parameter."""
        request = CrudiniRequest(
            file_path="/etc/app.conf",
            operation=CrudiniOperation(kind="set", section="database"),
        )
        result = engine.process(request)
        assert result.success is False
        assert "requires parameter and value" in result.error

    def test_process_del(self, engine) -> None:
        """process() dispatches delete operations."""
        request = CrudiniRequest(
            file_path="/etc/app.conf",
            operation=CrudiniOperation(kind="del", section="old", parameter="key"),
        )
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(),
        ):
            result = engine.process(request)

        assert result.success is True

    def test_process_merge(self, engine) -> None:
        """process() dispatches merge operations."""
        request = CrudiniRequest(
            file_path="/etc/app.conf",
            operation=CrudiniOperation(kind="merge", section="new", value="[new]\nfoo = bar\n"),
        )
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(),
        ):
            result = engine.process(request)

        assert result.success is True

    def test_process_merge_missing_value(self, engine) -> None:
        """process() rejects merge without content."""
        request = CrudiniRequest(
            file_path="/etc/app.conf",
            operation=CrudiniOperation(kind="merge", section="new"),
        )
        result = engine.process(request)
        assert result.success is False
        assert "requires content" in result.error


# ---------------------------------------------------------------------------
# Error handling in _run_crudini
# ---------------------------------------------------------------------------


class TestCrudiniRunErrors:
    """Tests for subprocess error handling in _run_crudini."""

    def test_timeout_returns_failure(self, engine) -> None:
        """_run_crudini returns failure tuple on subprocess timeout."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="crudini", timeout=30),
        ):
            success, stdout, stderr, code = engine._run_crudini("--get", "/etc/app.conf")

        assert success is False
        assert "timed out" in stderr
        assert code == -1

    def test_generic_exception_returns_failure(self, engine) -> None:
        """_run_crudini returns failure tuple on unexpected exceptions."""
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            side_effect=OSError("broken pipe"),
        ):
            success, stdout, stderr, code = engine._run_crudini("--get", "/etc/app.conf")

        assert success is False
        assert "broken pipe" in stderr
        assert code == -1

    def test_run_crudini_raises_if_path_none(self) -> None:
        """_run_crudini raises CrudiniUnavailableError if path is None."""
        import elle.ops.crudini.engine as mod

        mod._crudini_checked = False
        mod._crudini_path = None

        with patch("elle.ops.crudini.engine.shutil.which", return_value="/usr/bin/crudini"):
            eng = mod.CrudiniEngine()

        # Force path to None to simulate edge case
        eng._crudini_path = None

        with pytest.raises(CrudiniUnavailableError):
            eng._run_crudini("--get", "/etc/app.conf")

        mod._crudini_checked = False
        mod._crudini_path = None


# ---------------------------------------------------------------------------
# Dry run mode via execute_edit
# ---------------------------------------------------------------------------


class TestCrudiniDryRun:
    """Tests for dry run mode in execute_edit."""

    def test_dry_run_does_not_modify_file(self, engine, tmp_path) -> None:
        """execute_edit with dry_run=True does not change the file."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="database", parameter="host", value="newhost"),),
            description="test dry run",
            dry_run=True,
            skip_backup=True,
        )

        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(),
        ):
            # Patch the diff helpers so we don't need augeas.diff
            with (
                patch("elle.ops.crudini.engine.CrudiniEngine.preview_edit") as mock_preview,
            ):
                mock_preview.return_value = MagicMock(
                    diff="--- a\n+++ b\n",
                    diff_colored="colored",
                    is_valid_ini=True,
                )
                result = engine.execute_edit(request)

        assert result.success is True
        # File content should be unchanged
        assert ini_file.read_text() == SAMPLE_INI


# ---------------------------------------------------------------------------
# Backup creation
# ---------------------------------------------------------------------------


class TestCrudiniBackup:
    """Tests for backup creation in execute_edit."""

    def test_backup_failure_returns_error(self, engine, tmp_path) -> None:
        """execute_edit returns error when backup creation fails."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="database", parameter="host", value="newhost"),),
            description="test backup failure",
            dry_run=False,
            skip_backup=False,
        )

        with patch(
            "elle.ops.augeas.backup.backup_file",
            side_effect=OSError("disk full"),
        ):
            result = engine.execute_edit(request)

        assert result.success is False
        assert "Failed to create backup" in result.error

    def test_skip_backup_does_not_call_backup(self, engine, tmp_path) -> None:
        """execute_edit with skip_backup=True does not create a backup."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="database", parameter="host", value="newhost"),),
            description="test skip backup",
            dry_run=False,
            skip_backup=True,
            skip_validation=True,
        )

        with (
            patch(
                "elle.ops.crudini.engine.subprocess.run",
                return_value=_make_completed(),
            ),
            patch("elle.ops.augeas.diff.unified_diff", return_value=""),
            patch("elle.ops.augeas.diff.colored_diff", return_value=""),
        ):
            result = engine.execute_edit(request)

        assert result.backup_path is None


# ---------------------------------------------------------------------------
# execute_edit file not found
# ---------------------------------------------------------------------------


class TestCrudiniExecuteEditErrors:
    """Tests for execute_edit error paths."""

    def test_execute_edit_file_not_found(self, engine) -> None:
        """execute_edit returns error when the file does not exist."""
        request = CrudiniEditRequest(
            file_path="/nonexistent/file.ini",
            operations=(),
            description="test missing file",
        )
        result = engine.execute_edit(request)
        assert result.success is False
        assert "File not found" in result.error


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------


class TestGetVersionEdgeCases:
    """Tests for get_version cached path and exception handling."""

    def test_get_version_returns_cached_value(self) -> None:
        """get_version() returns the cached version without running subprocess."""
        import elle.ops.crudini.engine as mod

        mod._crudini_checked = False
        mod._crudini_path = None
        mod._crudini_version = "1.2.3"

        try:
            version = mod.get_version()
            assert version == "1.2.3"
        finally:
            mod._crudini_checked = False
            mod._crudini_path = None
            mod._crudini_version = None

    def test_get_version_returns_none_on_exception(self) -> None:
        """get_version() returns None when subprocess raises an exception."""
        import elle.ops.crudini.engine as mod

        mod._crudini_checked = False
        mod._crudini_path = None
        mod._crudini_version = None

        with (
            patch("elle.ops.crudini.engine.shutil.which", return_value="/usr/bin/crudini"),
            patch(
                "elle.ops.crudini.engine.subprocess.run",
                side_effect=OSError("binary not found"),
            ),
        ):
            version = mod.get_version()

        assert version is None

        mod._crudini_checked = False
        mod._crudini_path = None
        mod._crudini_version = None

    def test_get_version_returns_none_on_nonzero_exit(self) -> None:
        """get_version() returns None when crudini --version exits non-zero."""
        import elle.ops.crudini.engine as mod

        mod._crudini_checked = False
        mod._crudini_path = None
        mod._crudini_version = None

        with (
            patch("elle.ops.crudini.engine.shutil.which", return_value="/usr/bin/crudini"),
            patch(
                "elle.ops.crudini.engine.subprocess.run",
                return_value=_make_completed(stderr="error", rc=1),
            ),
        ):
            version = mod.get_version()

        assert version is None

        mod._crudini_checked = False
        mod._crudini_path = None
        mod._crudini_version = None


class TestGetSectionEdgeCases:
    """Tests for get_section line parsing edge cases."""

    def test_get_section_skips_lines_without_equals(self, engine) -> None:
        """get_section() skips lines that do not contain an = character."""
        sh_output = "host='localhost'\nsome-junk-line\nport='5432'\n"
        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stdout=sh_output),
        ):
            section = engine.get_section("/etc/app.conf", "database")

        assert section == {"host": "localhost", "port": "5432"}


class TestProcessUnknownOperation:
    """Tests for process() with an unknown operation kind."""

    def test_process_unknown_kind(self, engine) -> None:
        """process() returns error for an unknown operation kind."""
        # Use a MagicMock to bypass pydantic Literal validation
        mock_op = MagicMock()
        mock_op.kind = "unknown_op"
        mock_op.parameter = None
        mock_op.value = None
        mock_op.section = "test"

        request = MagicMock()
        request.file_path = "/etc/app.conf"
        request.operation = mock_op

        result = engine.process(request)
        assert result.success is False
        assert "Unknown operation kind" in result.error


class TestPreviewEdit:
    """Tests for preview_edit() method."""

    def test_preview_edit_file_not_found(self, engine) -> None:
        """preview_edit() returns empty preview when file does not exist."""
        request = CrudiniEditRequest(
            file_path="/nonexistent/file.ini",
            operations=(CrudiniOperation(kind="set", section="db", parameter="host", value="x"),),
            description="test preview missing file",
        )
        result = engine.preview_edit(request)
        assert result.is_valid_ini is False
        assert result.original_content == ""
        assert result.proposed_content == ""

    def test_preview_edit_set_operation(self, engine, tmp_path) -> None:
        """preview_edit() generates a diff for set operations."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="database", parameter="host", value="newhost"),),
            description="test preview set",
        )

        with (
            patch("elle.ops.augeas.diff.unified_diff", return_value="--- a\n+++ b\n") as mock_udiff,
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored") as mock_cdiff,
        ):
            result = engine.preview_edit(request)

        assert result.is_valid_ini is True
        assert result.original_content == SAMPLE_INI
        assert result.proposed_content != ""
        assert result.diff == "--- a\n+++ b\n"
        assert result.diff_colored == "colored"
        mock_udiff.assert_called_once()
        mock_cdiff.assert_called_once()

    def test_preview_edit_set_new_section(self, engine, tmp_path) -> None:
        """preview_edit() creates a new section when it does not exist."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="newsection", parameter="key", value="val"),),
            description="test preview new section",
        )

        with (
            patch("elle.ops.augeas.diff.unified_diff", return_value="diff"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
        ):
            result = engine.preview_edit(request)

        assert result.is_valid_ini is True
        assert "newsection" in result.proposed_content

    def test_preview_edit_del_parameter(self, engine, tmp_path) -> None:
        """preview_edit() removes a parameter in preview."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="del", section="database", parameter="host"),),
            description="test preview delete param",
        )

        with (
            patch("elle.ops.augeas.diff.unified_diff", return_value="diff"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
        ):
            result = engine.preview_edit(request)

        assert result.is_valid_ini is True
        assert "host" not in result.proposed_content or "host" not in result.proposed_content.split("[database]")[1].split("[")[0]

    def test_preview_edit_del_section(self, engine, tmp_path) -> None:
        """preview_edit() removes an entire section in preview."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="del", section="server"),),
            description="test preview delete section",
        )

        with (
            patch("elle.ops.augeas.diff.unified_diff", return_value="diff"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
        ):
            result = engine.preview_edit(request)

        assert result.is_valid_ini is True

    def test_preview_edit_invalid_ini_content(self, engine, tmp_path) -> None:
        """preview_edit() returns is_valid_ini=False when parsing fails."""
        ini_file = tmp_path / "test.ini"
        # Write content that will fail configparser.read_string
        ini_file.write_text("not valid ini content without section\nkey = value\n")

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="db", parameter="host", value="x"),),
            description="test preview invalid ini",
        )

        result = engine.preview_edit(request)
        assert result.is_valid_ini is False

    def test_preview_edit_requires_privilege(self, engine, tmp_path) -> None:
        """preview_edit() detects when file requires elevated access."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="database", parameter="host", value="x"),),
            description="test preview privilege",
        )

        with (
            patch("elle.ops.augeas.diff.unified_diff", return_value="diff"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
            patch("os.access", return_value=False),
        ):
            result = engine.preview_edit(request)

        assert result.requires_privilege is True


class TestExecuteEditRollback:
    """Tests for execute_edit rollback paths."""

    def test_execute_edit_rollback_on_operation_failure(self, engine, tmp_path) -> None:
        """execute_edit() rolls back on operation failure when backup exists."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="database", parameter="host", value="newhost"),),
            description="test rollback on failure",
            skip_backup=False,
            skip_validation=True,
        )

        mock_backup_record = MagicMock()
        mock_backup_record.backup_path = "/tmp/backup.ini"

        with (
            patch("elle.ops.augeas.backup.backup_file", return_value=mock_backup_record),
            patch(
                "elle.ops.crudini.engine.subprocess.run",
                return_value=_make_completed(stderr="write error", rc=1),
            ),
            patch("elle.ops.augeas.backup.restore_file") as mock_restore,
        ):
            result = engine.execute_edit(request)

        assert result.success is False
        assert result.rollback_applied is True
        assert "rolled back" in result.error
        mock_restore.assert_called_once_with("/tmp/backup.ini")

    def test_execute_edit_rollback_failure(self, engine, tmp_path) -> None:
        """execute_edit() reports both errors when rollback also fails."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="database", parameter="host", value="newhost"),),
            description="test rollback failure",
            skip_backup=False,
            skip_validation=True,
        )

        mock_backup_record = MagicMock()
        mock_backup_record.backup_path = "/tmp/backup.ini"

        with (
            patch("elle.ops.augeas.backup.backup_file", return_value=mock_backup_record),
            patch(
                "elle.ops.crudini.engine.subprocess.run",
                return_value=_make_completed(stderr="write error", rc=1),
            ),
            patch("elle.ops.augeas.backup.restore_file", side_effect=OSError("restore failed")),
        ):
            result = engine.execute_edit(request)

        assert result.success is False
        assert "rollback failed" in result.error
        assert result.rollback_applied is False

    def test_execute_edit_operation_failure_no_backup(self, engine, tmp_path) -> None:
        """execute_edit() returns error without rollback when skip_backup=True."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="database", parameter="host", value="newhost"),),
            description="test failure no backup",
            skip_backup=True,
            skip_validation=True,
        )

        with patch(
            "elle.ops.crudini.engine.subprocess.run",
            return_value=_make_completed(stderr="write error", rc=1),
        ):
            result = engine.execute_edit(request)

        assert result.success is False
        assert result.error == "write error"
        assert result.backup_path is None


class TestExecuteEditValidation:
    """Tests for execute_edit validation paths."""

    def test_execute_edit_validation_failure_rollback(self, engine, tmp_path) -> None:
        """execute_edit() rolls back when post-edit validation fails."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="database", parameter="host", value="newhost"),),
            description="test validation rollback",
            skip_backup=False,
            skip_validation=False,
        )

        mock_backup_record = MagicMock()
        mock_backup_record.backup_path = "/tmp/backup.ini"

        # First subprocess.run call is for the get (old_value), second is for set, third is for validate
        call_count = 0

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # get old_value and set operation succeed
                return _make_completed(stdout="localhost\n")
            else:
                # validate fails
                return _make_completed(stderr="parse error", rc=1)

        with (
            patch("elle.ops.augeas.backup.backup_file", return_value=mock_backup_record),
            patch("elle.ops.crudini.engine.subprocess.run", side_effect=_side_effect),
            patch("elle.ops.augeas.backup.restore_file") as mock_restore,
        ):
            result = engine.execute_edit(request)

        assert result.success is False
        assert result.validation_passed is False
        assert result.rollback_applied is True
        assert "Validation failed" in result.error
        mock_restore.assert_called_once_with("/tmp/backup.ini")

    def test_execute_edit_validation_failure_rollback_fails(self, engine, tmp_path) -> None:
        """execute_edit() reports both errors when validation fails and rollback also fails."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="database", parameter="host", value="newhost"),),
            description="test validation rollback failure",
            skip_backup=False,
            skip_validation=False,
        )

        mock_backup_record = MagicMock()
        mock_backup_record.backup_path = "/tmp/backup.ini"

        call_count = 0

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return _make_completed(stdout="localhost\n")
            else:
                return _make_completed(stderr="parse error", rc=1)

        with (
            patch("elle.ops.augeas.backup.backup_file", return_value=mock_backup_record),
            patch("elle.ops.crudini.engine.subprocess.run", side_effect=_side_effect),
            patch("elle.ops.augeas.backup.restore_file", side_effect=OSError("disk error")),
        ):
            result = engine.execute_edit(request)

        assert result.success is False
        assert result.validation_passed is False
        assert result.rollback_applied is False
        assert "rollback failed" in result.error


class TestExecuteEditSuccess:
    """Tests for execute_edit successful path with backup and validation."""

    def test_execute_edit_full_success_with_backup(self, engine, tmp_path) -> None:
        """execute_edit() succeeds with backup creation and validation."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="database", parameter="host", value="newhost"),),
            description="test full success",
            skip_backup=False,
            skip_validation=True,
        )

        mock_backup_record = MagicMock()
        mock_backup_record.backup_path = "/tmp/backup.ini"

        with (
            patch("elle.ops.augeas.backup.backup_file", return_value=mock_backup_record),
            patch(
                "elle.ops.crudini.engine.subprocess.run",
                return_value=_make_completed(stdout="localhost\n"),
            ),
            patch("elle.ops.augeas.diff.unified_diff", return_value="--- a\n+++ b\n"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
        ):
            result = engine.execute_edit(request)

        assert result.success is True
        assert result.backup_path == "/tmp/backup.ini"
        assert len(result.changes) == 1
        assert result.changes[0].section == "database"
        assert result.changes[0].parameter == "host"
        assert result.changes[0].old_value == "localhost"
        assert result.changes[0].new_value == "newhost"
        assert result.changes[0].operation == "set"

    def test_execute_edit_success_with_validation(self, engine, tmp_path) -> None:
        """execute_edit() passes when validation succeeds."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="database", parameter="host", value="newhost"),),
            description="test success with validation",
            skip_backup=True,
            skip_validation=False,
        )

        with (
            patch(
                "elle.ops.crudini.engine.subprocess.run",
                return_value=_make_completed(stdout="localhost\n"),
            ),
            patch("elle.ops.augeas.diff.unified_diff", return_value="diff"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
        ):
            result = engine.execute_edit(request)

        assert result.success is True
        assert result.validation_passed is True

    def test_execute_edit_del_operation_records_change(self, engine, tmp_path) -> None:
        """execute_edit() with del operation records new_value as None."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="del", section="database", parameter="host"),),
            description="test del change recording",
            skip_backup=True,
            skip_validation=True,
        )

        with (
            patch(
                "elle.ops.crudini.engine.subprocess.run",
                return_value=_make_completed(stdout="localhost\n"),
            ),
            patch("elle.ops.augeas.diff.unified_diff", return_value="diff"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
        ):
            result = engine.execute_edit(request)

        assert result.success is True
        assert len(result.changes) == 1
        assert result.changes[0].operation == "del"
        assert result.changes[0].new_value is None

    def test_execute_edit_dry_run_with_backup(self, engine, tmp_path) -> None:
        """execute_edit() with dry_run and backup creates backup but does not execute."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="database", parameter="host", value="newhost"),),
            description="test dry run with backup",
            dry_run=True,
            skip_backup=False,
        )

        mock_backup_record = MagicMock()
        mock_backup_record.backup_path = "/tmp/backup.ini"

        with (
            patch("elle.ops.augeas.backup.backup_file", return_value=mock_backup_record),
            patch("elle.ops.crudini.engine.CrudiniEngine.preview_edit") as mock_preview,
        ):
            mock_preview.return_value = MagicMock(
                diff="--- a\n+++ b\n",
                diff_colored="colored",
                is_valid_ini=True,
            )
            result = engine.execute_edit(request)

        assert result.success is True
        assert result.backup_path == "/tmp/backup.ini"
        assert result.validation_passed is True
        assert ini_file.read_text() == SAMPLE_INI


class TestExecuteEditValidationNoBackup:
    """Tests for execute_edit validation failure without backup."""

    def test_validation_fails_no_backup_continues(self, engine, tmp_path) -> None:
        """execute_edit() with validation failure and no backup does not rollback."""
        ini_file = tmp_path / "test.ini"
        ini_file.write_text(SAMPLE_INI)

        request = CrudiniEditRequest(
            file_path=str(ini_file),
            operations=(CrudiniOperation(kind="set", section="database", parameter="host", value="newhost"),),
            description="test validation no backup",
            skip_backup=True,
            skip_validation=False,
        )

        call_count = 0

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return _make_completed(stdout="localhost\n")
            else:
                # validation fails
                return _make_completed(stderr="parse error", rc=1)

        with (
            patch("elle.ops.crudini.engine.subprocess.run", side_effect=_side_effect),
            patch("elle.ops.augeas.diff.unified_diff", return_value="diff"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
        ):
            result = engine.execute_edit(request)

        # When validation fails but there is no backup_path, it continues to success
        assert result.success is True
        assert result.validation_passed is False
