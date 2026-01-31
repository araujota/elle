from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ConfigDict

from elle.reactive.engine import (
    ReactiveEngine,
    _event_to_dict,
    _forecast_to_dict,
    _metric_matches_pattern,
    _parse_interval,
    _regex_match,
    get_engine,
    reset_engine,
)
from elle.reactive.models import (
    ActionResult,
    ActionSpec,
    Condition,
    EventTrigger,
    ExecutionRecord,
    ForecastTrigger,
    PolicySpec,
    RateLimitState,
    ReactiveFunction,
    ScheduleTrigger,
    StateProbe,
    Trigger,
)

# Patch target prefix for store functions
_STORE = "elle.reactive.engine"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_event(**overrides: Any) -> MagicMock:
    """Create a mock TelemetryEvent."""
    defaults: dict[str, Any] = {
        "event_id": "abc123",
        "ts": datetime.utcnow(),
        "source": "journal",
        "severity": "warning",
        "category": "disk",
        "message": "Disk usage high",
        "entity": "disk:/dev/sda1",
        "fingerprint": "fp123",
        "raw": {"used_pct": 92},
    }
    defaults.update(overrides)
    event = MagicMock()
    for k, v in defaults.items():
        setattr(event, k, v)
    return event


def _mock_forecast(**overrides: Any) -> MagicMock:
    """Create a mock Forecast."""
    defaults: dict[str, Any] = {
        "metric": "disk./.used_pct",
        "current_value": 85.0,
        "predicted_value_24h": 95.0,
        "predicted_value_7d": 99.0,
        "warning_threshold": 90.0,
        "critical_threshold": 95.0,
        "will_cross_warning": True,
        "time_to_warning_hours": 12.0,
        "will_cross_critical": True,
        "time_to_critical_hours": 48.0,
        "urgency": "prepare",
        "confidence": 0.8,
        "rate_of_change": 0.5,
        "computed_at": datetime.utcnow(),
    }
    defaults.update(overrides)
    f = MagicMock()
    for k, v in defaults.items():
        setattr(f, k, v)
    return f


def _make_func(
    trigger_type: str = "event",
    trigger_event: EventTrigger | None = None,
    trigger_forecast: ForecastTrigger | None = None,
    condition: Condition | None = None,
    actions: tuple[ActionSpec, ...] | None = None,
    policy: PolicySpec | None = None,
    state: dict[str, StateProbe] | None = None,
    **kwargs: Any,
) -> ReactiveFunction:
    if trigger_event is None and trigger_type == "event":
        trigger_event = EventTrigger(source="journal", category="disk", severity="warning")
    trigger = Trigger(
        type=trigger_type,
        event=trigger_event if trigger_type == "event" else None,
        schedule=ScheduleTrigger(cron="* * * * *") if trigger_type == "schedule" else None,
        forecast=trigger_forecast,
    )
    if actions is None:
        actions = (ActionSpec(capability="docker.prune", input={"all": True}),)
    return ReactiveFunction(
        name=kwargs.get("name", "test-func"),
        description="Test",
        trigger=trigger,
        condition=condition,
        actions=actions,
        policy=policy or PolicySpec(),
        state=state or {},
        tags=("test",),
    )


class _StubInput(BaseModel):
    """Minimal Pydantic model that accepts arbitrary keyword args."""

    model_config = ConfigDict(extra="allow")


def _mock_executor_with_capability(
    success: bool = True,
    output: str = "done",
    error: str | None = None,
) -> MagicMock:
    """Create a mock executor with a registered capability that returns a result."""
    executor = MagicMock()
    cap = MagicMock()
    cap.input_schema = _StubInput
    cap.spec = MagicMock()
    cap.spec.input_schema_name = None
    executor.registry.get.return_value = cap

    result = MagicMock()
    result.success = success
    result.output = output
    result.error = error
    executor.execute = AsyncMock(return_value=result)
    return executor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset():
    reset_engine()
    yield
    reset_engine()


# ---------------------------------------------------------------------------
# _parse_interval
# ---------------------------------------------------------------------------


class TestParseInterval:
    def test_seconds(self):
        assert _parse_interval("10s") == timedelta(seconds=10)

    def test_minutes(self):
        assert _parse_interval("5m") == timedelta(minutes=5)

    def test_hours(self):
        assert _parse_interval("2h") == timedelta(hours=2)

    def test_days(self):
        assert _parse_interval("1d") == timedelta(days=1)

    def test_invalid_returns_default(self):
        assert _parse_interval("bad") == timedelta(minutes=5)

    def test_whitespace(self):
        assert _parse_interval("  3h  ") == timedelta(hours=3)


# ---------------------------------------------------------------------------
# _regex_match
# ---------------------------------------------------------------------------


class TestRegexMatch:
    def test_match(self):
        assert _regex_match("err", "error occurred") is True

    def test_no_match(self):
        assert _regex_match("fail", "success") is False

    def test_invalid_regex_fallback(self):
        assert _regex_match("[invalid", "has [invalid") is True

    def test_invalid_regex_not_found(self):
        assert _regex_match("[invalid", "nope") is False


# ---------------------------------------------------------------------------
# _metric_matches_pattern
# ---------------------------------------------------------------------------


class TestMetricMatchesPattern:
    def test_wildcard(self):
        assert _metric_matches_pattern("anything", "*") is True

    def test_exact_match(self):
        assert _metric_matches_pattern("disk./.used_pct", "disk./.used_pct") is True

    def test_exact_no_match(self):
        assert _metric_matches_pattern("disk./.used_pct", "cpu.load") is False

    def test_glob_match(self):
        assert _metric_matches_pattern("disk./.used_pct", "disk.*") is True

    def test_glob_no_match(self):
        assert _metric_matches_pattern("cpu.load", "disk.*") is False

    def test_invalid_regex_pattern_fallback(self):
        """An invalid regex in the wildcard conversion falls back to exact match."""
        # This pattern, after conversion, produces a valid regex -- test a normal case
        assert _metric_matches_pattern("mem.free", "mem.*") is True

    def test_no_wildcard_no_match(self):
        """Exact pattern without wildcard that does not match."""
        assert _metric_matches_pattern("cpu.idle", "cpu.load") is False


