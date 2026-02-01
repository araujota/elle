from __future__ import annotations

"""Mock-based tests for elle.capabilities.autogen.store.

These tests patch ``get_conn`` so they run without a real PostgreSQL
database, making them suitable for CI environments where the existing
``test_autogen_store.py`` (which requires ``autogen_conn``) is skipped.
"""

import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from elle.capabilities.autogen.models import (
    GeneratedCapabilitySpec,
    InputFieldSpec,
    StoredCapability,
    TrustLevel,
)
from elle.capabilities.autogen.store import (
    AutogenStore,
    get_store,
    reset_store,
)

PATCH_GET_CONN = "elle.capabilities.autogen.store.get_conn"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(overrides=None):
    """Return a dict that mimics a database row from ``generated_capabilities``."""
    row = {
        "id": "test-id",
        "capability_name": "test.cap",
        "spec_json": '{"name": "test"}',
        "input_model_code": "class Input: pass",
        "output_model_code": "class Output: pass",
        "capability_class_code": "class Cap: pass",
        "source_command": "test",
        "man_page_hash": "abc123",
        "generated_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "trust_level": "official",
        "approved": True,
        "enabled": True,
        "source_package": "testpkg",
        "package_version": "1.0",
        "binary_path": "/usr/bin/test",
    }
    if overrides:
        row.update(overrides)
    return row


def _make_spec(name="test.capability", source_command="test"):
    """Return a minimal ``GeneratedCapabilitySpec``."""
    return GeneratedCapabilitySpec(
        name=name,
        display_name="Test Capability",
        description="A test capability",
        domain="file",
        risk_level="low",
        side_effects=("file_write",),
        input_fields=(InputFieldSpec(name="path", field_type="str", description="File path"),),
        command_template="test {path}",
        source_command=source_command,
        confidence_score=0.8,
    )


