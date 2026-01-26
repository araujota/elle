"""Tests for Reactive Functions models."""

from datetime import datetime

import pytest

from elle.reactive.models import (
    ActionResult,
    ActionSpec,
    Condition,
    ConditionContext,
    EventTrigger,
    ExecutionRecord,
    PolicySpec,
    RateLimitState,
    ReactiveFunction,
    ScheduleTrigger,
    StateProbe,
    Trigger,
)


class TestEventTrigger:
    """Tests for EventTrigger model."""

    def test_minimal_creation(self):
        """Test creating EventTrigger with no filters."""
        trigger = EventTrigger()
        assert trigger.source is None
        assert trigger.category is None
        assert trigger.severity is None
        assert trigger.match == {}

    def test_full_creation(self):
        """Test creating EventTrigger with all filters."""
        trigger = EventTrigger(
            source="journal",
            category="disk",
            severity="warning",
            match={"path": "/"},
        )
        assert trigger.source == "journal"
        assert trigger.category == "disk"
        assert trigger.severity == "warning"
        assert trigger.match == {"path": "/"}

    def test_immutability(self):
        """Test that EventTrigger is immutable."""
        from pydantic import ValidationError

        trigger = EventTrigger(category="disk")
        with pytest.raises(ValidationError):
            trigger.category = "net"


class TestScheduleTrigger:
    """Tests for ScheduleTrigger model."""

    def test_creation(self):
        """Test creating ScheduleTrigger."""
        trigger = ScheduleTrigger(cron="0 * * * *")
        assert trigger.cron == "0 * * * *"
        assert trigger.timezone == "UTC"

    def test_custom_timezone(self):
        """Test creating ScheduleTrigger with custom timezone."""
        trigger = ScheduleTrigger(cron="0 9 * * *", timezone="America/New_York")
        assert trigger.timezone == "America/New_York"


class TestTrigger:
    """Tests for Trigger model."""

    def test_event_trigger(self):
        """Test creating event trigger."""
        trigger = Trigger(
            type="event",
            event=EventTrigger(category="disk"),
        )
        assert trigger.type == "event"
        assert trigger.event is not None
        assert trigger.event.category == "disk"
        assert trigger.schedule is None

    def test_schedule_trigger(self):
        """Test creating schedule trigger."""
        trigger = Trigger(
            type="schedule",
            schedule=ScheduleTrigger(cron="0 */4 * * *"),
        )
        assert trigger.type == "schedule"
        assert trigger.schedule is not None
        assert trigger.schedule.cron == "0 */4 * * *"
        assert trigger.event is None

    def test_manual_trigger(self):
        """Test creating manual trigger."""
        trigger = Trigger(type="manual")
        assert trigger.type == "manual"
        assert trigger.event is None
        assert trigger.schedule is None


class TestCondition:
    """Tests for Condition model."""

    def test_simple_expression(self):
        """Test creating simple condition."""
        condition = Condition(expression={"gte": ["{event.raw.used_pct}", 90]})
        assert "gte" in condition.expression

    def test_complex_expression(self):
        """Test creating complex nested condition."""
        condition = Condition(
            expression={
                "all": [
                    {"gte": ["{event.raw.used_pct}", 85]},
                    {"eq": ["{state.docker_running}", True]},
                ]
            }
        )
        assert "all" in condition.expression
        assert len(condition.expression["all"]) == 2


class TestActionSpec:
    """Tests for ActionSpec model."""

    def test_minimal_creation(self):
        """Test creating minimal action."""
        action = ActionSpec(capability="notify.send")
        assert action.capability == "notify.send"
        assert action.input == {}
        assert action.on_failure == "stop"

    def test_full_creation(self):
        """Test creating full action."""
        action = ActionSpec(
            capability="service.restart",
            input={"service": "nginx"},
            on_failure="continue",
        )
        assert action.capability == "service.restart"
        assert action.input == {"service": "nginx"}
        assert action.on_failure == "continue"


class TestPolicySpec:
    """Tests for PolicySpec model."""

    def test_defaults(self):
        """Test PolicySpec with default values."""
        policy = PolicySpec()
        assert policy.max_frequency == "5m"
        assert policy.max_daily_executions == 100
        assert policy.require_confirmation is False
        assert policy.allowed_hours is None
        assert policy.escalate_on_failure is True
        assert policy.notification_channel is None

    def test_custom_values(self):
        """Test PolicySpec with custom values."""
        policy = PolicySpec(
            max_frequency="1h",
            max_daily_executions=10,
            require_confirmation=True,
            allowed_hours=(9, 17),
            escalate_on_failure=False,
            notification_channel="alerts",
        )
        assert policy.max_frequency == "1h"
        assert policy.max_daily_executions == 10
        assert policy.require_confirmation is True
        assert policy.allowed_hours == (9, 17)
        assert policy.escalate_on_failure is False
        assert policy.notification_channel == "alerts"


