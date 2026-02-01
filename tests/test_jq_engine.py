"""Tests for the jq engine."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from elle.ops.jq.models import (
    JQEditRequest,
    JQFilterError,
    JQRequest,
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
    return subprocess.CompletedProcess(args=["jq"], returncode=rc, stdout=stdout, stderr=stderr)


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
            return_value=_make_completed(stdout="1\n2\n3\n"),
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
            return_value=_make_completed(stdout="1\n2\n"),
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


# ---------------------------------------------------------------------------
# Additional coverage: get_version edge cases
# ---------------------------------------------------------------------------


class TestGetVersionEdgeCases:
    """Tests for uncovered branches in get_version."""

    def test_get_version_cached(self) -> None:
        """get_version returns cached value without running subprocess."""
        import elle.ops.jq.engine as mod

        mod._jq_checked = False
        mod._jq_path = None
        mod._jq_version = "jq-1.6"

        version = mod.get_version()
        assert version == "jq-1.6"

        mod._jq_checked = False
        mod._jq_path = None
        mod._jq_version = None

    def test_get_version_subprocess_exception(self) -> None:
        """get_version returns None when subprocess raises."""
        import elle.ops.jq.engine as mod

        mod._jq_checked = False
        mod._jq_path = None
        mod._jq_version = None

        with (
            patch("elle.ops.jq.engine.shutil.which", return_value="/usr/bin/jq"),
            patch(
                "elle.ops.jq.engine.subprocess.run",
                side_effect=OSError("cannot exec"),
            ),
        ):
            version = mod.get_version()

        assert version is None

        mod._jq_checked = False
        mod._jq_path = None
        mod._jq_version = None

    def test_get_version_nonzero_returncode(self) -> None:
        """get_version returns None when jq --version exits with nonzero code."""
        import elle.ops.jq.engine as mod

        mod._jq_checked = False
        mod._jq_path = None
        mod._jq_version = None

        with (
            patch("elle.ops.jq.engine.shutil.which", return_value="/usr/bin/jq"),
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stderr="error", rc=1),
            ),
        ):
            version = mod.get_version()

        assert version is None

        mod._jq_checked = False
        mod._jq_path = None
        mod._jq_version = None


# ---------------------------------------------------------------------------
# Additional coverage: query / query_file JSON decode failures
# ---------------------------------------------------------------------------


class TestQueryParseEdgeCases:
    """Tests for JSON decode error branches in query and query_file."""

    def test_query_unparseable_output(self, engine) -> None:
        """query() handles output that is not valid JSON gracefully."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="not-json\n"),
        ):
            result = engine.query('{"a":1}', ".", raw_output=False)

        assert result.success is True
        assert result.parsed_output is None

    def test_query_file_unparseable_output(self, engine, tmp_path) -> None:
        """query_file() handles output that is not valid JSON gracefully."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key":"value"}')

        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="not-json\n"),
        ):
            result = engine.query_file(str(json_file), ".")

        assert result.success is True
        assert result.parsed_output is None

    def test_query_file_multiline_unparseable_output(self, engine, tmp_path) -> None:
        """query_file() returns None parsed_output for multi-line non-JSON."""
        json_file = tmp_path / "data.json"
        json_file.write_text("[1,2]")

        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="bad\ndata\n"),
        ):
            result = engine.query_file(str(json_file), ".[]")

        assert result.success is True
        assert result.parsed_output is None


# ---------------------------------------------------------------------------
# Additional coverage: transform JSON decode error
# ---------------------------------------------------------------------------


class TestTransformParseEdgeCases:
    """Tests for transform output that fails JSON parsing."""

    def test_transform_unparseable_output(self, engine) -> None:
        """transform() sets parsed_output to None when output is not JSON."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="not-json\n"),
        ):
            result = engine.transform('{"a":1}', ".")

        assert result.success is True
        assert result.parsed_output is None


# ---------------------------------------------------------------------------
# Additional coverage: get_value with null in output
# ---------------------------------------------------------------------------


class TestGetValueNullPath:
    """Tests for get_value when output contains 'null'."""

    def test_get_value_returns_none_for_null_output(self, engine) -> None:
        """get_value() returns None when jq fails and output contains 'null'."""
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="null\n", stderr="null is not defined", rc=5),
        ):
            value = engine.get_value('{"a":1}', ".missing")

        assert value is None


# ---------------------------------------------------------------------------
# Additional coverage: set_value with JSON literal string
# ---------------------------------------------------------------------------


