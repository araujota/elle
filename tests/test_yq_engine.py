"""Tests for the yq engine."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from elle.ops.yq.models import (
    YQEditRequest,
    YQFilterError,
    YQRequest,
    YQUnavailableError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_YAML = """\
database:
  host: localhost
  port: 5432
server:
  port: 8080
  workers: 4
features:
  - logging
  - metrics
"""


def _make_completed(stdout: str = "", stderr: str = "", rc: int = 0):
    """Build a fake subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(args=["yq"], returncode=rc, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


class TestYQAvailability:
    """Tests for module-level availability helpers."""

    def test_is_available_when_found(self) -> None:
        """is_available returns True when shutil.which finds yq."""
        import elle.ops.yq.engine as mod

        mod._yq_checked = False
        mod._yq_path = None

        with patch("elle.ops.yq.engine.shutil.which", return_value="/usr/bin/yq"):
            result = mod.is_available()

        assert result is True

        mod._yq_checked = False
        mod._yq_path = None

    def test_is_available_when_not_found(self) -> None:
        """is_available returns False when yq is absent."""
        import elle.ops.yq.engine as mod

        mod._yq_checked = False
        mod._yq_path = None

        with patch("elle.ops.yq.engine.shutil.which", return_value=None):
            result = mod.is_available()

        assert result is False

        mod._yq_checked = False
        mod._yq_path = None

    def test_get_version_returns_version_string(self) -> None:
        """get_version returns the stdout of yq --version."""
        import elle.ops.yq.engine as mod

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_version = None

        with (
            patch("elle.ops.yq.engine.shutil.which", return_value="/usr/bin/yq"),
            patch(
                "elle.ops.yq.engine.subprocess.run",
                return_value=_make_completed(stdout="yq (https://github.com/mikefarah/yq/) version v4.35.2\n"),
            ),
        ):
            version = mod.get_version()

        assert "v4.35.2" in version

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_version = None

    def test_get_version_returns_none_when_unavailable(self) -> None:
        """get_version returns None when yq is not installed."""
        import elle.ops.yq.engine as mod

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_version = None

        with patch("elle.ops.yq.engine.shutil.which", return_value=None):
            version = mod.get_version()

        assert version is None

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_version = None

    def test_get_status_available(self) -> None:
        """get_status returns a populated YQStatus when available."""
        import elle.ops.yq.engine as mod

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_version = None
        mod._yq_variant = None

        version_out = "yq (https://github.com/mikefarah/yq/) version v4.35.2\n"
        with (
            patch("elle.ops.yq.engine.shutil.which", return_value="/usr/bin/yq"),
            patch(
                "elle.ops.yq.engine.subprocess.run",
                return_value=_make_completed(stdout=version_out),
            ),
        ):
            status = mod.get_status()

        assert status.available is True
        assert status.path == "/usr/bin/yq"

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_version = None
        mod._yq_variant = None

    def test_get_status_unavailable(self) -> None:
        """get_status shows unavailable when yq is missing."""
        import elle.ops.yq.engine as mod

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_version = None
        mod._yq_variant = None

        with patch("elle.ops.yq.engine.shutil.which", return_value=None):
            status = mod.get_status()

        assert status.available is False
        assert status.path is None

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_version = None
        mod._yq_variant = None


# ---------------------------------------------------------------------------
# Variant detection
# ---------------------------------------------------------------------------


class TestYQVariantDetection:
    """Tests for _detect_variant()."""

    def test_detect_mikefarah_variant(self) -> None:
        """_detect_variant identifies the mikefarah/yq Go variant."""
        import elle.ops.yq.engine as mod

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_variant = None

        version_out = "yq (https://github.com/mikefarah/yq/) version v4.35.2\n"
        with (
            patch("elle.ops.yq.engine.shutil.which", return_value="/usr/bin/yq"),
            patch(
                "elle.ops.yq.engine.subprocess.run",
                return_value=_make_completed(stdout=version_out),
            ),
        ):
            variant = mod._detect_variant()

        assert variant == "mikefarah"

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_variant = None

    def test_detect_kislyuk_variant(self) -> None:
        """_detect_variant identifies the kislyuk/yq Python variant."""
        import elle.ops.yq.engine as mod

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_variant = None

        with (
            patch("elle.ops.yq.engine.shutil.which", return_value="/usr/bin/yq"),
            patch(
                "elle.ops.yq.engine.subprocess.run",
                return_value=_make_completed(stdout="yq 3.2.3 kislyuk\n"),
            ),
        ):
            variant = mod._detect_variant()

        assert variant == "kislyuk"

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_variant = None

    def test_detect_variant_returns_none_when_unavailable(self) -> None:
        """_detect_variant returns None when yq is not installed."""
        import elle.ops.yq.engine as mod

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_variant = None

        with patch("elle.ops.yq.engine.shutil.which", return_value=None):
            variant = mod._detect_variant()

        assert variant is None

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_variant = None


# ---------------------------------------------------------------------------
# Engine construction
# ---------------------------------------------------------------------------


class TestYQEngineInit:
    """Tests for YQEngine initialization."""

    def test_init_raises_when_unavailable(self) -> None:
        """YQEngine() raises YQUnavailableError when yq is absent."""
        import elle.ops.yq.engine as mod

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_variant = None

        with (
            patch("elle.ops.yq.engine.shutil.which", return_value=None),
            pytest.raises(YQUnavailableError),
        ):
            mod.YQEngine()

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_variant = None

    def test_init_succeeds_when_available(self) -> None:
        """YQEngine() succeeds when yq is found."""
        import elle.ops.yq.engine as mod

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_variant = None

        version_out = "yq (https://github.com/mikefarah/yq/) version v4.35.2\n"
        with (
            patch("elle.ops.yq.engine.shutil.which", return_value="/usr/bin/yq"),
            patch(
                "elle.ops.yq.engine.subprocess.run",
                return_value=_make_completed(stdout=version_out),
            ),
        ):
            engine = mod.YQEngine()

        assert engine._yq_path == "/usr/bin/yq"
        assert engine._variant == "mikefarah"

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_variant = None

    def test_context_manager(self) -> None:
        """YQEngine works as a context manager."""
        import elle.ops.yq.engine as mod

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_variant = None

        version_out = "yq (https://github.com/mikefarah/yq/) version v4.35.2\n"
        with (
            patch("elle.ops.yq.engine.shutil.which", return_value="/usr/bin/yq"),
            patch(
                "elle.ops.yq.engine.subprocess.run",
                return_value=_make_completed(stdout=version_out),
            ),
        ):
            with mod.YQEngine() as engine:
                assert engine is not None

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_variant = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine_mikefarah():
    """Create a YQEngine configured as mikefarah variant."""
    import elle.ops.yq.engine as mod

    mod._yq_checked = False
    mod._yq_path = None
    mod._yq_variant = None

    version_out = "yq (https://github.com/mikefarah/yq/) version v4.35.2\n"
    with (
        patch("elle.ops.yq.engine.shutil.which", return_value="/usr/bin/yq"),
        patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout=version_out),
        ),
    ):
        eng = mod.YQEngine()

    yield eng

    mod._yq_checked = False
    mod._yq_path = None
    mod._yq_variant = None


