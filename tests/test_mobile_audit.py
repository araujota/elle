from __future__ import annotations

"""Tests for elle.mobile.audit -- MobileAuditStore.

Every external dependency (psycopg, storage engine, config, migration
registration) is mocked so the suite runs without PostgreSQL or any
other service.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Pre-import: ensure storage modules are loaded so patch() can target them
# ---------------------------------------------------------------------------
import elle.storage.engine  # noqa: F401  (needed so patch can resolve the target)
import elle.storage.migrate  # noqa: F401

# Patch register_migration *before* importing audit.py, because audit.py
# calls register_migration() at module level during import.
with patch("elle.storage.migrate.register_migration"):
    from elle.mobile.audit import (
        PG_SCHEMA,
        MobileAuditStore,
        _migrate_audit_v2,
    )

from elle.mobile.config import MobileGatewayConfig
from elle.mobile.models import MobileAuditAction, MobileAuditEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> MobileGatewayConfig:
    """Return a default MobileGatewayConfig for tests."""
    return MobileGatewayConfig()


def _make_entry(**overrides) -> MobileAuditEntry:
    """Create a MobileAuditEntry with sane defaults, applying *overrides*."""
    defaults = {
        "action": MobileAuditAction.PAIR,
        "success": True,
        "ip_address": "192.168.1.10",
    }
    defaults.update(overrides)
    return MobileAuditEntry(**defaults)


@contextmanager
def _fake_get_conn(mock_conn):
    """Context-manager that yields *mock_conn*, imitating get_conn."""
    yield mock_conn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_conn():
    """A MagicMock standing in for a psycopg.Connection."""
    conn = MagicMock()
    conn.execute = MagicMock()
    return conn


@pytest.fixture()
def store(mock_conn):
    """Return a MobileAuditStore wired to *mock_conn* via patched get_conn."""
    config = _make_config()
    with patch("elle.mobile.audit.get_conn", return_value=_fake_get_conn(mock_conn)):
        yield MobileAuditStore(config=config)


@pytest.fixture()
def store_factory(mock_conn):
    """Yield a callable that creates stores; useful when testing __init__."""

    def _factory(config=None):
        with patch("elle.mobile.audit.get_conn", return_value=_fake_get_conn(mock_conn)):
            return MobileAuditStore(config=config)

    return _factory


# ---------------------------------------------------------------------------
# Module-level constants / migration
# ---------------------------------------------------------------------------


class TestModuleLevel:
    """Tests for module-level constants and migration registration."""

    def test_pg_schema_value(self):
        assert PG_SCHEMA == "mobile"

    def test_migrate_audit_v2_creates_table_and_indices(self):
        """_migrate_audit_v2 should issue CREATE TABLE + 3 CREATE INDEX."""
        conn = MagicMock()
        _migrate_audit_v2(conn)

        assert conn.execute.call_count == 4  # 1 table + 3 indexes
        # First call: CREATE TABLE
        first_sql = conn.execute.call_args_list[0][0][0]
        assert "CREATE TABLE IF NOT EXISTS audit_log" in first_sql
        # Index calls
        idx_calls = [c[0][0] for c in conn.execute.call_args_list[1:]]
        assert any("idx_audit_timestamp" in s for s in idx_calls)
        assert any("idx_audit_device" in s for s in idx_calls)
        assert any("idx_audit_action" in s for s in idx_calls)


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    """Tests for MobileAuditStore.__init__."""

    def test_init_with_explicit_config(self):
        config = _make_config()
        store = MobileAuditStore(config=config)
        assert store.config is config

    @patch("elle.mobile.audit.get_mobile_config")
    def test_init_falls_back_to_get_mobile_config(self, mock_get_cfg):
        sentinel_cfg = MobileGatewayConfig(bind_port=9999)
        mock_get_cfg.return_value = sentinel_cfg

        store = MobileAuditStore()
        assert store.config is sentinel_cfg
        mock_get_cfg.assert_called_once()


# ---------------------------------------------------------------------------
# log() – core INSERT
# ---------------------------------------------------------------------------


class TestLog:
    """Tests for MobileAuditStore.log()."""

    def test_log_inserts_entry(self, mock_conn):
        """log() should INSERT a row with the correct parameters."""
        config = _make_config()
        entry = _make_entry(
            device_id="dev-1",
            device_name="Phone",
            action=MobileAuditAction.REQUEST,
            endpoint="/status",
            execution_mode="readonly",
            success=True,
            error=None,
        )

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=config)
            s.log(entry)

        mock_conn.execute.assert_called_once()
        sql, params = mock_conn.execute.call_args[0]
        assert "INSERT INTO audit_log" in sql
        assert params[0] == entry.timestamp.isoformat()
        assert params[1] == "dev-1"
        assert params[2] == "Phone"
        assert params[3] == MobileAuditAction.REQUEST.value
        assert params[4] == "/status"
        assert params[5] == "readonly"
        assert params[6] is True
        assert params[7] is None
        assert params[8] == "192.168.1.10"

    def test_log_with_error(self, mock_conn):
        """log() should correctly pass error strings."""
        entry = _make_entry(success=False, error="connection refused")

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.log(entry)

        _, params = mock_conn.execute.call_args[0]
        assert params[6] is False
        assert params[7] == "connection refused"


# ---------------------------------------------------------------------------
# Convenience log_* helpers
# ---------------------------------------------------------------------------


class TestLogPair:
    """Tests for log_pair()."""

    def test_log_pair_success(self, mock_conn):
        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.log_pair(
                device_id="d1",
                device_name="Phone",
                ip_address="10.0.0.1",
                success=True,
            )

        _, params = mock_conn.execute.call_args[0]
        assert params[1] == "d1"
        assert params[2] == "Phone"
        assert params[3] == MobileAuditAction.PAIR.value
        assert params[6] is True
        assert params[7] is None

    def test_log_pair_failure_with_error(self, mock_conn):
        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.log_pair(
                device_id="d1",
                device_name="Phone",
                ip_address="10.0.0.1",
                success=False,
                error="cert mismatch",
            )

        _, params = mock_conn.execute.call_args[0]
        assert params[6] is False
        assert params[7] == "cert mismatch"


class TestLogRequest:
    """Tests for log_request()."""

    def test_log_request_success(self, mock_conn):
        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.log_request(
                device_id="d2",
                device_name="Tablet",
                endpoint="/api/query",
                execution_mode="execute",
                ip_address="10.0.0.2",
                success=True,
            )

        _, params = mock_conn.execute.call_args[0]
        assert params[1] == "d2"
        assert params[3] == MobileAuditAction.REQUEST.value
        assert params[4] == "/api/query"
        assert params[5] == "execute"

    def test_log_request_failure(self, mock_conn):
        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.log_request(
                device_id="d2",
                device_name="Tablet",
                endpoint="/api/exec",
                execution_mode="execute",
                ip_address="10.0.0.2",
                success=False,
                error="policy denied",
            )

        _, params = mock_conn.execute.call_args[0]
        assert params[6] is False
        assert params[7] == "policy denied"


class TestLogElevate:
    """Tests for log_elevate()."""

    def test_log_elevate_success(self, mock_conn):
        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.log_elevate(
                device_id="d3",
                device_name="Watch",
                ip_address="10.0.0.3",
                success=True,
            )

        _, params = mock_conn.execute.call_args[0]
        assert params[3] == MobileAuditAction.ELEVATE.value
        assert params[6] is True

    def test_log_elevate_failure(self, mock_conn):
        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.log_elevate(
                device_id="d3",
                device_name="Watch",
                ip_address="10.0.0.3",
                success=False,
                error="max elevation exceeded",
            )

        _, params = mock_conn.execute.call_args[0]
        assert params[6] is False
        assert params[7] == "max elevation exceeded"


class TestLogRevoke:
    """Tests for log_revoke()."""

    def test_log_revoke_success(self, mock_conn):
        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.log_revoke(
                device_id="d4",
                device_name="Old Phone",
                ip_address="10.0.0.4",
                success=True,
            )

        _, params = mock_conn.execute.call_args[0]
        assert params[3] == MobileAuditAction.REVOKE.value
        assert params[6] is True

    def test_log_revoke_failure(self, mock_conn):
        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.log_revoke(
                device_id="d4",
                device_name="Old Phone",
                ip_address="10.0.0.4",
                success=False,
                error="device not found",
            )

        _, params = mock_conn.execute.call_args[0]
        assert params[6] is False
        assert params[7] == "device not found"


class TestLogGatewayStart:
    """Tests for log_gateway_start()."""

    def test_log_gateway_start(self, mock_conn):
        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.log_gateway_start(ip_address="0.0.0.0")

        _, params = mock_conn.execute.call_args[0]
        assert params[3] == MobileAuditAction.GATEWAY_START.value
        assert params[6] is True
        assert params[8] == "0.0.0.0"
        # device_id and device_name should be None
        assert params[1] is None
        assert params[2] is None


class TestLogGatewayStop:
    """Tests for log_gateway_stop()."""

    def test_log_gateway_stop(self, mock_conn):
        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.log_gateway_stop(ip_address="0.0.0.0")

        _, params = mock_conn.execute.call_args[0]
        assert params[3] == MobileAuditAction.GATEWAY_STOP.value
        assert params[6] is True
        assert params[8] == "0.0.0.0"


# ---------------------------------------------------------------------------
# query()
# ---------------------------------------------------------------------------


def _build_row(
    id_=1,
    timestamp=None,
    device_id="dev-1",
    device_name="Phone",
    action="pair",
    endpoint=None,
    execution_mode=None,
    success=True,
    error=None,
    ip_address="10.0.0.1",
    extra_data=None,
):
    """Return a dict simulating a database row from audit_log."""
    return {
        "id": id_,
        "timestamp": timestamp or datetime.utcnow(),
        "device_id": device_id,
        "device_name": device_name,
        "action": action,
        "endpoint": endpoint,
        "execution_mode": execution_mode,
        "success": success,
        "error": error,
        "ip_address": ip_address,
        "extra_data": extra_data,
    }


class TestQuery:
    """Tests for MobileAuditStore.query()."""

    def test_query_no_filters(self, mock_conn):
        row = _build_row()
        cursor = MagicMock()
        cursor.fetchall.return_value = [row]
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            results = s.query()

        assert len(results) == 1
        assert results[0].device_id == "dev-1"
        # Only limit param (100)
        sql = mock_conn.execute.call_args[0][0]
        assert "1=1" in sql

    def test_query_with_device_id(self, mock_conn):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.query(device_id="dev-42")

        params = mock_conn.execute.call_args[0][1]
        assert "dev-42" in params

    def test_query_with_action(self, mock_conn):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.query(action=MobileAuditAction.REVOKE)

        params = mock_conn.execute.call_args[0][1]
        assert MobileAuditAction.REVOKE.value in params

    def test_query_with_since_and_until(self, mock_conn):
        now = datetime.utcnow()
        since = now - timedelta(hours=2)
        until = now

        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.query(since=since, until=until)

        sql = mock_conn.execute.call_args[0][0]
        assert "timestamp >=" in sql
        assert "timestamp <=" in sql
        params = mock_conn.execute.call_args[0][1]
        assert since.isoformat() in params
        assert until.isoformat() in params

    def test_query_success_only(self, mock_conn):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.query(success_only=True)

        sql = mock_conn.execute.call_args[0][0]
        assert "success = TRUE" in sql

    def test_query_custom_limit(self, mock_conn):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.query(limit=5)

        params = mock_conn.execute.call_args[0][1]
        assert params[-1] == 5

    def test_query_multiple_filters_combined(self, mock_conn):
        """All filters active at once."""
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.execute.return_value = cursor
        now = datetime.utcnow()

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.query(
                device_id="d1",
                action=MobileAuditAction.PAIR,
                since=now - timedelta(hours=1),
                until=now,
                success_only=True,
                limit=10,
            )

        sql = mock_conn.execute.call_args[0][0]
        assert "device_id = %s" in sql
        assert "action = %s" in sql
        assert "timestamp >= %s" in sql
        assert "timestamp <= %s" in sql
        assert "success = TRUE" in sql
        params = mock_conn.execute.call_args[0][1]
        # device_id, action, since, until, limit
        assert len(params) == 5
        assert params[-1] == 10

    def test_query_returns_multiple_entries(self, mock_conn):
        rows = [
            _build_row(id_=1, device_id="dev-1", action="pair"),
            _build_row(id_=2, device_id="dev-2", action="request", endpoint="/status"),
        ]
        cursor = MagicMock()
        cursor.fetchall.return_value = rows
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            results = s.query()

        assert len(results) == 2
        assert results[0].action == MobileAuditAction.PAIR
        assert results[1].action == MobileAuditAction.REQUEST


# ---------------------------------------------------------------------------
# get_recent()
# ---------------------------------------------------------------------------


class TestGetRecent:
    """Tests for get_recent()."""

    def test_get_recent_default(self, mock_conn):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            results = s.get_recent()

        assert results == []
        # Should set a since filter (timestamp >=)
        sql = mock_conn.execute.call_args[0][0]
        assert "timestamp >=" in sql

    def test_get_recent_custom_hours(self, mock_conn):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.get_recent(hours=48, limit=50)

        params = mock_conn.execute.call_args[0][1]
        # Last param is limit
        assert params[-1] == 50


# ---------------------------------------------------------------------------
# get_device_history()
# ---------------------------------------------------------------------------


class TestGetDeviceHistory:
    """Tests for get_device_history()."""

    def test_get_device_history(self, mock_conn):
        row = _build_row(device_id="dev-99")
        cursor = MagicMock()
        cursor.fetchall.return_value = [row]
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            results = s.get_device_history("dev-99")

        assert len(results) == 1
        params = mock_conn.execute.call_args[0][1]
        assert "dev-99" in params

    def test_get_device_history_custom_limit(self, mock_conn):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.get_device_history("dev-1", limit=10)

        params = mock_conn.execute.call_args[0][1]
        assert params[-1] == 10


# ---------------------------------------------------------------------------
# get_failed_attempts()
# ---------------------------------------------------------------------------


class TestGetFailedAttempts:
    """Tests for get_failed_attempts()."""

    def test_get_failed_attempts_default(self, mock_conn):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            results = s.get_failed_attempts()

        assert results == []
        sql = mock_conn.execute.call_args[0][0]
        assert "success = FALSE" in sql

    def test_get_failed_attempts_custom(self, mock_conn):
        row = _build_row(success=False, error="denied")
        cursor = MagicMock()
        cursor.fetchall.return_value = [row]
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            results = s.get_failed_attempts(hours=12, limit=25)

        assert len(results) == 1
        assert results[0].success is False
        params = mock_conn.execute.call_args[0][1]
        # (since_iso, limit)
        assert params[-1] == 25


# ---------------------------------------------------------------------------
# cleanup_old_entries()
# ---------------------------------------------------------------------------


class TestCleanupOldEntries:
    """Tests for cleanup_old_entries()."""

    def test_cleanup_default_30_days(self, mock_conn):
        cursor = MagicMock()
        cursor.rowcount = 7
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            deleted = s.cleanup_old_entries()

        assert deleted == 7
        sql = mock_conn.execute.call_args[0][0]
        assert "DELETE FROM audit_log" in sql

    def test_cleanup_custom_days(self, mock_conn):
        cursor = MagicMock()
        cursor.rowcount = 0
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            deleted = s.cleanup_old_entries(days=7)

        assert deleted == 0
        # Ensure cutoff is roughly 7 days ago (param)
        params = mock_conn.execute.call_args[0][1]
        cutoff_str = params[0]
        cutoff_dt = datetime.fromisoformat(cutoff_str)
        expected_approx = datetime.utcnow() - timedelta(days=7)
        assert abs((cutoff_dt - expected_approx).total_seconds()) < 5


# ---------------------------------------------------------------------------
# _row_to_entry()
# ---------------------------------------------------------------------------


class TestRowToEntry:
    """Tests for MobileAuditStore._row_to_entry()."""

    def test_row_with_datetime_object(self):
        now = datetime.utcnow()
        row = _build_row(timestamp=now, action="elevate", device_id="d5")
        s = MobileAuditStore(config=_make_config())
        entry = s._row_to_entry(row)

        assert entry.timestamp == now
        assert entry.device_id == "d5"
        assert entry.action == MobileAuditAction.ELEVATE
        assert entry.success is True

    def test_row_with_iso_string_timestamp(self):
        now = datetime.utcnow()
        row = _build_row(timestamp=now.isoformat(), action="revoke", device_id="d6")
        s = MobileAuditStore(config=_make_config())
        entry = s._row_to_entry(row)

        # Should parse the ISO string back to datetime
        assert isinstance(entry.timestamp, datetime)
        assert entry.action == MobileAuditAction.REVOKE

    def test_row_with_none_optional_fields(self):
        row = _build_row(
            device_id=None,
            device_name=None,
            endpoint=None,
            execution_mode=None,
            error=None,
            action="gateway_start",
        )
        s = MobileAuditStore(config=_make_config())
        entry = s._row_to_entry(row)

        assert entry.device_id is None
        assert entry.device_name is None
        assert entry.endpoint is None
        assert entry.execution_mode is None
        assert entry.error is None
        assert entry.action == MobileAuditAction.GATEWAY_START

    def test_row_success_truthy_int(self):
        """success stored as int (1) should become bool True."""
        row = _build_row(success=1)
        s = MobileAuditStore(config=_make_config())
        entry = s._row_to_entry(row)
        assert entry.success is True

    def test_row_success_falsy_int(self):
        """success stored as int (0) should become bool False."""
        row = _build_row(success=0)
        s = MobileAuditStore(config=_make_config())
        entry = s._row_to_entry(row)
        assert entry.success is False

    def test_row_all_fields_populated(self):
        now = datetime.utcnow()
        row = _build_row(
            timestamp=now,
            device_id="full-device",
            device_name="Full Phone",
            action="request",
            endpoint="/api/query",
            execution_mode="execute",
            success=True,
            error=None,
            ip_address="172.16.0.5",
        )
        s = MobileAuditStore(config=_make_config())
        entry = s._row_to_entry(row)

        assert entry.device_id == "full-device"
        assert entry.device_name == "Full Phone"
        assert entry.endpoint == "/api/query"
        assert entry.execution_mode == "execute"
        assert entry.ip_address == "172.16.0.5"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Miscellaneous edge-case tests."""

    def test_log_gateway_events_have_no_device(self, mock_conn):
        """Gateway start/stop should not set device_id or device_name."""
        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.log_gateway_start("127.0.0.1")

        _, params = mock_conn.execute.call_args[0]
        assert params[1] is None  # device_id
        assert params[2] is None  # device_name

    def test_query_empty_result(self, mock_conn):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            results = s.query(device_id="nonexistent")

        assert results == []

    def test_get_conn_called_with_mobile_schema_on_log(self, mock_conn):
        """get_conn should always be called with schema='mobile'."""
        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.log(_make_entry())

        patched_gc.assert_called_once_with(schema="mobile")

    def test_get_conn_called_with_mobile_schema_on_query(self, mock_conn):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.query()

        patched_gc.assert_called_once_with(schema="mobile")

    def test_get_conn_called_with_mobile_schema_on_failed_attempts(self, mock_conn):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.get_failed_attempts()

        patched_gc.assert_called_once_with(schema="mobile")

    def test_get_conn_called_with_mobile_schema_on_cleanup(self, mock_conn):
        cursor = MagicMock()
        cursor.rowcount = 0
        mock_conn.execute.return_value = cursor

        with patch("elle.mobile.audit.get_conn") as patched_gc:
            patched_gc.return_value = _fake_get_conn(mock_conn)
            s = MobileAuditStore(config=_make_config())
            s.cleanup_old_entries()

        patched_gc.assert_called_once_with(schema="mobile")
