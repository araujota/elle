from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
        assert engine._event_matches_trigger(
            _mock_event(source="journal", category="disk", severity="warning"), func
        ) is True

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
            trigger_type="forecast", trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="act_now"),
        )
        assert engine._forecast_matches_trigger(_mock_forecast(urgency="prepare"), func) is False

    def test_confidence_too_low(self):
        engine = ReactiveEngine()
        func = _make_func(
            trigger_type="forecast", trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="prepare", min_confidence=0.9),
        )
        assert engine._forecast_matches_trigger(_mock_forecast(confidence=0.5), func) is False

    def test_metric_pattern_match(self):
        engine = ReactiveEngine()
        func = _make_func(
            trigger_type="forecast", trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="prepare", metric="disk.*"),
        )
        assert engine._forecast_matches_trigger(_mock_forecast(), func) is True

    def test_metric_pattern_no_match(self):
        engine = ReactiveEngine()
        func = _make_func(
            trigger_type="forecast", trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="prepare", metric="cpu.*"),
        )
        assert engine._forecast_matches_trigger(_mock_forecast(metric="disk./.used_pct"), func) is False


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
            function_id=func.id, last_execution=datetime.utcnow() - timedelta(minutes=10),
            daily_executions=0, daily_reset_date="2000-01-01",
        )
        assert engine._check_rate_limit(func) is False

    @patch("elle.reactive.engine.get_rate_limit_state")
    def test_frequency_passed(self, mock_state):
        engine = ReactiveEngine()
        func = _make_func(policy=PolicySpec(max_frequency="5m"))
        mock_state.return_value = RateLimitState(
            function_id=func.id, last_execution=datetime.utcnow() - timedelta(hours=1),
            daily_executions=0, daily_reset_date="2000-01-01",
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
            trigger_type="forecast", trigger_event=None,
            trigger_forecast=ForecastTrigger(urgency="prepare"),
        )
        mock_list.return_value = [func]
        with patch.object(engine, "_process_forecast_function", side_effect=RuntimeError("oops")):
            records = await engine.process_forecast(_mock_forecast())
        assert len(records) == 1
        assert records[0].success is False


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
