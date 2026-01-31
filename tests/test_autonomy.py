from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from elle.capabilities.autonomy import (
    RISK_ORDERING,
    AutonomyEngine,
    AutonomyLevel,
    AutonomyOverride,
    AutonomyPreferences,
    AutonomyStatus,
    AutonomyStore,
    EarnedAutonomyConfig,
    ExecutionStats,
    get_autonomy_engine,
    reset_autonomy_engine,
    risk_allowed,
)

# ---------------------------------------------------------------------------
# Helpers for mocking get_conn context manager
# ---------------------------------------------------------------------------


def _make_mock_conn(rows=None, fetchone_val=None, rowcount=0):
    """Build a mock connection + cursor returned by get_conn.

    Parameters
    ----------
    rows : list[dict] | None
        Rows returned by cursor.fetchall().
    fetchone_val : dict | None
        Value returned by cursor.fetchone().
    rowcount : int
        Value for cursor.rowcount (used by DELETE).
    """
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = fetchone_val
    mock_cursor.fetchall.return_value = rows or []
    mock_cursor.rowcount = rowcount

    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_cursor

    @contextmanager
    def _ctx_mgr(schema=None):
        yield mock_conn

    return mock_conn, _ctx_mgr


# ---------------------------------------------------------------------------
# AutonomyLevel
# ---------------------------------------------------------------------------


class TestAutonomyLevel:
    def test_readonly_max_risk(self):
        assert AutonomyLevel.READONLY.max_risk is None

    def test_low_risk_max_risk(self):
        assert AutonomyLevel.LOW_RISK.max_risk == "low"

    def test_medium_risk_max_risk(self):
        assert AutonomyLevel.MEDIUM_RISK.max_risk == "medium"

    def test_high_risk_max_risk(self):
        assert AutonomyLevel.HIGH_RISK.max_risk == "high"

    def test_full_auto_max_risk(self):
        assert AutonomyLevel.FULL_AUTO.max_risk == "critical"

    def test_enum_values(self):
        assert AutonomyLevel.READONLY.value == "readonly"
        assert AutonomyLevel.LOW_RISK.value == "low_risk"
        assert AutonomyLevel.MEDIUM_RISK.value == "medium_risk"
        assert AutonomyLevel.HIGH_RISK.value == "high_risk"
        assert AutonomyLevel.FULL_AUTO.value == "full_auto"

    def test_is_str_enum(self):
        # AutonomyLevel inherits from str
        assert isinstance(AutonomyLevel.LOW_RISK, str)


# ---------------------------------------------------------------------------
# RISK_ORDERING
# ---------------------------------------------------------------------------


class TestRiskOrdering:
    def test_ordering_values(self):
        assert RISK_ORDERING["none"] == 0
        assert RISK_ORDERING["low"] == 1
        assert RISK_ORDERING["medium"] == 2
        assert RISK_ORDERING["high"] == 3
        assert RISK_ORDERING["critical"] == 4


# ---------------------------------------------------------------------------
# risk_allowed
# ---------------------------------------------------------------------------


class TestRiskAllowed:
    def test_none_max_denies_all(self):
        assert risk_allowed("low", None) is False

    def test_low_allows_none(self):
        assert risk_allowed("none", "low") is True

    def test_low_allows_low(self):
        assert risk_allowed("low", "low") is True

    def test_low_denies_medium(self):
        assert risk_allowed("medium", "low") is False

    def test_critical_allows_all(self):
        for risk in RISK_ORDERING:
            assert risk_allowed(risk, "critical") is True

    def test_unknown_risk_defaults_to_zero(self):
        # Unknown risks get ordering 0, so they pass under "low" (ordering 1)
        assert risk_allowed("unknown_risk", "low") is True

    def test_unknown_max_risk_defaults_to_zero(self):
        # Unknown max_risk ordering is 0; "low" has ordering 1 -> denied
        assert risk_allowed("low", "unknown_max") is False

    def test_none_risk_allowed_with_none_level(self):
        # cap_risk "none" (0) vs max_risk None -> False
        assert risk_allowed("none", None) is False

    def test_equal_risk_levels(self):
        for risk in RISK_ORDERING:
            assert risk_allowed(risk, risk) is True


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class TestEarnedAutonomyConfig:
    def test_defaults(self):
        cfg = EarnedAutonomyConfig()
        assert cfg.enabled is False
        assert cfg.min_executions == 10
        assert cfg.min_success_rate == 0.95
        assert cfg.cooldown_after_failure == 5

    def test_frozen(self):
        cfg = EarnedAutonomyConfig()
        with pytest.raises(Exception):
            cfg.enabled = True  # type: ignore[misc]

    def test_custom_values(self):
        cfg = EarnedAutonomyConfig(
            enabled=True,
            min_executions=5,
            min_success_rate=0.8,
            cooldown_after_failure=3,
        )
        assert cfg.enabled is True
        assert cfg.min_executions == 5
        assert cfg.min_success_rate == 0.8
        assert cfg.cooldown_after_failure == 3


class TestAutonomyPreferences:
    def test_defaults(self):
        prefs = AutonomyPreferences()
        assert prefs.base_level == AutonomyLevel.LOW_RISK
        assert prefs.earned_autonomy.enabled is False
        assert isinstance(prefs.updated_at, datetime)

    def test_frozen(self):
        prefs = AutonomyPreferences()
        with pytest.raises(Exception):
            prefs.base_level = AutonomyLevel.FULL_AUTO  # type: ignore[misc]


