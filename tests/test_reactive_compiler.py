"""Tests for Reactive Functions NL compiler."""

from unittest.mock import MagicMock

import pytest

from elle.reactive.compiler import (
    CompilationError,
    ReactiveFunctionCompiler,
    get_compiler,
    reset_compiler,
)
from elle.reactive.models import ReactiveFunction


@pytest.fixture
def mock_llm():
    """Create a mock LLM."""
    llm = MagicMock()
    return llm


@pytest.fixture
def mock_registry():
    """Create a mock capability registry."""
    registry = MagicMock()
    registry.list_all.return_value = []
    registry.get.return_value = None
    return registry


@pytest.fixture
def compiler(mock_llm, mock_registry):
    """Create a compiler with mocks."""
    reset_compiler()
    return ReactiveFunctionCompiler(llm=mock_llm, registry=mock_registry)


class TestCompilerParsing:
    """Tests for compiler parsing logic."""

    def test_parse_event_trigger(self, compiler):
        """Test parsing event trigger from LLM output."""
        data = {
            "name": "disk-alert",
            "description": "Alert on disk",
            "trigger": {
                "type": "event",
                "event": {
                    "source": "probe",
                    "category": "disk",
                    "severity": "warning",
                },
            },
            "actions": [{"capability": "notify.send"}],
            "policy": {},
        }
        result = compiler._parse_function(data, "test prompt")
        assert result.trigger.type == "event"
        assert result.trigger.event.category == "disk"

    def test_parse_schedule_trigger(self, compiler):
        """Test parsing schedule trigger from LLM output."""
        data = {
            "name": "daily-check",
            "description": "Daily check",
            "trigger": {
                "type": "schedule",
                "schedule": {
                    "cron": "0 9 * * *",
                    "timezone": "America/New_York",
                },
            },
            "actions": [{"capability": "notify.send"}],
            "policy": {},
        }
        result = compiler._parse_function(data, "test prompt")
        assert result.trigger.type == "schedule"
        assert result.trigger.schedule.cron == "0 9 * * *"

    def test_parse_condition(self, compiler):
        """Test parsing condition from LLM output."""
        data = {
            "name": "test",
            "trigger": {"type": "manual"},
            "condition": {
                "expression": {"gte": ["{event.raw.used_pct}", 90]},
            },
            "actions": [{"capability": "notify.send"}],
            "policy": {},
        }
        result = compiler._parse_function(data, "test")
        assert result.condition is not None
        assert "gte" in result.condition.expression

    def test_parse_multiple_actions(self, compiler):
        """Test parsing multiple actions."""
        data = {
            "name": "test",
            "trigger": {"type": "manual"},
            "actions": [
                {"capability": "docker.prune", "input": {"all": True}},
                {"capability": "notify.send", "input": {"title": "Done"}},
            ],
            "policy": {},
        }
        result = compiler._parse_function(data, "test")
        assert len(result.actions) == 2
        assert result.actions[0].capability == "docker.prune"
        assert result.actions[1].capability == "notify.send"

    def test_parse_policy(self, compiler):
        """Test parsing policy from LLM output."""
        data = {
            "name": "test",
            "trigger": {"type": "manual"},
            "actions": [{"capability": "notify.send"}],
            "policy": {
                "max_frequency": "1h",
                "max_daily_executions": 10,
                "require_confirmation": True,
                "allowed_hours": [9, 17],
            },
        }
        result = compiler._parse_function(data, "test")
        assert result.policy.max_frequency == "1h"
        assert result.policy.max_daily_executions == 10
        assert result.policy.require_confirmation is True
        assert result.policy.allowed_hours == (9, 17)

    def test_parse_tags(self, compiler):
        """Test parsing tags from LLM output."""
        data = {
            "name": "test",
            "trigger": {"type": "manual"},
            "actions": [{"capability": "notify.send"}],
            "policy": {},
            "tags": ["disk", "cleanup"],
        }
        result = compiler._parse_function(data, "test")
        assert result.tags == ("disk", "cleanup")

    def test_parse_source_prompt_preserved(self, compiler):
        """Test that source prompt is preserved."""
        data = {
            "name": "test",
            "trigger": {"type": "manual"},
            "actions": [{"capability": "notify.send"}],
            "policy": {},
        }
        result = compiler._parse_function(data, "original prompt")
        assert result.source_prompt == "original prompt"

    def test_parse_no_actions_raises_error(self, compiler):
        """Test that no actions raises error."""
        data = {
            "name": "test",
            "trigger": {"type": "manual"},
            "actions": [],
            "policy": {},
        }
        with pytest.raises(CompilationError):
            compiler._parse_function(data, "test")