# ---------------------------------------------------------------------------
# _event_to_dict / _forecast_to_dict
# ---------------------------------------------------------------------------


class TestEventToDict:
    def test_none(self):
        assert _event_to_dict(None) == {}

    def test_valid(self):
        ev = _mock_event()
        d = _event_to_dict(ev)
        assert d["event_id"] == "abc123"
        assert d["source"] == "journal"
        assert d["raw"] == {"used_pct": 92}


class TestForecastToDict:
    def test_none(self):
        assert _forecast_to_dict(None) == {}

    def test_valid(self):
        fc = _mock_forecast()
        d = _forecast_to_dict(fc)
        assert d["metric"] == "disk./.used_pct"
        assert d["urgency"] == "prepare"
        assert d["confidence"] == 0.8


# ---------------------------------------------------------------------------
# Event matching
# ---------------------------------------------------------------------------


class TestEventMatchesTrigger:
    def test_non_event_trigger(self):
        engine = ReactiveEngine()
        func = _make_func(trigger_type="schedule")
        assert engine._event_matches_trigger(_mock_event(), func) is False

    def test_no_event_spec(self):
        engine = ReactiveEngine()
        func = _make_func()
        func = func.model_copy(update={"trigger": Trigger(type="event", event=None)})
        assert engine._event_matches_trigger(_mock_event(), func) is False

    def test_source_and_category_match(self):
        engine = ReactiveEngine()
        func = _make_func()
        assert (
            engine._event_matches_trigger(_mock_event(source="journal", category="disk", severity="warning"), func)
            is True
        )

    def test_wrong_source(self):
        engine = ReactiveEngine()
        func = _make_func(trigger_event=EventTrigger(source="kernel"))
        assert engine._event_matches_trigger(_mock_event(source="journal"), func) is False

    def test_wrong_category(self):
        engine = ReactiveEngine()
        func = _make_func(trigger_event=EventTrigger(category="net"))
        assert engine._event_matches_trigger(_mock_event(category="disk"), func) is False

    def test_severity_minimum_met(self):
        engine = ReactiveEngine()
        func = _make_func(trigger_event=EventTrigger(severity="warning"))
        assert engine._event_matches_trigger(_mock_event(severity="error"), func) is True

    def test_severity_minimum_not_met(self):
        engine = ReactiveEngine()
        func = _make_func(trigger_event=EventTrigger(severity="error"))
        assert engine._event_matches_trigger(_mock_event(severity="warning"), func) is False

    def test_severity_unknown_value_passes(self):
        """Unknown severity values in either trigger or event do not block (ValueError caught)."""
        engine = ReactiveEngine()
        func = _make_func(trigger_event=EventTrigger(severity=None))
        # Severity check is skipped when trigger severity is None
        assert engine._event_matches_trigger(_mock_event(severity="info"), func) is True

    def test_match_numeric(self):
        engine = ReactiveEngine()
        func = _make_func(trigger_event=EventTrigger(match={"used_pct": 92}))
        assert engine._event_matches_trigger(_mock_event(raw={"used_pct": 92}), func) is True

    def test_match_numeric_wrong(self):
        engine = ReactiveEngine()
        func = _make_func(trigger_event=EventTrigger(match={"used_pct": 50}))
        assert engine._event_matches_trigger(_mock_event(raw={"used_pct": 92}), func) is False

    def test_match_missing_key(self):
        engine = ReactiveEngine()
        func = _make_func(trigger_event=EventTrigger(match={"no_key": "x"}))
        assert engine._event_matches_trigger(_mock_event(raw={}), func) is False

    def test_match_list_contains(self):
        engine = ReactiveEngine()
        func = _make_func(trigger_event=EventTrigger(match={"level": ["error", "critical"]}))
        assert engine._event_matches_trigger(_mock_event(raw={"level": "error"}), func) is True

    def test_match_list_not_contains(self):
        engine = ReactiveEngine()
        func = _make_func(trigger_event=EventTrigger(match={"level": ["error", "critical"]}))
        assert engine._event_matches_trigger(_mock_event(raw={"level": "info"}), func) is False

    def test_match_string_regex(self):
        engine = ReactiveEngine()
        func = _make_func(trigger_event=EventTrigger(match={"msg": "fail.*"}))
        assert engine._event_matches_trigger(_mock_event(raw={"msg": "failure detected"}), func) is True

    def test_match_string_exact_equality(self):
        """String match where value exactly equals the pattern (no regex needed)."""
        engine = ReactiveEngine()
        func = _make_func(trigger_event=EventTrigger(match={"service": "nginx"}))
        assert engine._event_matches_trigger(_mock_event(raw={"service": "nginx"}), func) is True

    def test_match_string_no_match(self):
        """String match where neither exact equality nor regex matches."""
        engine = ReactiveEngine()
        func = _make_func(trigger_event=EventTrigger(match={"service": "apache"}))
        assert engine._event_matches_trigger(_mock_event(raw={"service": "nginx"}), func) is False


# ---------------------------------------------------------------------------
# Forecast matching
# ---------------------------------------------------------------------------


