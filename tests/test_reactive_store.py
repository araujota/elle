from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from elle.reactive.models import (
    ActionResult,
    ActionSpec,
    Condition,
    EventTrigger,
    ExecutionRecord,
    ForecastTrigger,
    PolicySpec,
    ReactiveFunction,
    ScheduleTrigger,
    Trigger,
)
from elle.reactive.store import (
    create_function,
    delete_function,
    delete_function_by_name,
    get_execution_count,
    get_execution_history,
    get_function,
    get_function_by_name,
    get_function_count,
    get_function_state,
    get_rate_limit_state,
    get_recent_executions,
    list_enabled_with_event_trigger,
    list_enabled_with_forecast_trigger,
    list_enabled_with_schedule_trigger,
    list_functions,
    record_execution,
    set_function_state,
    update_function,
    update_rate_limit_state,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_func(
    name: str = "test-func",
    trigger_type: str = "event",
    enabled: bool = True,
    **kw: Any,
) -> ReactiveFunction:
    trigger_event = None
    trigger_schedule = None
    trigger_forecast = None
    if trigger_type == "event":
        trigger_event = EventTrigger(source="journal", category="disk", severity="warning")
    elif trigger_type == "schedule":
        trigger_schedule = ScheduleTrigger(cron="0 */4 * * *")
    elif trigger_type == "forecast":
        trigger_forecast = ForecastTrigger(urgency="prepare")
    return ReactiveFunction(
        name=name,
        description=kw.get("description", "Test function"),
        enabled=enabled,
        trigger=Trigger(
            type=trigger_type,
            event=trigger_event,
            schedule=trigger_schedule,
            forecast=trigger_forecast,
        ),
        condition=kw.get("condition"),
        actions=kw.get("actions", (ActionSpec(capability="docker.prune", input={"all": True}),)),
        policy=kw.get("policy", PolicySpec()),
        tags=kw.get("tags", ("test",)),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(reactive_conn):
    return reactive_conn


# ---------------------------------------------------------------------------
# create_function
# ---------------------------------------------------------------------------


class TestCreateFunction:
    def test_basic_create(self, conn):
        func = _make_func()
        result = create_function(func, conn=conn)
        assert result.name == "test-func"
        assert result.id == func.id

    def test_duplicate_name_raises(self, conn):
        import psycopg.errors

        create_function(_make_func(name="dupe"), conn=conn)
        with pytest.raises(psycopg.errors.UniqueViolation):
            create_function(_make_func(name="dupe"), conn=conn)

    def test_roundtrip_with_condition(self, conn):
        func = _make_func(condition=Condition(expression={"gte": ["{event.raw.used_pct}", 90]}))
        create_function(func, conn=conn)
        loaded = get_function(func.id, conn=conn)
        assert loaded is not None
        assert loaded.condition is not None
        assert loaded.condition.expression["gte"][1] == 90

    def test_roundtrip_with_tags(self, conn):
        func = _make_func(tags=("docker", "disk"))
        create_function(func, conn=conn)
        loaded = get_function(func.id, conn=conn)
        assert loaded is not None
        assert "docker" in loaded.tags
        assert "disk" in loaded.tags

    def test_roundtrip_schedule_trigger(self, conn):
        func = _make_func(trigger_type="schedule")
        create_function(func, conn=conn)
        loaded = get_function(func.id, conn=conn)
        assert loaded is not None
        assert loaded.trigger.type == "schedule"
        assert loaded.trigger.schedule is not None
        assert loaded.trigger.schedule.cron == "0 */4 * * *"

    def test_roundtrip_forecast_trigger(self, conn):
        func = _make_func(trigger_type="forecast")
        create_function(func, conn=conn)
        loaded = get_function(func.id, conn=conn)
        assert loaded is not None
        assert loaded.trigger.type == "forecast"
        assert loaded.trigger.forecast is not None


# ---------------------------------------------------------------------------
# get_function / get_function_by_name
# ---------------------------------------------------------------------------


class TestGetFunction:
    def test_not_found(self, conn):
        assert get_function("nonexistent", conn=conn) is None

    def test_found_by_id(self, conn):
        func = _make_func()
        create_function(func, conn=conn)
        loaded = get_function(func.id, conn=conn)
        assert loaded is not None
        assert loaded.name == "test-func"

    def test_by_name_not_found(self, conn):
        assert get_function_by_name("nope", conn=conn) is None

    def test_by_name_found(self, conn):
        func = _make_func(name="by-name-test")
        create_function(func, conn=conn)
        loaded = get_function_by_name("by-name-test", conn=conn)
        assert loaded is not None
        assert loaded.id == func.id


# ---------------------------------------------------------------------------
# update_function
# ---------------------------------------------------------------------------


class TestUpdateFunction:
    def test_update_name(self, conn):
        func = _make_func()
        create_function(func, conn=conn)
        updated = update_function(func.id, name="renamed", conn=conn)
        assert updated is not None
        assert updated.name == "renamed"

    def test_update_enabled(self, conn):
        func = _make_func()
        create_function(func, conn=conn)
        updated = update_function(func.id, enabled=False, conn=conn)
        assert updated is not None
        assert updated.enabled is False

    def test_update_description(self, conn):
        func = _make_func()
        create_function(func, conn=conn)
        updated = update_function(func.id, description="new desc", conn=conn)
        assert updated is not None
        assert updated.description == "new desc"

    def test_update_nonexistent(self, conn):
        result = update_function("nonexistent", name="x", conn=conn)
        assert result is None

    def test_update_tags(self, conn):
        func = _make_func()
        create_function(func, conn=conn)
        updated = update_function(func.id, tags=("new-tag",), conn=conn)
        assert updated is not None
        assert "new-tag" in updated.tags


# ---------------------------------------------------------------------------
# list_functions
# ---------------------------------------------------------------------------


class TestListFunctions:
    def test_empty(self, conn):
        assert list_functions(conn=conn) == []

    def test_returns_all(self, conn):
        for i in range(3):
            create_function(_make_func(name=f"func-{i}"), conn=conn)
        result = list_functions(conn=conn)
        assert len(result) == 3

    def test_enabled_only(self, conn):
        create_function(_make_func(name="on", enabled=True), conn=conn)
        create_function(_make_func(name="off", enabled=False), conn=conn)
        result = list_functions(enabled_only=True, conn=conn)
        assert len(result) == 1
        assert result[0].name == "on"

    def test_limit_offset(self, conn):
        for i in range(5):
            create_function(_make_func(name=f"fn-{i}"), conn=conn)
        result = list_functions(limit=2, offset=0, conn=conn)
        assert len(result) == 2

    def test_filter_by_tags(self, conn):
        create_function(_make_func(name="tagged", tags=("docker",)), conn=conn)
        create_function(_make_func(name="untagged", tags=("other",)), conn=conn)
        result = list_functions(tags=["docker"], conn=conn)
        assert len(result) == 1
        assert result[0].name == "tagged"


# ---------------------------------------------------------------------------
# list_enabled_with_*_trigger
# ---------------------------------------------------------------------------


class TestListByTriggerType:
    def test_event_triggers(self, conn):
        create_function(_make_func(name="ev", trigger_type="event"), conn=conn)
        create_function(_make_func(name="sched", trigger_type="schedule"), conn=conn)
        result = list_enabled_with_event_trigger(conn=conn)
        assert len(result) == 1
        assert result[0].name == "ev"

    def test_schedule_triggers(self, conn):
        create_function(_make_func(name="ev", trigger_type="event"), conn=conn)
        create_function(_make_func(name="sched", trigger_type="schedule"), conn=conn)
        result = list_enabled_with_schedule_trigger(conn=conn)
        assert len(result) == 1
        assert result[0].name == "sched"

    def test_forecast_triggers(self, conn):
        create_function(_make_func(name="fc", trigger_type="forecast"), conn=conn)
        create_function(_make_func(name="ev", trigger_type="event"), conn=conn)
        result = list_enabled_with_forecast_trigger(conn=conn)
        assert len(result) == 1
        assert result[0].name == "fc"

    def test_forecast_with_urgency_filter(self, conn):
        create_function(_make_func(name="fc", trigger_type="forecast"), conn=conn)
        result = list_enabled_with_forecast_trigger(urgency="prepare", conn=conn)
        assert len(result) == 1

    def test_disabled_excluded(self, conn):
        create_function(_make_func(name="disabled", trigger_type="event", enabled=False), conn=conn)
        result = list_enabled_with_event_trigger(conn=conn)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# delete_function
# ---------------------------------------------------------------------------


class TestDeleteFunction:
    def test_delete_by_id(self, conn):
        func = _make_func()
        create_function(func, conn=conn)
        assert delete_function(func.id, conn=conn) is True
        assert get_function(func.id, conn=conn) is None

    def test_delete_nonexistent(self, conn):
        assert delete_function("nonexistent", conn=conn) is False

    def test_delete_by_name(self, conn):
        func = _make_func(name="del-me")
        create_function(func, conn=conn)
        assert delete_function_by_name("del-me", conn=conn) is True
        assert get_function_by_name("del-me", conn=conn) is None

    def test_delete_by_name_nonexistent(self, conn):
        assert delete_function_by_name("nope", conn=conn) is False


# ---------------------------------------------------------------------------
# record_execution / execution history
# ---------------------------------------------------------------------------


class TestExecutionHistory:
    def test_record_and_retrieve(self, conn):
        func = _make_func()
        create_function(func, conn=conn)
        rec = ExecutionRecord(
            function_id=func.id,
            function_name=func.name,
            triggered_at=datetime.utcnow(),
            condition_result=True,
            condition_explanation="all good",
            actions_executed=("docker.prune",),
            actions_results=(ActionResult(capability="docker.prune", success=True),),
            success=True,
        )
        record_execution(rec, conn=conn)
        history = get_execution_history(func.id, conn=conn)
        assert len(history) == 1
        assert history[0].function_id == func.id
        assert history[0].success is True

    def test_recent_executions(self, conn):
        func = _make_func()
        create_function(func, conn=conn)
        for i in range(3):
            rec = ExecutionRecord(
                function_id=func.id,
                function_name=func.name,
                triggered_at=datetime.utcnow(),
                condition_result=True,
                success=(i % 2 == 0),
            )
            record_execution(rec, conn=conn)
        recent = get_recent_executions(conn=conn)
        assert len(recent) == 3

    def test_recent_success_only(self, conn):
        func = _make_func()
        create_function(func, conn=conn)
        for success in [True, False, True]:
            rec = ExecutionRecord(
                function_id=func.id,
                function_name=func.name,
                triggered_at=datetime.utcnow(),
                condition_result=True,
                success=success,
            )
            record_execution(rec, conn=conn)
        recent = get_recent_executions(success_only=True, conn=conn)
        assert len(recent) == 2


# ---------------------------------------------------------------------------
# Rate limit state
# ---------------------------------------------------------------------------


class TestRateLimitState:
    def test_default_state(self, conn):
        state = get_rate_limit_state("nonexistent", conn=conn)
        assert state.function_id == "nonexistent"
        assert state.daily_executions == 0
        assert state.last_execution is None

    def test_update_and_read(self, conn):
        func = _make_func()
        create_function(func, conn=conn)
        now = datetime.utcnow()
        update_rate_limit_state(
            func.id, last_execution=now, daily_executions=3, daily_reset_date="2024-01-15", conn=conn
        )
        state = get_rate_limit_state(func.id, conn=conn)
        assert state.daily_executions == 3
        assert state.daily_reset_date == "2024-01-15"
        assert state.last_execution is not None


# ---------------------------------------------------------------------------
# Custom function state
# ---------------------------------------------------------------------------


class TestFunctionState:
    def test_set_and_get(self, conn):
        func = _make_func()
        create_function(func, conn=conn)
        set_function_state(func.id, "counter", 42, conn=conn)
        assert get_function_state(func.id, "counter", conn=conn) == 42

    def test_get_nonexistent(self, conn):
        assert get_function_state("nope", "key", conn=conn) is None

    def test_overwrite(self, conn):
        func = _make_func()
        create_function(func, conn=conn)
        set_function_state(func.id, "val", "old", conn=conn)
        set_function_state(func.id, "val", "new", conn=conn)
        assert get_function_state(func.id, "val", conn=conn) == "new"


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class TestStatistics:
    def test_function_count_empty(self, conn):
        assert get_function_count(conn=conn) == 0

    def test_function_count(self, conn):
        create_function(_make_func(name="a"), conn=conn)
        create_function(_make_func(name="b", enabled=False), conn=conn)
        assert get_function_count(conn=conn) == 2
        assert get_function_count(enabled_only=True, conn=conn) == 1

    def test_execution_count(self, conn):
        func = _make_func()
        create_function(func, conn=conn)
        for _ in range(3):
            record_execution(
                ExecutionRecord(
                    function_id=func.id,
                    function_name=func.name,
                    triggered_at=datetime.utcnow(),
                    condition_result=True,
                    success=True,
                ),
                conn=conn,
            )
        assert get_execution_count(conn=conn) == 3
        assert get_execution_count(function_id=func.id, conn=conn) == 3

    def test_execution_count_since(self, conn):
        func = _make_func()
        create_function(func, conn=conn)
        record_execution(
            ExecutionRecord(
                function_id=func.id,
                function_name=func.name,
                triggered_at=datetime.utcnow(),
                condition_result=True,
                success=True,
            ),
            conn=conn,
        )
        future = datetime(2099, 1, 1)
        assert get_execution_count(since=future, conn=conn) == 0
