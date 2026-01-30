"""Tests for the jq engine."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from elle.ops.jq.models import (
    JQEditRequest,
    JQFilterError,
    JQRequest,
    JQResult,
    JQUnavailableError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_JSON = json.dumps(
    {
        "database": {"host": "localhost", "port": 5432},
        "server": {"port": 8080, "workers": 4},
        "features": ["logging", "metrics"],
    },
    indent=2,
)


def _make_completed(stdout: str = "", stderr: str = "", rc: int = 0):
    """Build a fake subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(
        args=["jq"], returncode=rc, stdout=stdout, stderr=stderr
    )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


class TestJQAvailability:
    """Tests for module-level availability helpers."""

    def test_is_available_when_found(self) -> None:
        """is_available returns True when shutil.which finds jq."""
        import elle.ops.jq.engine as mod

        mod._jq_checked = False
        mod._jq_path = None

        with patch("elle.ops.jq.engine.shutil.which", return_value="/usr/bin/jq"):
            result = mod.is_available()

        assert result is True

        mod._jq_checked = False
        mod._jq_path = None

    def test_is_available_when_not_found(self) -> None:
        """is_available returns False when jq is absent."""
        import elle.ops.jq.engine as mod

        mod._jq_checked = False
        mod._jq_path = None

        with patch("elle.ops.jq.engine.shutil.which", return_value=None):
            result = mod.is_available()

        assert result is False

        mod._jq_checked = False
        mod._jq_path = None

    def test_get_version_returns_version_string(self) -> None:
        """get_version returns the stdout of jq --version."""
        import elle.ops.jq.engine as mod

        mod._jq_checked = False
        mod._jq_path = None
        mod._jq_version = None

        with (
            patch("elle.ops.jq.engine.shutil.which", return_value="/usr/bin/jq"),
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stdout="jq-1.7.1\n"),
            ),
        ):
            version = mod.get_version()

        assert version == "jq-1.7.1"

        mod._jq_checked = False
        mod._jq_path = None
        mod._jq_version = None

    def test_get_version_returns_none_when_unavailable(self) -> None:
        """get_version returns None when jq is not installed."""
        import elle.ops.jq.engine as mod

        mod._jq_checked = False
        mod._jq_path = None
        mod._jq_version = None

        with patch("elle.ops.jq.engine.shutil.which", return_value=None):
            version = mod.get_version()

        assert version is None

        mod._jq_checked = False
        mod._jq_path = None
        mod._jq_version = None

    def test_get_status_available(self) -> None:
        """get_status returns a populated JQStatus when available."""
        import elle.ops.jq.engine as mod

        mod._jq_checked = False
        mod._jq_path = None
        mod._jq_version = None

        with (
            patch("elle.ops.jq.engine.shutil.which", return_value="/usr/bin/jq"),
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stdout="jq-1.7.1\n"),
            ),
        ):
            status = mod.get_status()

        assert status.available is True
        assert status.path == "/usr/bin/jq"
        assert status.version == "jq-1.7.1"

        mod._jq_checked = False
        mod._jq_path = None
        mod._jq_version = None

    def test_get_status_unavailable(self) -> None:
        """get_status shows unavailable when jq is missing."""
        import elle.ops.jq.engine as mod

        mod._jq_checked = False
        mod._jq_path = None
        mod._jq_version = None

        with patch("elle.ops.jq.engine.shutil.which", return_value=None):
            status = mod.get_status()

        assert status.available is False
        assert status.path is None

        mod._jq_checked = False
        mod._jq_path = None
        mod._jq_version = None


# ---------------------------------------------------------------------------
# Engine construction
# ---------------------------------------------------------------------------


class TestJQEngineInit:
    """Tests for JQEngine initialization."""

    def test_init_raises_when_unavailable(self) -> None:
        """JQEngine() raises JQUnavailableError when jq is absent."""
        import elle.ops.jq.engine as mod

        mod._jq_checked = False
        mod._jq_path = None

        with (
            patch("elle.ops.jq.engine.shutil.which", return_value=None),
            pytest.raises(JQUnavailableError),
        ):
            mod.JQEngine()

        mod._jq_checked = False
        mod._jq_path = None

    def test_init_succeeds_when_available(self) -> None:
        """JQEngine() succeeds when jq is found."""
        import elle.ops.jq.engine as mod

        mod._jq_checked = False
        mod._jq_path = None

        with patch("elle.ops.jq.engine.shutil.which", return_value="/usr/bin/jq"):
            engine = mod.JQEngine()

        assert engine._jq_path == "/usr/bin/jq"

        mod._jq_checked = False
        mod._jq_path = None

    def test_context_manager(self) -> None:
        """JQEngine works as a context manager."""
        import elle.ops.jq.engine as mod

        mod._jq_checked = False
        mod._jq_path = None

        with patch("elle.ops.jq.engine.shutil.which", return_value="/usr/bin/jq"):
            with mod.JQEngine() as engine:
                assert engine is not None

        mod._jq_checked = False
        mod._jq_path = None


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    """Create a JQEngine with a mocked jq binary path."""
    import elle.ops.jq.engine as mod

    mod._jq_checked = False
    mod._jq_path = None

    with patch("elle.ops.jq.engine.shutil.which", return_value="/usr/bin/jq"):
        eng = mod.JQEngine()

    yield eng

    mod._jq_checked = False
    mod._jq_path = None