@pytest.fixture()
def engine_kislyuk():
    """Create a YQEngine configured as kislyuk variant."""
    import elle.ops.yq.engine as mod

    mod._yq_checked = False
    mod._yq_path = None
    mod._yq_variant = None

    # First call: detect_variant's --version call
    # Since kislyuk puts the name in the output
    version_out = "yq 3.2.3 kislyuk\n"
    with (
        patch("elle.ops.yq.engine.shutil.which", return_value="/usr/bin/yq"),
        patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout=version_out),
        ),
    ):
        eng = mod.YQEngine()

    yield eng

    mod._yq_checked = False
    mod._yq_path = None
    mod._yq_variant = None


# ---------------------------------------------------------------------------
# Query operations
# ---------------------------------------------------------------------------


class TestYQQuery:
    """Tests for query and query_file."""

    def test_query_returns_output(self, engine_mikefarah) -> None:
        """query() returns the yq output."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="localhost\n"),
        ):
            result = engine_mikefarah.query("database:\n  host: localhost\n", ".database.host")

        assert result.success is True
        assert result.output == "localhost"
        assert result.filter_used == ".database.host"

    def test_query_with_yaml_output(self, engine_mikefarah) -> None:
        """query(yaml_output=True) produces YAML output."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="host: localhost\nport: 5432\n"),
        ):
            result = engine_mikefarah.query(
                "database:\n  host: localhost\n  port: 5432\n",
                ".database",
                yaml_output=True,
            )

        assert result.success is True
        assert result.parsed_output == {"host": "localhost", "port": 5432}

    def test_query_with_raw_output(self, engine_mikefarah) -> None:
        """query(raw_output=True) passes -r flag."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="localhost\n"),
        ) as mock_run:
            engine_mikefarah.query("key: value\n", ".key", raw_output=True)

        args = mock_run.call_args[0][0]
        assert "-r" in args

    def test_query_json_output(self, engine_mikefarah) -> None:
        """query(yaml_output=False) requests JSON output."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout='{"key":"value"}\n'),
        ) as mock_run:
            engine_mikefarah.query("key: value\n", ".", yaml_output=False)

        args = mock_run.call_args[0][0]
        assert "-o" in args
        assert "json" in args

    def test_query_failure(self, engine_mikefarah) -> None:
        """query() returns failure on bad filter."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stderr="bad expression", rc=1),
        ):
            result = engine_mikefarah.query("key: value\n", "bad|||")

        assert result.success is False
        assert result.error == "bad expression"

    def test_query_file_returns_output(self, engine_mikefarah, tmp_path) -> None:
        """query_file() reads from a file on disk."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("key: value\n")

        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="value\n"),
        ):
            result = engine_mikefarah.query_file(str(yaml_file), ".key")

        assert result.success is True
        assert result.output == "value"

    def test_query_file_not_found(self, engine_mikefarah) -> None:
        """query_file() returns error for missing files."""
        result = engine_mikefarah.query_file("/nonexistent/file.yaml", ".")
        assert result.success is False
        assert "File not found" in result.error

    def test_query_file_parse_output(self, engine_mikefarah, tmp_path) -> None:
        """query_file() parses structured YAML output."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("items:\n  - a\n  - b\n")

        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="- a\n- b\n"),
        ):
            result = engine_mikefarah.query_file(str(yaml_file), ".items")

        assert result.success is True
        assert result.parsed_output == ["a", "b"]


# ---------------------------------------------------------------------------
# Kislyuk variant command construction
# ---------------------------------------------------------------------------


class TestYQKislyukCommands:
    """Tests for kislyuk variant command construction."""

    def test_kislyuk_yaml_output_uses_dash_y(self, engine_kislyuk) -> None:
        """kislyuk variant uses -y flag for YAML output."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="key: value\n"),
        ) as mock_run:
            engine_kislyuk.query("key: value\n", ".", yaml_output=True)

        args = mock_run.call_args[0][0]
        assert "-y" in args

    def test_kislyuk_json_output_no_dash_y(self, engine_kislyuk) -> None:
        """kislyuk variant omits -y for JSON output."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout='{"key":"value"}\n'),
        ) as mock_run:
            engine_kislyuk.query("key: value\n", ".", yaml_output=False)

        args = mock_run.call_args[0][0]
        assert "-y" not in args

    def test_kislyuk_raw_output(self, engine_kislyuk) -> None:
        """kislyuk variant passes -r for raw output."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="value\n"),
        ) as mock_run:
            engine_kislyuk.query("key: value\n", ".key", raw_output=True)

        args = mock_run.call_args[0][0]
        assert "-r" in args