@contextmanager
def _mock_conn(mock_conn):
    """Wrap *mock_conn* so it behaves like ``get_conn`` (a context manager)."""
    yield mock_conn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure the module-level singleton is cleared before and after each test."""
    reset_store()
    yield
    reset_store()


@pytest.fixture()
def mock_conn():
    """Return a ``MagicMock`` that acts as a psycopg ``Connection``."""
    return MagicMock()


@pytest.fixture()
def store():
    """Return a fresh ``AutogenStore`` instance (no DB connection needed)."""
    return AutogenStore()


# ===========================================================================
# _row_to_stored
# ===========================================================================


class TestRowToStored:
    """Tests for the ``_row_to_stored`` private helper."""

    def test_generated_at_iso_string(self, store):
        """ISO-8601 string in ``generated_at`` is parsed to a datetime."""
        row = _make_row({"generated_at": "2024-06-15T12:30:00+00:00"})
        result = store._row_to_stored(row)

        assert isinstance(result.generated_at, datetime)
        assert result.generated_at == datetime(2024, 6, 15, 12, 30, tzinfo=timezone.utc)

    def test_generated_at_datetime_passthrough(self, store):
        """A ``datetime`` object in ``generated_at`` is kept as-is."""
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        row = _make_row({"generated_at": dt})
        result = store._row_to_stored(row)

        assert result.generated_at is dt

    def test_spec_json_dict_converted_to_str(self, store):
        """A ``dict`` in ``spec_json`` is converted via ``str()``."""
        spec_dict = {"name": "test", "domain": "file"}
        row = _make_row({"spec_json": spec_dict})
        result = store._row_to_stored(row)

        assert isinstance(result.spec_json, str)
        assert result.spec_json == str(spec_dict)

    def test_spec_json_string_passthrough(self, store):
        """A plain string in ``spec_json`` is passed through unchanged."""
        json_str = '{"name": "test"}'
        row = _make_row({"spec_json": json_str})
        result = store._row_to_stored(row)

        assert result.spec_json == json_str

    def test_package_fields_present(self, store):
        """Package metadata fields are mapped when present in the row."""
        row = _make_row(
            {
                "source_package": "nginx",
                "package_version": "1.24.0",
                "binary_path": "/usr/sbin/nginx",
            }
        )
        result = store._row_to_stored(row)

        assert result.source_package == "nginx"
        assert result.package_version == "1.24.0"
        assert result.binary_path == "/usr/sbin/nginx"

    def test_package_fields_absent(self, store):
        """Package metadata fields default to ``None`` when missing from the row."""
        row = _make_row()
        # Simulate a row that does not contain the optional keys at all.
        row.pop("source_package", None)
        row.pop("package_version", None)
        row.pop("binary_path", None)
        result = store._row_to_stored(row)

        assert result.source_package is None
        assert result.package_version is None
        assert result.binary_path is None


# ===========================================================================
# AutogenStore.save
# ===========================================================================


class TestSave:
    """Tests for ``AutogenStore.save``."""

    def test_save_returns_id(self, store, mock_conn):
        """A successful insert returns the capability id from RETURNING."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": "returned-id-123"}
        mock_conn.execute.return_value = mock_cursor

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            cap_id = store.save(
                spec=_make_spec(),
                input_model_code="class In: pass",
                output_model_code="class Out: pass",
                capability_class_code="class Cap: pass",
                man_page_text="man page text",
                trust_level=TrustLevel.OFFICIAL,
            )

        assert cap_id == "returned-id-123"
        mock_conn.execute.assert_called_once()

    def test_save_upsert_returns_existing_id(self, store, mock_conn):
        """On conflict the RETURNING clause returns the *existing* row id."""
        existing_id = "existing-uuid-456"
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": existing_id}
        mock_conn.execute.return_value = mock_cursor

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            cap_id = store.save(
                spec=_make_spec(),
                input_model_code="v2",
                output_model_code="v2",
                capability_class_code="v2",
                man_page_text="updated man",
            )

        assert cap_id == existing_id

    def test_save_with_optional_fields(self, store, mock_conn):
        """Optional parameters (validation_json, source_package, etc.) are forwarded."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": "new-id"}
        mock_conn.execute.return_value = mock_cursor

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            store.save(
                spec=_make_spec(),
                input_model_code="",
                output_model_code="",
                capability_class_code="",
                man_page_text="man",
                validation_json='{"passed": true}',
                source_package="curl",
                package_version="7.88.1",
                binary_path="/usr/bin/curl",
            )

        # Verify the execute call included all 14 positional parameters.
        args = mock_conn.execute.call_args
        params_tuple = args[0][1]  # second positional arg is the parameter tuple
        assert len(params_tuple) == 14
        # validation_json, source_package, package_version, binary_path are the last four
        assert params_tuple[10] == '{"passed": true}'
        assert params_tuple[11] == "curl"
        assert params_tuple[12] == "7.88.1"
        assert params_tuple[13] == "/usr/bin/curl"


# ===========================================================================
# AutogenStore.get / get_by_id
# ===========================================================================


class TestGetMethods:
    """Tests for ``get`` and ``get_by_id``."""

    def test_get_found(self, store, mock_conn):
        """``get`` returns a ``StoredCapability`` when the row exists."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = _make_row()
        mock_conn.execute.return_value = mock_cursor

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.get("test.cap")

        assert isinstance(result, StoredCapability)
        assert result.capability_name == "test.cap"

    def test_get_not_found(self, store, mock_conn):
        """``get`` returns ``None`` when no row matches."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.execute.return_value = mock_cursor

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.get("nonexistent")

        assert result is None

    def test_get_by_id_found(self, store, mock_conn):
        """``get_by_id`` returns a ``StoredCapability`` when the row exists."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = _make_row({"id": "lookup-id"})
        mock_conn.execute.return_value = mock_cursor

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.get_by_id("lookup-id")

        assert isinstance(result, StoredCapability)
        assert result.id == "lookup-id"

    def test_get_by_id_not_found(self, store, mock_conn):
        """``get_by_id`` returns ``None`` when no row matches."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.execute.return_value = mock_cursor

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.get_by_id("missing-id")

        assert result is None


# ===========================================================================
# AutogenStore.list_* methods
# ===========================================================================


def _setup_list_mock(mock_conn, rows):
    """Configure *mock_conn* so ``execute(...).fetchall()`` returns *rows*."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_conn.execute.return_value = mock_cursor