# ---------------------------------------------------------------------------
# Query operations
# ---------------------------------------------------------------------------


class TestJQQuery:
    """Tests for query and query_file."""

    def test_query_returns_output(self, engine) -> None:
        """query() returns the jq output."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout='"localhost"\n'),
        ):
            result = engine.query('{"host":"localhost"}', ".host")

        assert result.success is True
        assert result.output == '"localhost"'
        assert result.parsed_output == "localhost"
        assert result.filter_used == ".host"

    def test_query_with_raw_output(self, engine) -> None:
        """query(raw_output=True) passes -r flag to jq."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="localhost\n"),
        ) as mock_run:
            result = engine.query('{"host":"localhost"}', ".host", raw_output=True)

        assert result.success is True
        assert result.output == "localhost"
        # Verify -r flag was passed
        args = mock_run.call_args[0][0]
        assert "-r" in args

    def test_query_with_slurp(self, engine) -> None:
        """query(slurp=True) passes -s flag to jq."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="[1,2]\n"),
        ) as mock_run:
            engine.query("1\n2\n", ".", slurp=True)

        args = mock_run.call_args[0][0]
        assert "-s" in args

    def test_query_failure(self, engine) -> None:
        """query() returns failure result on bad filter."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stderr="compile error", rc=2),
        ):
            result = engine.query("{}", "invalid|||")

        assert result.success is False
        assert result.error == "compile error"

    def test_query_multiline_output_parsed(self, engine) -> None:
        """query() parses multi-line output as a list."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout='1\n2\n3\n'),
        ):
            result = engine.query("[1,2,3]", ".[]")

        assert result.success is True
        assert result.parsed_output == [1, 2, 3]

    def test_query_file_returns_output(self, engine, tmp_path) -> None:
        """query_file() reads from a file on disk."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key": "value"}')

        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout='"value"\n'),
        ):
            result = engine.query_file(str(json_file), ".key")

        assert result.success is True
        assert result.parsed_output == "value"

    def test_query_file_not_found(self, engine) -> None:
        """query_file() returns error for missing files."""
        result = engine.query_file("/nonexistent/file.json", ".")
        assert result.success is False
        assert "File not found" in result.error

    def test_query_file_multiline_output(self, engine, tmp_path) -> None:
        """query_file() handles multi-line parsed output."""
        json_file = tmp_path / "data.json"
        json_file.write_text('[{"a":1},{"a":2}]')

        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout='1\n2\n'),
        ):
            result = engine.query_file(str(json_file), ".[].a")

        assert result.success is True
        assert result.parsed_output == [1, 2]


# ---------------------------------------------------------------------------
# Transform operations
# ---------------------------------------------------------------------------


