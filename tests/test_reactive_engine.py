"""Tests for Reactive Engine."""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.reactive.engine import (
    ReactiveEngine,
    _event_to_dict,
    _parse_interval,
    get_engine,
    reset_engine,
)
from elle.reactive.models import (
    ActionSpec,
    Condition,
    EventTrigger,
    PolicySpec,
    ReactiveFunction,
    Trigger,
)
from elle.reactive.schema import ensure_schema


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "reactive_test.db"
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    conn.close()

    # Patch the store to use temp DB
    with patch("elle.reactive.store.get_connection") as mock:
        def get_temp_conn():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            ensure_schema(c)
            return c
        mock.side_effect = get_temp_conn
        yield db_path


@pytest.fixture
def mock_executor():
    """Create a mock capability executor."""
    executor = MagicMock()
    executor.registry = MagicMock()
    executor.execute = AsyncMock()
    return executor


@pytest.fixture
def engine(mock_executor):
    """Create a test engine with mock executor."""
    reset_engine()
    engine = ReactiveEngine(executor=mock_executor)
    return engine


@pytest.fixture
def sample_event():
    """Create a sample telemetry event."""
    event = MagicMock()
    event.event_id = "event-123"
    event.ts = datetime.utcnow()
    event.source = "probe"
    event.severity = "warning"
    event.category = "disk"
    event.message = "Disk usage high"
    event.entity = "disk:/"
    event.fingerprint = "abc123"
    event.raw = {"used_pct": 95, "path": "/"}
    return event


@pytest.fixture
def sample_function():
    """Create a sample reactive function."""
    return ReactiveFunction(
        name="disk-alert",
        description="Alert on high disk usage",
        trigger=Trigger(
            type="event",
            event=EventTrigger(category="disk"),
        ),
        condition=Condition(
            expression={"gte": ["{event.raw.used_pct}", 90]}
        ),
        actions=(
            ActionSpec(capability="notify.send", input={"title": "Disk alert"}),
        ),
        policy=PolicySpec(max_frequency="5m", max_daily_executions=100),
    )


class TestEventMatching:
    """Tests for event trigger matching."""

    def test_match_by_category(self, engine, sample_event, sample_function):
        """Test matching event by category."""
        result = engine._event_matches_trigger(sample_event, sample_function)
        assert result is True

    def test_no_match_wrong_category(self, engine, sample_event):
        """Test no match when category differs."""
        func = ReactiveFunction(
            name="net-alert",
            trigger=Trigger(
                type="event",
                event=EventTrigger(category="net"),
            ),
            actions=(ActionSpec(capability="notify.send"),),
        )
        result = engine._event_matches_trigger(sample_event, func)
        assert result is False

    def test_match_by_source(self, engine, sample_event):
        """Test matching event by source."""
        func = ReactiveFunction(
            name="probe-alert",
            trigger=Trigger(
                type="event",
                event=EventTrigger(source="probe"),
            ),
            actions=(ActionSpec(capability="notify.send"),),
        )
        result = engine._event_matches_trigger(sample_event, func)
        assert result is True

    def test_match_by_severity(self, engine, sample_event):
        """Test matching event by minimum severity."""
        func = ReactiveFunction(
            name="warning-alert",
            trigger=Trigger(
                type="event",
                event=EventTrigger(severity="warning"),
            ),
            actions=(ActionSpec(capability="notify.send"),),
        )
        result = engine._event_matches_trigger(sample_event, func)
        assert result is True

    def test_no_match_severity_too_low(self, engine, sample_event):
        """Test no match when severity too low."""
        func = ReactiveFunction(
            name="error-alert",
            trigger=Trigger(
                type="event",
                event=EventTrigger(severity="error"),
            ),
            actions=(ActionSpec(capability="notify.send"),),
        )
        result = engine._event_matches_trigger(sample_event, func)
        assert result is False

    def test_match_with_field_match(self, engine, sample_event):
        """Test matching with field patterns."""
        func = ReactiveFunction(
            name="root-disk-alert",
            trigger=Trigger(
                type="event",
                event=EventTrigger(
                    category="disk",
                    match={"path": "/"},
                ),
            ),
            actions=(ActionSpec(capability="notify.send"),),
        )
        result = engine._event_matches_trigger(sample_event, func)
        assert result is True

    def test_no_match_wrong_field_value(self, engine, sample_event):
        """Test no match when field value differs."""
        func = ReactiveFunction(
            name="home-disk-alert",
            trigger=Trigger(
                type="event",
                event=EventTrigger(
                    category="disk",
                    match={"path": "/home"},
                ),
            ),
            actions=(ActionSpec(capability="notify.send"),),
        )
        result = engine._event_matches_trigger(sample_event, func)
        assert result is False


