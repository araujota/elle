from __future__ import annotations

from unittest.mock import patch

import pytest

from elle.capabilities.autonomy import (
    RISK_ORDERING,
    AutonomyEngine,
    AutonomyLevel,
    AutonomyPreferences,
    AutonomyStore,
    EarnedAutonomyConfig,
    ExecutionStats,
    get_autonomy_engine,
    reset_autonomy_engine,
    risk_allowed,
)

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
        assert risk_allowed("unknown_risk", "low") is True


# ---------------------------------------------------------------------------
# ExecutionStats
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AutonomyStore (with tmp_path)
# ---------------------------------------------------------------------------


class TestAutonomyStore:
    @pytest.fixture
    def store(self, deps_conn):
        with patch("elle.capabilities.autonomy.AutonomyStore._ensure_schema"):
            s = AutonomyStore.__new__(AutonomyStore)
            s.conn = deps_conn
        return s

    def test_get_preferences_default(self, store):
        prefs = store.get_preferences()
        assert prefs.base_level == AutonomyLevel.LOW_RISK

    def test_save_and_get_preferences(self, store):
        prefs = AutonomyPreferences(base_level=AutonomyLevel.MEDIUM_RISK)
        store.save_preferences(prefs)
        loaded = store.get_preferences()
        assert loaded.base_level == AutonomyLevel.MEDIUM_RISK

    def test_set_base_level(self, store):
        store.set_base_level(AutonomyLevel.HIGH_RISK)
        prefs = store.get_preferences()
        assert prefs.base_level == AutonomyLevel.HIGH_RISK

    def test_set_earned_autonomy_enabled(self, store):
        store.set_earned_autonomy_enabled(True)
        prefs = store.get_preferences()
        assert prefs.earned_autonomy.enabled is True

    def test_override_crud(self, store):
        # No override initially
        assert store.get_override("test.cap") is None

        # Set override
        store.set_override("test.cap", True, reason="manual")
        override = store.get_override("test.cap")
        assert override is not None
        assert override.autonomous is True

        # Update override
        store.set_override("test.cap", False, reason="manual")
        override = store.get_override("test.cap")
        assert override.autonomous is False

        # Remove
        removed = store.remove_override("test.cap")
        assert removed is True
        assert store.get_override("test.cap") is None

        # Remove non-existent
        removed = store.remove_override("nonexistent")
        assert removed is False

    def test_list_overrides(self, store):
        store.set_override("a.cap", True)
        store.set_override("b.cap", False)
        overrides = store.list_overrides()
        assert len(overrides) == 2
        names = {o.capability_name for o in overrides}
        assert names == {"a.cap", "b.cap"}

    def test_earned_override_has_earned_at(self, store):
        store.set_override("test.cap", True, reason="earned")
        override = store.get_override("test.cap")
        assert override is not None
        assert override.earned_at is not None

    def test_execution_stats(self, store):
        stats = store.get_stats("test.cap")
        assert stats.total_executions == 0

        # Record success
        stats = store.record_execution("test.cap", True)
        assert stats.total_executions == 1
        assert stats.successful_executions == 1
        assert stats.consecutive_successes == 1
        assert stats.last_success_at is not None

        # Record failure
        stats = store.record_execution("test.cap", False)
        assert stats.total_executions == 2
        assert stats.failed_executions == 1
        assert stats.consecutive_successes == 0
        assert stats.last_failure_at is not None

    def test_get_all_stats(self, store):
        store.record_execution("a.cap", True)
        store.record_execution("b.cap", False)
        all_stats = store.get_all_stats()
        assert len(all_stats) == 2


# ---------------------------------------------------------------------------
# AutonomyEngine
# ---------------------------------------------------------------------------