class TestAutonomyOverride:
    def test_manual_override(self):
        ovr = AutonomyOverride(
            capability_name="test.cap",
            autonomous=True,
            reason="manual",
        )
        assert ovr.capability_name == "test.cap"
        assert ovr.autonomous is True
        assert ovr.reason == "manual"
        assert ovr.earned_at is None

    def test_earned_override(self):
        now = datetime.now(tz=timezone.utc)
        ovr = AutonomyOverride(
            capability_name="test.cap",
            autonomous=True,
            reason="earned",
            earned_at=now,
        )
        assert ovr.reason == "earned"
        assert ovr.earned_at == now

    def test_frozen(self):
        ovr = AutonomyOverride(capability_name="x", autonomous=True)
        with pytest.raises(Exception):
            ovr.autonomous = False  # type: ignore[misc]


class TestExecutionStats:
    def test_success_rate_no_executions(self):
        stats = ExecutionStats(capability_name="test")
        assert stats.success_rate == 0.0

    def test_success_rate_with_executions(self):
        stats = ExecutionStats(
            capability_name="test",
            total_executions=10,
            successful_executions=8,
        )
        assert stats.success_rate == 0.8

    def test_success_rate_all_successful(self):
        stats = ExecutionStats(
            capability_name="test",
            total_executions=5,
            successful_executions=5,
        )
        assert stats.success_rate == 1.0

    def test_success_rate_all_failed(self):
        stats = ExecutionStats(
            capability_name="test",
            total_executions=5,
            successful_executions=0,
            failed_executions=5,
        )
        assert stats.success_rate == 0.0

    def test_defaults(self):
        stats = ExecutionStats(capability_name="test")
        assert stats.total_executions == 0
        assert stats.successful_executions == 0
        assert stats.failed_executions == 0
        assert stats.consecutive_successes == 0
        assert stats.last_executed_at is None
        assert stats.last_success_at is None
        assert stats.last_failure_at is None

    def test_frozen(self):
        stats = ExecutionStats(capability_name="test")
        with pytest.raises(Exception):
            stats.total_executions = 5  # type: ignore[misc]


class TestAutonomyStatus:
    def test_construction(self):
        status = AutonomyStatus(
            capability_name="test.cap",
            can_run_autonomously=True,
            reason="Base level allows",
            source="base_level",
        )
        assert status.capability_name == "test.cap"
        assert status.can_run_autonomously is True
        assert status.source == "base_level"
        assert status.stats is None
        assert status.earned_progress is None

    def test_with_stats_and_progress(self):
        stats = ExecutionStats(capability_name="test", total_executions=5, successful_executions=5)
        status = AutonomyStatus(
            capability_name="test",
            can_run_autonomously=True,
            reason="Earned",
            source="earned",
            stats=stats,
            earned_progress=0.5,
        )
        assert status.stats is not None
        assert status.earned_progress == 0.5


# ---------------------------------------------------------------------------
# AutonomyStore (fully mocked -- no database required)
# ---------------------------------------------------------------------------