class TestJQTransform:
    """Tests for transform."""

    def test_transform_returns_modified_json(self, engine) -> None:
        """transform() returns the modified JSON."""
        modified = json.dumps({"a": 1, "b": 2}, indent=2) + "\n"
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout=modified),
        ):
            result = engine.transform('{"a":1}', ".b = 2")

        assert result.success is True
        assert result.parsed_output == {"a": 1, "b": 2}

    def test_transform_with_sort_keys(self, engine) -> None:
        """transform(sort_keys=True) passes -S flag."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="{}\n"),
        ) as mock_run:
            engine.transform("{}", ".", sort_keys=True)

        args = mock_run.call_args[0][0]
        assert "-S" in args

    def test_transform_with_compact(self, engine) -> None:
        """transform(compact=True) passes -c flag."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="{}\n"),
        ) as mock_run:
            engine.transform("{}", ".", compact=True)

        args = mock_run.call_args[0][0]
        assert "-c" in args

    def test_transform_failure(self, engine) -> None:
        """transform() returns failure on error."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stderr="type error", rc=5),
        ):
            result = engine.transform("{}", ".foo |= bad")

        assert result.success is False
        assert result.error == "type error"


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


class TestJQValidate:
    """Tests for validate and validate_file."""

    def test_validate_valid_json(self, engine) -> None:
        """validate() returns success for valid JSON."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout='{"key":"value"}\n'),
        ):
            result = engine.validate('{"key":"value"}')

        assert result.success is True

    def test_validate_invalid_json(self, engine) -> None:
        """validate() returns failure for invalid JSON."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stderr="parse error", rc=2),
        ):
            result = engine.validate("not json")

        assert result.success is False

    def test_validate_file_valid(self, engine, tmp_path) -> None:
        """validate_file() returns success for a valid JSON file."""
        json_file = tmp_path / "valid.json"
        json_file.write_text('{"ok": true}')

        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout='{"ok":true}\n'),
        ):
            result = engine.validate_file(str(json_file))

        assert result.success is True

    def test_validate_file_missing(self, engine) -> None:
        """validate_file() returns error for missing file."""
        result = engine.validate_file("/nonexistent/file.json")
        assert result.success is False


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------


class TestJQFormat:
    """Tests for format()."""

    def test_format_pretty_prints(self, engine) -> None:
        """format() pretty-prints JSON by default."""
        pretty = json.dumps({"a": 1}, indent=2) + "\n"
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout=pretty),
        ):
            result = engine.format('{"a":1}')

        assert result.success is True
        assert result.parsed_output == {"a": 1}

    def test_format_compact(self, engine) -> None:
        """format(indent=0) requests compact output."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout='{"a":1}\n'),
        ) as mock_run:
            engine.format('{"a":1}', indent=0)

        args = mock_run.call_args[0][0]
        assert "-c" in args

    def test_format_sorted_keys(self, engine) -> None:
        """format(sort_keys=True) sorts output keys."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout='{"a":1,"b":2}\n'),
        ) as mock_run:
            engine.format('{"b":2,"a":1}', sort_keys=True)

        args = mock_run.call_args[0][0]
        assert "-S" in args


# ---------------------------------------------------------------------------
# High-level: get_value, set_value, delete_value, has_path
# ---------------------------------------------------------------------------


class TestJQHighLevel:
    """Tests for get_value, set_value, delete_value, has_path."""

    def test_get_value_returns_parsed(self, engine) -> None:
        """get_value() returns the parsed value at a path."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="5432\n"),
        ):
            value = engine.get_value('{"port":5432}', ".port")

        assert value == 5432

    def test_get_value_raises_on_invalid_filter(self, engine) -> None:
        """get_value() raises JQFilterError on bad path."""
        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stderr="syntax error", rc=2),
            ),
            pytest.raises(JQFilterError),
        ):
            engine.get_value("{}", "invalid|||")

    def test_set_value_string(self, engine) -> None:
        """set_value() sets a string value."""
        modified = json.dumps({"host": "newhost"}) + "\n"
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout=modified),
        ):
            result = engine.set_value('{"host":"old"}', ".host", "newhost")

        assert "newhost" in result

    def test_set_value_number(self, engine) -> None:
        """set_value() sets a numeric value."""
        modified = json.dumps({"port": 9090}) + "\n"
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout=modified),
        ):
            result = engine.set_value('{"port":8080}', ".port", 9090)

        assert "9090" in result

    def test_set_value_bool(self, engine) -> None:
        """set_value() sets a boolean value."""
        modified = json.dumps({"active": True}) + "\n"
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout=modified),
        ):
            result = engine.set_value('{"active":false}', ".active", True)

        assert "true" in result

    def test_set_value_none(self, engine) -> None:
        """set_value() sets null value."""
        modified = json.dumps({"key": None}) + "\n"
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout=modified),
        ):
            result = engine.set_value('{"key":"x"}', ".key", None)

        assert "null" in result

    def test_set_value_complex(self, engine) -> None:
        """set_value() sets complex (list/dict) values."""
        modified = json.dumps({"items": [1, 2, 3]}) + "\n"
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout=modified),
        ):
            result = engine.set_value('{"items":[]}', ".items", [1, 2, 3])

        assert "[1, 2, 3]" in result

    def test_set_value_raises_on_failure(self, engine) -> None:
        """set_value() raises JQFilterError on failure."""
        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stderr="bad path", rc=2),
            ),
            pytest.raises(JQFilterError),
        ):
            engine.set_value("{}", ".bad|||", "value")

    def test_delete_value(self, engine) -> None:
        """delete_value() removes a key."""
        modified = json.dumps({"a": 1}) + "\n"
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout=modified),
        ):
            result = engine.delete_value('{"a":1,"b":2}', ".b")

        assert "b" not in result

    def test_delete_value_raises_on_failure(self, engine) -> None:
        """delete_value() raises JQFilterError on failure."""
        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stderr="error", rc=2),
            ),
            pytest.raises(JQFilterError),
        ):
            engine.delete_value("{}", ".bad|||")

    def test_has_path_true(self, engine) -> None:
        """has_path() returns True when path exists."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="true\n"),
        ):
            assert engine.has_path('{"key":"value"}', ".key") is True

    def test_has_path_false(self, engine) -> None:
        """has_path() returns False when path does not exist."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="false\n"),
        ):
            assert engine.has_path('{"key":"value"}', ".missing") is False

    def test_has_path_on_failure(self, engine) -> None:
        """has_path() returns False on query failure."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stderr="error", rc=2),
        ):
            assert engine.has_path("{}", ".bad|||") is False


# ---------------------------------------------------------------------------
# Process (request dispatch)
# ---------------------------------------------------------------------------


class TestJQProcess:
    """Tests for process() dispatching to correct operations."""

    def test_process_with_json_input(self, engine) -> None:
        """process() dispatches to query() when json_input is provided."""
        request = JQRequest(json_input='{"key":"value"}', filter=".key")
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout='"value"\n'),
        ):
            result = engine.process(request)

        assert result.success is True
        assert result.parsed_output == "value"

    def test_process_with_file_path(self, engine, tmp_path) -> None:
        """process() dispatches to query_file() when file_path is provided."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key":"value"}')

        request = JQRequest(file_path=str(json_file), filter=".key")
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout='"value"\n'),
        ):
            result = engine.process(request)

        assert result.success is True

    def test_process_with_null_input(self, engine) -> None:
        """process() dispatches to null input mode."""
        request = JQRequest(filter="{}", null_input=True)
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="{}\n"),
        ) as mock_run:
            result = engine.process(request)

        assert result.success is True
        args = mock_run.call_args[0][0]
        assert "-n" in args

    def test_process_no_input_source(self, engine) -> None:
        """process() returns error when no input source is specified."""
        request = JQRequest(filter=".")
        result = engine.process(request)
        assert result.success is False
        assert "No input source" in result.error