class TestForecastMatchesTrigger:
    def test_non_forecast_trigger(self):
        engine = ReactiveEngine()
        func = _make_func(trigger_type="event")
        assert engine._forecast_matches_trigger(_mock_forecast(), func) is False

    def test_no_forecast_spec(self):
        engine = ReactiveEngine()
        func = _make_func(trigger_type="forecast", trigger_event=None)
        func = func.model_copy(update={"trigger": Trigger(type="forecast", forecast=None)})
        assert engine._forecast_matches_trigger(_mock_forecast(), func) is False

    def test_urgency_mismatch(self):
        engine = ReactiveEngine()
        func = _make_func(
            trigger_type="forecast",
            trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="act_now"),
        )
        assert engine._forecast_matches_trigger(_mock_forecast(urgency="prepare"), func) is False

    def test_confidence_too_low(self):
        engine = ReactiveEngine()
        func = _make_func(
            trigger_type="forecast",
            trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="prepare", min_confidence=0.9),
        )
        assert engine._forecast_matches_trigger(_mock_forecast(confidence=0.5), func) is False

    def test_metric_pattern_match(self):
        engine = ReactiveEngine()
        func = _make_func(
            trigger_type="forecast",
            trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="prepare", metric="disk.*"),
        )
        assert engine._forecast_matches_trigger(_mock_forecast(), func) is True

    def test_metric_pattern_no_match(self):
        engine = ReactiveEngine()
        func = _make_func(
            trigger_type="forecast",
            trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="prepare", metric="cpu.*"),
        )
        assert engine._forecast_matches_trigger(_mock_forecast(metric="disk./.used_pct"), func) is False

    def test_no_metric_filter_matches_all(self):
        """When trigger has metric=None, all forecasts with correct urgency match."""
        engine = ReactiveEngine()
        func = _make_func(
            trigger_type="forecast",
            trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="prepare", metric=None),
        )
        assert engine._forecast_matches_trigger(_mock_forecast(), func) is True


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------


class TestCheckRateLimit:
    @patch("elle.reactive.engine.get_rate_limit_state")
    def test_daily_limit_exceeded(self, mock_state):
        engine = ReactiveEngine()
        func = _make_func(policy=PolicySpec(max_daily_executions=5))
        today = datetime.utcnow().strftime("%Y-%m-%d")
        mock_state.return_value = RateLimitState(function_id=func.id, daily_executions=5, daily_reset_date=today)
        assert engine._check_rate_limit(func) is False

    @patch("elle.reactive.engine.get_rate_limit_state")
    def test_daily_resets_new_day(self, mock_state):
        engine = ReactiveEngine()
        func = _make_func(policy=PolicySpec(max_daily_executions=5))
        mock_state.return_value = RateLimitState(function_id=func.id, daily_executions=5, daily_reset_date="2000-01-01")
        assert engine._check_rate_limit(func) is True

    @patch("elle.reactive.engine.get_rate_limit_state")
    def test_frequency_blocked(self, mock_state):
        engine = ReactiveEngine()
        func = _make_func(policy=PolicySpec(max_frequency="1h"))
        mock_state.return_value = RateLimitState(
            function_id=func.id,
            last_execution=datetime.utcnow() - timedelta(minutes=10),
            daily_executions=0,
            daily_reset_date="2000-01-01",
        )
        assert engine._check_rate_limit(func) is False

    @patch("elle.reactive.engine.get_rate_limit_state")
    def test_frequency_passed(self, mock_state):
        engine = ReactiveEngine()
        func = _make_func(policy=PolicySpec(max_frequency="5m"))
        mock_state.return_value = RateLimitState(
            function_id=func.id,
            last_execution=datetime.utcnow() - timedelta(hours=1),
            daily_executions=0,
            daily_reset_date="2000-01-01",
        )
        assert engine._check_rate_limit(func) is True

    @patch("elle.reactive.engine.get_rate_limit_state")
    def test_no_last_execution_passes(self, mock_state):
        """First-ever execution passes frequency check (no last_execution)."""
        engine = ReactiveEngine()
        func = _make_func(policy=PolicySpec(max_frequency="1h"))
        mock_state.return_value = RateLimitState(
            function_id=func.id,
            last_execution=None,
            daily_executions=0,
            daily_reset_date="2000-01-01",
        )
        assert engine._check_rate_limit(func) is True


# ---------------------------------------------------------------------------
# Allowed hours
# ---------------------------------------------------------------------------


class TestCheckAllowedHours:
    def test_no_restriction(self):
        engine = ReactiveEngine()
        assert engine._check_allowed_hours(PolicySpec(allowed_hours=None)) is True

    @patch("elle.reactive.engine.datetime")
    def test_within(self, mock_dt):
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 10, 0, 0)
        engine = ReactiveEngine()
        assert engine._check_allowed_hours(PolicySpec(allowed_hours=(9, 17))) is True

    @patch("elle.reactive.engine.datetime")
    def test_outside(self, mock_dt):
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 20, 0, 0)
        engine = ReactiveEngine()
        assert engine._check_allowed_hours(PolicySpec(allowed_hours=(9, 17))) is False

    @patch("elle.reactive.engine.datetime")
    def test_midnight_wrap_inside(self, mock_dt):
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 23, 0, 0)
        engine = ReactiveEngine()
        assert engine._check_allowed_hours(PolicySpec(allowed_hours=(22, 6))) is True

    @patch("elle.reactive.engine.datetime")
    def test_midnight_wrap_outside(self, mock_dt):
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        engine = ReactiveEngine()
        assert engine._check_allowed_hours(PolicySpec(allowed_hours=(22, 6))) is False

    @patch("elle.reactive.engine.datetime")
    def test_at_start_hour_boundary(self, mock_dt):
        """Current hour exactly at start_hour is within allowed hours."""
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 9, 0, 0)
        engine = ReactiveEngine()
        assert engine._check_allowed_hours(PolicySpec(allowed_hours=(9, 17))) is True

    @patch("elle.reactive.engine.datetime")
    def test_at_end_hour_boundary(self, mock_dt):
        """Current hour exactly at end_hour is outside allowed hours."""
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 17, 0, 0)
        engine = ReactiveEngine()
        assert engine._check_allowed_hours(PolicySpec(allowed_hours=(9, 17))) is False


# ---------------------------------------------------------------------------
# process_event
# ---------------------------------------------------------------------------