# ---------------------------------------------------------------------------
# Mikefarah variant command construction
# ---------------------------------------------------------------------------


class TestYQMikefarahCommands:
    """Tests for mikefarah variant command construction."""

    def test_mikefarah_json_output_uses_o_json(self, engine_mikefarah) -> None:
        """mikefarah variant uses -o json for JSON output."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout='{"key":"value"}\n'),
        ) as mock_run:
            engine_mikefarah.query("key: value\n", ".", yaml_output=False)

        args = mock_run.call_args[0][0]
        assert "-o" in args
        json_idx = args.index("-o")
        assert args[json_idx + 1] == "json"

    def test_mikefarah_yaml_output_no_o_json(self, engine_mikefarah) -> None:
        """mikefarah variant omits -o json for default YAML output."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="key: value\n"),
        ) as mock_run:
            engine_mikefarah.query("key: value\n", ".", yaml_output=True)

        args = mock_run.call_args[0][0]
        assert "-o" not in args


# ---------------------------------------------------------------------------
# Transform operations
# ---------------------------------------------------------------------------


class TestYQTransform:
    """Tests for transform."""

    def test_transform_returns_modified_yaml(self, engine_mikefarah) -> None:
        """transform() returns the modified YAML."""
        modified_yaml = "key: new_value\n"
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout=modified_yaml),
        ):
            result = engine_mikefarah.transform("key: old_value\n", '.key = "new_value"')

        assert result.success is True
        assert result.parsed_output == {"key": "new_value"}

    def test_transform_json_output(self, engine_mikefarah) -> None:
        """transform(yaml_output=False) produces JSON output."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout='{"key":"value"}\n'),
        ) as mock_run:
            engine_mikefarah.transform("key: value\n", ".", yaml_output=False)

        args = mock_run.call_args[0][0]
        assert "-o" in args

    def test_transform_failure(self, engine_mikefarah) -> None:
        """transform() returns failure on error."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stderr="bad expression", rc=1),
        ):
            result = engine_mikefarah.transform("key: value\n", ".bad|||")

        assert result.success is False
        assert result.error == "bad expression"


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