class TestSetValueJsonLiteralString:
    """Tests for set_value with a string that is valid JSON."""

    def test_set_value_json_literal_string(self, engine) -> None:
        """set_value() passes valid JSON string literals directly."""
        modified = json.dumps({"items": [1, 2]}) + "\n"
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout=modified),
        ):
            result = engine.set_value('{"items":[]}', ".items", "[1, 2]")

        assert "[1, 2]" in result

    def test_set_value_bool_false(self, engine) -> None:
        """set_value() handles False boolean value."""
        modified = json.dumps({"active": False}) + "\n"
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout=modified),
        ):
            result = engine.set_value('{"active":true}', ".active", False)

        assert "false" in result

    def test_set_value_float(self, engine) -> None:
        """set_value() handles float values."""
        modified = json.dumps({"rate": 3.14}) + "\n"
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout=modified),
        ):
            result = engine.set_value('{"rate":0}', ".rate", 3.14)

        assert "3.14" in result


# ---------------------------------------------------------------------------
# Additional coverage: preview_edit
# ---------------------------------------------------------------------------


class TestPreviewEdit:
    """Tests for preview_edit."""

    def test_preview_edit_file_not_found(self, engine) -> None:
        """preview_edit returns empty preview for missing file."""
        request = JQEditRequest(
            file_path="/nonexistent/file.json",
            filter=".key = 1",
            description="test",
        )
        preview = engine.preview_edit(request)
        assert preview.is_valid_json is False
        assert preview.original_content == ""
        assert preview.proposed_content == ""

    def test_preview_edit_transform_failure(self, engine, tmp_path) -> None:
        """preview_edit returns invalid preview when transform fails."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key": "value"}\n')

        request = JQEditRequest(
            file_path=str(json_file),
            filter=".bad|||",
            description="bad filter",
        )

        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stderr="compile error", rc=2),
        ):
            preview = engine.preview_edit(request)

        assert preview.is_valid_json is False
        assert preview.original_content == '{"key": "value"}\n'
        assert preview.proposed_content == ""

    def test_preview_edit_success(self, engine, tmp_path) -> None:
        """preview_edit returns a valid preview with diff."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key": "value"}\n')

        request = JQEditRequest(
            file_path=str(json_file),
            filter='.key = "new"',
            description="update key",
        )

        modified = '{"key": "new"}'
        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stdout=modified),
            ),
            patch("elle.ops.augeas.diff.unified_diff", return_value="--- a\n+++ b\n") as mock_udiff,
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored") as mock_cdiff,
        ):
            preview = engine.preview_edit(request)

        assert preview.is_valid_json is True
        assert preview.proposed_content.endswith("\n")
        assert mock_udiff.called
        assert mock_cdiff.called

    def test_preview_edit_content_already_has_newline(self, engine, tmp_path) -> None:
        """preview_edit does not double-add trailing newline."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key": "value"}\n')

        request = JQEditRequest(
            file_path=str(json_file),
            filter='.key = "new"',
            description="update key",
        )

        modified = '{"key": "new"}\n'
        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stdout=modified),
            ),
            patch("elle.ops.augeas.diff.unified_diff", return_value="diff"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
        ):
            preview = engine.preview_edit(request)

        assert preview.is_valid_json is True
        assert preview.proposed_content == '{"key": "new"}\n'

    def test_preview_edit_privilege_check_exception(self, engine, tmp_path) -> None:
        """preview_edit handles exception during privilege check gracefully."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key": "value"}\n')

        request = JQEditRequest(
            file_path=str(json_file),
            filter='.key = "new"',
            description="update",
        )

        modified = '{"key": "new"}\n'
        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stdout=modified),
            ),
            patch("elle.ops.augeas.diff.unified_diff", return_value="diff"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
            patch("elle.ops.jq.engine.Path.cwd", side_effect=OSError("cwd failed")),
        ):
            preview = engine.preview_edit(request)

        assert preview.is_valid_json is True
        assert preview.requires_privilege is False


# ---------------------------------------------------------------------------
# Additional coverage: execute_edit write/validation/rollback paths
# ---------------------------------------------------------------------------


