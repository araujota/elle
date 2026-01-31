"""Tests for the reactive functions built-in templates module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# Mock psycopg at import time since templates module transitively imports schema
# which imports psycopg
mock_pg = MagicMock()
mock_pg.errors = MagicMock()
mock_pg.errors.UndefinedTable = type("UndefinedTable", (Exception,), {})
mock_pg.Connection = MagicMock

with patch.dict("sys.modules", {"psycopg": mock_pg, "psycopg.errors": mock_pg.errors}):
    from elle.reactive.templates import (
        BUILTIN_TEMPLATES,
        CONTAINER_RESTART_ALERT,
        DISK_PRESSURE_CLEANUP,
        DOCKER_AUTO_DIAGNOSE,
        get_template,
        list_templates,
    )


# ===========================================================================
# DOCKER_AUTO_DIAGNOSE template
# ===========================================================================


class TestDockerAutoDiagnose:
    def test_id(self):
        assert DOCKER_AUTO_DIAGNOSE.id == "builtin:docker-auto-diagnose"

    def test_name(self):
        assert DOCKER_AUTO_DIAGNOSE.name == "docker-auto-diagnose"

    def test_enabled_by_default(self):
        assert DOCKER_AUTO_DIAGNOSE.enabled is True

    def test_created_by(self):
        assert DOCKER_AUTO_DIAGNOSE.created_by == "builtin"

    def test_trigger_type(self):
        assert DOCKER_AUTO_DIAGNOSE.trigger.type == "event"

    def test_trigger_event(self):
        event = DOCKER_AUTO_DIAGNOSE.trigger.event
        assert event is not None
        assert event.source == "probe"
        assert event.category == "docker"
        assert event.severity == "warning"

    def test_trigger_event_match(self):
        match = DOCKER_AUTO_DIAGNOSE.trigger.event.match
        assert "$or" in match
        events = match["$or"]
        event_types = [e["_DOCKER_EVENT"] for e in events]
        assert "die" in event_types
        assert "oom" in event_types
        assert "kill" in event_types

    def test_condition_is_none(self):
        assert DOCKER_AUTO_DIAGNOSE.condition is None

    def test_actions(self):
        assert len(DOCKER_AUTO_DIAGNOSE.actions) == 1
        action = DOCKER_AUTO_DIAGNOSE.actions[0]
        assert action.capability == "docker.diagnose"
        assert action.on_failure == "continue"

    def test_policy(self):
        policy = DOCKER_AUTO_DIAGNOSE.policy
        assert policy.max_frequency == "1m"
        assert policy.max_daily_executions == 100
        assert policy.require_confirmation is False
        assert policy.escalate_on_failure is False

    def test_tags(self):
        assert "docker" in DOCKER_AUTO_DIAGNOSE.tags
        assert "diagnose" in DOCKER_AUTO_DIAGNOSE.tags
        assert "builtin" in DOCKER_AUTO_DIAGNOSE.tags

    def test_source_prompt_is_none(self):
        assert DOCKER_AUTO_DIAGNOSE.source_prompt is None


# ===========================================================================
# DISK_PRESSURE_CLEANUP template
# ===========================================================================


class TestDiskPressureCleanup:
    def test_id(self):
        assert DISK_PRESSURE_CLEANUP.id == "builtin:disk-pressure-cleanup"

    def test_name(self):
        assert DISK_PRESSURE_CLEANUP.name == "disk-pressure-cleanup"

    def test_disabled_by_default(self):
        assert DISK_PRESSURE_CLEANUP.enabled is False

    def test_created_by(self):
        assert DISK_PRESSURE_CLEANUP.created_by == "builtin"

    def test_trigger(self):
        assert DISK_PRESSURE_CLEANUP.trigger.type == "event"
        event = DISK_PRESSURE_CLEANUP.trigger.event
        assert event is not None
        assert event.source == "probe"
        assert event.category == "disk"
        assert event.severity == "warning"

    def test_actions(self):
        assert len(DISK_PRESSURE_CLEANUP.actions) == 1
        action = DISK_PRESSURE_CLEANUP.actions[0]
        assert action.capability == "docker.prune"
        assert action.on_failure == "stop"
        assert action.input.get("all_images") is False
        assert action.input.get("volumes") is False

    def test_policy_requires_confirmation(self):
        policy = DISK_PRESSURE_CLEANUP.policy
        assert policy.require_confirmation is True
        assert policy.max_frequency == "1h"
        assert policy.max_daily_executions == 5
        assert policy.escalate_on_failure is True

    def test_tags(self):
        assert "disk" in DISK_PRESSURE_CLEANUP.tags
        assert "docker" in DISK_PRESSURE_CLEANUP.tags
        assert "cleanup" in DISK_PRESSURE_CLEANUP.tags
        assert "builtin" in DISK_PRESSURE_CLEANUP.tags


# ===========================================================================
# CONTAINER_RESTART_ALERT template
# ===========================================================================


class TestContainerRestartAlert:
    def test_id(self):
        assert CONTAINER_RESTART_ALERT.id == "builtin:container-restart-alert"

    def test_name(self):
        assert CONTAINER_RESTART_ALERT.name == "container-restart-alert"

    def test_disabled_by_default(self):
        assert CONTAINER_RESTART_ALERT.enabled is False

    def test_trigger(self):
        assert CONTAINER_RESTART_ALERT.trigger.type == "event"
        event = CONTAINER_RESTART_ALERT.trigger.event
        assert event is not None
        assert event.source == "probe"
        assert event.category == "docker"
        assert event.severity == "warning"
        assert event.match.get("restart_count", {}).get("$gt") == 3

    def test_actions(self):
        assert len(CONTAINER_RESTART_ALERT.actions) == 1
        action = CONTAINER_RESTART_ALERT.actions[0]
        assert action.capability == "notify.send"
        assert action.on_failure == "continue"
        assert "title" in action.input
        assert "message" in action.input

    def test_policy(self):
        policy = CONTAINER_RESTART_ALERT.policy
        assert policy.max_frequency == "15m"
        assert policy.max_daily_executions == 20
        assert policy.require_confirmation is False

    def test_tags(self):
        assert "docker" in CONTAINER_RESTART_ALERT.tags
        assert "alert" in CONTAINER_RESTART_ALERT.tags
        assert "builtin" in CONTAINER_RESTART_ALERT.tags


# ===========================================================================
# BUILTIN_TEMPLATES tuple
# ===========================================================================


class TestBuiltinTemplates:
    def test_contains_all_templates(self):
        assert DOCKER_AUTO_DIAGNOSE in BUILTIN_TEMPLATES
        assert DISK_PRESSURE_CLEANUP in BUILTIN_TEMPLATES
        assert CONTAINER_RESTART_ALERT in BUILTIN_TEMPLATES

    def test_count(self):
        assert len(BUILTIN_TEMPLATES) == 3

    def test_is_tuple(self):
        assert isinstance(BUILTIN_TEMPLATES, tuple)

    def test_all_unique_ids(self):
        ids = [t.id for t in BUILTIN_TEMPLATES]
        assert len(ids) == len(set(ids))

    def test_all_unique_names(self):
        names = [t.name for t in BUILTIN_TEMPLATES]
        assert len(names) == len(set(names))

    def test_all_builtin_created_by(self):
        for template in BUILTIN_TEMPLATES:
            assert template.created_by == "builtin"

    def test_all_have_builtin_tag(self):
        for template in BUILTIN_TEMPLATES:
            assert "builtin" in template.tags


# ===========================================================================
# get_template
# ===========================================================================


class TestGetTemplate:
    def test_find_docker_diagnose(self):
        result = get_template("builtin:docker-auto-diagnose")
        assert result is not None
        assert result.id == "builtin:docker-auto-diagnose"

    def test_find_disk_pressure(self):
        result = get_template("builtin:disk-pressure-cleanup")
        assert result is not None
        assert result.id == "builtin:disk-pressure-cleanup"

    def test_find_container_restart(self):
        result = get_template("builtin:container-restart-alert")
        assert result is not None
        assert result.id == "builtin:container-restart-alert"

    def test_not_found_returns_none(self):
        result = get_template("nonexistent:template")
        assert result is None

    def test_empty_string(self):
        result = get_template("")
        assert result is None

    def test_partial_id_not_found(self):
        result = get_template("builtin:docker")
        assert result is None


# ===========================================================================
# list_templates
# ===========================================================================


class TestListTemplates:
    def test_returns_list(self):
        result = list_templates()
        assert isinstance(result, list)

    def test_contains_all_templates(self):
        result = list_templates()
        assert len(result) == 3

    def test_items_are_reactive_functions(self):
        result = list_templates()
        for item in result:
            assert type(item).__name__ == "ReactiveFunction"
            assert hasattr(item, "id")
            assert hasattr(item, "name")
            assert hasattr(item, "trigger")
            assert hasattr(item, "actions")

    def test_is_independent_copy(self):
        """Returned list should be a new list (not the tuple reference)."""
        result = list_templates()
        assert result is not BUILTIN_TEMPLATES
        assert isinstance(result, list)