class TestStateProbe:
    """Tests for StateProbe model."""

    def test_creation(self):
        """Test creating StateProbe."""
        probe = StateProbe(probe="disk.usage")
        assert probe.probe == "disk.usage"
        assert probe.cache_ttl == 60

    def test_custom_ttl(self):
        """Test StateProbe with custom TTL."""
        probe = StateProbe(probe="docker.running", cache_ttl=30)
        assert probe.cache_ttl == 30


class TestReactiveFunction:
    """Tests for ReactiveFunction model."""

    def test_minimal_creation(self):
        """Test creating minimal reactive function."""
        func = ReactiveFunction(
            name="test-function",
            trigger=Trigger(type="manual"),
            actions=(ActionSpec(capability="notify.send"),),
        )
        assert func.name == "test-function"
        assert func.enabled is True
        assert func.description == ""
        assert len(func.actions) == 1

    def test_full_creation(self):
        """Test creating full reactive function."""
        func = ReactiveFunction(
            name="disk-cleanup",
            description="Clean disk when full",
            enabled=False,
            trigger=Trigger(
                type="event",
                event=EventTrigger(category="disk"),
            ),
            condition=Condition(expression={"gte": ["{event.raw.used_pct}", 90]}),
            actions=(
                ActionSpec(capability="docker.prune", input={"all": True}),
                ActionSpec(capability="notify.send", input={"title": "Cleanup done"}),
            ),
            policy=PolicySpec(max_frequency="1h"),
            state={"disk_free": StateProbe(probe="disk.free")},
            tags=("disk", "cleanup"),
            source_prompt="Clean disk when full",
        )
        assert func.name == "disk-cleanup"
        assert func.enabled is False
        assert func.description == "Clean disk when full"
        assert len(func.actions) == 2
        assert func.tags == ("disk", "cleanup")

    def test_id_auto_generated(self):
        """Test that ID is auto-generated."""
        func1 = ReactiveFunction(
            name="test1",
            trigger=Trigger(type="manual"),
            actions=(ActionSpec(capability="notify.send"),),
        )
        func2 = ReactiveFunction(
            name="test2",
            trigger=Trigger(type="manual"),
            actions=(ActionSpec(capability="notify.send"),),
        )
        assert func1.id != func2.id

    def test_timestamps_auto_set(self):
        """Test that timestamps are auto-set."""
        func = ReactiveFunction(
            name="test",
            trigger=Trigger(type="manual"),
            actions=(ActionSpec(capability="notify.send"),),
        )
        assert func.created_at is not None
        assert func.updated_at is not None
        assert isinstance(func.created_at, datetime)


class TestExecutionRecord:
    """Tests for ExecutionRecord model."""

    def test_creation(self):
        """Test creating execution record."""
        record = ExecutionRecord(
            function_id="func-123",
            function_name="test-function",
            condition_result=True,
            condition_explanation="All conditions passed",
            actions_executed=("notify.send",),
            actions_results=(
                ActionResult(
                    capability="notify.send",
                    success=True,
                    execution_time_ms=50,
                ),
            ),
            success=True,
            execution_time_ms=100,
        )
        assert record.function_id == "func-123"
        assert record.success is True
        assert len(record.actions_executed) == 1
        assert len(record.actions_results) == 1

    def test_failed_record(self):
        """Test creating failed execution record."""
        record = ExecutionRecord(
            function_id="func-123",
            function_name="test-function",
            condition_result=True,
            success=False,
            error="Action failed: connection refused",
        )
        assert record.success is False
        assert record.error is not None


class TestConditionContext:
    """Tests for ConditionContext model."""

    def test_creation(self):
        """Test creating condition context."""
        ctx = ConditionContext(
            event={"category": "disk", "raw": {"used_pct": 95}},
            state={"docker_running": True},
            system={"hostname": "test-host"},
        )
        assert ctx.event["category"] == "disk"
        assert ctx.state["docker_running"] is True
        assert ctx.system["hostname"] == "test-host"

    def test_empty_context(self):
        """Test creating empty context."""
        ctx = ConditionContext()
        assert ctx.event is None
        assert ctx.state == {}
        assert ctx.system == {}


class TestRateLimitState:
    """Tests for RateLimitState model."""

    def test_creation(self):
        """Test creating rate limit state."""
        state = RateLimitState(
            function_id="func-123",
            last_execution=datetime.utcnow(),
            daily_executions=5,
            daily_reset_date="2024-01-01",
        )
        assert state.function_id == "func-123"
        assert state.daily_executions == 5

    def test_defaults(self):
        """Test rate limit state defaults."""
        state = RateLimitState(function_id="func-123")
        assert state.last_execution is None
        assert state.daily_executions == 0
        assert state.daily_reset_date == ""