class TestExecuteEditWritePaths:
    """Tests for execute_edit: PermissionError, write failure, validation, rollback."""

    def test_execute_edit_permission_error(self, engine, tmp_path) -> None:
        """execute_edit returns error on PermissionError during write."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key": "value"}\n')

        request = JQEditRequest(
            file_path=str(json_file),
            filter='.key = "new"',
            description="test perm",
            skip_backup=True,
        )

        modified = '{"key": "new"}\n'
        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stdout=modified),
            ),
            patch.object(type(json_file), "write_text", side_effect=PermissionError("denied")),
        ):
            result = engine.execute_edit(request)

        assert result.success is False
        assert "Permission denied" in result.error
        assert result.requires_privilege is True

    def test_execute_edit_write_generic_failure(self, engine, tmp_path) -> None:
        """execute_edit returns error on unexpected write failure."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key": "value"}\n')

        request = JQEditRequest(
            file_path=str(json_file),
            filter='.key = "new"',
            description="test write error",
            skip_backup=True,
        )

        modified = '{"key": "new"}\n'
        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stdout=modified),
            ),
            patch.object(type(json_file), "write_text", side_effect=OSError("disk full")),
        ):
            result = engine.execute_edit(request)

        assert result.success is False
        assert "Failed to write file" in result.error

    def test_execute_edit_validation_failure_rollback_success(self, engine, tmp_path) -> None:
        """execute_edit rolls back when validation fails and backup exists."""
        from unittest.mock import MagicMock

        json_file = tmp_path / "data.json"
        json_file.write_text('{"key": "value"}\n')

        request = JQEditRequest(
            file_path=str(json_file),
            filter='.key = "new"',
            description="test rollback",
            skip_backup=False,
            skip_validation=False,
        )

        backup_record = MagicMock()
        backup_record.backup_path = "/tmp/backup.json"

        # First call: transform succeeds; second call: validate fails
        transform_result = _make_completed(stdout='{"key": "new"}\n')
        validate_result = _make_completed(stderr="parse error", rc=2)

        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                side_effect=[transform_result, validate_result],
            ),
            patch("elle.ops.augeas.backup.backup_file", return_value=backup_record),
            patch("elle.ops.augeas.backup.restore_file") as mock_restore,
        ):
            result = engine.execute_edit(request)

        assert result.success is False
        assert result.rollback_applied is True
        assert result.validation_passed is False
        assert "rolled back" in result.error
        mock_restore.assert_called_once_with("/tmp/backup.json")

    def test_execute_edit_validation_failure_rollback_failure(self, engine, tmp_path) -> None:
        """execute_edit reports both validation and rollback failures."""
        from unittest.mock import MagicMock

        json_file = tmp_path / "data.json"
        json_file.write_text('{"key": "value"}\n')

        request = JQEditRequest(
            file_path=str(json_file),
            filter='.key = "new"',
            description="test rollback fail",
            skip_backup=False,
            skip_validation=False,
        )

        backup_record = MagicMock()
        backup_record.backup_path = "/tmp/backup.json"

        transform_result = _make_completed(stdout='{"key": "new"}\n')
        validate_result = _make_completed(stderr="parse error", rc=2)

        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                side_effect=[transform_result, validate_result],
            ),
            patch("elle.ops.augeas.backup.backup_file", return_value=backup_record),
            patch(
                "elle.ops.augeas.backup.restore_file",
                side_effect=OSError("restore failed"),
            ),
        ):
            result = engine.execute_edit(request)

        assert result.success is False
        assert result.validation_passed is False
        assert "rollback failed" in result.error

    def test_execute_edit_success_with_changes(self, engine, tmp_path) -> None:
        """execute_edit succeeds and detects changes on a full write path."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key": "value"}\n')

        request = JQEditRequest(
            file_path=str(json_file),
            filter='.key = "new"',
            description="update key",
            skip_backup=True,
            skip_validation=True,
        )

        modified = '{"key": "new"}'
        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stdout=modified),
            ),
            patch("elle.ops.augeas.diff.unified_diff", return_value="diff-output"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored-output"),
        ):
            result = engine.execute_edit(request)

        assert result.success is True
        assert result.diff == "diff-output"
        assert result.diff_colored == "colored-output"
        assert result.validation_passed is True
        assert result.completed_at is not None

    def test_execute_edit_success_with_backup(self, engine, tmp_path) -> None:
        """execute_edit creates backup and succeeds when skip_backup is False."""
        from unittest.mock import MagicMock

        json_file = tmp_path / "data.json"
        json_file.write_text('{"key": "value"}\n')

        request = JQEditRequest(
            file_path=str(json_file),
            filter='.key = "new"',
            description="update key",
            skip_backup=False,
            skip_validation=True,
        )

        backup_record = MagicMock()
        backup_record.backup_path = "/tmp/backup.json"

        modified = '{"key": "new"}\n'
        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stdout=modified),
            ),
            patch("elle.ops.augeas.backup.backup_file", return_value=backup_record),
            patch("elle.ops.augeas.diff.unified_diff", return_value="diff"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
        ):
            result = engine.execute_edit(request)

        assert result.success is True
        assert result.backup_path == "/tmp/backup.json"

    def test_execute_edit_transform_failure_with_backup(self, engine, tmp_path) -> None:
        """execute_edit includes backup_path in error result when transform fails."""
        from unittest.mock import MagicMock

        json_file = tmp_path / "data.json"
        json_file.write_text('{"key":"value"}')

        request = JQEditRequest(
            file_path=str(json_file),
            filter=".bad|||",
            description="bad filter",
            skip_backup=False,
        )

        backup_record = MagicMock()
        backup_record.backup_path = "/tmp/backup.json"

        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stderr="compile error", rc=2),
            ),
            patch("elle.ops.augeas.backup.backup_file", return_value=backup_record),
        ):
            result = engine.execute_edit(request)

        assert result.success is False
        assert result.backup_path == "/tmp/backup.json"

    def test_execute_edit_validation_passes(self, engine, tmp_path) -> None:
        """execute_edit completes successfully when validation passes."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key": "value"}\n')

        request = JQEditRequest(
            file_path=str(json_file),
            filter='.key = "new"',
            description="update key",
            skip_backup=True,
            skip_validation=False,
        )

        transform_result = _make_completed(stdout='{"key": "new"}\n')
        validate_result = _make_completed(stdout='{"key": "new"}\n')

        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                side_effect=[transform_result, validate_result],
            ),
            patch("elle.ops.augeas.diff.unified_diff", return_value="diff"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
        ):
            result = engine.execute_edit(request)

        assert result.success is True
        assert result.validation_passed is True

    def test_execute_edit_content_no_trailing_newline(self, engine, tmp_path) -> None:
        """execute_edit appends newline when jq output lacks it."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key": "value"}\n')

        request = JQEditRequest(
            file_path=str(json_file),
            filter='.key = "new"',
            description="update key",
            skip_backup=True,
            skip_validation=True,
        )

        # Return output without trailing newline
        modified = '{"key": "new"}'
        with (
            patch(
                "elle.ops.jq.engine.subprocess.run",
                return_value=_make_completed(stdout=modified),
            ),
            patch("elle.ops.augeas.diff.unified_diff", return_value="diff"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
        ):
            result = engine.execute_edit(request)

        assert result.success is True
        # Verify newline was appended to file content
        assert json_file.read_text().endswith("\n")


# ---------------------------------------------------------------------------
# Additional coverage: _detect_changes array element comparison
# ---------------------------------------------------------------------------


class TestDetectChangesArrayElements:
    """Tests for array element-level change detection in _detect_changes."""

    def test_detect_array_element_modification(self, engine) -> None:
        """_detect_changes detects modifications within same-length arrays."""
        original = '{"items": [1, 2, 3]}'
        modified = '{"items": [1, 99, 3]}'
        changes = engine._detect_changes(original, modified)
        assert len(changes) == 1
        assert changes[0].operation == "modify"
        assert changes[0].path == "$.items[1]"
        assert changes[0].old_value == 2
        assert changes[0].new_value == 99

    def test_detect_nested_dict_changes(self, engine) -> None:
        """_detect_changes recurses into nested dictionaries."""
        original = '{"a": {"b": {"c": 1}}}'
        modified = '{"a": {"b": {"c": 2}}}'
        changes = engine._detect_changes(original, modified)
        assert len(changes) == 1
        assert changes[0].path == "$.a.b.c"
        assert changes[0].operation == "modify"

    def test_detect_array_of_objects(self, engine) -> None:
        """_detect_changes detects changes in arrays of objects."""
        original = '{"items": [{"name": "a"}, {"name": "b"}]}'
        modified = '{"items": [{"name": "a"}, {"name": "c"}]}'
        changes = engine._detect_changes(original, modified)
        assert len(changes) == 1
        assert changes[0].path == "$.items[1].name"
        assert changes[0].old_value == "b"
        assert changes[0].new_value == "c"

    def test_detect_multiple_changes(self, engine) -> None:
        """_detect_changes detects multiple simultaneous changes."""
        original = '{"a": 1, "b": 2, "c": 3}'
        modified = '{"a": 10, "b": 2, "d": 4}'
        changes = engine._detect_changes(original, modified)
        ops = {c.operation for c in changes}
        assert "modify" in ops  # a changed
        assert "delete" in ops  # c removed
        assert "add" in ops  # d added


# ---------------------------------------------------------------------------
# Additional coverage: process() with null_input failure and options
# ---------------------------------------------------------------------------


class TestProcessEdgeCases:
    """Tests for process() additional branches."""

    def test_process_null_input_failure(self, engine) -> None:
        """process() returns failure for null_input when jq errors."""
        request = JQRequest(filter="invalid|||", null_input=True)
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stderr="compile error", rc=2),
        ):
            result = engine.process(request)

        assert result.success is False
        assert result.error == "compile error"

    def test_process_null_input_with_compact_and_sort(self, engine) -> None:
        """process() passes compact and sort_keys for null_input."""
        request = JQRequest(filter="{}", null_input=True, compact=True, sort_keys=True)
        with patch(
            "elle.ops.jq.engine.subprocess.run",
            return_value=_make_completed(stdout="{}\n"),
        ) as mock_run:
            result = engine.process(request)

        assert result.success is True
        args = mock_run.call_args[0][0]
        assert "-c" in args
        assert "-S" in args