class TestAutonomyEngine:
    @pytest.fixture
    def engine_store(self, deps_conn):
        with patch("elle.capabilities.autonomy.AutonomyStore._ensure_schema"):
            s = AutonomyStore.__new__(AutonomyStore)
            s.conn = deps_conn
        return s

    @pytest.fixture
    def engine(self, engine_store):
        return AutonomyEngine(store=engine_store)

    def test_store_property(self, engine, engine_store):
        assert engine.store is engine_store

    def test_manual_grant(self, engine, engine_store):
        engine_store.set_override("test.cap", True, reason="manual")
        can_run, reason = engine.can_run_autonomously("test.cap", "high")
        assert can_run is True
        assert "Manual grant" in reason

    def test_manual_revoke(self, engine, engine_store):
        engine_store.set_override("test.cap", False, reason="manual")
        can_run, reason = engine.can_run_autonomously("test.cap", "none")
        assert can_run is False
        assert "Manual revoke" in reason

    def test_earned_override(self, engine, engine_store):
        engine_store.set_override("test.cap", True, reason="earned")
        can_run, reason = engine.can_run_autonomously("test.cap", "high")
        assert can_run is True
        assert "Earned" in reason

    def test_base_level_allows(self, engine, engine_store):
        # Default is LOW_RISK which allows none and low
        can_run, reason = engine.can_run_autonomously("test.cap", "low")
        assert can_run is True

    def test_base_level_blocks(self, engine, engine_store):
        # Default is LOW_RISK which blocks medium
        can_run, reason = engine.can_run_autonomously("test.cap", "medium")
        assert can_run is False
        assert "exceeds" in reason

    def test_earned_autonomy_check(self, engine, engine_store):
        engine_store.set_earned_autonomy_enabled(True)
        # Record enough successes
        for _ in range(15):
            engine_store.record_execution("test.cap", True)

        can_run, reason = engine.can_run_autonomously("test.cap", "high")
        assert can_run is True
        assert "Earned" in reason

    def test_get_autonomy_status_base(self, engine, engine_store):
        status = engine.get_autonomy_status("test.cap", "low")
        assert status.source == "base_level"
        assert status.can_run_autonomously is True

    def test_get_autonomy_status_blocked(self, engine, engine_store):
        status = engine.get_autonomy_status("test.cap", "critical")
        assert status.source == "risk_blocked"
        assert status.can_run_autonomously is False

    def test_get_autonomy_status_manual_grant(self, engine, engine_store):
        engine_store.set_override("test.cap", True, reason="manual")
        status = engine.get_autonomy_status("test.cap", "high")
        assert status.source == "manual_grant"

    def test_get_autonomy_status_manual_revoke(self, engine, engine_store):
        engine_store.set_override("test.cap", False, reason="manual")
        status = engine.get_autonomy_status("test.cap", "low")
        assert status.source == "manual_revoke"

    def test_get_autonomy_status_earned(self, engine, engine_store):
        engine_store.set_earned_autonomy_enabled(True)
        for _ in range(15):
            engine_store.record_execution("test.cap", True)
        engine_store.set_override("test.cap", True, reason="earned")
        status = engine.get_autonomy_status("test.cap", "high")
        assert status.source == "earned"

    def test_earned_progress_calculated(self, engine, engine_store):
        engine_store.set_earned_autonomy_enabled(True)
        engine_store.record_execution("test.cap", True)
        status = engine.get_autonomy_status("test.cap", "low")
        assert status.earned_progress is not None
        assert 0.0 < status.earned_progress <= 1.0

    def test_earned_progress_none_when_disabled(self, engine, engine_store):
        status = engine.get_autonomy_status("test.cap", "low")
        assert status.earned_progress is None

    def test_update_after_execution_disabled(self, engine, engine_store):
        msg = engine.update_after_execution("test.cap", True, "low")
        assert msg is None

    def test_update_earns_autonomy(self, engine, engine_store):
        engine_store.set_earned_autonomy_enabled(True)
        # Run enough to earn
        for _ in range(9):
            engine_store.record_execution("test.cap", True)
        msg = engine.update_after_execution("test.cap", True, "low")
        assert msg is not None
        assert "earned" in msg

    def test_update_revokes_after_failure(self, engine, engine_store):
        engine_store.set_earned_autonomy_enabled(True)
        engine_store.set_override("test.cap", True, reason="earned")
        msg = engine.update_after_execution("test.cap", False, "low")
        assert msg is not None
        assert "revoked" in msg

    def test_update_no_change_on_success_already_autonomous(self, engine, engine_store):
        engine_store.set_earned_autonomy_enabled(True)
        engine_store.set_override("test.cap", True, reason="manual")
        msg = engine.update_after_execution("test.cap", True, "low")
        assert msg is None

    def test_update_no_revoke_manual_grant(self, engine, engine_store):
        engine_store.set_earned_autonomy_enabled(True)
        engine_store.set_override("test.cap", True, reason="manual")
        msg = engine.update_after_execution("test.cap", False, "low")
        # Manual grants are not revoked
        assert msg is None

    def test_has_earned_autonomy_insufficient_executions(self, engine):
        stats = ExecutionStats(
            capability_name="test",
            total_executions=5,
            successful_executions=5,
            consecutive_successes=5,
        )
        config = EarnedAutonomyConfig(enabled=True, min_executions=10)
        assert engine._has_earned_autonomy(stats, config) is False

    def test_has_earned_autonomy_low_success_rate(self, engine):
        stats = ExecutionStats(
            capability_name="test",
            total_executions=20,
            successful_executions=10,
            consecutive_successes=5,
        )
        config = EarnedAutonomyConfig(enabled=True)
        assert engine._has_earned_autonomy(stats, config) is False

    def test_has_earned_autonomy_cooldown_not_met(self, engine):
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
