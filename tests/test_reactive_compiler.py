"""Tests for Reactive Functions NL compiler.

Covers: rule compilation, condition parsing, action generation, validation,
trigger parsing, policy parsing, singleton management, suggestions, and
edge cases for ~90%+ line and branch coverage.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from elle.reactive.compiler import (
    COMPILER_SYSTEM_PROMPT,
    CompilationError,
    ReactiveFunctionCompiler,
    ValidationError,
    compile_reactive_function,
    get_compiler,
    reset_compiler,
)
from elle.reactive.models import (
    ActionSpec,
    EventTrigger,
    ReactiveFunction,
    ScheduleTrigger,
    StateProbe,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_mock_llm(return_value=None):
    """Create a mock LLM that returns the given dict from generate_json."""
    llm = MagicMock()
    llm.generate_json.return_value = return_value or {}
    return llm


def _make_mock_registry(capabilities=None):
    """Create a mock capability registry."""
    registry = MagicMock()
    registry.list_all.return_value = capabilities or []
    registry.get.return_value = MagicMock()  # all capabilities found by default
    return registry


def _mock_capability_spec(name, summary):
    """Create a mock CapabilitySpec with name and summary."""
    spec = MagicMock()
    spec.name = name
    spec.summary = summary
    return spec


def _full_llm_output(**overrides):
    """Build a complete, valid LLM JSON output for the compiler."""
    base = {
        "name": "disk-cleanup",
        "description": "Clean docker images when disk usage is high",
        "trigger": {
            "type": "event",
            "event": {
                "source": "probe",
                "category": "disk",
                "severity": "warning",
                "match": {"unit": "sda1"},
            },
        },
        "condition": {
            "expression": {"gte": ["{event.raw.used_pct}", 90]},
        },
        "actions": [
            {
                "capability": "docker.prune",
                "input": {"all": True},
                "on_failure": "stop",
            },
            {
                "capability": "notify.send",
                "input": {"title": "Disk cleaned"},
                "on_failure": "continue",
            },
        ],
        "policy": {
            "max_frequency": "1h",
            "max_daily_executions": 50,
            "require_confirmation": False,
            "escalate_on_failure": True,
        },
        "tags": ["disk", "docker"],
    }
    base.update(overrides)
    return base


@pytest.fixture
def mock_llm():
    """Create a mock LLM."""
    return _make_mock_llm()


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


# =============================================================================
# TestCompilerInit -- lazy property initialization
# =============================================================================


class TestCompilerInit:
    """Tests for ReactiveFunctionCompiler construction and lazy init."""

    def test_init_stores_explicit_llm_and_registry(self):
        """Compiler stores injected LLM and registry without calling get_*."""
        llm = _make_mock_llm()
        registry = _make_mock_registry()
        comp = ReactiveFunctionCompiler(llm=llm, registry=registry)
        assert comp.llm is llm
        assert comp.registry is registry

    @patch("elle.rag.llm.get_llm")
    def test_lazy_llm_initialization(self, mock_get_llm):
        """LLM property calls get_llm lazily when no LLM was provided."""
        sentinel = MagicMock()
        mock_get_llm.return_value = sentinel

        comp = ReactiveFunctionCompiler()
        result = comp.llm

        mock_get_llm.assert_called_once()
        assert result is sentinel

    @patch("elle.capabilities.registry.get_registry")
    def test_lazy_registry_initialization(self, mock_get_registry):
        """Registry property calls get_registry lazily when none was provided."""
        sentinel = MagicMock()
        mock_get_registry.return_value = sentinel

        comp = ReactiveFunctionCompiler()
        result = comp.registry

        mock_get_registry.assert_called_once()
        assert result is sentinel


# =============================================================================
# TestBuildCapabilitiesContext
# =============================================================================


class TestBuildCapabilitiesContext:
    """Tests for _build_capabilities_context."""

    def test_empty_registry_returns_fallback(self, compiler, mock_registry):
        """Empty registry produces a fallback context mentioning notify.send."""
        mock_registry.list_all.return_value = []
        result = compiler._build_capabilities_context()
        assert "notify.send" in result
        assert "send notifications" in result

    def test_capabilities_listed_in_context(self, compiler, mock_registry):
        """Non-empty registry lists each capability name and summary."""
        spec1 = _mock_capability_spec("service.restart", "Restart a service")
        spec2 = _mock_capability_spec("docker.prune", "Prune Docker resources")
        mock_registry.list_all.return_value = [spec1, spec2]

        result = compiler._build_capabilities_context()
        assert "Available capabilities:" in result
        assert "service.restart: Restart a service" in result
        assert "docker.prune: Prune Docker resources" in result


# =============================================================================
# TestParseTrigger -- all trigger type branches
# =============================================================================


class TestParseTrigger:
    """Tests for _parse_trigger covering event, schedule, manual, and edge cases."""

    def test_event_trigger_parsed(self, compiler):
        """Event trigger with full event data produces EventTrigger."""
        data = {
            "type": "event",
            "event": {
                "source": "journal",
                "category": "oom",
                "severity": "critical",
                "match": {"unit": "nginx.service"},
            },
        }
        trigger = compiler._parse_trigger(data)

        assert trigger.type == "event"
        assert isinstance(trigger.event, EventTrigger)
        assert trigger.event.source == "journal"
        assert trigger.event.category == "oom"
        assert trigger.event.severity == "critical"
        assert trigger.event.match == {"unit": "nginx.service"}
        assert trigger.schedule is None

    def test_schedule_trigger_parsed(self, compiler):
        """Schedule trigger with full data produces ScheduleTrigger."""
        data = {
            "type": "schedule",
            "schedule": {"cron": "0 3 * * *", "timezone": "America/New_York"},
        }
        trigger = compiler._parse_trigger(data)

        assert trigger.type == "schedule"
        assert isinstance(trigger.schedule, ScheduleTrigger)
        assert trigger.schedule.cron == "0 3 * * *"
        assert trigger.schedule.timezone == "America/New_York"
        assert trigger.event is None

    def test_manual_trigger_default_when_empty(self, compiler):
        """Empty dict defaults to manual trigger type with no sub-triggers."""
        trigger = compiler._parse_trigger({})
        assert trigger.type == "manual"
        assert trigger.event is None
        assert trigger.schedule is None

    def test_event_type_without_event_data_has_no_event_trigger(self, compiler):
        """Event type with missing event dict yields event=None."""
        trigger = compiler._parse_trigger({"type": "event"})
        assert trigger.type == "event"
        assert trigger.event is None

    def test_schedule_type_without_schedule_data_has_no_schedule_trigger(self, compiler):
        """Schedule type with missing schedule dict yields schedule=None."""
        trigger = compiler._parse_trigger({"type": "schedule"})
        assert trigger.type == "schedule"
        assert trigger.schedule is None

    def test_schedule_empty_dict_is_falsy_no_schedule(self, compiler):
        """Empty schedule dict is falsy so no ScheduleTrigger is created."""
        trigger = compiler._parse_trigger({"type": "schedule", "schedule": {}})
        assert trigger.type == "schedule"
        assert trigger.schedule is None

    def test_schedule_defaults_for_cron_and_timezone(self, compiler):
        """Schedule trigger uses defaults when cron and timezone are omitted but dict is truthy."""
        trigger = compiler._parse_trigger({"type": "schedule", "schedule": {"extra_field": True}})
        assert trigger.schedule is not None
        assert trigger.schedule.cron == "0 * * * *"
        assert trigger.schedule.timezone == "UTC"


# =============================================================================
# TestParseAction -- defaults and full specification
# =============================================================================


class TestParseAction:
    """Tests for _parse_action."""

    def test_full_action_parsed(self, compiler):
        """Action with all fields is parsed correctly."""
        data = {
            "capability": "service.restart",
            "input": {"name": "nginx"},
            "on_failure": "continue",
        }
        action = compiler._parse_action(data)

        assert isinstance(action, ActionSpec)
        assert action.capability == "service.restart"
        assert action.input == {"name": "nginx"}
        assert action.on_failure == "continue"

    def test_action_defaults_when_empty(self, compiler):
        """Missing action fields fall back to defaults."""
        action = compiler._parse_action({})
        assert action.capability == "notify.send"
        assert action.input == {}
        assert action.on_failure == "stop"


# =============================================================================
# TestParsePolicy -- all branches for allowed_hours, defaults
# =============================================================================


class TestParsePolicy:
    """Tests for _parse_policy."""

    def test_full_policy_parsed(self, compiler):
        """Policy with all fields is parsed correctly."""
        data = {
            "max_frequency": "30m",
            "max_daily_executions": 10,
            "require_confirmation": True,
            "allowed_hours": [9, 17],
            "escalate_on_failure": False,
            "notification_channel": "alerts",
        }
        policy = compiler._parse_policy(data)

        assert policy.max_frequency == "30m"
        assert policy.max_daily_executions == 10
        assert policy.require_confirmation is True
        assert policy.allowed_hours == (9, 17)
        assert policy.escalate_on_failure is False
        assert policy.notification_channel == "alerts"

    def test_policy_defaults(self, compiler):
        """Empty policy data uses all default values."""
        policy = compiler._parse_policy({})
        assert policy.max_frequency == "5m"
        assert policy.max_daily_executions == 100
        assert policy.require_confirmation is False
        assert policy.allowed_hours is None
        assert policy.escalate_on_failure is True
        assert policy.notification_channel is None

    def test_allowed_hours_ignored_when_single_element(self, compiler):
        """allowed_hours is None when list has fewer than 2 elements."""
        policy = compiler._parse_policy({"allowed_hours": [9]})
        assert policy.allowed_hours is None

    def test_allowed_hours_ignored_when_empty_list(self, compiler):
        """allowed_hours is None when list is empty."""
        policy = compiler._parse_policy({"allowed_hours": []})
        assert policy.allowed_hours is None

    def test_allowed_hours_from_tuple(self, compiler):
        """allowed_hours accepts a tuple as well as a list."""
        policy = compiler._parse_policy({"allowed_hours": (8, 20)})
        assert policy.allowed_hours == (8, 20)

    def test_allowed_hours_none_when_falsy(self, compiler):
        """allowed_hours is None when value is None/0/False."""
        policy = compiler._parse_policy({"allowed_hours": None})
        assert policy.allowed_hours is None


# =============================================================================
# TestParseFunction -- condition edge cases, state probes, defaults
# =============================================================================


class TestParseFunction:
    """Tests for _parse_function covering edge cases."""

    def test_parse_event_trigger(self, compiler):
        """Parsing a full event-triggered function."""
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
        """Parsing a schedule-triggered function."""
        data = {
            "name": "daily-check",
            "description": "Daily check",
            "trigger": {
                "type": "schedule",
                "schedule": {"cron": "0 9 * * *", "timezone": "America/New_York"},
            },
            "actions": [{"capability": "notify.send"}],
            "policy": {},
        }
        result = compiler._parse_function(data, "test prompt")
        assert result.trigger.type == "schedule"
        assert result.trigger.schedule.cron == "0 9 * * *"

    def test_parse_condition_expression(self, compiler):
        """Condition with a valid expression is parsed."""
        data = {
            "name": "test",
            "trigger": {"type": "manual"},
            "condition": {"expression": {"gte": ["{event.raw.used_pct}", 90]}},
            "actions": [{"capability": "notify.send"}],
            "policy": {},
        }
        result = compiler._parse_function(data, "test")
        assert result.condition is not None
        assert "gte" in result.condition.expression

    def test_parse_multiple_actions(self, compiler):
        """Multiple actions are all parsed and ordered."""
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

    def test_parse_policy_with_allowed_hours(self, compiler):
        """Policy with allowed_hours is parsed as a tuple."""
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
        """Tags are parsed as a tuple of strings."""
        data = {
            "name": "test",
            "trigger": {"type": "manual"},
            "actions": [{"capability": "notify.send"}],
            "policy": {},
            "tags": ["disk", "cleanup"],
        }
        result = compiler._parse_function(data, "test")
        assert result.tags == ("disk", "cleanup")

    def test_source_prompt_preserved(self, compiler):
        """Original source prompt is stored on the result."""
        data = {
            "name": "test",
            "trigger": {"type": "manual"},
            "actions": [{"capability": "notify.send"}],
            "policy": {},
        }
        result = compiler._parse_function(data, "original prompt")
        assert result.source_prompt == "original prompt"

    def test_no_actions_raises_compilation_error(self, compiler):
        """Empty actions list raises CompilationError."""
        with pytest.raises(CompilationError, match="No actions specified"):
            compiler._parse_function({"actions": []}, "test")

    def test_missing_actions_key_raises_compilation_error(self, compiler):
        """Completely missing actions key also raises CompilationError."""
        with pytest.raises(CompilationError, match="No actions specified"):
            compiler._parse_function({}, "test")

    def test_no_condition_results_in_none(self, compiler):
        """When condition key is absent, result.condition is None."""
        data = _full_llm_output()
        del data["condition"]
        func = compiler._parse_function(data, "test")
        assert func.condition is None

    def test_empty_condition_expression_results_in_none(self, compiler):
        """Condition with empty expression dict results in condition=None."""
        data = _full_llm_output(condition={"expression": {}})
        func = compiler._parse_function(data, "test")
        assert func.condition is None

    def test_condition_key_present_but_no_expression_key(self, compiler):
        """Condition dict exists but has no 'expression' key -> condition=None."""
        data = _full_llm_output(condition={"other_field": "value"})
        func = compiler._parse_function(data, "test")
        assert func.condition is None

    def test_state_probes_parsed(self, compiler):
        """State probes dict is converted to StateProbe instances."""
        data = _full_llm_output(
            state={"docker_running": {"probe": "docker.running", "cache_ttl": 30}},
        )
        func = compiler._parse_function(data, "test")
        assert "docker_running" in func.state
        assert isinstance(func.state["docker_running"], StateProbe)
        assert func.state["docker_running"].probe == "docker.running"
        assert func.state["docker_running"].cache_ttl == 30

    def test_empty_state_results_in_empty_dict(self, compiler):
        """Missing state key results in an empty dict."""
        data = _full_llm_output()
        # state key not present at all
        data.pop("state", None)
        func = compiler._parse_function(data, "test")
        assert func.state == {}

    def test_defaults_for_name_and_description(self, compiler):
        """Missing name defaults to 'unnamed-function'; description to ''."""
        data = {"actions": [{"capability": "notify.send"}]}
        func = compiler._parse_function(data, "test")
        assert func.name == "unnamed-function"
        assert func.description == ""

    def test_tags_default_to_empty_tuple(self, compiler):
        """Missing tags default to empty tuple."""
        data = _full_llm_output()
        del data["tags"]
        func = compiler._parse_function(data, "test")
        assert func.tags == ()


# =============================================================================
# TestValidateCapabilities -- known/unknown branches
# =============================================================================


class TestValidateCapabilities:
    """Tests for _validate_capabilities."""

    def test_known_capabilities_no_warning(self):
        """No warning when all capabilities are found in registry."""
        registry = _make_mock_registry()
        registry.get.return_value = MagicMock()  # found
        comp = ReactiveFunctionCompiler(llm=_make_mock_llm(), registry=registry)

        func = MagicMock(spec=ReactiveFunction)
        func.actions = (
            ActionSpec(capability="docker.prune", input={}),
            ActionSpec(capability="notify.send", input={}),
        )

        with patch("elle.reactive.compiler.logger") as mock_logger:
            comp._validate_capabilities(func)
            mock_logger.warning.assert_not_called()
        assert registry.get.call_count == 2

    def test_unknown_capability_logs_warning_without_raising(self):
        """Unknown capability logs a warning but does not raise."""
        registry = _make_mock_registry()
        registry.get.return_value = None  # not found
        comp = ReactiveFunctionCompiler(llm=_make_mock_llm(), registry=registry)

        func = MagicMock(spec=ReactiveFunction)
        func.actions = (ActionSpec(capability="nonexistent.cap", input={}),)

        with patch("elle.reactive.compiler.logger") as mock_logger:
            comp._validate_capabilities(func)
            mock_logger.warning.assert_called_once()
            assert "nonexistent.cap" in mock_logger.warning.call_args[0][0]


# =============================================================================
# TestSuggestImprovements -- all five suggestion checks
# =============================================================================


class TestSuggestImprovements:
    """Tests for suggest_improvements covering each check."""

    def test_suggest_missing_description(self, compiler):
        """Empty description triggers a suggestion."""
        func = ReactiveFunction(
            name="test",
            trigger={"type": "manual"},
            actions=({"capability": "notify.send"},),
        )
        suggestions = compiler.suggest_improvements(func)
        assert any("description" in s.lower() for s in suggestions)

    def test_suggest_high_frequency_1s(self, compiler):
        """max_frequency of 1s triggers frequency suggestion."""
        func = ReactiveFunction(
            name="test",
            description="Test",
            trigger={"type": "manual"},
            actions=({"capability": "notify.send"},),
            policy={"max_frequency": "1s"},
        )
        suggestions = compiler.suggest_improvements(func)
        assert any("frequency" in s.lower() for s in suggestions)

    def test_suggest_high_frequency_5s(self, compiler):
        """max_frequency of 5s triggers frequency suggestion."""
        func = ReactiveFunction(
            name="test",
            description="Test",
            trigger={"type": "manual"},
            actions=({"capability": "notify.send"},),
            policy={"max_frequency": "5s"},
        )
        suggestions = compiler.suggest_improvements(func)
        assert any("frequency" in s.lower() for s in suggestions)

    def test_suggest_high_frequency_10s(self, compiler):
        """max_frequency of 10s triggers frequency suggestion."""
        func = ReactiveFunction(
            name="test",
            description="Test",
            trigger={"type": "manual"},
            actions=({"capability": "notify.send"},),
            policy={"max_frequency": "10s"},
        )
        suggestions = compiler.suggest_improvements(func)
        assert any("frequency" in s.lower() for s in suggestions)

    def test_no_frequency_suggestion_for_normal_interval(self, compiler):
        """max_frequency of 5m does not trigger frequency suggestion."""
        func = ReactiveFunction(
            name="test",
            description="Test",
            trigger={"type": "manual"},
            actions=({"capability": "notify.send"},),
            policy={"max_frequency": "5m"},
        )
        suggestions = compiler.suggest_improvements(func)
        assert not any("frequency" in s.lower() for s in suggestions)

    def test_suggest_confirmation_for_dangerous_actions(self, compiler):
        """Dangerous capability without require_confirmation triggers suggestion."""
        func = ReactiveFunction(
            name="test",
            description="Test",
            trigger={"type": "manual"},
            actions=({"capability": "file.delete", "input": {"path": "/tmp/x"}},),
            policy={"require_confirmation": False},
        )
        suggestions = compiler.suggest_improvements(func)
        assert any("confirmation" in s.lower() for s in suggestions)

    def test_no_confirmation_suggestion_when_enabled(self, compiler):
        """Dangerous capability with require_confirmation=True has no suggestion."""
        func = ReactiveFunction(
            name="test",
            description="Test",
            trigger={"type": "manual"},
            actions=({"capability": "config.edit", "input": {}},),
            policy={"require_confirmation": True},
        )
        suggestions = compiler.suggest_improvements(func)
        assert not any("confirmation" in s.lower() for s in suggestions)

    def test_suggest_condition_for_event_trigger(self, compiler):
        """Event trigger without condition triggers suggestion."""
        func = ReactiveFunction(
            name="test",
            description="Test",
            trigger={"type": "event", "event": {"category": "disk"}},
            actions=({"capability": "notify.send"},),
        )
        suggestions = compiler.suggest_improvements(func)
        assert any("condition" in s.lower() for s in suggestions)

    def test_no_condition_suggestion_for_manual_trigger(self, compiler):
        """Manual trigger without condition does not trigger suggestion."""
        func = ReactiveFunction(
            name="test",
            description="Test",
            trigger={"type": "manual"},
            actions=({"capability": "notify.send"},),
        )
        suggestions = compiler.suggest_improvements(func)
        assert not any("condition" in s.lower() for s in suggestions)

    def test_suggest_split_for_many_actions(self, compiler):
        """More than 5 actions triggers a split suggestion."""
        func = ReactiveFunction(
            name="test",
            description="Test",
            trigger={"type": "manual"},
            actions=tuple({"capability": f"action.{i}"} for i in range(6)),
        )
        suggestions = compiler.suggest_improvements(func)
        assert any("split" in s.lower() for s in suggestions)

    def test_well_formed_function_no_suggestions(self, compiler):
        """Well-formed function with good practices yields no suggestions."""
        func = ReactiveFunction(
            name="test",
            description="Good description",
            trigger={"type": "manual"},
            actions=({"capability": "notify.send"},),
            policy={"max_frequency": "5m", "require_confirmation": False},
        )
        suggestions = compiler.suggest_improvements(func)
        assert suggestions == []


# =============================================================================
# TestCompileIntegration -- compile() async method paths
# =============================================================================


class TestCompileIntegration:
    """Integration tests for the compile() async method."""

    @pytest.mark.asyncio
    async def test_compile_success_full_output(self, compiler, mock_llm):
        """Successful compile returns a fully populated ReactiveFunction."""
        mock_llm.generate_json.return_value = _full_llm_output()

        result = await compiler.compile(
            "clean docker when disk > 90%",
            validate_capabilities=False,
        )

        assert isinstance(result, ReactiveFunction)
        assert result.name == "disk-cleanup"
        assert result.source_prompt == "clean docker when disk > 90%"
        assert len(result.actions) == 2
        assert result.tags == ("disk", "docker")

    @pytest.mark.asyncio
    async def test_compile_llm_failure_raises_compilation_error(self, compiler, mock_llm):
        """LLM exception is wrapped in CompilationError."""
        mock_llm.generate_json.side_effect = RuntimeError("Ollama connection refused")

        with pytest.raises(CompilationError, match="Failed to generate function"):
            await compiler.compile("test prompt")

    @pytest.mark.asyncio
    async def test_compile_parse_failure_raises_compilation_error(self, compiler, mock_llm):
        """Malformed LLM output (empty actions) raises CompilationError."""
        mock_llm.generate_json.return_value = {"actions": []}

        with pytest.raises(CompilationError, match="Failed to parse function"):
            await compiler.compile("test prompt")

    @pytest.mark.asyncio
    async def test_compile_validates_capabilities_by_default(self, mock_llm):
        """Default compile validates capabilities via registry.get()."""
        mock_llm.generate_json.return_value = _full_llm_output()
        registry = _make_mock_registry()
        comp = ReactiveFunctionCompiler(llm=mock_llm, registry=registry)

        await comp.compile("clean disk")

        # get() called once per action (2 actions in _full_llm_output)
        assert registry.get.call_count == 2

    @pytest.mark.asyncio
    async def test_compile_skip_capability_validation(self, mock_llm):
        """validate_capabilities=False skips registry.get() calls entirely."""
        output = _full_llm_output()
        output["actions"] = [{"capability": "nonexistent.cap", "input": {}}]
        mock_llm.generate_json.return_value = output
        registry = _make_mock_registry()
        registry.get.return_value = None
        comp = ReactiveFunctionCompiler(llm=mock_llm, registry=registry)

        func = await comp.compile("test", validate_capabilities=False)
        assert func.actions[0].capability == "nonexistent.cap"
        registry.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_compile_system_prompt_includes_capabilities(self, mock_llm):
        """The system prompt sent to LLM embeds the capabilities context."""
        caps = [_mock_capability_spec("file.read", "Read a file")]
        registry = _make_mock_registry(capabilities=caps)
        mock_llm.generate_json.return_value = _full_llm_output()
        comp = ReactiveFunctionCompiler(llm=mock_llm, registry=registry)

        await comp.compile("Read my config", validate_capabilities=False)

        call_kwargs = mock_llm.generate_json.call_args
        system_arg = call_kwargs.kwargs.get("system", "")
        assert "file.read" in system_arg


# =============================================================================
# TestSingleton -- get_compiler / reset_compiler
# =============================================================================


class TestSingleton:
    """Tests for singleton pattern."""

    def teardown_method(self):
        reset_compiler()

    def test_get_compiler_returns_same_instance(self):
        """get_compiler returns the same singleton on repeated calls."""
        reset_compiler()
        c1 = get_compiler()
        c2 = get_compiler()
        assert c1 is c2

    def test_reset_compiler_clears_instance(self):
        """reset_compiler makes the next get_compiler return a fresh instance."""
        reset_compiler()
        c1 = get_compiler()
        reset_compiler()
        c2 = get_compiler()
        assert c1 is not c2


# =============================================================================
# TestCompileReactiveFunctionConvenience
# =============================================================================


class TestCompileConvenience:
    """Tests for the module-level compile_reactive_function convenience function."""

    def teardown_method(self):
        reset_compiler()

    @pytest.mark.asyncio
    async def test_convenience_delegates_to_singleton(self):
        """compile_reactive_function uses get_compiler().compile()."""
        reset_compiler()
        llm = _make_mock_llm(return_value=_full_llm_output())
        registry = _make_mock_registry()
        with patch(
            "elle.reactive.compiler.get_compiler",
            return_value=ReactiveFunctionCompiler(llm=llm, registry=registry),
        ):
            func = await compile_reactive_function(
                "Clean docker on disk full",
                validate_capabilities=False,
            )
        assert isinstance(func, ReactiveFunction)
        assert func.name == "disk-cleanup"


# =============================================================================
# TestExceptions
# =============================================================================


class TestExceptions:
    """Tests for custom exception classes."""

    def test_compilation_error_is_exception(self):
        """CompilationError inherits from Exception."""
        err = CompilationError("bad compile")
        assert isinstance(err, Exception)
        assert str(err) == "bad compile"

    def test_validation_error_is_exception(self):
        """ValidationError inherits from Exception."""
        err = ValidationError("invalid function")
        assert isinstance(err, Exception)
        assert str(err) == "invalid function"


# =============================================================================
# TestSystemPrompt -- template verification
# =============================================================================


class TestSystemPrompt:
    """Tests for the COMPILER_SYSTEM_PROMPT template string."""

    def test_prompt_has_capabilities_placeholder(self):
        """The system prompt contains the {capabilities_context} placeholder."""
        assert "{capabilities_context}" in COMPILER_SYSTEM_PROMPT

    def test_prompt_formats_without_error(self):
        """The prompt can be formatted and the placeholder is replaced."""
        formatted = COMPILER_SYSTEM_PROMPT.format(capabilities_context="- docker.prune: Prune images")
        assert "docker.prune: Prune images" in formatted
        assert "{capabilities_context}" not in formatted