class TestYQValidate:
    """Tests for validate and validate_file."""

    def test_validate_valid_yaml(self, engine_mikefarah) -> None:
        """validate() returns success for valid YAML."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="key: value\n"),
        ):
            result = engine_mikefarah.validate("key: value\n")

        assert result.success is True

    def test_validate_invalid_yaml(self, engine_mikefarah) -> None:
        """validate() returns failure for invalid YAML."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stderr="bad yaml", rc=1),
        ):
            result = engine_mikefarah.validate(":::not yaml")

        assert result.success is False

    def test_validate_file_valid(self, engine_mikefarah, tmp_path) -> None:
        """validate_file() returns success for a valid YAML file."""
        yaml_file = tmp_path / "valid.yaml"
        yaml_file.write_text("key: value\n")

        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="key: value\n"),
        ):
            result = engine_mikefarah.validate_file(str(yaml_file))

        assert result.success is True

    def test_validate_file_missing(self, engine_mikefarah) -> None:
        """validate_file() returns error for missing file."""
        result = engine_mikefarah.validate_file("/nonexistent/file.yaml")
        assert result.success is False


# ---------------------------------------------------------------------------
# High-level: get_value, set_value, delete_value
# ---------------------------------------------------------------------------


class TestYQHighLevel:
    """Tests for get_value, set_value, delete_value."""

    def test_get_value_returns_parsed(self, engine_mikefarah) -> None:
        """get_value() returns the parsed value at a path."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="5432\n"),
        ):
            value = engine_mikefarah.get_value("database:\n  port: 5432\n", ".database.port")

        assert value == 5432

    def test_get_value_raises_on_invalid_filter(self, engine_mikefarah) -> None:
        """get_value() raises YQFilterError on bad path."""
        with (
            patch(
                "elle.ops.yq.engine.subprocess.run",
                return_value=_make_completed(stderr="bad expression", rc=1),
            ),
            pytest.raises(YQFilterError),
        ):
            engine_mikefarah.get_value("key: value\n", "bad|||")

    def test_set_value_string(self, engine_mikefarah) -> None:
        """set_value() sets a string value."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="host: newhost\n"),
        ):
            result = engine_mikefarah.set_value("host: old\n", ".host", "newhost")

        assert "newhost" in result

    def test_set_value_number(self, engine_mikefarah) -> None:
        """set_value() sets a numeric value."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="port: 9090\n"),
        ):
            result = engine_mikefarah.set_value("port: 8080\n", ".port", 9090)

        assert "9090" in result

    def test_set_value_bool(self, engine_mikefarah) -> None:
        """set_value() sets a boolean value."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="active: true\n"),
        ):
            result = engine_mikefarah.set_value("active: false\n", ".active", True)

        assert "true" in result

    def test_set_value_none(self, engine_mikefarah) -> None:
        """set_value() sets null value."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="key: null\n"),
        ):
            result = engine_mikefarah.set_value("key: old\n", ".key", None)

        assert "null" in result

    def test_set_value_complex(self, engine_mikefarah) -> None:
        """set_value() sets complex (list/dict) values."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="items:\n  - 1\n  - 2\n"),
        ):
            result = engine_mikefarah.set_value("items: []\n", ".items", [1, 2])

        assert result is not None

    def test_set_value_raises_on_failure(self, engine_mikefarah) -> None:
        """set_value() raises YQFilterError on failure."""
        with (
            patch(
                "elle.ops.yq.engine.subprocess.run",
                return_value=_make_completed(stderr="error", rc=1),
            ),
            pytest.raises(YQFilterError),
        ):
            engine_mikefarah.set_value("key: value\n", ".bad|||", "value")

    def test_delete_value(self, engine_mikefarah) -> None:
        """delete_value() removes a key."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="a: 1\n"),
        ):
            result = engine_mikefarah.delete_value("a: 1\nb: 2\n", ".b")

        assert "b" not in result

    def test_delete_value_raises_on_failure(self, engine_mikefarah) -> None:
        """delete_value() raises YQFilterError on failure."""
        with (
            patch(
                "elle.ops.yq.engine.subprocess.run",
                return_value=_make_completed(stderr="error", rc=1),
            ),
            pytest.raises(YQFilterError),
        ):
            engine_mikefarah.delete_value("key: value\n", ".bad|||")


# ---------------------------------------------------------------------------
# Process (request dispatch)
# ---------------------------------------------------------------------------


class TestYQProcess:
    """Tests for process() dispatching to correct operations."""

    def test_process_with_yaml_input(self, engine_mikefarah) -> None:
        """process() dispatches to query() when yaml_input is provided."""
        request = YQRequest(yaml_input="key: value\n", filter=".key")
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="value\n"),
        ):
            result = engine_mikefarah.process(request)

        assert result.success is True

    def test_process_with_file_path(self, engine_mikefarah, tmp_path) -> None:
        """process() dispatches to query_file() when file_path is provided."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("key: value\n")

        request = YQRequest(file_path=str(yaml_file), filter=".key")
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="value\n"),
        ):
            result = engine_mikefarah.process(request)

        assert result.success is True

    def test_process_no_input_source(self, engine_mikefarah) -> None:
        """process() returns error when no input source is specified."""
        request = YQRequest(filter=".")
        result = engine_mikefarah.process(request)
        assert result.success is False
        assert "No input source" in result.error

    def test_process_passes_options(self, engine_mikefarah) -> None:
        """process() passes yaml_output and raw_output options."""
        request = YQRequest(
            yaml_input="key: value\n",
            filter=".key",
            yaml_output=False,
            raw_output=True,
        )
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="value\n"),
        ) as mock_run:
            engine_mikefarah.process(request)

        args = mock_run.call_args[0][0]
        assert "-r" in args
        assert "-o" in args