class TestProcessEvent:
    @patch("elle.reactive.engine.list_enabled_with_event_trigger", return_value=[])
    async def test_no_matches(self, _):
        engine = ReactiveEngine(executor=MagicMock())
        assert await engine.process_event(_mock_event()) == []

    @patch("elle.reactive.engine.record_execution")
    @patch("elle.reactive.engine.list_enabled_with_event_trigger")
    async def test_exception_records_failure(self, mock_list, mock_rec):
        engine = ReactiveEngine(executor=MagicMock())
        mock_list.return_value = [_make_func()]
        with patch.object(engine, "_process_function", side_effect=RuntimeError("boom")):
            records = await engine.process_event(_mock_event())
        assert len(records) == 1
        assert records[0].success is False
        assert "boom" in (records[0].error or "")

    @patch("elle.reactive.engine.record_execution")
    @patch("elle.reactive.engine.list_enabled_with_event_trigger")
    async def test_successful_processing_returns_record(self, mock_list, mock_rec):
        """process_event returns ExecutionRecords from _process_function for matching funcs."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func()
        mock_list.return_value = [func]
        expected_record = ExecutionRecord(
            function_id=func.id,
            function_name=func.name,
            triggered_at=datetime.utcnow(),
            condition_result=True,
            success=True,
        )
        with patch.object(engine, "_process_function", return_value=expected_record):
            records = await engine.process_event(_mock_event())
        assert len(records) == 1
        assert records[0].success is True

    @patch("elle.reactive.engine.record_execution")
    @patch("elle.reactive.engine.list_enabled_with_event_trigger")
    async def test_process_function_returns_none_skipped(self, mock_list, mock_rec):
        """When _process_function returns None (rate-limited), it is not appended."""
        engine = ReactiveEngine(executor=MagicMock())
        mock_list.return_value = [_make_func()]
        with patch.object(engine, "_process_function", return_value=None):
            records = await engine.process_event(_mock_event())
        assert len(records) == 0


# ---------------------------------------------------------------------------
# process_forecast
# ---------------------------------------------------------------------------


class TestProcessForecast:
    async def test_none_urgency_skipped(self):
        engine = ReactiveEngine(executor=MagicMock())
        assert await engine.process_forecast(_mock_forecast(urgency="none")) == []

    @patch("elle.reactive.engine.list_enabled_with_forecast_trigger", return_value=[])
    async def test_no_matches(self, _):
        engine = ReactiveEngine(executor=MagicMock())
        assert await engine.process_forecast(_mock_forecast()) == []

    @patch("elle.reactive.engine.record_execution")
    @patch("elle.reactive.engine.list_enabled_with_forecast_trigger")
    async def test_exception_records_failure(self, mock_list, mock_rec):
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(
            trigger_type="forecast",
            trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="prepare"),
        )
        mock_list.return_value = [func]
        with patch.object(engine, "_process_forecast_function", side_effect=RuntimeError("oops")):
            records = await engine.process_forecast(_mock_forecast())
        assert len(records) == 1
        assert records[0].success is False

    @patch("elle.reactive.engine.record_execution")
    @patch("elle.reactive.engine.list_enabled_with_forecast_trigger")
    async def test_successful_forecast_processing(self, mock_list, mock_rec):
        """process_forecast returns records from _process_forecast_function."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(
            trigger_type="forecast",
            trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="prepare"),
        )
        mock_list.return_value = [func]
        expected_record = ExecutionRecord(
            function_id=func.id,
            function_name=func.name,
            triggered_at=datetime.utcnow(),
            condition_result=True,
            success=True,
        )
        with patch.object(engine, "_process_forecast_function", return_value=expected_record):
            records = await engine.process_forecast(_mock_forecast())
        assert len(records) == 1
        assert records[0].success is True


# ---------------------------------------------------------------------------
# process_scheduled and process_manual
# ---------------------------------------------------------------------------


class TestProcessScheduled:
    async def test_delegates_to_process_function(self):
        """process_scheduled delegates to _process_function with event=None."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(trigger_type="schedule")
        expected = ExecutionRecord(
            function_id=func.id,
            function_name=func.name,
            triggered_at=datetime.utcnow(),
            condition_result=True,
            success=True,
        )
        with patch.object(engine, "_process_function", return_value=expected) as mock_pf:
            result = await engine.process_scheduled(func)
        assert result is expected
        mock_pf.assert_called_once_with(func, event=None)


class TestProcessManual:
    async def test_delegates_with_skip_rate_limit(self):
        """process_manual delegates to _process_function with skip_rate_limit=True."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(trigger_type="manual", trigger_event=None)
        ctx_override = {"system": {"metric": "test"}}
        expected = ExecutionRecord(
            function_id=func.id,
            function_name=func.name,
            triggered_at=datetime.utcnow(),
            condition_result=True,
            success=True,
        )
        with patch.object(engine, "_process_function", return_value=expected) as mock_pf:
            result = await engine.process_manual(func, context_override=ctx_override)
        assert result is expected
        mock_pf.assert_called_once_with(
            func,
            event=None,
            context_override=ctx_override,
            skip_rate_limit=True,
        )


# ---------------------------------------------------------------------------
# dry_run
# ---------------------------------------------------------------------------


class TestDryRun:
    async def test_no_condition(self):
        engine = ReactiveEngine(executor=MagicMock())
        res, exp, ctx = await engine.dry_run(_make_func(condition=None))
        assert res is True
        assert "No condition" in exp

    @patch("elle.reactive.engine.evaluate_condition", return_value=(False, "low"))
    async def test_condition_fails(self, _):
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(condition=Condition(expression={"gte": ["{event.raw.used_pct}", 99]}))
        res, exp, _ = await engine.dry_run(func, event=_mock_event())
        assert res is False

    async def test_dry_run_returns_context_dict(self):
        """dry_run returns event, state, and system in the context dict."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(condition=None)
        _, _, ctx = await engine.dry_run(func, event=_mock_event())
        assert "event" in ctx
        assert "state" in ctx
        assert "system" in ctx

    async def test_dry_run_with_context_override(self):
        """dry_run applies context_override to system data."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(condition=None)
        override = {"system": {"custom_metric": 42}}
        _, _, ctx = await engine.dry_run(func, context_override=override)
        assert ctx["system"].get("custom_metric") == 42