class TestListMethods:
    """Tests for the various ``list_*`` query methods."""

    # --- list_all ---

    def test_list_all_with_rows(self, store, mock_conn):
        rows = [_make_row({"capability_name": "a.cap"}), _make_row({"capability_name": "b.cap"})]
        _setup_list_mock(mock_conn, rows)

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_all()

        assert len(result) == 2
        assert result[0].capability_name == "a.cap"
        assert result[1].capability_name == "b.cap"

    def test_list_all_empty(self, store, mock_conn):
        _setup_list_mock(mock_conn, [])

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_all()

        assert result == []

    # --- list_approved_enabled ---

    def test_list_approved_enabled_with_rows(self, store, mock_conn):
        rows = [_make_row({"approved": True, "enabled": True})]
        _setup_list_mock(mock_conn, rows)

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_approved_enabled()

        assert len(result) == 1

    def test_list_approved_enabled_empty(self, store, mock_conn):
        _setup_list_mock(mock_conn, [])

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_approved_enabled()

        assert result == []

    # --- list_pending_approval ---

    def test_list_pending_approval_with_rows(self, store, mock_conn):
        rows = [
            _make_row({"approved": False, "trust_level": "third_party"}),
            _make_row({"capability_name": "other.cap", "approved": False, "trust_level": "third_party"}),
        ]
        _setup_list_mock(mock_conn, rows)

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_pending_approval()

        assert len(result) == 2

    def test_list_pending_approval_empty(self, store, mock_conn):
        _setup_list_mock(mock_conn, [])

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_pending_approval()

        assert result == []

    # --- list_core ---

    def test_list_core_with_rows(self, store, mock_conn):
        rows = [_make_row({"trust_level": "core"})]
        _setup_list_mock(mock_conn, rows)

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_core()

        assert len(result) == 1
        assert result[0].trust_level == TrustLevel.CORE

    def test_list_core_empty(self, store, mock_conn):
        _setup_list_mock(mock_conn, [])

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_core()

        assert result == []

    # --- list_by_trust_level ---

    def test_list_by_trust_level_with_rows(self, store, mock_conn):
        rows = [
            _make_row({"trust_level": "official"}),
            _make_row({"capability_name": "second.cap", "trust_level": "official"}),
        ]
        _setup_list_mock(mock_conn, rows)

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_by_trust_level(TrustLevel.OFFICIAL)

        assert len(result) == 2
        mock_conn.execute.assert_called_once()
        # Verify the trust level value was passed as a parameter.
        args = mock_conn.execute.call_args
        assert args[0][1] == ("official",)

    def test_list_by_trust_level_empty(self, store, mock_conn):
        _setup_list_mock(mock_conn, [])

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_by_trust_level(TrustLevel.CORE)

        assert result == []

    # --- list_by_command ---

    def test_list_by_command_with_rows(self, store, mock_conn):
        rows = [_make_row({"source_command": "systemctl"})]
        _setup_list_mock(mock_conn, rows)

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_by_command("systemctl")

        assert len(result) == 1
        args = mock_conn.execute.call_args
        assert args[0][1] == ("systemctl",)

    def test_list_by_command_empty(self, store, mock_conn):
        _setup_list_mock(mock_conn, [])

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_by_command("nonexistent_cmd")

        assert result == []

    # --- list_by_package ---

    def test_list_by_package_with_rows(self, store, mock_conn):
        rows = [_make_row({"source_package": "nginx"})]
        _setup_list_mock(mock_conn, rows)

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_by_package("nginx")

        assert len(result) == 1
        args = mock_conn.execute.call_args
        assert args[0][1] == ("nginx",)

    def test_list_by_package_empty(self, store, mock_conn):
        _setup_list_mock(mock_conn, [])

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_by_package("unknown-pkg")

        assert result == []