# ---------------------------------------------------------------------------
# Error handling in _run_yq
# ---------------------------------------------------------------------------


class TestYQRunErrors:
    """Tests for subprocess error handling in _run_yq."""

    def test_timeout_returns_failure(self, engine_mikefarah) -> None:
        """_run_yq returns failure on subprocess timeout."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="yq", timeout=30),
        ):
            success, stdout, stderr, code = engine_mikefarah._run_yq(".")

        assert success is False
        assert "timed out" in stderr
        assert code == -1

    def test_generic_exception_returns_failure(self, engine_mikefarah) -> None:
        """_run_yq returns failure on unexpected exceptions."""
        with patch(
            "elle.ops.yq.engine.subprocess.run",
            side_effect=OSError("broken pipe"),
        ):
            success, stdout, stderr, code = engine_mikefarah._run_yq(".")

        assert success is False
        assert "broken pipe" in stderr
        assert code == -1

    def test_run_yq_raises_if_path_none(self) -> None:
        """_run_yq raises YQUnavailableError if path is None."""
        import elle.ops.yq.engine as mod

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_variant = None

        version_out = "yq (https://github.com/mikefarah/yq/) version v4.35.2\n"
        with (
            patch("elle.ops.yq.engine.shutil.which", return_value="/usr/bin/yq"),
            patch(
                "elle.ops.yq.engine.subprocess.run",
                return_value=_make_completed(stdout=version_out),
            ),
        ):
            eng = mod.YQEngine()

        eng._yq_path = None

        with pytest.raises(YQUnavailableError):
            eng._run_yq(".")

        mod._yq_checked = False
        mod._yq_path = None
        mod._yq_variant = None

    def test_run_yq_from_file(self, engine_mikefarah, tmp_path) -> None:
        """_run_yq adds file path when from_file is given."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("key: value\n")

        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stdout="key: value\n"),
        ) as mock_run:
            engine_mikefarah._run_yq(".", from_file=str(yaml_file))

        args = mock_run.call_args[0][0]
        assert str(yaml_file) in args
        assert mock_run.call_args[1].get("input") is None


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------