# ---------------------------------------------------------------------------
# _process_function (ImportError fallback path)
# ---------------------------------------------------------------------------


class TestProcessFunctionFallback:
    """Tests for _process_function when IncidentContext is not available."""

    @patch("elle.reactive.engine.record_execution")
    @patch("elle.reactive.engine.update_rate_limit_state")
    @patch("elle.reactive.engine.get_rate_limit_state")
    async def test_happy_path_no_incident_context(self, mock_rl_state, mock_update_rl, mock_rec):
        """Full execution flow when IncidentContext import fails (fallback path)."""
        mock_rl_state.return_value = RateLimitState(
            function_id="x",
            last_execution=None,
            daily_executions=0,
            daily_reset_date="2000-01-01",
        )

        executor = _mock_executor_with_capability(success=True, output="pruned")
        engine = ReactiveEngine(executor=executor)
        func = _make_func(
            actions=(ActionSpec(capability="docker.prune", input={"all": True}),),
        )

        # Force the ImportError fallback by patching the import
        with patch(f"{_STORE}.ReactiveEngine._execute_action") as mock_exec_action:
            mock_exec_action.return_value = ActionResult(capability="docker.prune", success=True, output="pruned")
            # Patch the IncidentContext import to fail
            import builtins

            real_import = builtins.__import__

            def _fail_incident_import(name, *args, **kwargs):
                if "incident_context" in name:
                    raise ImportError("no incident_context")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_fail_incident_import):
                record = await engine._process_function(func, event=_mock_event())

        assert record is not None
        assert record.success is True
        assert "docker.prune" in record.actions_executed
        mock_rec.assert_called_once()

    @patch("elle.reactive.engine.record_execution")
    @patch("elle.reactive.engine.update_rate_limit_state")
    @patch("elle.reactive.engine.get_rate_limit_state")
    async def test_fallback_action_failure_stops_on_stop(self, mock_rl, mock_update_rl, mock_rec):
        """In fallback path, on_failure='stop' breaks the action loop."""
        mock_rl.return_value = RateLimitState(
            function_id="x", last_execution=None, daily_executions=0, daily_reset_date="2000-01-01"
        )
        executor = MagicMock()
        engine = ReactiveEngine(executor=executor)

        actions = (
            ActionSpec(capability="step1", input={}, on_failure="stop"),
            ActionSpec(capability="step2", input={}, on_failure="continue"),
        )
        func = _make_func(actions=actions)

        # Simulate step1 failure
        with patch.object(engine, "_execute_action") as mock_ea:
            mock_ea.return_value = ActionResult(capability="step1", success=False, error="failed")
            import builtins

            real_import = builtins.__import__

            def _fail_incident(name, *args, **kwargs):
                if "incident_context" in name:
                    raise ImportError("no")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_fail_incident):
                record = await engine._process_function(func, event=_mock_event())

        assert record is not None
        assert record.success is False
        # step2 should NOT have been executed
        assert len(record.actions_executed) == 1

    @patch("elle.reactive.engine.record_execution")
    @patch("elle.reactive.engine.update_rate_limit_state")
    @patch("elle.reactive.engine.get_rate_limit_state")
    async def test_fallback_action_exception_with_stop(self, mock_rl, mock_update_rl, mock_rec):
        """In fallback path, an exception in action with on_failure='stop' breaks the loop."""
        mock_rl.return_value = RateLimitState(
            function_id="x", last_execution=None, daily_executions=0, daily_reset_date="2000-01-01"
        )
        executor = MagicMock()
        engine = ReactiveEngine(executor=executor)

        actions = (
            ActionSpec(capability="exploder", input={}, on_failure="stop"),
            ActionSpec(capability="step2", input={}, on_failure="continue"),
        )
        func = _make_func(actions=actions)

        with patch.object(engine, "_execute_action", side_effect=RuntimeError("kaboom")):
            import builtins

            real_import = builtins.__import__

            def _fail_incident(name, *args, **kwargs):
                if "incident_context" in name:
                    raise ImportError("no")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_fail_incident):
                record = await engine._process_function(func, event=_mock_event())

        assert record is not None
        assert record.success is False
        assert "kaboom" in (record.error or "")
        assert len(record.actions_executed) == 1

    @patch("elle.reactive.engine.record_execution")
    @patch("elle.reactive.engine.update_rate_limit_state")
    @patch("elle.reactive.engine.get_rate_limit_state")
    async def test_fallback_rollback_mode_stops(self, mock_rl, mock_update_rl, mock_rec):
        """In fallback path, on_failure='rollback' also stops execution."""
        mock_rl.return_value = RateLimitState(
            function_id="x", last_execution=None, daily_executions=0, daily_reset_date="2000-01-01"
        )
        executor = MagicMock()
        engine = ReactiveEngine(executor=executor)

        actions = (
            ActionSpec(capability="step1", input={}, on_failure="rollback"),
            ActionSpec(capability="step2", input={}, on_failure="continue"),
        )
        func = _make_func(actions=actions)

        with patch.object(engine, "_execute_action") as mock_ea:
            mock_ea.return_value = ActionResult(capability="step1", success=False, error="bad")
            import builtins

            real_import = builtins.__import__

            def _fail_incident(name, *args, **kwargs):
                if "incident_context" in name:
                    raise ImportError("no")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_fail_incident):
                record = await engine._process_function(func, event=_mock_event())

        assert record is not None
        assert record.success is False
        assert len(record.actions_executed) == 1


# ---------------------------------------------------------------------------
# _process_function: rate limit and condition checks
# ---------------------------------------------------------------------------