# ---------------------------------------------------------------------------
# Error handling in _run_jq
# ---------------------------------------------------------------------------


class TestJQRunErrors:
    """Tests for subprocess error handling in _run_jq."""

    def test_timeout_returns_failure(self, engine) -> None:
        """_run_jq returns failure on subprocess timeout."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="jq", timeout=30),
        ):
            success, stdout, stderr, code = engine._run_jq(".")

        assert success is False
        assert "timed out" in stderr
        assert code == -1

    def test_generic_exception_returns_failure(self, engine) -> None:
        """_run_jq returns failure on unexpected exceptions."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            side_effect=OSError("broken pipe"),
        ):
            success, stdout, stderr, code = engine._run_jq(".")

        assert success is False
        assert "broken pipe" in stderr
        assert code == -1

    def test_run_jq_raises_if_path_none(self) -> None:
        """_run_jq raises JQUnavailableError if path is None."""
        import elle.ops.jq.engine as mod

        mod._jq_checked = False
        mod._jq_path = None

        with patch("elle.ops.jq.engine.shutil.which", return_value="/usr/bin/jq"):
            eng = mod.JQEngine()

        eng._jq_path = None

        with pytest.raises(JQUnavailableError):
            eng._run_jq(".")

        mod._jq_checked = False
        mod._jq_path = None

    def test_run_jq_flags_combination(self, engine) -> None:
        """_run_jq correctly combines multiple flags."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="ok\n"),
        ) as mock_run:
            engine._run_jq(
                ".",
                raw_output=True,
                slurp=True,
                compact=True,
                sort_keys=True,
            )

        args = mock_run.call_args[0][0]
        assert "-r" in args
        assert "-s" in args
        assert "-c" in args
        assert "-S" in args

    def test_run_jq_null_input_flag(self, engine) -> None:
        """_run_jq passes -n for null_input=True."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="{}\n"),
        ) as mock_run:
            engine._run_jq("{}", null_input=True)

        args = mock_run.call_args[0][0]
        assert "-n" in args

    def test_run_jq_from_file(self, engine, tmp_path) -> None:
        """_run_jq adds file path to command args when from_file is given."""
        json_file = tmp_path / "data.json"
        json_file.write_text("{}")

        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="{}\n"),
        ) as mock_run:
            engine._run_jq(".", from_file=str(json_file))

        args = mock_run.call_args[0][0]
        assert str(json_file) in args
        # When from_file is used, input should not be passed
        assert mock_run.call_args[1].get("input") is None


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------