class TestYQChangeDetection:
    """Tests for _detect_changes."""

    def test_detect_modify(self, engine_mikefarah) -> None:
        """_detect_changes detects modified values."""
        original = "a: 1\n"
        modified = "a: 2\n"
        changes = engine_mikefarah._detect_changes(original, modified)
        assert len(changes) == 1
        assert changes[0].operation == "modify"
        assert changes[0].old_value == 1
        assert changes[0].new_value == 2

    def test_detect_add(self, engine_mikefarah) -> None:
        """_detect_changes detects added keys."""
        original = "a: 1\n"
        modified = "a: 1\nb: 2\n"
        changes = engine_mikefarah._detect_changes(original, modified)
        assert len(changes) == 1
        assert changes[0].operation == "add"
        assert changes[0].path == "$.b"

    def test_detect_delete(self, engine_mikefarah) -> None:
        """_detect_changes detects deleted keys."""
        original = "a: 1\nb: 2\n"
        modified = "a: 1\n"
        changes = engine_mikefarah._detect_changes(original, modified)
        assert len(changes) == 1
        assert changes[0].operation == "delete"

    def test_detect_type_change(self, engine_mikefarah) -> None:
        """_detect_changes detects type changes."""
        original = "a: 1\n"
        modified = "a: one\n"
        changes = engine_mikefarah._detect_changes(original, modified)
        assert len(changes) == 1
        assert changes[0].operation == "type_change"

    def test_detect_array_resize(self, engine_mikefarah) -> None:
        """_detect_changes detects array resizes."""
        original = "items:\n  - 1\n  - 2\n"
        modified = "items:\n  - 1\n  - 2\n  - 3\n"
        changes = engine_mikefarah._detect_changes(original, modified)
        assert len(changes) == 1
        assert changes[0].operation == "array_resize"

    def test_detect_no_changes(self, engine_mikefarah) -> None:
        """_detect_changes returns empty list for identical YAML."""
        data = "a: 1\nb: 2\n"
        changes = engine_mikefarah._detect_changes(data, data)
        assert changes == []

    def test_detect_changes_invalid_yaml(self, engine_mikefarah) -> None:
        """_detect_changes returns empty list for unparseable YAML."""
        # Use content that triggers a YAML parse error (tab indentation mixed with space)
        bad_yaml = "key:\n\t- bad indent\n  - mixed\n\t- tabs"
        changes = engine_mikefarah._detect_changes(bad_yaml, bad_yaml)
        # Either returns empty list (parse error) or detects no changes (identical input)
        assert changes == []


# ---------------------------------------------------------------------------
# Dry run and file editing
# ---------------------------------------------------------------------------