# ===========================================================================
# AutogenStore.list_packages_with_capabilities
# ===========================================================================


class TestListPackagesWithCapabilities:
    """Tests for ``list_packages_with_capabilities``."""

    def test_returns_tuples(self, store, mock_conn):
        """Returns a list of ``(package, version)`` tuples."""
        rows = [
            {"source_package": "curl", "package_version": "7.88.1"},
            {"source_package": "nginx", "package_version": None},
        ]
        _setup_list_mock(mock_conn, rows)

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_packages_with_capabilities()

        assert result == [("curl", "7.88.1"), ("nginx", None)]

    def test_empty(self, store, mock_conn):
        _setup_list_mock(mock_conn, [])

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.list_packages_with_capabilities()

        assert result == []


# ===========================================================================
# AutogenStore.approve / disable / enable / delete
# ===========================================================================


class TestMutationMethods:
    """Tests for ``approve``, ``disable``, ``enable``, and ``delete``."""

    @pytest.mark.parametrize("method_name", ["approve", "disable", "enable", "delete"])
    def test_returns_true_when_row_affected(self, store, mock_conn, method_name):
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_conn.execute.return_value = mock_result

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = getattr(store, method_name)("test.cap")

        assert result is True

    @pytest.mark.parametrize("method_name", ["approve", "disable", "enable", "delete"])
    def test_returns_false_when_no_row_affected(self, store, mock_conn, method_name):
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_conn.execute.return_value = mock_result

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = getattr(store, method_name)("nonexistent.cap")

        assert result is False


# ===========================================================================
# AutogenStore.check_needs_regeneration
# ===========================================================================


class TestCheckNeedsRegeneration:
    """Tests for ``check_needs_regeneration``."""

    def test_same_hash_returns_false(self, store, mock_conn):
        """When the stored hash matches the new text hash, no regen needed."""
        man_text = "sample man page content"
        expected_hash = hashlib.sha256(man_text.encode()).hexdigest()[:16]
        row = _make_row({"man_page_hash": expected_hash})

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = row
        mock_conn.execute.return_value = mock_cursor

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.check_needs_regeneration("test.cap", man_text)

        assert result is False

    def test_different_hash_returns_true(self, store, mock_conn):
        """When the stored hash differs from the new text hash, regen is needed."""
        row = _make_row({"man_page_hash": "oldhash1234abcd"})

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = row
        mock_conn.execute.return_value = mock_cursor

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.check_needs_regeneration("test.cap", "completely new man page")

        assert result is True

    def test_no_stored_capability_returns_true(self, store, mock_conn):
        """When the capability does not exist at all, regen (generation) is needed."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.execute.return_value = mock_cursor

        with patch(PATCH_GET_CONN, return_value=_mock_conn(mock_conn)):
            result = store.check_needs_regeneration("brand.new", "any text")

        assert result is True


# ===========================================================================
# Module-level get_store / reset_store
# ===========================================================================


class TestModuleSingleton:
    """Tests for the module-level ``get_store`` and ``reset_store`` functions."""

    def test_get_store_creates_singleton(self):
        """``get_store`` returns the same instance on repeated calls."""
        s1 = get_store()
        s2 = get_store()

        assert s1 is s2
        assert isinstance(s1, AutogenStore)

    def test_reset_store_clears_singleton(self):
        """``reset_store`` sets the cached instance to ``None``."""
        s1 = get_store()
        reset_store()
        s2 = get_store()

        assert s1 is not s2

    def test_get_store_after_reset_creates_new_instance(self):
        """After ``reset_store``, a fresh ``AutogenStore`` is created."""
        first = get_store()
        reset_store()
        second = get_store()

        assert isinstance(second, AutogenStore)
        assert first is not second