class TestCapabilitiesContext:
    """Tests for capabilities context building."""

    def test_build_context_empty_registry(self, compiler, mock_registry):
        """Test building context with empty registry."""
        mock_registry.list_all.return_value = []
        result = compiler._build_capabilities_context()
        assert "notify.send" in result

    def test_build_context_with_capabilities(self, compiler, mock_registry):
        """Test building context with capabilities."""
        cap1 = MagicMock()
        cap1.spec.name = "service.restart"
        cap1.spec.summary = "Restart a service"

        cap2 = MagicMock()
        cap2.spec.name = "docker.prune"
        cap2.spec.summary = "Prune Docker resources"

        mock_registry.list_all.return_value = [cap1, cap2]

        result = compiler._build_capabilities_context()
        assert "service.restart" in result
        assert "docker.prune" in result


class TestSuggestions:
    """Tests for improvement suggestions."""

    def test_suggest_missing_description(self, compiler):
        """Test suggestion for missing description."""
        func = ReactiveFunction(
            name="test",
            trigger={"type": "manual"},
            actions=({"capability": "notify.send"},),
        )
        suggestions = compiler.suggest_improvements(func)
        assert any("description" in s.lower() for s in suggestions)

    def test_suggest_high_frequency(self, compiler):
        """Test suggestion for high frequency."""
        func = ReactiveFunction(
            name="test",
            description="Test",
            trigger={"type": "manual"},
            actions=({"capability": "notify.send"},),
            policy={"max_frequency": "1s"},
        )
        suggestions = compiler.suggest_improvements(func)
        assert any("frequency" in s.lower() for s in suggestions)

    def test_suggest_confirmation_for_dangerous(self, compiler):
        """Test suggestion for dangerous actions without confirmation."""
        func = ReactiveFunction(
            name="test",
            description="Test",
            trigger={"type": "manual"},
            actions=({"capability": "file.delete", "input": {"path": "/tmp/test"}},),
            policy={"require_confirmation": False},
        )
        suggestions = compiler.suggest_improvements(func)
        assert any("confirmation" in s.lower() for s in suggestions)

    def test_suggest_condition_for_event_trigger(self, compiler):
        """Test suggestion for event trigger without condition."""
        func = ReactiveFunction(
            name="test",
            description="Test",
            trigger={"type": "event", "event": {"category": "disk"}},
            actions=({"capability": "notify.send"},),
        )
        suggestions = compiler.suggest_improvements(func)
        assert any("condition" in s.lower() for s in suggestions)

    def test_suggest_split_many_actions(self, compiler):
        """Test suggestion for too many actions."""
        func = ReactiveFunction(
            name="test",
            description="Test",
            trigger={"type": "manual"},
            actions=tuple({"capability": f"action.{i}"} for i in range(6)),
        )
        suggestions = compiler.suggest_improvements(func)
        assert any("split" in s.lower() for s in suggestions)


class TestCompilerIntegration:
    """Integration tests for compiler."""

    @pytest.mark.asyncio
    async def test_compile_success(self, compiler, mock_llm):
        """Test successful compilation."""
        mock_llm.generate_json.return_value = {
            "name": "disk-cleanup",
            "description": "Clean disk when full",
            "trigger": {
                "type": "event",
                "event": {"category": "disk"},
            },
            "condition": {
                "expression": {"gte": ["{event.raw.used_pct}", 90]},
            },
            "actions": [
                {"capability": "docker.prune", "input": {}},
            ],
            "policy": {"max_frequency": "1h"},
        }

        result = await compiler.compile(
            "clean docker when disk > 90%",
            validate_capabilities=False,
        )
        assert isinstance(result, ReactiveFunction)
        assert result.name == "disk-cleanup"
        assert result.source_prompt == "clean docker when disk > 90%"

    @pytest.mark.asyncio
    async def test_compile_llm_failure(self, compiler, mock_llm):
        """Test compilation when LLM fails."""
        mock_llm.generate_json.side_effect = Exception("LLM error")

        with pytest.raises(CompilationError):
            await compiler.compile("test prompt")


class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_compiler_returns_same_instance(self):
        """Test get_compiler returns singleton."""
        reset_compiler()
        c1 = get_compiler()
        c2 = get_compiler()
        assert c1 is c2

    def test_reset_compiler_clears_instance(self):
        """Test reset_compiler clears singleton."""
        reset_compiler()
        c1 = get_compiler()
        reset_compiler()
        c2 = get_compiler()
        assert c1 is not c2