class TestJQChangeDetection:
    """Tests for _detect_changes."""

    def test_detect_modify(self, engine) -> None:
        """_detect_changes detects modified values."""
        original = '{"a": 1}'
        modified = '{"a": 2}'
        changes = engine._detect_changes(original, modified)
        assert len(changes) == 1
        assert changes[0].operation == "modify"
        assert changes[0].old_value == 1
        assert changes[0].new_value == 2

    def test_detect_add(self, engine) -> None:
        """_detect_changes detects added keys."""
        original = '{"a": 1}'
        modified = '{"a": 1, "b": 2}'
        changes = engine._detect_changes(original, modified)
        assert len(changes) == 1
        assert changes[0].operation == "add"
        assert changes[0].path == "$.b"

    def test_detect_delete(self, engine) -> None:
        """_detect_changes detects deleted keys."""
        original = '{"a": 1, "b": 2}'
        modified = '{"a": 1}'
        changes = engine._detect_changes(original, modified)
        assert len(changes) == 1
        assert changes[0].operation == "delete"
        assert changes[0].path == "$.b"

    def test_detect_type_change(self, engine) -> None:
        """_detect_changes detects type changes."""
        original = '{"a": 1}'
        modified = '{"a": "one"}'
        changes = engine._detect_changes(original, modified)
        assert len(changes) == 1
        assert changes[0].operation == "type_change"

    def test_detect_array_resize(self, engine) -> None:
        """_detect_changes detects array resizes."""
        original = '{"items": [1, 2]}'
        modified = '{"items": [1, 2, 3]}'
        changes = engine._detect_changes(original, modified)
        assert len(changes) == 1
        assert changes[0].operation == "array_resize"

    def test_detect_no_changes(self, engine) -> None:
        """_detect_changes returns empty list for identical JSON."""
        data = '{"a": 1}'
        changes = engine._detect_changes(data, data)
        assert changes == []

    def test_detect_changes_invalid_json(self, engine) -> None:
        """_detect_changes returns empty list for invalid JSON."""
        changes = engine._detect_changes("not json", "also not json")
        assert changes == []


# ---------------------------------------------------------------------------
# Dry run and file editing
# ---------------------------------------------------------------------------


class TestJQDryRun:
    """Tests for dry run mode in execute_edit."""

    def test_dry_run_does_not_modify_file(self, engine, tmp_path) -> None:
        """execute_edit with dry_run=True does not change the file."""
        json_file = tmp_path / "data.json"
        original_content = '{"key": "value"}\n'
        json_file.write_text(original_content)

        request = JQEditRequest(
            file_path=str(json_file),
            filter='.key = "new"',
            description="test dry run",
            dry_run=True,
            skip_backup=True,
        )

        modified = '{"key": "new"}\n'
        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stdout=modified),
            ),
            patch("elle.ops.augeas.diff.unified_diff", return_value="--- a\n+++ b\n"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
        ):
            result = engine.execute_edit(request)

        assert result.success is True
        # File content should be unchanged
        assert json_file.read_text() == original_content


class TestJQExecuteEditErrors:
    """Tests for execute_edit error paths."""

    def test_execute_edit_file_not_found(self, engine) -> None:
        """execute_edit returns error when the file does not exist."""
        request = JQEditRequest(
            file_path="/nonexistent/file.json",
            filter=".",
            description="missing file",
        )
        result = engine.execute_edit(request)
        assert result.success is False
        assert "File not found" in result.error

    def test_execute_edit_backup_failure(self, engine, tmp_path) -> None:
        """execute_edit returns error when backup creation fails."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key":"value"}')

        request = JQEditRequest(
            file_path=str(json_file),
            filter=".",
            description="test backup failure",
            skip_backup=False,
        )

        with patch(
            "elle.ops.augeas.backup.backup_file",
            side_effect=OSError("disk full"),
        ):
            result = engine.execute_edit(request)

        assert result.success is False
        assert "Failed to create backup" in result.error

    def test_execute_edit_transform_failure(self, engine, tmp_path) -> None:
        """execute_edit returns error when jq transform fails."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key":"value"}')

        request = JQEditRequest(
            file_path=str(json_file),
            filter=".bad|||",
            description="bad filter",
            skip_backup=True,
        )

        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stderr="compile error", rc=2),
        ):
            result = engine.execute_edit(request)

        assert result.success is False
        assert result.error == "compile error"