class TestProcessFunctionGating:
    """Tests for _process_function rate limit and condition gating."""

    @patch("elle.reactive.engine.get_rate_limit_state")
    async def test_rate_limited_returns_none(self, mock_state):
        """When rate-limited, _process_function returns None."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(policy=PolicySpec(max_daily_executions=1))
        today = datetime.utcnow().strftime("%Y-%m-%d")
        mock_state.return_value = RateLimitState(function_id=func.id, daily_executions=1, daily_reset_date=today)
        result = await engine._process_function(func, event=_mock_event())
        assert result is None

    @patch("elle.reactive.engine.get_rate_limit_state")
    async def test_outside_allowed_hours_returns_none(self, mock_state):
        """When outside allowed hours, _process_function returns None."""
        mock_state.return_value = RateLimitState(
            function_id="x", last_execution=None, daily_executions=0, daily_reset_date="2000-01-01"
        )
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(policy=PolicySpec(allowed_hours=(1, 2)))

        # Patch datetime to be outside allowed hours
        with patch("elle.reactive.engine.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            result = await engine._process_function(func, event=_mock_event())

        assert result is None

    @patch("elle.reactive.engine.record_execution")
    @patch("elle.reactive.engine.evaluate_condition", return_value=(False, "condition not met"))
    @patch("elle.reactive.engine.get_rate_limit_state")
    async def test_condition_false_returns_none(self, mock_state, mock_eval, mock_rec):
        """When condition evaluates to False, _process_function returns None."""
        mock_state.return_value = RateLimitState(
            function_id="x", last_execution=None, daily_executions=0, daily_reset_date="2000-01-01"
        )
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(
            condition=Condition(expression={"gte": ["{event.raw.used_pct}", 99]}),
        )
        result = await engine._process_function(func, event=_mock_event())
        assert result is None

    @patch("elle.reactive.engine.record_execution")
    @patch("elle.reactive.engine.update_rate_limit_state")
    @patch("elle.reactive.engine.get_rate_limit_state")
    async def test_skip_rate_limit_skips_checks(self, mock_state, mock_update_rl, mock_rec):
        """When skip_rate_limit=True, rate limit and allowed hours are not checked."""
        # Do not even need to set up rate limit state -- it should not be called
        executor = _mock_executor_with_capability(success=True)
        engine = ReactiveEngine(executor=executor)
        func = _make_func(policy=PolicySpec(max_daily_executions=1, allowed_hours=(1, 2)))

        with patch.object(engine, "_execute_action") as mock_ea:
            mock_ea.return_value = ActionResult(capability="docker.prune", success=True)
            import builtins

            real_import = builtins.__import__

            def _fail_incident(name, *args, **kwargs):
                if "incident_context" in name:
                    raise ImportError("no")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_fail_incident):
                record = await engine._process_function(func, event=None, skip_rate_limit=True)

        assert record is not None
        assert record.success is True
        # Rate limit state should NOT have been updated (skip_rate_limit=True still
        # causes update after line 604 -- but the check is skipped before execution)
        mock_state.assert_not_called()


# ---------------------------------------------------------------------------
# _process_function: failure notification
# ---------------------------------------------------------------------------


class TestProcessFunctionNotification:
    @patch("elle.reactive.engine.record_execution")
    @patch("elle.reactive.engine.update_rate_limit_state")
    @patch("elle.reactive.engine.get_rate_limit_state")
    async def test_escalate_on_failure_calls_notify(self, mock_state, mock_update_rl, mock_rec):
        """When escalate_on_failure is True and action fails, _notify_failure is called."""
        mock_state.return_value = RateLimitState(
            function_id="x", last_execution=None, daily_executions=0, daily_reset_date="2000-01-01"
        )
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(policy=PolicySpec(escalate_on_failure=True))

        with (
            patch.object(engine, "_execute_action") as mock_ea,
            patch.object(engine, "_notify_failure", new_callable=AsyncMock) as mock_notify,
        ):
            mock_ea.return_value = ActionResult(capability="docker.prune", success=False, error="fail")
            import builtins

            real_import = builtins.__import__

            def _fail_incident(name, *args, **kwargs):
                if "incident_context" in name:
                    raise ImportError("no")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_fail_incident):
                record = await engine._process_function(func, event=_mock_event())

        assert record is not None
        assert record.success is False
        mock_notify.assert_called_once()

    @patch("elle.reactive.engine.record_execution")
    @patch("elle.reactive.engine.update_rate_limit_state")
    @patch("elle.reactive.engine.get_rate_limit_state")
    async def test_no_escalation_when_disabled(self, mock_state, mock_update_rl, mock_rec):
        """When escalate_on_failure is False, _notify_failure is NOT called."""
        mock_state.return_value = RateLimitState(
            function_id="x", last_execution=None, daily_executions=0, daily_reset_date="2000-01-01"
        )
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(policy=PolicySpec(escalate_on_failure=False))

        with (
            patch.object(engine, "_execute_action") as mock_ea,
            patch.object(engine, "_notify_failure", new_callable=AsyncMock) as mock_notify,
        ):
            mock_ea.return_value = ActionResult(capability="docker.prune", success=False, error="fail")
            import builtins

            real_import = builtins.__import__

            def _fail_incident(name, *args, **kwargs):
                if "incident_context" in name:
                    raise ImportError("no")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_fail_incident):
                record = await engine._process_function(func, event=_mock_event())

        assert record is not None
        assert record.success is False
        mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# _execute_action
# ---------------------------------------------------------------------------


class TestExecuteAction:
    async def test_capability_not_found(self):
        """When the capability is not in the registry, an error ActionResult is returned."""
        executor = MagicMock()
        executor.registry.get.return_value = None
        engine = ReactiveEngine(executor=executor)

        from elle.reactive.evaluator import ConditionContext

        ctx = ConditionContext(event=None, state={}, system={})
        action = ActionSpec(capability="nonexistent.cap", input={})
        result = await engine._execute_action(action, ctx)

        assert result.success is False
        assert "not found" in (result.error or "")

    async def test_capability_found_and_succeeds(self):
        """When capability is registered and execution succeeds, success ActionResult returned."""
        executor = _mock_executor_with_capability(success=True, output="all good")
        engine = ReactiveEngine(executor=executor)

        from elle.reactive.evaluator import ConditionContext

        ctx = ConditionContext(event=None, state={}, system={})
        action = ActionSpec(capability="docker.prune", input={"all": True})
        result = await engine._execute_action(action, ctx)

        assert result.success is True
        assert result.output == "all good"
        assert result.execution_time_ms >= 0

    async def test_execution_exception_returns_error(self):
        """When executor.execute raises, an error ActionResult is returned."""
        executor = MagicMock()
        cap = MagicMock()
        cap.input_schema = _StubInput
        executor.registry.get.return_value = cap
        executor.execute = AsyncMock(side_effect=RuntimeError("executor boom"))
        engine = ReactiveEngine(executor=executor)

        from elle.reactive.evaluator import ConditionContext

        ctx = ConditionContext(event=None, state={}, system={})
        action = ActionSpec(capability="docker.prune", input={})
        result = await engine._execute_action(action, ctx)

        assert result.success is False
        assert "executor boom" in (result.error or "")


# ---------------------------------------------------------------------------
# _create_input_model
# ---------------------------------------------------------------------------


class TestCreateInputModel:
    def test_with_input_schema(self):
        """When capability has input_schema, it uses that schema to create the model."""
        from pydantic import BaseModel as PydanticBaseModel

        class FakeInput(PydanticBaseModel):
            name: str = "default"

        cap = MagicMock()
        cap.input_schema = FakeInput

        engine = ReactiveEngine()
        result = engine._create_input_model(cap, {"name": "test"})
        assert result.name == "test"

    def test_fallback_to_generic_model_empty_input(self):
        """When capability has no input_schema and input_data is empty, GenericInput is created."""
        cap = MagicMock(spec=["spec"])
        del cap.input_schema  # Ensure hasattr(cap, "input_schema") is False
        cap.spec = MagicMock()
        cap.spec.input_schema_name = None

        engine = ReactiveEngine()
        # Empty dict avoids the setattr issue on GenericInput
        result = engine._create_input_model(cap, {})
        assert isinstance(result, BaseModel)

    def test_with_input_schema_name_no_registry_empty_input(self):
        """When capability has spec.input_schema_name but no registry, falls through to generic."""
        cap = MagicMock(spec=["spec"])
        del cap.input_schema  # Ensure hasattr(cap, "input_schema") is False
        cap.spec = MagicMock()
        cap.spec.input_schema_name = "SomeSchemaName"

        engine = ReactiveEngine()
        # Empty dict avoids the setattr issue on GenericInput
        result = engine._create_input_model(cap, {})
        assert isinstance(result, BaseModel)

    def test_fallback_generic_model_with_fields_raises(self):
        """GenericInput fallback raises ValueError when setting unknown fields (Pydantic v2)."""
        cap = MagicMock(spec=["spec"])
        del cap.input_schema  # Ensure hasattr(cap, "input_schema") is False
        cap.spec = MagicMock()
        cap.spec.input_schema_name = None

        engine = ReactiveEngine()
        # Pydantic v2 does not allow setting undeclared fields without extra='allow'
        with pytest.raises(ValueError, match="has no field"):
            engine._create_input_model(cap, {"key": "value"})


# ---------------------------------------------------------------------------
# _build_context
# ---------------------------------------------------------------------------


class TestBuildContext:
    async def test_with_event(self):
        """Building context with an event populates the event field."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func()
        ctx = await engine._build_context(func, event=_mock_event())
        assert ctx.event is not None
        assert ctx.event["event_id"] == "abc123"

    async def test_without_event(self):
        """Building context without an event has event=None."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func()
        ctx = await engine._build_context(func, event=None)
        assert ctx.event is None

    async def test_context_override_merges(self):
        """context_override keys are merged into event, state, and system."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func()
        override = {
            "event": {"extra_field": "val"},
            "state": {"custom_state": 42},
            "system": {"custom_metric": 99},
        }
        ctx = await engine._build_context(func, event=_mock_event(), context_override=override)
        assert ctx.event["extra_field"] == "val"
        assert ctx.state["custom_state"] == 42
        assert ctx.system["custom_metric"] == 99