class TestRateLimiting:
    """Tests for rate limiting."""

    def test_check_rate_limit_no_state(self, engine, sample_function):
        """Test rate limit check with no prior executions."""
        with patch("elle.reactive.engine.get_rate_limit_state") as mock:
            from elle.reactive.models import RateLimitState
            mock.return_value = RateLimitState(function_id=sample_function.id)
            result = engine._check_rate_limit(sample_function)
            assert result is True

    def test_check_rate_limit_within_interval(self, engine, sample_function):
        """Test rate limit blocks within interval."""
        with patch("elle.reactive.engine.get_rate_limit_state") as mock:
            from elle.reactive.models import RateLimitState
            mock.return_value = RateLimitState(
                function_id=sample_function.id,
                last_execution=datetime.utcnow(),
                daily_executions=1,
                daily_reset_date=datetime.utcnow().strftime("%Y-%m-%d"),
            )
            result = engine._check_rate_limit(sample_function)
            assert result is False

    def test_check_rate_limit_after_interval(self, engine, sample_function):
        """Test rate limit allows after interval."""
        with patch("elle.reactive.engine.get_rate_limit_state") as mock:
            from elle.reactive.models import RateLimitState
            mock.return_value = RateLimitState(
                function_id=sample_function.id,
                last_execution=datetime.utcnow() - timedelta(hours=1),
                daily_executions=1,
                daily_reset_date=datetime.utcnow().strftime("%Y-%m-%d"),
            )
            result = engine._check_rate_limit(sample_function)
            assert result is True

    def test_check_daily_limit_exceeded(self, engine):
        """Test daily execution limit."""
        func = ReactiveFunction(
            name="limited",
            trigger=Trigger(type="manual"),
            actions=(ActionSpec(capability="notify.send"),),
            policy=PolicySpec(max_daily_executions=5),
        )
        with patch("elle.reactive.engine.get_rate_limit_state") as mock:
            from elle.reactive.models import RateLimitState
            mock.return_value = RateLimitState(
                function_id=func.id,
                last_execution=datetime.utcnow() - timedelta(hours=1),
                daily_executions=5,
                daily_reset_date=datetime.utcnow().strftime("%Y-%m-%d"),
            )
            result = engine._check_rate_limit(func)
            assert result is False


class TestAllowedHours:
    """Tests for allowed hours checking."""

    def test_allowed_hours_within_range(self, engine):
        """Test execution allowed within hours."""
        policy = PolicySpec(allowed_hours=(9, 17))
        with patch("elle.reactive.engine.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2024, 1, 1, 12, 0)
            result = engine._check_allowed_hours(policy)
            assert result is True

    def test_allowed_hours_outside_range(self, engine):
        """Test execution blocked outside hours."""
        policy = PolicySpec(allowed_hours=(9, 17))
        with patch("elle.reactive.engine.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2024, 1, 1, 20, 0)
            result = engine._check_allowed_hours(policy)
            assert result is False

    def test_allowed_hours_no_restriction(self, engine):
        """Test execution allowed when no restriction."""
        policy = PolicySpec()
        result = engine._check_allowed_hours(policy)
        assert result is True


class TestEventToDict:
    """Tests for event to dict conversion."""

    def test_event_to_dict(self, sample_event):
        """Test converting event to dict."""
        result = _event_to_dict(sample_event)
        assert result["event_id"] == "event-123"
        assert result["category"] == "disk"
        assert result["raw"]["used_pct"] == 95

    def test_event_to_dict_none(self):
        """Test converting None event."""
        result = _event_to_dict(None)
        assert result == {}


class TestParseInterval:
    """Tests for interval parsing."""

    def test_parse_seconds(self):
        """Test parsing seconds interval."""
        result = _parse_interval("30s")
        assert result == timedelta(seconds=30)

    def test_parse_minutes(self):
        """Test parsing minutes interval."""
        result = _parse_interval("5m")
        assert result == timedelta(minutes=5)

    def test_parse_hours(self):
        """Test parsing hours interval."""
        result = _parse_interval("2h")
        assert result == timedelta(hours=2)

    def test_parse_days(self):
        """Test parsing days interval."""
        result = _parse_interval("1d")
        assert result == timedelta(days=1)

    def test_parse_invalid(self):
        """Test parsing invalid interval returns default."""
        result = _parse_interval("invalid")
        assert result == timedelta(minutes=5)


class TestDryRun:
    """Tests for dry run functionality."""

    @pytest.mark.asyncio
    async def test_dry_run_condition_pass(self, engine, sample_function):
        """Test dry run with passing condition."""
        with patch.object(engine, "_probe_state", return_value={}), \
             patch.object(engine, "_get_system_metrics", return_value={}):
            # Create mock event
            event = MagicMock()
            event.event_id = "test"
            event.ts = datetime.utcnow()
            event.source = "probe"
            event.severity = "warning"
            event.category = "disk"
            event.message = "Test"
            event.entity = None
            event.fingerprint = None
            event.raw = {"used_pct": 95}

            result, explanation, context = await engine.dry_run(
                sample_function, event=event
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_dry_run_condition_fail(self, engine, sample_function):
        """Test dry run with failing condition."""
        with patch.object(engine, "_probe_state", return_value={}), \
             patch.object(engine, "_get_system_metrics", return_value={}):
            # Create mock event with low usage
            event = MagicMock()
            event.event_id = "test"
            event.ts = datetime.utcnow()
            event.source = "probe"
            event.severity = "warning"
            event.category = "disk"
            event.message = "Test"
            event.entity = None
            event.fingerprint = None
            event.raw = {"used_pct": 50}

            result, explanation, context = await engine.dry_run(
                sample_function, event=event
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_dry_run_no_condition(self, engine):
        """Test dry run with no condition always passes."""
        func = ReactiveFunction(
            name="no-condition",
            trigger=Trigger(type="manual"),
            actions=(ActionSpec(capability="notify.send"),),
        )
        with patch.object(engine, "_probe_state", return_value={}), \
             patch.object(engine, "_get_system_metrics", return_value={}):
            result, explanation, context = await engine.dry_run(func)
            assert result is True
            assert "No condition" in explanation


class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_engine_returns_same_instance(self):
        """Test get_engine returns singleton."""
        reset_engine()
        e1 = get_engine()
        e2 = get_engine()
        assert e1 is e2

    def test_reset_engine_clears_instance(self):
        """Test reset_engine clears singleton."""
        reset_engine()
        e1 = get_engine()
        reset_engine()
        e2 = get_engine()
        assert e1 is not e2
