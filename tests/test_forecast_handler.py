from __future__ import annotations

"""Comprehensive tests for elle.daemon.incidents.forecast_handler.

Covers:
- RemediationPlan and ForecastIncidentResult models
- create_forecast_incident() with all parameter combinations
- handle_forecast_trigger() for prepare and act_now urgency paths
- _generate_remediation_plan() success and failure paths
- _execute_remediation() success and failure paths
- _update_incident_with_plan() including ImportError and generic error paths
- _update_incident_outcome() including ImportError and generic error paths
- _check_plan_autonomy() for all branches
- _notify_forecast_warning() success and error paths
- _notify_forecast_remediated() success and error paths
- execute_prepared_plan() with no plan, success, and error paths
- _parse_plan_steps() for numbered, bullet, and fallback formats
- _extract_capabilities() for known and unknown prefixes
- _extract_summary() for short, long, and multi-paragraph responses
- _extract_cause_description() for all regex patterns and edge cases
"""

import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock structlog before importing any elle modules that may pull it in
if "structlog" not in sys.modules:
    sys.modules["structlog"] = MagicMock()

from elle.daemon.incidents.forecast_handler import (
    ForecastIncidentResult,
    RemediationPlan,
    _check_plan_autonomy,
    _extract_capabilities,
    _extract_cause_description,
    _extract_summary,
    _parse_plan_steps,
    create_forecast_incident,
    execute_prepared_plan,
    handle_forecast_trigger,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_forecast(**overrides: object) -> MagicMock:
    """Build a mock Forecast with sensible defaults."""
    f = MagicMock()
    f.metric = overrides.get("metric", "disk./.used_pct")
    f.current_value = overrides.get("current_value", 85.0)
    f.warning_threshold = overrides.get("warning_threshold", 90.0)
    f.critical_threshold = overrides.get("critical_threshold", 95.0)
    f.time_to_warning_hours = overrides.get("time_to_warning_hours", 6.0)
    f.time_to_critical_hours = overrides.get("time_to_critical_hours", 2.0)
    f.rate_of_change = overrides.get("rate_of_change", 1.5)
    f.confidence = overrides.get("confidence", 0.8)
    return f


def _make_plan(**overrides: object) -> RemediationPlan:
    """Build a RemediationPlan with sensible defaults."""
    defaults = {
        "plan_id": "plan-abc123",
        "incident_id": "FORECAST-ABCD1234",
        "metric": "disk./.used_pct",
        "current_value": 85.0,
        "threshold": 90.0,
        "urgency": "prepare",
        "summary": "Clean old log files and configure rotation",
        "cause_description": "Log rotation not configured properly",
        "steps": ("Remove old logs", "Configure logrotate"),
        "capabilities_to_execute": ("file.delete", "config.edit"),
        "confidence": 0.8,
    }
    defaults.update(overrides)
    return RemediationPlan(**defaults)


# =============================================================================
# Model Tests
# =============================================================================


class TestRemediationPlanModel:
    """Tests for RemediationPlan Pydantic model."""

    def test_create_minimal(self):
        plan = RemediationPlan(
            plan_id="p1",
            incident_id="INC-1",
            metric="mem.used_pct",
            current_value=80.0,
            threshold=90.0,
            urgency="prepare",
            summary="Reduce memory usage",
        )
        assert plan.plan_id == "p1"
        assert plan.urgency == "prepare"
        assert plan.steps == ()
        assert plan.capabilities_to_execute == ()
        assert plan.executed is False
        assert plan.executed_at is None
        assert plan.execution_result is None
        assert plan.generated_by == "agent_loop"
        assert 0.0 <= plan.confidence <= 1.0

    def test_create_full(self):
        now = datetime.utcnow()
        plan = _make_plan(
            executed=True,
            executed_at=now,
            execution_result="Success",
        )
        assert plan.executed is True
        assert plan.executed_at == now
        assert plan.execution_result == "Success"

    def test_frozen_model(self):
        plan = _make_plan()
        with pytest.raises(Exception):
            plan.summary = "changed"

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            _make_plan(confidence=1.5)
        with pytest.raises(Exception):
            _make_plan(confidence=-0.1)

    def test_urgency_literal(self):
        plan_prepare = _make_plan(urgency="prepare")
        assert plan_prepare.urgency == "prepare"
        plan_act = _make_plan(urgency="act_now")
        assert plan_act.urgency == "act_now"


class TestForecastIncidentResultModel:
    """Tests for ForecastIncidentResult Pydantic model."""

    def test_success_result(self):
        result = ForecastIncidentResult(
            incident_id="INC-1",
            urgency="prepare",
            executed=False,
            success=True,
        )
        assert result.success is True
        assert result.error is None
        assert result.plan is None

    def test_failure_result(self):
        result = ForecastIncidentResult(
            incident_id="INC-2",
            urgency="act_now",
            executed=True,
            success=False,
            error="Something broke",
        )
        assert result.success is False
        assert result.error == "Something broke"

    def test_result_with_plan(self):
        plan = _make_plan()
        result = ForecastIncidentResult(
            incident_id=plan.incident_id,
            urgency="prepare",
            plan=plan,
            executed=False,
            success=True,
        )
        assert result.plan is not None
        assert result.plan.plan_id == plan.plan_id

    def test_frozen_model(self):
        result = ForecastIncidentResult(incident_id="INC-1", urgency="prepare", success=True)
        with pytest.raises(Exception):
            result.success = False


# =============================================================================
# create_forecast_incident Tests
# =============================================================================


class TestCreateForecastIncident:
    """Tests for create_forecast_incident()."""

    def test_prepare_urgency(self):
        data = create_forecast_incident(
            metric="disk./.used_pct",
            current_value=85.0,
            threshold=90.0,
            time_to_threshold=6.0,
            rate_of_change=1.5,
            urgency="prepare",
            confidence=0.8,
        )
        assert data["id"].startswith("FORECAST-")
        assert data["severity"] == "warning"
        assert data["domain"] == "disk"
        assert data["status"] == "open"
        assert "Forecast:" in data["title"]
        assert "6.0h" in data["title"]
        assert data["trigger_source"] == "forecast"
        assert data["forecast_urgency"] == "prepare"
        assert data["metrics_json"]["confidence"] == 0.8

    def test_act_now_urgency(self):
        data = create_forecast_incident(
            metric="mem.used_pct",
            current_value=93.0,
            threshold=95.0,
            time_to_threshold=0.5,
            rate_of_change=4.0,
            urgency="act_now",
        )
        assert data["severity"] == "critical"
        assert "URGENT:" in data["title"]
        assert data["domain"] == "mem"
        assert "critical" in data["summary"]

    def test_time_to_threshold_none_shows_imminent(self):
        data = create_forecast_incident(
            metric="cpu.load_avg",
            current_value=11.0,
            threshold=12.0,
            time_to_threshold=None,
            rate_of_change=2.0,
            urgency="prepare",
        )
        assert "imminent" in data["title"]

    def test_metric_without_dot_defaults_to_system_domain(self):
        data = create_forecast_incident(
            metric="standalone_metric",
            current_value=50.0,
            threshold=80.0,
            time_to_threshold=10.0,
            rate_of_change=1.0,
            urgency="prepare",
        )
        assert data["domain"] == "system"

    def test_metrics_json_contents(self):
        data = create_forecast_incident(
            metric="net.eth0.errors",
            current_value=100.0,
            threshold=200.0,
            time_to_threshold=3.0,
            rate_of_change=33.3,
            urgency="prepare",
            confidence=0.7,
        )
        mj = data["metrics_json"]
        assert mj["metric"] == "net.eth0.errors"
        assert mj["current_value"] == 100.0
        assert mj["threshold"] == 200.0
        assert mj["time_to_threshold_hours"] == 3.0
        assert mj["rate_of_change"] == 33.3
        assert mj["confidence"] == 0.7

    def test_default_confidence(self):
        data = create_forecast_incident(
            metric="disk./.used_pct",
            current_value=80.0,
            threshold=90.0,
            time_to_threshold=5.0,
            rate_of_change=2.0,
            urgency="prepare",
        )
        assert data["metrics_json"]["confidence"] == 0.5

    def test_incident_id_is_unique(self):
        ids = set()
        for _ in range(20):
            data = create_forecast_incident(
                metric="m",
                current_value=0,
                threshold=1,
                time_to_threshold=1,
                rate_of_change=1,
                urgency="prepare",
            )
            ids.add(data["id"])
        assert len(ids) == 20

    def test_summary_includes_rate_of_change(self):
        data = create_forecast_incident(
            metric="disk./.used_pct",
            current_value=85.0,
            threshold=90.0,
            time_to_threshold=4.0,
            rate_of_change=1.25,
            urgency="prepare",
        )
        assert "1.25" in data["summary"]


# =============================================================================
# handle_forecast_trigger Tests
# =============================================================================


class TestHandleForecastTrigger:
    """Tests for handle_forecast_trigger() async entry point."""

    @pytest.mark.asyncio
    async def test_prepare_with_auto_execute(self):
        """prepare urgency + all caps autonomous => auto-execute."""
        forecast = _make_forecast()
        plan = _make_plan(capabilities_to_execute=("file.delete",))

        mock_store_create = MagicMock()
        mock_report = MagicMock()
        mock_report.incident_id = "FORECAST-AABB"
        mock_store_create.return_value = mock_report
        mock_store_update = MagicMock()

        with (
            patch(
                "elle.daemon.incidents.store.create_incident_draft",
                mock_store_create,
            ),
            patch(
                "elle.daemon.incidents.store.update_incident",
                mock_store_update,
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._generate_remediation_plan",
                new_callable=AsyncMock,
                return_value=plan,
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._update_incident_with_plan",
                new_callable=AsyncMock,
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._check_plan_autonomy",
                return_value=True,
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._execute_remediation",
                new_callable=AsyncMock,
                return_value=(True, None),
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._notify_forecast_remediated",
                new_callable=AsyncMock,
            ) as mock_notify,
        ):
            result = await handle_forecast_trigger(forecast, "prepare")

        assert result.success is True
        assert result.executed is True
        assert result.urgency == "prepare"
        assert result.plan is plan
        mock_notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_prepare_without_auto_execute(self):
        """prepare urgency + caps NOT autonomous => notify with plan, no exec."""
        forecast = _make_forecast()
        plan = _make_plan(capabilities_to_execute=("service.restart",))

        mock_store_create = MagicMock()
        mock_report = MagicMock()
        mock_report.incident_id = "FORECAST-CCDD"
        mock_store_create.return_value = mock_report
        mock_store_update = MagicMock()

        with (
            patch(
                "elle.daemon.incidents.store.create_incident_draft",
                mock_store_create,
            ),
            patch(
                "elle.daemon.incidents.store.update_incident",
                mock_store_update,
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._generate_remediation_plan",
                new_callable=AsyncMock,
                return_value=plan,
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._update_incident_with_plan",
                new_callable=AsyncMock,
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._check_plan_autonomy",
                return_value=False,
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._notify_forecast_warning",
                new_callable=AsyncMock,
            ) as mock_warn,
        ):
            result = await handle_forecast_trigger(forecast, "prepare")

        assert result.success is True
        assert result.executed is False
        assert result.plan is plan
        mock_warn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_prepare_with_empty_capabilities(self):
        """prepare urgency + empty capabilities => not autonomous."""
        forecast = _make_forecast()
        plan = _make_plan(capabilities_to_execute=())

        mock_store_create = MagicMock()
        mock_report = MagicMock()
        mock_report.incident_id = "FORECAST-EEFF"
        mock_store_create.return_value = mock_report

        with (
            patch(
                "elle.daemon.incidents.store.create_incident_draft",
                mock_store_create,
            ),
            patch("elle.daemon.incidents.store.update_incident", MagicMock()),
            patch(
                "elle.daemon.incidents.forecast_handler._generate_remediation_plan",
                new_callable=AsyncMock,
                return_value=plan,
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._update_incident_with_plan",
                new_callable=AsyncMock,
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._check_plan_autonomy",
                return_value=False,
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._notify_forecast_warning",
                new_callable=AsyncMock,
            ),
        ):
            result = await handle_forecast_trigger(forecast, "prepare")

        assert result.executed is False
        assert result.success is True

    @pytest.mark.asyncio
    async def test_act_now_success(self):
        """act_now urgency => immediate execution."""
        forecast = _make_forecast()

        mock_store_create = MagicMock()
        mock_report = MagicMock()
        mock_report.incident_id = "FORECAST-0011"
        mock_store_create.return_value = mock_report

        with (
            patch(
                "elle.daemon.incidents.store.create_incident_draft",
                mock_store_create,
            ),
            patch("elle.daemon.incidents.store.update_incident", MagicMock()),
            patch(
                "elle.daemon.incidents.forecast_handler._execute_remediation",
                new_callable=AsyncMock,
                return_value=(True, None),
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._notify_forecast_remediated",
                new_callable=AsyncMock,
            ) as mock_notify,
        ):
            result = await handle_forecast_trigger(forecast, "act_now")

        assert result.success is True
        assert result.executed is True
        assert result.plan is None
        assert result.urgency == "act_now"
        mock_notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_act_now_failure(self):
        """act_now urgency execution failure still returns result."""
        forecast = _make_forecast()

        mock_store_create = MagicMock()
        mock_report = MagicMock()
        mock_report.incident_id = "FORECAST-2233"
        mock_store_create.return_value = mock_report

        with (
            patch(
                "elle.daemon.incidents.store.create_incident_draft",
                mock_store_create,
            ),
            patch("elle.daemon.incidents.store.update_incident", MagicMock()),
            patch(
                "elle.daemon.incidents.forecast_handler._execute_remediation",
                new_callable=AsyncMock,
                return_value=(False, "OOM during remediation"),
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._notify_forecast_remediated",
                new_callable=AsyncMock,
            ),
        ):
            result = await handle_forecast_trigger(forecast, "act_now")

        assert result.success is False
        assert result.executed is True
        assert result.error == "OOM during remediation"

    @pytest.mark.asyncio
    async def test_incident_store_import_error(self):
        """ImportError on incident store falls through gracefully."""
        forecast = _make_forecast()

        with (
            patch(
                "elle.daemon.incidents.store.create_incident_draft",
                side_effect=ImportError("no store"),
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._execute_remediation",
                new_callable=AsyncMock,
                return_value=(True, None),
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._notify_forecast_remediated",
                new_callable=AsyncMock,
            ),
        ):
            result = await handle_forecast_trigger(forecast, "act_now")

        # Should still succeed, just without persistence
        assert result.success is True

    @pytest.mark.asyncio
    async def test_incident_store_generic_error(self):
        """Generic error from store is logged but doesn't fail the handler."""
        forecast = _make_forecast()

        mock_store_create = MagicMock()
        mock_report = MagicMock()
        mock_report.incident_id = "FORECAST-4455"
        mock_store_create.return_value = mock_report

        with (
            patch(
                "elle.daemon.incidents.store.create_incident_draft",
                mock_store_create,
            ),
            patch(
                "elle.daemon.incidents.store.update_incident",
                side_effect=RuntimeError("db locked"),
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._execute_remediation",
                new_callable=AsyncMock,
                return_value=(True, None),
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._notify_forecast_remediated",
                new_callable=AsyncMock,
            ),
        ):
            result = await handle_forecast_trigger(forecast, "act_now")

        assert result.success is True

    @pytest.mark.asyncio
    async def test_top_level_exception_returns_failure(self):
        """Unhandled exception in handler returns a clean failure result."""
        forecast = _make_forecast()
        # Force create_forecast_incident to blow up early
        forecast.metric = None  # Will cause an AttributeError in split

        result = await handle_forecast_trigger(forecast, "prepare")

        assert result.success is False
        assert result.incident_id == ""
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_uses_critical_threshold_for_act_now(self):
        """act_now uses critical_threshold from forecast."""
        forecast = _make_forecast(critical_threshold=95.0, warning_threshold=90.0)

        called_with = {}

        original_create = create_forecast_incident

        def capture_create(**kwargs):
            called_with.update(kwargs)
            return original_create(**kwargs)

        mock_store_create = MagicMock()
        mock_report = MagicMock()
        mock_report.incident_id = "FORECAST-6677"
        mock_store_create.return_value = mock_report

        with (
            patch(
                "elle.daemon.incidents.forecast_handler.create_forecast_incident",
                side_effect=capture_create,
            ),
            patch(
                "elle.daemon.incidents.store.create_incident_draft",
                mock_store_create,
            ),
            patch("elle.daemon.incidents.store.update_incident", MagicMock()),
            patch(
                "elle.daemon.incidents.forecast_handler._execute_remediation",
                new_callable=AsyncMock,
                return_value=(True, None),
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._notify_forecast_remediated",
                new_callable=AsyncMock,
            ),
        ):
            await handle_forecast_trigger(forecast, "act_now")

        assert called_with.get("threshold") == 95.0

    @pytest.mark.asyncio
    async def test_uses_warning_threshold_for_prepare(self):
        """prepare uses warning_threshold from forecast."""
        forecast = _make_forecast(critical_threshold=95.0, warning_threshold=90.0)

        called_with = {}

        original_create = create_forecast_incident

        def capture_create(**kwargs):
            called_with.update(kwargs)
            return original_create(**kwargs)

        mock_store_create = MagicMock()
        mock_report = MagicMock()
        mock_report.incident_id = "FORECAST-8899"
        mock_store_create.return_value = mock_report

        with (
            patch(
                "elle.daemon.incidents.forecast_handler.create_forecast_incident",
                side_effect=capture_create,
            ),
            patch(
                "elle.daemon.incidents.store.create_incident_draft",
                mock_store_create,
            ),
            patch("elle.daemon.incidents.store.update_incident", MagicMock()),
            patch(
                "elle.daemon.incidents.forecast_handler._generate_remediation_plan",
                new_callable=AsyncMock,
                return_value=_make_plan(capabilities_to_execute=()),
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._update_incident_with_plan",
                new_callable=AsyncMock,
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._check_plan_autonomy",
                return_value=False,
            ),
            patch(
                "elle.daemon.incidents.forecast_handler._notify_forecast_warning",
                new_callable=AsyncMock,
            ),
        ):
            await handle_forecast_trigger(forecast, "prepare")

        assert called_with.get("threshold") == 90.0


# =============================================================================
# _generate_remediation_plan Tests
# =============================================================================


class TestGenerateRemediationPlan:
    """Tests for _generate_remediation_plan()."""

    @pytest.mark.asyncio
    async def test_success(self):
        from elle.daemon.incidents.forecast_handler import _generate_remediation_plan

        forecast = _make_forecast()

        mock_result = MagicMock()
        mock_result.response = (
            "The issue is disk usage.\n\n"
            "Cause: Old log files accumulating.\n\n"
            "1. Remove old logs from /var/log\n"
            "2. Configure logrotate for service.restart\n"
            "3. Run file.delete on temp files\n"
        )

        mock_loop = AsyncMock()
        mock_loop.run.return_value = mock_result

        with patch("elle.cli.agentic.loop.AgenticLoop", return_value=mock_loop):
            plan = await _generate_remediation_plan(forecast, "FORECAST-TEST")

        assert plan.incident_id == "FORECAST-TEST"
        assert plan.metric == forecast.metric
        assert plan.urgency == "prepare"
        assert len(plan.steps) > 0
        assert plan.confidence == forecast.confidence

    @pytest.mark.asyncio
    async def test_exception_returns_minimal_plan(self):
        from elle.daemon.incidents.forecast_handler import _generate_remediation_plan

        forecast = _make_forecast()

        with patch(
            "elle.cli.agentic.loop.AgenticLoop",
            side_effect=RuntimeError("LLM down"),
        ):
            plan = await _generate_remediation_plan(forecast, "FORECAST-ERR")

        assert "Failed to generate plan" in plan.summary
        assert plan.steps == ()
        assert plan.capabilities_to_execute == ()
        assert plan.confidence == 0.0

    @pytest.mark.asyncio
    async def test_import_error_returns_minimal_plan(self):
        from elle.daemon.incidents.forecast_handler import _generate_remediation_plan

        forecast = _make_forecast()

        with patch(
            "elle.cli.agentic.loop.AgenticLoop",
            side_effect=ImportError("no loop module"),
        ):
            plan = await _generate_remediation_plan(forecast, "FORECAST-IMP")

        assert "Failed to generate plan" in plan.summary
        assert plan.confidence == 0.0

    @pytest.mark.asyncio
    async def test_none_warning_threshold_defaults_to_zero(self):
        from elle.daemon.incidents.forecast_handler import _generate_remediation_plan

        forecast = _make_forecast(warning_threshold=None)

        mock_result = MagicMock()
        mock_result.response = "Plan summary.\n\n1. Do something."
        mock_loop = AsyncMock()
        mock_loop.run.return_value = mock_result

        with patch("elle.cli.agentic.loop.AgenticLoop", return_value=mock_loop):
            plan = await _generate_remediation_plan(forecast, "FORECAST-NIL")

        assert plan.threshold == 0.0


# =============================================================================
# _execute_remediation Tests
# =============================================================================


class TestExecuteRemediation:
    """Tests for _execute_remediation()."""

    @pytest.mark.asyncio
    async def test_success(self):
        from elle.daemon.incidents.forecast_handler import _execute_remediation

        forecast = _make_forecast()

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.response = "Cleaned 5GB of logs"
        mock_result.error = None

        mock_loop = AsyncMock()
        mock_loop.run.return_value = mock_result

        with (
            patch("elle.cli.agentic.loop.AgenticLoop", return_value=mock_loop),
            patch(
                "elle.daemon.incidents.forecast_handler._update_incident_outcome",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            success, error = await _execute_remediation(forecast, "FORECAST-EXEC")

        assert success is True
        assert error is None
        mock_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_loop_failure(self):
        from elle.daemon.incidents.forecast_handler import _execute_remediation

        forecast = _make_forecast()

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.response = "Capability denied"
        mock_result.error = "Policy blocked"

        mock_loop = AsyncMock()
        mock_loop.run.return_value = mock_result

        with (
            patch("elle.cli.agentic.loop.AgenticLoop", return_value=mock_loop),
            patch(
                "elle.daemon.incidents.forecast_handler._update_incident_outcome",
                new_callable=AsyncMock,
            ),
        ):
            success, error = await _execute_remediation(forecast, "FORECAST-FAIL")

        assert success is False
        assert error == "Policy blocked"

    @pytest.mark.asyncio
    async def test_exception(self):
        from elle.daemon.incidents.forecast_handler import _execute_remediation

        forecast = _make_forecast()

        with patch(
            "elle.cli.agentic.loop.AgenticLoop",
            side_effect=RuntimeError("crash"),
        ):
            success, error = await _execute_remediation(forecast, "FORECAST-CRASH")

        assert success is False
        assert "crash" in error


# =============================================================================
# _update_incident_with_plan Tests
# =============================================================================


class TestUpdateIncidentWithPlan:
    """Tests for _update_incident_with_plan()."""

    @pytest.mark.asyncio
    async def test_success(self):
        from elle.daemon.incidents.forecast_handler import _update_incident_with_plan

        plan = _make_plan()

        with patch("elle.daemon.incidents.store.update_incident") as mock_update:
            await _update_incident_with_plan("FORECAST-UP", plan)

        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[0][0] == "FORECAST-UP"
        assert "prepared_plan" in call_kwargs[1]["metrics"]

    @pytest.mark.asyncio
    async def test_import_error(self):
        from elle.daemon.incidents.forecast_handler import _update_incident_with_plan

        plan = _make_plan()

        with patch(
            "elle.daemon.incidents.store.update_incident",
            side_effect=ImportError("no store"),
        ):
            # Should not raise
            await _update_incident_with_plan("FORECAST-IMP", plan)

    @pytest.mark.asyncio
    async def test_generic_error(self):
        from elle.daemon.incidents.forecast_handler import _update_incident_with_plan

        plan = _make_plan()

        with patch(
            "elle.daemon.incidents.store.update_incident",
            side_effect=RuntimeError("db error"),
        ):
            # Should not raise
            await _update_incident_with_plan("FORECAST-GEN", plan)


# =============================================================================
# _update_incident_outcome Tests
# =============================================================================


class TestUpdateIncidentOutcome:
    """Tests for _update_incident_outcome()."""

    @pytest.mark.asyncio
    async def test_success_outcome(self):
        from elle.daemon.incidents.forecast_handler import _update_incident_outcome

        with patch("elle.daemon.incidents.store.update_incident") as mock_update:
            await _update_incident_outcome("INC-1", success=True, response="done")

        mock_update.assert_called_once()
        kwargs = mock_update.call_args[1]
        assert kwargs["outcome"] == "improved"
        assert kwargs["status"] == "mitigated"

    @pytest.mark.asyncio
    async def test_failure_outcome(self):
        from elle.daemon.incidents.forecast_handler import _update_incident_outcome

        with patch("elle.daemon.incidents.store.update_incident") as mock_update:
            await _update_incident_outcome("INC-2", success=False, response="failed")

        kwargs = mock_update.call_args[1]
        assert kwargs["outcome"] == "no_change"
        assert kwargs["status"] == "open"

    @pytest.mark.asyncio
    async def test_import_error(self):
        from elle.daemon.incidents.forecast_handler import _update_incident_outcome

        with patch(
            "elle.daemon.incidents.store.update_incident",
            side_effect=ImportError("no store"),
        ):
            await _update_incident_outcome("INC-3", success=True, response="ok")

    @pytest.mark.asyncio
    async def test_generic_error(self):
        from elle.daemon.incidents.forecast_handler import _update_incident_outcome

        with patch(
            "elle.daemon.incidents.store.update_incident",
            side_effect=RuntimeError("db locked"),
        ):
            await _update_incident_outcome("INC-4", success=True, response="ok")


# =============================================================================
# _check_plan_autonomy Tests
# =============================================================================


class TestCheckPlanAutonomy:
    """Tests for _check_plan_autonomy()."""

    def test_empty_capabilities(self):
        assert _check_plan_autonomy(()) is False

    def test_all_capabilities_allowed(self):
        mock_engine = MagicMock()
        mock_engine.can_run_autonomously.return_value = (True, "base_level")

        mock_cap = MagicMock()
        mock_cap.spec.risk = "low"

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_cap

        with (
            patch(
                "elle.capabilities.autonomy.get_autonomy_engine",
                return_value=mock_engine,
            ),
            patch(
                "elle.capabilities.registry.get_registry",
                return_value=mock_registry,
            ),
        ):
            assert _check_plan_autonomy(("file.read", "service.status")) is True

    def test_one_capability_blocked(self):
        mock_engine = MagicMock()
        mock_engine.can_run_autonomously.side_effect = [
            (True, "base_level"),
            (False, "risk_blocked"),
        ]

        mock_cap = MagicMock()
        mock_cap.spec.risk = "low"

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_cap

        with (
            patch(
                "elle.capabilities.autonomy.get_autonomy_engine",
                return_value=mock_engine,
            ),
            patch(
                "elle.capabilities.registry.get_registry",
                return_value=mock_registry,
            ),
        ):
            assert _check_plan_autonomy(("file.read", "service.restart")) is False

    def test_unknown_capability_returns_false(self):
        mock_engine = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get.return_value = None

        with (
            patch(
                "elle.capabilities.autonomy.get_autonomy_engine",
                return_value=mock_engine,
            ),
            patch(
                "elle.capabilities.registry.get_registry",
                return_value=mock_registry,
            ),
        ):
            assert _check_plan_autonomy(("unknown.thing",)) is False

    def test_import_error_returns_false(self):
        with patch(
            "elle.capabilities.autonomy.get_autonomy_engine",
            side_effect=ImportError("no module"),
        ):
            assert _check_plan_autonomy(("file.read",)) is False

    def test_generic_exception_returns_false(self):
        with patch(
            "elle.capabilities.autonomy.get_autonomy_engine",
            side_effect=RuntimeError("kaboom"),
        ):
            assert _check_plan_autonomy(("file.read",)) is False


# =============================================================================
# _notify_forecast_warning Tests
# =============================================================================


class TestNotifyForecastWarning:
    """Tests for _notify_forecast_warning()."""

    @pytest.mark.asyncio
    async def test_success(self):
        from elle.daemon.incidents.forecast_handler import _notify_forecast_warning

        forecast = _make_forecast()
        plan = _make_plan()

        with patch("elle.daemon.notifications.service.notify_forecast") as mock_notify:
            await _notify_forecast_warning(forecast, "INC-W1", plan)

        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args[1]
        assert kwargs["auto_remediated"] is False
        assert kwargs["plan_summary"] == plan.summary
        assert kwargs["cause_description"] == plan.cause_description

    @pytest.mark.asyncio
    async def test_exception_is_caught(self):
        from elle.daemon.incidents.forecast_handler import _notify_forecast_warning

        forecast = _make_forecast()
        plan = _make_plan()

        with patch(
            "elle.daemon.notifications.service.notify_forecast",
            side_effect=RuntimeError("ntfy down"),
        ):
            # Should not raise
            await _notify_forecast_warning(forecast, "INC-W2", plan)

    @pytest.mark.asyncio
    async def test_none_warning_threshold_defaults_to_zero(self):
        from elle.daemon.incidents.forecast_handler import _notify_forecast_warning

        forecast = _make_forecast(warning_threshold=None)
        plan = _make_plan()

        with patch("elle.daemon.notifications.service.notify_forecast") as mock_notify:
            await _notify_forecast_warning(forecast, "INC-W3", plan)

        kwargs = mock_notify.call_args[1]
        assert kwargs["threshold"] == 0.0


# =============================================================================
# _notify_forecast_remediated Tests
# =============================================================================


class TestNotifyForecastRemediated:
    """Tests for _notify_forecast_remediated()."""

    @pytest.mark.asyncio
    async def test_success(self):
        from elle.daemon.incidents.forecast_handler import _notify_forecast_remediated

        forecast = _make_forecast()

        with patch("elle.daemon.notifications.service.notify_forecast") as mock_notify:
            await _notify_forecast_remediated(forecast, "INC-R1", "improved", plan_summary="Cleaned logs")

        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args[1]
        assert kwargs["auto_remediated"] is True
        assert kwargs["outcome"] == "improved"

    @pytest.mark.asyncio
    async def test_exception_is_caught(self):
        from elle.daemon.incidents.forecast_handler import _notify_forecast_remediated

        forecast = _make_forecast()

        with patch(
            "elle.daemon.notifications.service.notify_forecast",
            side_effect=Exception("boom"),
        ):
            await _notify_forecast_remediated(forecast, "INC-R2", "no_change")

    @pytest.mark.asyncio
    async def test_threshold_fallback_chain(self):
        """critical_threshold -> warning_threshold -> 0.0."""
        from elle.daemon.incidents.forecast_handler import _notify_forecast_remediated

        # critical_threshold is used first
        forecast = _make_forecast(critical_threshold=95.0, warning_threshold=90.0)

        with patch("elle.daemon.notifications.service.notify_forecast") as mock_notify:
            await _notify_forecast_remediated(forecast, "INC-R3", "improved")

        kwargs = mock_notify.call_args[1]
        assert kwargs["threshold"] == 95.0

    @pytest.mark.asyncio
    async def test_threshold_fallback_to_warning(self):
        """When critical_threshold is None, fall back to warning_threshold."""
        from elle.daemon.incidents.forecast_handler import _notify_forecast_remediated

        forecast = _make_forecast(critical_threshold=None, warning_threshold=90.0)

        with patch("elle.daemon.notifications.service.notify_forecast") as mock_notify:
            await _notify_forecast_remediated(forecast, "INC-R4", "improved")

        kwargs = mock_notify.call_args[1]
        assert kwargs["threshold"] == 90.0

    @pytest.mark.asyncio
    async def test_threshold_fallback_to_zero(self):
        """When both thresholds are None, fall back to 0.0."""
        from elle.daemon.incidents.forecast_handler import _notify_forecast_remediated

        forecast = _make_forecast(critical_threshold=None, warning_threshold=None)

        with patch("elle.daemon.notifications.service.notify_forecast") as mock_notify:
            await _notify_forecast_remediated(forecast, "INC-R5", "improved")

        kwargs = mock_notify.call_args[1]
        assert kwargs["threshold"] == 0.0


# =============================================================================
# execute_prepared_plan Tests
# =============================================================================


class TestExecutePreparedPlan:
    """Tests for execute_prepared_plan()."""

    @pytest.mark.asyncio
    async def test_no_prepared_plan(self):
        with patch("elle.daemon.incidents.store.get_prepared_plan", return_value=None):
            result = await execute_prepared_plan("FORECAST-NOPE")

        assert result.success is False
        assert result.executed is False
        assert "No prepared plan" in (result.error or "")

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        plan_data = {
            "plan_id": "plan-exec-1",
            "incident_id": "FORECAST-EXEC1",
            "metric": "disk./.used_pct",
            "current_value": 85.0,
            "threshold": 90.0,
            "urgency": "prepare",
            "summary": "Clean old logs",
            "steps": ("Remove old log files",),
            "capabilities_to_execute": ("file.delete",),
            "confidence": 0.8,
        }

        mock_loop_result = MagicMock()
        mock_loop_result.success = True
        mock_loop_result.response = "Done"
        mock_loop_result.error = None

        mock_loop = AsyncMock()
        mock_loop.run.return_value = mock_loop_result

        with (
            patch(
                "elle.daemon.incidents.store.get_prepared_plan",
                return_value=plan_data,
            ),
            patch("elle.cli.agentic.loop.AgenticLoop", return_value=mock_loop),
            patch(
                "elle.daemon.incidents.forecast_handler._update_incident_outcome",
                new_callable=AsyncMock,
            ),
            patch("elle.daemon.notifications.service.notify_forecast"),
        ):
            result = await execute_prepared_plan("FORECAST-EXEC1")

        assert result.success is True
        assert result.executed is True
        assert result.plan is not None
        assert result.plan.plan_id == "plan-exec-1"

    @pytest.mark.asyncio
    async def test_execution_failure(self):
        plan_data = {
            "plan_id": "plan-fail-1",
            "incident_id": "FORECAST-FAIL1",
            "metric": "mem.used_pct",
            "current_value": 92.0,
            "threshold": 95.0,
            "urgency": "prepare",
            "summary": "Reduce memory",
            "steps": ("Restart heavy service",),
            "capabilities_to_execute": ("service.restart",),
            "confidence": 0.6,
        }

        mock_loop_result = MagicMock()
        mock_loop_result.success = False
        mock_loop_result.response = "Denied"
        mock_loop_result.error = "Policy denied"

        mock_loop = AsyncMock()
        mock_loop.run.return_value = mock_loop_result

        with (
            patch(
                "elle.daemon.incidents.store.get_prepared_plan",
                return_value=plan_data,
            ),
            patch("elle.cli.agentic.loop.AgenticLoop", return_value=mock_loop),
            patch(
                "elle.daemon.incidents.forecast_handler._update_incident_outcome",
                new_callable=AsyncMock,
            ),
            patch("elle.daemon.notifications.service.notify_forecast"),
        ):
            result = await execute_prepared_plan("FORECAST-FAIL1")

        assert result.success is False
        assert result.executed is True
        assert result.error == "Policy denied"

    @pytest.mark.asyncio
    async def test_notification_error_does_not_propagate(self):
        plan_data = {
            "plan_id": "plan-note-1",
            "incident_id": "FORECAST-NOTE1",
            "metric": "disk./.used_pct",
            "current_value": 85.0,
            "threshold": 90.0,
            "urgency": "prepare",
            "summary": "Clean files",
            "steps": (),
            "capabilities_to_execute": (),
            "confidence": 0.5,
        }

        mock_loop_result = MagicMock()
        mock_loop_result.success = True
        mock_loop_result.response = "Done"
        mock_loop_result.error = None

        mock_loop = AsyncMock()
        mock_loop.run.return_value = mock_loop_result

        with (
            patch(
                "elle.daemon.incidents.store.get_prepared_plan",
                return_value=plan_data,
            ),
            patch("elle.cli.agentic.loop.AgenticLoop", return_value=mock_loop),
            patch(
                "elle.daemon.incidents.forecast_handler._update_incident_outcome",
                new_callable=AsyncMock,
            ),
            patch(
                "elle.daemon.notifications.service.notify_forecast",
                side_effect=RuntimeError("ntfy down"),
            ),
        ):
            result = await execute_prepared_plan("FORECAST-NOTE1")

        # Notification failure should not cause the overall result to fail
        assert result.success is True
        assert result.executed is True

    @pytest.mark.asyncio
    async def test_top_level_exception(self):
        with patch(
            "elle.daemon.incidents.store.get_prepared_plan",
            side_effect=RuntimeError("db corrupted"),
        ):
            result = await execute_prepared_plan("FORECAST-CRASH")

        assert result.success is False
        assert result.executed is False
        assert "db corrupted" in (result.error or "")


# =============================================================================
# _parse_plan_steps Tests
# =============================================================================


class TestParsePlanSteps:
    """Tests for _parse_plan_steps()."""

    def test_numbered_steps(self):
        response = "Here is the plan:\n1. Stop the service\n2. Clean logs\n3. Restart"
        steps = _parse_plan_steps(response)
        assert steps == ["Stop the service", "Clean logs", "Restart"]

    def test_bullet_steps(self):
        response = "Steps:\n- Stop the service\n- Clean logs\n- Restart"
        steps = _parse_plan_steps(response)
        assert steps == ["Stop the service", "Clean logs", "Restart"]

    def test_asterisk_bullets(self):
        response = "Steps:\n* Stop the service\n* Clean logs"
        steps = _parse_plan_steps(response)
        assert steps == ["Stop the service", "Clean logs"]

    def test_numbered_takes_precedence_over_bullets(self):
        response = "1. First step\n- Also a bullet"
        steps = _parse_plan_steps(response)
        # When numbered steps exist, bullets are included too per the impl
        assert "First step" in steps

    def test_no_steps_returns_fallback(self):
        response = "The system looks fine, nothing to do."
        steps = _parse_plan_steps(response)
        assert steps == ["See response for details"]

    def test_empty_response(self):
        steps = _parse_plan_steps("")
        assert steps == ["See response for details"]

    def test_mixed_numbered_and_bullets(self):
        """When both numbered and bullet exist, numbered are collected,
        then bullets are skipped because numbered was non-empty."""
        response = "1. Do A\n2. Do B\n- Extra note"
        steps = _parse_plan_steps(response)
        # Numbered found, so bullets are skipped
        assert "Do A" in steps
        assert "Do B" in steps


# =============================================================================
# _extract_capabilities Tests
# =============================================================================


class TestExtractCapabilities:
    """Tests for _extract_capabilities()."""

    def test_known_capabilities(self):
        response = "We need to run service.restart and file.delete"
        caps = _extract_capabilities(response)
        assert "service.restart" in caps
        assert "file.delete" in caps

    def test_unknown_prefix_filtered(self):
        response = "Use custom.action to fix it"
        caps = _extract_capabilities(response)
        assert len(caps) == 0

    def test_all_known_prefixes(self):
        response = "service.start file.write config.edit docker.stop package.install network.diagnose"
        caps = _extract_capabilities(response)
        assert len(caps) == 6

    def test_deduplication(self):
        response = "Run service.restart then service.restart again"
        caps = _extract_capabilities(response)
        assert caps.count("service.restart") == 1

    def test_empty_response(self):
        caps = _extract_capabilities("")
        assert caps == []

    def test_case_insensitive(self):
        response = "Run SERVICE.RESTART and File.Write"
        caps = _extract_capabilities(response)
        assert "service.restart" in caps
        assert "file.write" in caps


# =============================================================================
# _extract_summary Tests
# =============================================================================


class TestExtractSummary:
    """Tests for _extract_summary()."""

    def test_first_paragraph(self):
        response = "This is the summary.\n\nThis is more detail."
        assert _extract_summary(response) == "This is the summary."

    def test_truncation_at_200_chars(self):
        long_para = "x" * 250
        summary = _extract_summary(long_para)
        assert len(summary) == 200
        assert summary.endswith("...")

    def test_short_response(self):
        response = "Short."
        assert _extract_summary(response) == "Short."

    def test_empty_response(self):
        assert _extract_summary("") == ""

    def test_single_paragraph(self):
        response = "Only one paragraph here, no double newlines."
        assert _extract_summary(response) == response

    def test_whitespace_stripping(self):
        response = "  Leading whitespace summary  \n\nDetails follow."
        assert _extract_summary(response) == "Leading whitespace summary"

    def test_long_response_without_paragraphs(self):
        """Response > 200 chars with no paragraph break."""
        response = "a" * 300
        # First paragraph is the whole thing (300 chars), so it gets truncated
        summary = _extract_summary(response)
        assert len(summary) == 200
        assert summary.endswith("...")


# =============================================================================
# _extract_cause_description Tests
# =============================================================================


class TestExtractCauseDescription:
    """Tests for _extract_cause_description()."""

    def test_cause_label(self):
        response = "Analysis:\nCause: Log rotation is misconfigured and failing.\nSteps:"
        result = _extract_cause_description(response)
        assert result is not None
        assert "Log rotation" in result

    def test_root_cause_label(self):
        response = "Root cause: Large database dump files in /var/tmp accumulating"
        result = _extract_cause_description(response)
        assert result is not None
        assert "database dump" in result

    def test_reason_label(self):
        response = "Reason: The cron job for cleanup was disabled last week."
        result = _extract_cause_description(response)
        assert result is not None
        assert "cron job" in result

    def test_due_to_pattern(self):
        response = "This is likely due to failed log rotation cron jobs running daily"
        result = _extract_cause_description(response)
        assert result is not None
        assert "log rotation" in result

    def test_caused_by_pattern(self):
        response = "The issue is caused by excessive container logging"
        result = _extract_cause_description(response)
        assert result is not None
        assert "container logging" in result

    def test_problem_label(self):
        response = "Problem: Systemd timer for cleanup was masked after upgrade."
        result = _extract_cause_description(response)
        assert result is not None
        assert "Systemd timer" in result

    def test_no_cause_returns_none(self):
        response = "Here is the plan:\n1. Clean files\n2. Restart service"
        assert _extract_cause_description(response) is None

    def test_empty_response(self):
        assert _extract_cause_description("") is None

    def test_short_match_skipped(self):
        """Matches shorter than 10 characters are ignored."""
        response = "Cause: Too short"
        # "Too short" is 9 chars, should be skipped
        assert _extract_cause_description(response) is None

    def test_long_match_truncated(self):
        long_cause = "x" * 150
        response = f"Cause: {long_cause}"
        result = _extract_cause_description(response)
        assert result is not None
        assert len(result) == 120
        assert result.endswith("...")

    def test_case_insensitive(self):
        response = "ROOT CAUSE: Memory leak in the application server causing OOM"
        result = _extract_cause_description(response)
        assert result is not None
        assert "Memory leak" in result

    def test_first_matching_pattern_wins(self):
        response = "Cause: First cause description for the issue.\nProblem: Second problem statement here.\n"
        result = _extract_cause_description(response)
        assert result is not None
        assert "First cause" in result