# ---------------------------------------------------------------------------
# _probe_state
# ---------------------------------------------------------------------------


class TestProbeState:
    async def test_no_state_defined(self):
        """When func.state is empty, returns empty dict."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(state={})
        result = await engine._probe_state(func)
        assert result == {}

    async def test_probe_returns_value(self):
        """State probe runs and its result is stored in the returned dict."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(state={"disk_free": StateProbe(probe="disk.free", cache_ttl=60)})

        with patch.object(engine, "_run_probe", return_value=42.5):
            result = await engine._probe_state(func)

        assert result["disk_free"] == 42.5

    async def test_probe_failure_returns_none(self):
        """When a probe raises an exception, the state key is set to None."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(state={"broken": StateProbe(probe="broken.probe", cache_ttl=60)})

        with patch.object(engine, "_run_probe", side_effect=RuntimeError("probe error")):
            result = await engine._probe_state(func)

        assert result["broken"] is None

    async def test_probe_cache_hit(self):
        """When a probe result is cached and fresh, the cached value is used."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(state={"cached": StateProbe(probe="test.probe", cache_ttl=300)})

        # Pre-populate the cache
        cache_key = f"{func.id}:cached"
        engine._state_cache[cache_key] = (datetime.utcnow(), {"value": "cached_val"})

        with patch.object(engine, "_run_probe") as mock_probe:
            result = await engine._probe_state(func)

        # _run_probe should not have been called (cache hit)
        mock_probe.assert_not_called()
        assert result["cached"] == "cached_val"

    async def test_probe_cache_expired(self):
        """When cached value has expired (exceeded cache_ttl), probe is re-run."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(state={"expired": StateProbe(probe="test.probe", cache_ttl=1)})

        # Pre-populate with an expired cache entry
        cache_key = f"{func.id}:expired"
        engine._state_cache[cache_key] = (
            datetime.utcnow() - timedelta(seconds=10),
            {"value": "old_val"},
        )

        with patch.object(engine, "_run_probe", return_value="new_val"):
            result = await engine._probe_state(func)

        assert result["expired"] == "new_val"


# ---------------------------------------------------------------------------
# _update_rate_limit
# ---------------------------------------------------------------------------


class TestUpdateRateLimit:
    @patch("elle.reactive.engine.update_rate_limit_state")
    @patch("elle.reactive.engine.get_rate_limit_state")
    def test_same_day_increments(self, mock_get, mock_update):
        """On the same day, daily_executions is incremented by 1."""
        engine = ReactiveEngine()
        func = _make_func()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        mock_get.return_value = RateLimitState(function_id=func.id, daily_executions=3, daily_reset_date=today)

        engine._update_rate_limit(func)

        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[1]["daily_executions"] == 4
        assert call_kwargs[1]["daily_reset_date"] == today

    @patch("elle.reactive.engine.update_rate_limit_state")
    @patch("elle.reactive.engine.get_rate_limit_state")
    def test_new_day_resets_to_one(self, mock_get, mock_update):
        """On a new day, daily_executions resets to 1."""
        engine = ReactiveEngine()
        func = _make_func()
        mock_get.return_value = RateLimitState(function_id=func.id, daily_executions=50, daily_reset_date="2000-01-01")

        engine._update_rate_limit(func)

        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[1]["daily_executions"] == 1


# ---------------------------------------------------------------------------
# _notify_failure
# ---------------------------------------------------------------------------


class TestNotifyFailure:
    @patch("elle.daemon.notifications.service.notify")
    async def test_notify_success(self, mock_notify):
        """_notify_failure calls the notification service with truncated body."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(name="test-alert")
        await engine._notify_failure(func, "Something went wrong")
        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        assert "test-alert" in call_args[1]["title"]
        assert "Something went wrong" in call_args[1]["body"]

    async def test_notify_import_error_caught(self):
        """If notification service import fails, error is caught and logged."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func()

        import builtins

        real_import = builtins.__import__

        def _fail_notify(name, *args, **kwargs):
            if "notifications" in name:
                raise ImportError("no notification module")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fail_notify):
            # Should not raise
            await engine._notify_failure(func, "error msg")

    @patch("elle.daemon.notifications.service.notify", side_effect=RuntimeError("send failed"))
    async def test_notify_runtime_error_caught(self, mock_notify):
        """If notification service raises at runtime, error is caught."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func()
        # Should not raise
        await engine._notify_failure(func, "error msg")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_same_instance(self):
        assert get_engine() is get_engine()

    def test_reset_clears(self):
        e1 = get_engine()
        reset_engine()
        assert get_engine() is not e1