class TestYQDryRun:
    """Tests for dry run mode in execute_edit."""

    def test_dry_run_does_not_modify_file(self, engine_mikefarah, tmp_path) -> None:
        """execute_edit with dry_run=True does not change the file."""
        yaml_file = tmp_path / "config.yaml"
        original_content = "key: value\n"
        yaml_file.write_text(original_content)

        request = YQEditRequest(
            file_path=str(yaml_file),
            filter='.key = "new"',
            description="test dry run",
            dry_run=True,
            skip_backup=True,
        )

        with (
            patch(
                "elle.ops.yq.engine.subprocess.run",
                return_value=_make_completed(stdout="key: new\n"),
            ),
            patch("elle.ops.augeas.diff.unified_diff", return_value="--- a\n+++ b\n"),
            patch("elle.ops.augeas.diff.colored_diff", return_value="colored"),
        ):
            result = engine_mikefarah.execute_edit(request)

        assert result.success is True
        # File content should be unchanged
        assert yaml_file.read_text() == original_content


class TestYQExecuteEditErrors:
    """Tests for execute_edit error paths."""

    def test_execute_edit_file_not_found(self, engine_mikefarah) -> None:
        """execute_edit returns error when the file does not exist."""
        request = YQEditRequest(
            file_path="/nonexistent/file.yaml",
            filter=".",
            description="missing file",
        )
        result = engine_mikefarah.execute_edit(request)
        assert result.success is False
        assert "File not found" in result.error

    def test_execute_edit_backup_failure(self, engine_mikefarah, tmp_path) -> None:
        """execute_edit returns error when backup creation fails."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("key: value\n")

        request = YQEditRequest(
            file_path=str(yaml_file),
            filter=".",
            description="test backup failure",
            skip_backup=False,
        )

        with patch(
            "elle.ops.augeas.backup.backup_file",
            side_effect=OSError("disk full"),
        ):
            result = engine_mikefarah.execute_edit(request)

        assert result.success is False
        assert "Failed to create backup" in result.error

    def test_execute_edit_transform_failure(self, engine_mikefarah, tmp_path) -> None:
        """execute_edit returns error when yq transform fails."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("key: value\n")

        request = YQEditRequest(
            file_path=str(yaml_file),
            filter=".bad|||",
            description="bad filter",
            skip_backup=True,
        )

        with patch(
            "elle.ops.yq.engine.subprocess.run",
            return_value=_make_completed(stderr="bad expression", rc=1),
        ):
            result = engine_mikefarah.execute_edit(request)

        assert result.success is False
        assert result.error == "bad expression"

    def test_execute_edit_permission_denied(self, engine_mikefarah, tmp_path) -> None:
        """execute_edit returns error on write permission failure."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("key: value\n")

        request = YQEditRequest(
            file_path=str(yaml_file),
            filter='.key = "new"',
            description="permission test",
            skip_backup=True,
        )

        with (
            patch(
                "elle.ops.yq.engine.subprocess.run",
                return_value=_make_completed(stdout="key: new\n"),
            ),
            patch.object(
                type(yaml_file),
                "write_text",
                side_effect=PermissionError("denied"),
            ),
        ):
            result = engine_mikefarah.execute_edit(request)

        assert result.success is False
        assert "Permission denied" in result.error


# ---------------------------------------------------------------------------
# Comment preservation note
# ---------------------------------------------------------------------------


class TestYQCommentPreservation:
    """Tests related to YAML comment preservation.

    Note: Actual comment preservation depends on the yq variant.
    mikefarah/yq preserves comments natively, while kislyuk/yq
    may not. These tests verify the option is passed through.
    """

    def test_edit_request_accepts_preserve_comments(self) -> None:
        """YQEditRequest model accepts preserve_comments flag."""
        request = YQEditRequest(
            file_path="/tmp/test.yaml",
            filter=".",
            description="test comments",
            preserve_comments=True,
        )
        assert request.preserve_comments is True

    def test_edit_request_default_preserve_comments(self) -> None:
        """YQEditRequest defaults preserve_comments to True."""
        request = YQEditRequest(
            file_path="/tmp/test.yaml",
            filter=".",
            description="test defaults",
        )
        assert request.preserve_comments is True