class TestAutonomyStore:
    """Tests for AutonomyStore with all DB calls mocked via get_conn."""

    @pytest.fixture
    def store(self):
        """Create a store with _ensure_schema mocked out."""
        with patch("elle.capabilities.autonomy.AutonomyStore._ensure_schema"):
            s = AutonomyStore(schema="test")
        return s

    # -- _ensure_schema -------------------------------------------------------

    def test_ensure_schema_executes_statements(self):
        """_ensure_schema splits SCHEMA by semicolon and executes each."""
        mock_conn, ctx = _make_mock_conn()
        with patch("elle.storage.engine.get_conn", ctx):
            # Call the real _ensure_schema
            store = AutonomyStore.__new__(AutonomyStore)
            store._schema = "test"
            store._ensure_schema()
        # Should have called execute for each non-empty statement
        assert mock_conn.execute.call_count >= 3  # 3 CREATE TABLE statements

    def test_ensure_schema_handles_error(self):
        """_ensure_schema logs errors but does not raise."""

        @contextmanager
        def _failing_ctx(schema=None):
            raise RuntimeError("DB unavailable")
            yield  # noqa: F841

        with patch("elle.storage.engine.get_conn", _failing_ctx):
            store = AutonomyStore.__new__(AutonomyStore)
            store._schema = "test"
            # Should not raise
            store._ensure_schema()

    # -- get_preferences ------------------------------------------------------

    def test_get_preferences_default_when_no_row(self, store):
        """Returns default prefs when no row in DB."""
        mock_conn, ctx = _make_mock_conn(fetchone_val=None)
        with patch("elle.storage.engine.get_conn", ctx):
            prefs = store.get_preferences()
        assert prefs.base_level == AutonomyLevel.LOW_RISK
        assert prefs.earned_autonomy.enabled is False

    def test_get_preferences_from_row(self, store):
        """Reconstructs prefs from a DB row."""
        row = {
            "base_level": "medium_risk",
            "earned_autonomy_enabled": 1,
            "min_executions": 20,
            "min_success_rate": 0.9,
            "cooldown_after_failure": 3,
            "updated_at": "2024-01-15T10:30:00",
        }
        mock_conn, ctx = _make_mock_conn(fetchone_val=row)
        with patch("elle.storage.engine.get_conn", ctx):
            prefs = store.get_preferences()
        assert prefs.base_level == AutonomyLevel.MEDIUM_RISK
        assert prefs.earned_autonomy.enabled is True
        assert prefs.earned_autonomy.min_executions == 20
        assert prefs.earned_autonomy.min_success_rate == 0.9
        assert prefs.earned_autonomy.cooldown_after_failure == 3
        assert prefs.updated_at == datetime.fromisoformat("2024-01-15T10:30:00")

    # -- save_preferences -----------------------------------------------------

    def test_save_preferences(self, store):
        """save_preferences calls execute with correct parameters."""
        mock_conn, ctx = _make_mock_conn()
        prefs = AutonomyPreferences(
            base_level=AutonomyLevel.HIGH_RISK,
            earned_autonomy=EarnedAutonomyConfig(enabled=True, min_executions=5),
        )
        with patch("elle.storage.engine.get_conn", ctx):
            store.save_preferences(prefs)
        mock_conn.execute.assert_called_once()
        args = mock_conn.execute.call_args
        sql = args[0][0]
        params = args[0][1]
        assert "INSERT INTO autonomy_preferences" in sql
        assert params[0] == "high_risk"
        assert params[1] == 1  # enabled = True -> 1

    # -- set_base_level -------------------------------------------------------

    def test_set_base_level(self, store):
        """set_base_level reads current prefs then saves with new level."""
        row = {
            "base_level": "low_risk",
            "earned_autonomy_enabled": 0,
            "min_executions": 10,
            "min_success_rate": 0.95,
            "cooldown_after_failure": 5,
            "updated_at": "2024-01-01T00:00:00",
        }
        mock_conn, ctx = _make_mock_conn(fetchone_val=row)
        with patch("elle.storage.engine.get_conn", ctx):
            store.set_base_level(AutonomyLevel.FULL_AUTO)
        # Should have executed: one SELECT (get_preferences) and one INSERT (save_preferences)
        assert mock_conn.execute.call_count == 2

    # -- set_earned_autonomy_enabled ------------------------------------------

    def test_set_earned_autonomy_enabled(self, store):
        """set_earned_autonomy_enabled toggles the flag."""
        row = {
            "base_level": "low_risk",
            "earned_autonomy_enabled": 0,
            "min_executions": 10,
            "min_success_rate": 0.95,
            "cooldown_after_failure": 5,
            "updated_at": "2024-01-01T00:00:00",
        }
        mock_conn, ctx = _make_mock_conn(fetchone_val=row)
        with patch("elle.storage.engine.get_conn", ctx):
            store.set_earned_autonomy_enabled(True)
        # Second call is the INSERT for save_preferences
        save_call = mock_conn.execute.call_args_list[1]
        params = save_call[0][1]
        assert params[1] == 1  # earned_autonomy_enabled = True

    # -- get_override ---------------------------------------------------------

    def test_get_override_none(self, store):
        """Returns None when no override row exists."""
        mock_conn, ctx = _make_mock_conn(fetchone_val=None)
        with patch("elle.storage.engine.get_conn", ctx):
            result = store.get_override("test.cap")
        assert result is None

    def test_get_override_manual(self, store):
        """Returns AutonomyOverride from a manual override row."""
        row = {
            "capability_name": "test.cap",
            "autonomous": 1,
            "reason": "manual",
            "earned_at": None,
            "updated_at": "2024-06-01T12:00:00",
        }
        mock_conn, ctx = _make_mock_conn(fetchone_val=row)
        with patch("elle.storage.engine.get_conn", ctx):
            result = store.get_override("test.cap")
        assert result is not None
        assert result.capability_name == "test.cap"
        assert result.autonomous is True
        assert result.reason == "manual"
        assert result.earned_at is None

    def test_get_override_earned_with_earned_at(self, store):
        """Returns AutonomyOverride with earned_at parsed."""
        row = {
            "capability_name": "test.cap",
            "autonomous": 1,
            "reason": "earned",
            "earned_at": "2024-06-15T10:00:00",
            "updated_at": "2024-06-15T10:00:00",
        }
        mock_conn, ctx = _make_mock_conn(fetchone_val=row)
        with patch("elle.storage.engine.get_conn", ctx):
            result = store.get_override("test.cap")
        assert result is not None
        assert result.reason == "earned"
        assert result.earned_at == datetime.fromisoformat("2024-06-15T10:00:00")

    # -- set_override ---------------------------------------------------------

    def test_set_override_manual(self, store):
        """set_override inserts with reason=manual and no earned_at."""
        mock_conn, ctx = _make_mock_conn()
        with patch("elle.storage.engine.get_conn", ctx):
            store.set_override("test.cap", True, reason="manual")
        args = mock_conn.execute.call_args[0]
        params = args[1]
        assert params[0] == "test.cap"
        assert params[1] == 1  # autonomous = True
        assert params[2] == "manual"
        assert params[3] is None  # earned_at is None for manual

    def test_set_override_earned_autonomous(self, store):
        """set_override earned+autonomous sets earned_at to now."""
        mock_conn, ctx = _make_mock_conn()
        with patch("elle.storage.engine.get_conn", ctx):
            store.set_override("test.cap", True, reason="earned")
        args = mock_conn.execute.call_args[0]
        params = args[1]
        assert params[2] == "earned"
        assert params[3] is not None  # earned_at should be set

    def test_set_override_earned_not_autonomous(self, store):
        """set_override earned but autonomous=False -> earned_at is None."""
        mock_conn, ctx = _make_mock_conn()
        with patch("elle.storage.engine.get_conn", ctx):
            store.set_override("test.cap", False, reason="earned")
        args = mock_conn.execute.call_args[0]
        params = args[1]
        assert params[3] is None  # earned_at None when not autonomous

    # -- remove_override ------------------------------------------------------

    def test_remove_override_exists(self, store):
        """remove_override returns True when a row was deleted."""
        mock_conn, ctx = _make_mock_conn(rowcount=1)
        with patch("elle.storage.engine.get_conn", ctx):
            removed = store.remove_override("test.cap")
        assert removed is True

    def test_remove_override_not_found(self, store):
        """remove_override returns False when no row matched."""
        mock_conn, ctx = _make_mock_conn(rowcount=0)
        with patch("elle.storage.engine.get_conn", ctx):
            removed = store.remove_override("nonexistent")
        assert removed is False

    # -- list_overrides -------------------------------------------------------

    def test_list_overrides_empty(self, store):
        """Returns empty list when no overrides exist."""
        mock_conn, ctx = _make_mock_conn(rows=[])
        with patch("elle.storage.engine.get_conn", ctx):
            overrides = store.list_overrides()
        assert overrides == []

    def test_list_overrides_multiple(self, store):
        """Returns list of AutonomyOverride from rows."""
        rows = [
            {
                "capability_name": "a.cap",
                "autonomous": 1,
                "reason": "manual",
                "earned_at": None,
                "updated_at": "2024-01-01T00:00:00",
            },
            {
                "capability_name": "b.cap",
                "autonomous": 0,
                "reason": "earned",
                "earned_at": "2024-06-01T00:00:00",
                "updated_at": "2024-06-01T00:00:00",
            },
        ]
        mock_conn, ctx = _make_mock_conn(rows=rows)
        with patch("elle.storage.engine.get_conn", ctx):
            overrides = store.list_overrides()
        assert len(overrides) == 2
        names = {o.capability_name for o in overrides}
        assert names == {"a.cap", "b.cap"}
        assert overrides[0].autonomous is True
        assert overrides[1].autonomous is False

    # -- get_stats ------------------------------------------------------------

    def test_get_stats_no_row(self, store):
        """Returns default stats when no row exists."""
        mock_conn, ctx = _make_mock_conn(fetchone_val=None)
        with patch("elle.storage.engine.get_conn", ctx):
            stats = store.get_stats("test.cap")
        assert stats.capability_name == "test.cap"
        assert stats.total_executions == 0
        assert stats.success_rate == 0.0

    def test_get_stats_from_row(self, store):
        """Reconstructs ExecutionStats from a DB row."""
        row = {
            "capability_name": "test.cap",
            "total_executions": 20,
            "successful_executions": 18,
            "failed_executions": 2,
            "consecutive_successes": 10,
            "last_executed_at": "2024-06-15T12:00:00",
            "last_success_at": "2024-06-15T12:00:00",
            "last_failure_at": "2024-06-10T09:00:00",
        }
        mock_conn, ctx = _make_mock_conn(fetchone_val=row)
        with patch("elle.storage.engine.get_conn", ctx):
            stats = store.get_stats("test.cap")
        assert stats.total_executions == 20
        assert stats.successful_executions == 18
        assert stats.failed_executions == 2
        assert stats.consecutive_successes == 10
        assert stats.last_executed_at is not None
        assert stats.last_success_at is not None
        assert stats.last_failure_at is not None

    def test_get_stats_null_timestamps(self, store):
        """Handles null timestamps in stats row."""
        row = {
            "capability_name": "test.cap",
            "total_executions": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "consecutive_successes": 0,
            "last_executed_at": None,
            "last_success_at": None,
            "last_failure_at": None,
        }
        mock_conn, ctx = _make_mock_conn(fetchone_val=row)
        with patch("elle.storage.engine.get_conn", ctx):
            stats = store.get_stats("test.cap")
        assert stats.last_executed_at is None
        assert stats.last_success_at is None
        assert stats.last_failure_at is None

    # -- record_execution -----------------------------------------------------

    def test_record_execution_success_from_zero(self, store):
        """Recording a success from zero stats."""
        # First call to get_stats returns no row; second call is INSERT
        call_count = 0
        mock_cursor_get = MagicMock()
        mock_cursor_get.fetchone.return_value = None
        mock_cursor_insert = MagicMock()

        mock_conn = MagicMock()

        def _exec_side_effect(sql, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # This is get_stats -> SELECT
                return mock_cursor_get
            return mock_cursor_insert

        mock_conn.execute.side_effect = _exec_side_effect

        @contextmanager
        def _ctx(schema=None):
            yield mock_conn

        with patch("elle.storage.engine.get_conn", _ctx):
            result = store.record_execution("test.cap", True)
        assert result.total_executions == 1
        assert result.successful_executions == 1
        assert result.failed_executions == 0
        assert result.consecutive_successes == 1
        assert result.last_success_at is not None
        assert result.last_failure_at is None

    def test_record_execution_failure(self, store):
        """Recording a failure resets consecutive_successes to 0."""
        # get_stats returns an existing row with 5 consecutive successes
        stats_row = {
            "capability_name": "test.cap",
            "total_executions": 5,
            "successful_executions": 5,
            "failed_executions": 0,
            "consecutive_successes": 5,
            "last_executed_at": "2024-06-15T12:00:00",
            "last_success_at": "2024-06-15T12:00:00",
            "last_failure_at": None,
        }

        call_count = 0
        mock_cursor_get = MagicMock()
        mock_cursor_get.fetchone.return_value = stats_row
        mock_cursor_insert = MagicMock()

        mock_conn = MagicMock()

        def _exec_side_effect(sql, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_cursor_get
            return mock_cursor_insert

        mock_conn.execute.side_effect = _exec_side_effect

        @contextmanager
        def _ctx(schema=None):
            yield mock_conn

        with patch("elle.storage.engine.get_conn", _ctx):
            result = store.record_execution("test.cap", False)
        assert result.total_executions == 6
        assert result.successful_executions == 5
        assert result.failed_executions == 1
        assert result.consecutive_successes == 0
        assert result.last_failure_at is not None
        # last_success_at preserved from prior
        assert result.last_success_at == datetime.fromisoformat("2024-06-15T12:00:00")

    def test_record_execution_success_preserves_last_failure(self, store):
        """Success preserves the prior last_failure_at."""
        stats_row = {
            "capability_name": "test.cap",
            "total_executions": 3,
            "successful_executions": 2,
            "failed_executions": 1,
            "consecutive_successes": 0,
            "last_executed_at": "2024-06-15T12:00:00",
            "last_success_at": "2024-06-14T12:00:00",
            "last_failure_at": "2024-06-15T12:00:00",
        }

        call_count = 0
        mock_cursor_get = MagicMock()
        mock_cursor_get.fetchone.return_value = stats_row
        mock_cursor_insert = MagicMock()

        mock_conn = MagicMock()

        def _exec_side_effect(sql, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_cursor_get
            return mock_cursor_insert

        mock_conn.execute.side_effect = _exec_side_effect

        @contextmanager
        def _ctx(schema=None):
            yield mock_conn

        with patch("elle.storage.engine.get_conn", _ctx):
            result = store.record_execution("test.cap", True)
        assert result.total_executions == 4
        assert result.successful_executions == 3
        assert result.consecutive_successes == 1
        # last_failure_at should still be the old one
        assert result.last_failure_at == datetime.fromisoformat("2024-06-15T12:00:00")

    # -- get_all_stats --------------------------------------------------------

    def test_get_all_stats_empty(self, store):
        """Returns empty list when no stats rows exist."""
        mock_conn, ctx = _make_mock_conn(rows=[])
        with patch("elle.storage.engine.get_conn", ctx):
            all_stats = store.get_all_stats()
        assert all_stats == []

    def test_get_all_stats_multiple(self, store):
        """Returns list of ExecutionStats from multiple rows."""
        rows = [
            {
                "capability_name": "a.cap",
                "total_executions": 10,
                "successful_executions": 9,
                "failed_executions": 1,
                "consecutive_successes": 5,
                "last_executed_at": "2024-06-15T12:00:00",
                "last_success_at": "2024-06-15T12:00:00",
                "last_failure_at": "2024-06-10T09:00:00",
            },
            {
                "capability_name": "b.cap",
                "total_executions": 3,
                "successful_executions": 3,
                "failed_executions": 0,
                "consecutive_successes": 3,
                "last_executed_at": None,
                "last_success_at": None,
                "last_failure_at": None,
            },
        ]
        mock_conn, ctx = _make_mock_conn(rows=rows)
        with patch("elle.storage.engine.get_conn", ctx):
            all_stats = store.get_all_stats()
        assert len(all_stats) == 2
        assert all_stats[0].capability_name == "a.cap"
        assert all_stats[1].capability_name == "b.cap"
        assert all_stats[0].last_failure_at is not None
        assert all_stats[1].last_failure_at is None


# ---------------------------------------------------------------------------
# AutonomyEngine (store is a MagicMock)
# ---------------------------------------------------------------------------


class TestAutonomyEngine:
    """Tests for AutonomyEngine with a fully mocked AutonomyStore."""

    @pytest.fixture
    def mock_store(self):
        """Return a MagicMock AutonomyStore with sensible defaults."""
        store = MagicMock(spec=AutonomyStore)
        # Default preferences: LOW_RISK, earned autonomy disabled
        store.get_preferences.return_value = AutonomyPreferences()
        # Default override: none
        store.get_override.return_value = None
        # Default stats: empty
        store.get_stats.return_value = ExecutionStats(capability_name="test.cap")
        return store

    @pytest.fixture
    def engine(self, mock_store):
        return AutonomyEngine(store=mock_store)

    # -- store property -------------------------------------------------------

    def test_store_property(self, engine, mock_store):
        assert engine.store is mock_store

    # -- can_run_autonomously: manual override --------------------------------

    def test_manual_grant(self, engine, mock_store):
        mock_store.get_override.return_value = AutonomyOverride(
            capability_name="test.cap",
            autonomous=True,
            reason="manual",
        )
        can_run, reason = engine.can_run_autonomously("test.cap", "high")
        assert can_run is True
        assert "Manual grant" in reason

    def test_manual_revoke(self, engine, mock_store):
        mock_store.get_override.return_value = AutonomyOverride(
            capability_name="test.cap",
            autonomous=False,
            reason="manual",
        )
        can_run, reason = engine.can_run_autonomously("test.cap", "none")
        assert can_run is False
        assert "Manual revoke" in reason

    def test_earned_override(self, engine, mock_store):
        now = datetime.now(tz=timezone.utc)
        mock_store.get_override.return_value = AutonomyOverride(
            capability_name="test.cap",
            autonomous=True,
            reason="earned",
            earned_at=now,
        )
        can_run, reason = engine.can_run_autonomously("test.cap", "high")
        assert can_run is True
        assert "Earned" in reason

    # -- can_run_autonomously: earned autonomy (no override) ------------------

    def test_earned_autonomy_check_grants(self, engine, mock_store):
        """If earned autonomy is enabled and stats qualify, it grants."""
        mock_store.get_preferences.return_value = AutonomyPreferences(
            earned_autonomy=EarnedAutonomyConfig(
                enabled=True,
                min_executions=10,
                min_success_rate=0.9,
            ),
        )
        mock_store.get_stats.return_value = ExecutionStats(
            capability_name="test.cap",
            total_executions=15,
            successful_executions=15,
            consecutive_successes=15,
        )
        can_run, reason = engine.can_run_autonomously("test.cap", "high")
        assert can_run is True
        assert "Earned" in reason

    def test_earned_autonomy_insufficient_runs(self, engine, mock_store):
        """If earned autonomy is enabled but not enough runs, falls through to base level."""
        mock_store.get_preferences.return_value = AutonomyPreferences(
            earned_autonomy=EarnedAutonomyConfig(
                enabled=True,
                min_executions=10,
            ),
        )
        mock_store.get_stats.return_value = ExecutionStats(
            capability_name="test.cap",
            total_executions=5,
            successful_executions=5,
            consecutive_successes=5,
        )
        # Risk "high" exceeds LOW_RISK base level -> denied
        can_run, reason = engine.can_run_autonomously("test.cap", "high")
        assert can_run is False

    # -- can_run_autonomously: base level -------------------------------------

    def test_base_level_allows_low(self, engine, mock_store):
        """Default LOW_RISK allows risk=low."""
        can_run, reason = engine.can_run_autonomously("test.cap", "low")
        assert can_run is True
        assert "Base level" in reason

    def test_base_level_allows_none(self, engine, mock_store):
        """Default LOW_RISK allows risk=none."""
        can_run, reason = engine.can_run_autonomously("test.cap", "none")
        assert can_run is True

    def test_base_level_blocks_medium(self, engine, mock_store):
        """Default LOW_RISK blocks risk=medium."""
        can_run, reason = engine.can_run_autonomously("test.cap", "medium")
        assert can_run is False
        assert "exceeds" in reason

    def test_base_level_blocks_high(self, engine, mock_store):
        can_run, reason = engine.can_run_autonomously("test.cap", "high")
        assert can_run is False

    def test_base_level_blocks_critical(self, engine, mock_store):
        can_run, reason = engine.can_run_autonomously("test.cap", "critical")
        assert can_run is False

    def test_full_auto_allows_critical(self, engine, mock_store):
        mock_store.get_preferences.return_value = AutonomyPreferences(
            base_level=AutonomyLevel.FULL_AUTO,
        )
        can_run, reason = engine.can_run_autonomously("test.cap", "critical")
        assert can_run is True

    def test_readonly_blocks_everything(self, engine, mock_store):
        mock_store.get_preferences.return_value = AutonomyPreferences(
            base_level=AutonomyLevel.READONLY,
        )
        can_run, reason = engine.can_run_autonomously("test.cap", "none")
        assert can_run is False

    # -- get_autonomy_status --------------------------------------------------

    def test_status_base_level(self, engine, mock_store):
        status = engine.get_autonomy_status("test.cap", "low")
        assert status.source == "base_level"
        assert status.can_run_autonomously is True

    def test_status_risk_blocked(self, engine, mock_store):
        status = engine.get_autonomy_status("test.cap", "critical")
        assert status.source == "risk_blocked"
        assert status.can_run_autonomously is False

    def test_status_manual_grant(self, engine, mock_store):
        mock_store.get_override.return_value = AutonomyOverride(
            capability_name="test.cap",
            autonomous=True,
            reason="manual",
        )
        status = engine.get_autonomy_status("test.cap", "high")
        assert status.source == "manual_grant"
        assert status.can_run_autonomously is True

    def test_status_manual_revoke(self, engine, mock_store):
        mock_store.get_override.return_value = AutonomyOverride(
            capability_name="test.cap",
            autonomous=False,
            reason="manual",
        )
        status = engine.get_autonomy_status("test.cap", "low")
        assert status.source == "manual_revoke"
        assert status.can_run_autonomously is False

    def test_status_earned_via_override(self, engine, mock_store):
        """Earned override with reason='earned' gives source='earned'."""
        now = datetime.now(tz=timezone.utc)
        mock_store.get_override.return_value = AutonomyOverride(
            capability_name="test.cap",
            autonomous=True,
            reason="earned",
            earned_at=now,
        )
        mock_store.get_preferences.return_value = AutonomyPreferences(
            earned_autonomy=EarnedAutonomyConfig(enabled=True),
        )
        mock_store.get_stats.return_value = ExecutionStats(
            capability_name="test.cap",
            total_executions=15,
            successful_executions=15,
            consecutive_successes=15,
        )
        status = engine.get_autonomy_status("test.cap", "high")
        assert status.source == "earned"

    def test_status_earned_via_stats_no_override(self, engine, mock_store):
        """Earned autonomy via stats (no override stored) gives source='earned'."""
        mock_store.get_preferences.return_value = AutonomyPreferences(
            earned_autonomy=EarnedAutonomyConfig(
                enabled=True,
                min_executions=10,
                min_success_rate=0.9,
            ),
        )
        mock_store.get_stats.return_value = ExecutionStats(
            capability_name="test.cap",
            total_executions=15,
            successful_executions=15,
            consecutive_successes=15,
        )
        status = engine.get_autonomy_status("test.cap", "high")
        assert status.source == "earned"
        assert status.can_run_autonomously is True

    def test_status_earned_progress_calculated(self, engine, mock_store):
        """earned_progress is computed when earned autonomy is enabled."""
        mock_store.get_preferences.return_value = AutonomyPreferences(
            earned_autonomy=EarnedAutonomyConfig(
                enabled=True,
                min_executions=10,
                min_success_rate=0.95,
            ),
        )
        mock_store.get_stats.return_value = ExecutionStats(
            capability_name="test.cap",
            total_executions=5,
            successful_executions=5,
            consecutive_successes=5,
        )
        status = engine.get_autonomy_status("test.cap", "low")
        assert status.earned_progress is not None
        # exec_progress = 5/10 = 0.5, rate_progress = (5/5)/(0.95) capped at 1.0
        # earned_progress = (0.5 + 1.0) / 2 = 0.75
        assert 0.0 < status.earned_progress <= 1.0

    def test_status_earned_progress_none_when_disabled(self, engine, mock_store):
        """earned_progress is None when earned autonomy is disabled."""
        status = engine.get_autonomy_status("test.cap", "low")
        assert status.earned_progress is None

    def test_status_earned_progress_none_when_no_executions(self, engine, mock_store):
        """earned_progress is None when enabled but no executions."""
        mock_store.get_preferences.return_value = AutonomyPreferences(
            earned_autonomy=EarnedAutonomyConfig(enabled=True),
        )
        mock_store.get_stats.return_value = ExecutionStats(
            capability_name="test.cap",
            total_executions=0,
        )
        status = engine.get_autonomy_status("test.cap", "low")
        assert status.earned_progress is None

    def test_status_stats_none_when_zero_executions(self, engine, mock_store):
        """stats field is None when total_executions is 0."""
        status = engine.get_autonomy_status("test.cap", "low")
        assert status.stats is None

    def test_status_stats_present_when_has_executions(self, engine, mock_store):
        """stats field is present when total_executions > 0."""
        mock_store.get_stats.return_value = ExecutionStats(
            capability_name="test.cap",
            total_executions=3,
            successful_executions=3,
        )
        status = engine.get_autonomy_status("test.cap", "low")
        assert status.stats is not None
        assert status.stats.total_executions == 3

    # -- update_after_execution -----------------------------------------------

    def test_update_disabled_records_but_returns_none(self, engine, mock_store):
        """When earned autonomy is disabled, stats are still recorded."""
        msg = engine.update_after_execution("test.cap", True, "low")
        assert msg is None
        mock_store.record_execution.assert_called_once_with("test.cap", True)

    def test_update_earns_autonomy(self, engine, mock_store):
        """Capability earns autonomy after enough successful runs."""
        mock_store.get_preferences.return_value = AutonomyPreferences(
            earned_autonomy=EarnedAutonomyConfig(
                enabled=True,
                min_executions=10,
                min_success_rate=0.9,
            ),
        )
        mock_store.get_override.return_value = None  # not yet autonomous
        mock_store.record_execution.return_value = ExecutionStats(
            capability_name="test.cap",
            total_executions=10,
            successful_executions=10,
            consecutive_successes=10,
        )
        msg = engine.update_after_execution("test.cap", True, "low")
        assert msg is not None
        assert "earned" in msg
        mock_store.set_override.assert_called_once_with("test.cap", True, reason="earned")

    def test_update_does_not_earn_if_already_autonomous(self, engine, mock_store):
        """If already autonomous (manual), update returns None."""
        mock_store.get_preferences.return_value = AutonomyPreferences(
            earned_autonomy=EarnedAutonomyConfig(enabled=True, min_executions=10),
        )
        mock_store.get_override.return_value = AutonomyOverride(
            capability_name="test.cap",
            autonomous=True,
            reason="manual",
        )
        mock_store.record_execution.return_value = ExecutionStats(
            capability_name="test.cap",
            total_executions=15,
            successful_executions=15,
            consecutive_successes=15,
        )
        msg = engine.update_after_execution("test.cap", True, "low")
        assert msg is None

    def test_update_revokes_earned_on_failure(self, engine, mock_store):
        """Failure revokes earned autonomy."""
        mock_store.get_preferences.return_value = AutonomyPreferences(
            earned_autonomy=EarnedAutonomyConfig(enabled=True),
        )
        mock_store.get_override.return_value = AutonomyOverride(
            capability_name="test.cap",
            autonomous=True,
            reason="earned",
        )
        mock_store.record_execution.return_value = ExecutionStats(
            capability_name="test.cap",
            total_executions=11,
            successful_executions=10,
            failed_executions=1,
            consecutive_successes=0,
        )
        msg = engine.update_after_execution("test.cap", False, "low")
        assert msg is not None
        assert "revoked" in msg
        mock_store.remove_override.assert_called_once_with("test.cap")

    def test_update_does_not_revoke_manual_on_failure(self, engine, mock_store):
        """Failure does NOT revoke a manual grant."""
        mock_store.get_preferences.return_value = AutonomyPreferences(
            earned_autonomy=EarnedAutonomyConfig(enabled=True),
        )
        mock_store.get_override.return_value = AutonomyOverride(
            capability_name="test.cap",
            autonomous=True,
            reason="manual",
        )
        mock_store.record_execution.return_value = ExecutionStats(
            capability_name="test.cap",
            total_executions=11,
            successful_executions=10,
            failed_executions=1,
            consecutive_successes=0,
        )
        msg = engine.update_after_execution("test.cap", False, "low")
        assert msg is None
        mock_store.remove_override.assert_not_called()

    def test_update_success_not_autonomous_not_enough(self, engine, mock_store):
        """Success when not autonomous and not enough stats returns None."""
        mock_store.get_preferences.return_value = AutonomyPreferences(
            earned_autonomy=EarnedAutonomyConfig(
                enabled=True,
                min_executions=10,
            ),
        )
        mock_store.get_override.return_value = None
        mock_store.record_execution.return_value = ExecutionStats(
            capability_name="test.cap",
            total_executions=3,
            successful_executions=3,
            consecutive_successes=3,
        )
        msg = engine.update_after_execution("test.cap", True, "low")
        assert msg is None

    def test_update_failure_not_autonomous(self, engine, mock_store):
        """Failure when not autonomous returns None (nothing to revoke)."""
        mock_store.get_preferences.return_value = AutonomyPreferences(
            earned_autonomy=EarnedAutonomyConfig(enabled=True),
        )
        mock_store.get_override.return_value = None
        mock_store.record_execution.return_value = ExecutionStats(
            capability_name="test.cap",
            total_executions=1,
            successful_executions=0,
            failed_executions=1,
            consecutive_successes=0,
        )
        msg = engine.update_after_execution("test.cap", False, "low")
        assert msg is None

    # -- _has_earned_autonomy -------------------------------------------------

    def test_has_earned_insufficient_executions(self, engine):
        stats = ExecutionStats(
            capability_name="test",
            total_executions=5,
            successful_executions=5,
            consecutive_successes=5,
        )
        config = EarnedAutonomyConfig(enabled=True, min_executions=10)
        assert engine._has_earned_autonomy(stats, config) is False

    def test_has_earned_low_success_rate(self, engine):
        stats = ExecutionStats(
            capability_name="test",
            total_executions=20,
            successful_executions=10,
            failed_executions=10,
            consecutive_successes=5,
        )
        config = EarnedAutonomyConfig(enabled=True)
        assert engine._has_earned_autonomy(stats, config) is False

    def test_has_earned_cooldown_not_met(self, engine):
        stats = ExecutionStats(
            capability_name="test",
            total_executions=20,
            successful_executions=19,
            failed_executions=1,
            consecutive_successes=2,
        )
        config = EarnedAutonomyConfig(
            enabled=True,
            min_executions=10,
            min_success_rate=0.90,
            cooldown_after_failure=5,
        )
        assert engine._has_earned_autonomy(stats, config) is False

    def test_has_earned_all_criteria_met(self, engine):
        stats = ExecutionStats(
            capability_name="test",
            total_executions=20,
            successful_executions=19,
            failed_executions=1,
            consecutive_successes=10,
        )
        config = EarnedAutonomyConfig(
            enabled=True,
            min_executions=10,
            min_success_rate=0.90,
            cooldown_after_failure=5,
        )
        assert engine._has_earned_autonomy(stats, config) is True

    def test_has_earned_no_failures_meets_criteria(self, engine):
        """When there are zero failures, cooldown check passes."""
        stats = ExecutionStats(
            capability_name="test",
            total_executions=10,
            successful_executions=10,
            failed_executions=0,
            consecutive_successes=10,
        )
        config = EarnedAutonomyConfig(
            enabled=True,
            min_executions=10,
            min_success_rate=0.95,
            cooldown_after_failure=5,
        )
        assert engine._has_earned_autonomy(stats, config) is True

    def test_has_earned_exactly_at_thresholds(self, engine):
        """Exactly at min_executions and min_success_rate."""
        stats = ExecutionStats(
            capability_name="test",
            total_executions=10,
            successful_executions=10,
            failed_executions=0,
            consecutive_successes=10,
        )
        config = EarnedAutonomyConfig(
            enabled=True,
            min_executions=10,
            min_success_rate=1.0,
        )
        assert engine._has_earned_autonomy(stats, config) is True


# ---------------------------------------------------------------------------
# AutonomyEngine construction
# ---------------------------------------------------------------------------


class TestAutonomyEngineInit:
    def test_default_store_creation(self):
        """When no store passed, AutonomyEngine creates one (mocked)."""
        with patch("elle.capabilities.autonomy.AutonomyStore") as MockStore:
            engine = AutonomyEngine()
            MockStore.assert_called_once()
            assert engine.store is MockStore.return_value

    def test_custom_store(self):
        """When store is passed, it uses that store."""
        mock_store = MagicMock(spec=AutonomyStore)
        engine = AutonomyEngine(store=mock_store)
        assert engine.store is mock_store


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


class TestModuleFunctions:
    def test_get_and_reset_engine(self):
        import elle.capabilities.autonomy as mod

        reset_autonomy_engine()
        assert mod._engine is None
        with patch("elle.capabilities.autonomy.AutonomyStore"):
            e1 = get_autonomy_engine()
            e2 = get_autonomy_engine()
            assert e1 is e2
        reset_autonomy_engine()
        assert mod._engine is None

    def test_reset_clears_engine(self):
        import elle.capabilities.autonomy as mod

        with patch("elle.capabilities.autonomy.AutonomyStore"):
            _ = get_autonomy_engine()
            assert mod._engine is not None
        reset_autonomy_engine()
        assert mod._engine is None

    def test_get_engine_creates_fresh(self):
        reset_autonomy_engine()
        with patch("elle.capabilities.autonomy.AutonomyStore"):
            engine = get_autonomy_engine()
        assert isinstance(engine, AutonomyEngine)
        reset_autonomy_engine()