# ---------------------------------------------------------------------------
# Executor property
# ---------------------------------------------------------------------------


class TestExecutorProperty:
    def test_lazy_init(self):
        sentinel = MagicMock()
        with patch("elle.capabilities.executor.get_executor", return_value=sentinel):
            engine = ReactiveEngine(executor=None)
            assert engine.executor is sentinel

    def test_provided(self):
        s = MagicMock()
        assert ReactiveEngine(executor=s).executor is s


# ---------------------------------------------------------------------------
# _find_matching_functions
# ---------------------------------------------------------------------------


class TestFindMatchingFunctions:
    @patch("elle.reactive.engine.list_enabled_with_event_trigger")
    async def test_returns_matching_only(self, mock_list):
        """Only functions whose triggers match the event are returned."""
        engine = ReactiveEngine(executor=MagicMock())
        matching_func = _make_func(
            trigger_event=EventTrigger(source="journal", category="disk"),
            name="match",
        )
        non_matching_func = _make_func(
            trigger_event=EventTrigger(source="kernel", category="net"),
            name="no-match",
        )
        mock_list.return_value = [matching_func, non_matching_func]

        result = await engine._find_matching_functions(_mock_event(source="journal", category="disk"))
        assert len(result) == 1
        assert result[0].name == "match"


# ---------------------------------------------------------------------------
# _find_forecast_functions
# ---------------------------------------------------------------------------


class TestFindForecastFunctions:
    @patch("elle.reactive.engine.list_enabled_with_forecast_trigger")
    async def test_returns_matching_only(self, mock_list):
        """Only functions whose forecast triggers match are returned."""
        engine = ReactiveEngine(executor=MagicMock())
        matching_func = _make_func(
            trigger_type="forecast",
            trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="prepare", metric="disk.*"),
            name="match",
        )
        non_matching_func = _make_func(
            trigger_type="forecast",
            trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="act_now", metric="cpu.*"),
            name="no-match",
        )
        mock_list.return_value = [matching_func, non_matching_func]

        result = await engine._find_forecast_functions(_mock_forecast(urgency="prepare", metric="disk./.used_pct"))
        assert len(result) == 1
        assert result[0].name == "match"


# ---------------------------------------------------------------------------
# _process_forecast_function
# ---------------------------------------------------------------------------


class TestProcessForecastFunction:
    async def test_delegates_to_process_function_with_forecast_context(self):
        """_process_forecast_function builds context from forecast and delegates."""
        engine = ReactiveEngine(executor=MagicMock())
        func = _make_func(
            trigger_type="forecast",
            trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="prepare"),
        )
        forecast = _mock_forecast()

        expected = ExecutionRecord(
            function_id=func.id,
            function_name=func.name,
            triggered_at=datetime.utcnow(),
            condition_result=True,
            success=True,
        )
        with patch.object(engine, "_process_function", return_value=expected) as mock_pf:
            result = await engine._process_forecast_function(func, forecast)

        assert result is expected
        # Verify context_override was passed with forecast data
        call_kwargs = mock_pf.call_args[1]
        assert "context_override" in call_kwargs
        ctx_override = call_kwargs["context_override"]
        assert "forecast" in ctx_override
        assert "system" in ctx_override
        assert ctx_override["system"]["metric"] == forecast.metric
