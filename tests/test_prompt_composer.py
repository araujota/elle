"""Tests for PromptComposer and prompt segments."""

import pytest

from elle.rag.prompts import (
    BASE_SYSTEM_PROMPT,
    PromptComposer,
    PromptSegment,
    get_composer,
)
from elle.rag.prompts.segments import (
    CAPABILITY_DISCOVERY_SEGMENT,
    DEFAULT_SEGMENTS,
    FIXIT_SEGMENT,
    INCIDENT_ANALYSIS_SEGMENT,
    REACTIVE_FUNCTION_SEGMENT,
)


class TestPromptSegment:
    """Tests for PromptSegment model."""

    def test_basic_segment(self) -> None:
        """Test creating a basic segment."""
        segment = PromptSegment(
            id="test_segment",
            intent="system_task",
            content="Test content",
        )

        assert segment.id == "test_segment"
        assert segment.intent == "system_task"
        assert segment.content == "Test content"
        assert segment.priority == 100  # default
        assert segment.requires_context == ()  # default
        assert segment.enabled  # default

    def test_segment_with_all_fields(self) -> None:
        """Test segment with all fields specified."""
        segment = PromptSegment(
            id="full_segment",
            intent="system_question",
            priority=50,
            content="Content with {variable}",
            requires_context=("variable",),
            enabled=False,
        )

        assert segment.priority == 50
        assert segment.requires_context == ("variable",)
        assert not segment.enabled

    def test_segment_is_frozen(self) -> None:
        """Test that segment is immutable."""
        segment = PromptSegment(
            id="frozen_test",
            intent="system_task",
            content="Test",
        )

        with pytest.raises(Exception):
            segment.content = "Modified"


class TestPromptComposer:
    """Tests for PromptComposer."""

    def test_initialization(self) -> None:
        """Test composer initialization."""
        composer = PromptComposer("Base prompt here")
        assert composer.base_prompt == "Base prompt here"

    def test_register_segment(self) -> None:
        """Test registering a segment."""
        composer = PromptComposer("Base")
        segment = PromptSegment(
            id="test",
            intent="system_task",
            content="Test content",
        )

        composer.register(segment)

        retrieved = composer.get_segment("test")
        assert retrieved is not None
        assert retrieved.id == "test"

    def test_unregister_segment(self) -> None:
        """Test unregistering a segment."""
        composer = PromptComposer("Base")
        segment = PromptSegment(
            id="test",
            intent="system_task",
            content="Test content",
        )

        composer.register(segment)
        assert composer.get_segment("test") is not None

        result = composer.unregister("test")
        assert result is True
        assert composer.get_segment("test") is None

    def test_unregister_nonexistent(self) -> None:
        """Test unregistering non-existent segment."""
        composer = PromptComposer("Base")
        result = composer.unregister("nonexistent")
        assert result is False

    def test_list_segments_all(self) -> None:
        """Test listing all segments."""
        composer = PromptComposer("Base")

        composer.register(PromptSegment(id="a", intent="task", content="A"))
        composer.register(PromptSegment(id="b", intent="question", content="B"))

        segments = composer.list_segments()
        assert len(segments) == 2

    def test_list_segments_by_intent(self) -> None:
        """Test listing segments filtered by intent."""
        composer = PromptComposer("Base")

        composer.register(PromptSegment(id="a", intent="task", content="A"))
        composer.register(PromptSegment(id="b", intent="question", content="B"))
        composer.register(PromptSegment(id="c", intent="task", content="C"))

        task_segments = composer.list_segments(intent="task")
        assert len(task_segments) == 2
        assert all(s.intent == "task" for s in task_segments)

    def test_list_segments_sorted_by_priority(self) -> None:
        """Test that listed segments are sorted by priority."""
        composer = PromptComposer("Base")

        composer.register(PromptSegment(id="high", intent="task", priority=100, content="H"))
        composer.register(PromptSegment(id="low", intent="task", priority=10, content="L"))
        composer.register(PromptSegment(id="mid", intent="task", priority=50, content="M"))

        segments = composer.list_segments(intent="task")
        priorities = [s.priority for s in segments]
        assert priorities == [10, 50, 100]


class TestPromptComposerCompose:
    """Tests for compose method."""

    def test_compose_base_only(self) -> None:
        """Test composing with no matching segments."""
        composer = PromptComposer("Base prompt")

        result = composer.compose("nonexistent_intent")
        assert result == "Base prompt"

    def test_compose_with_matching_segment(self) -> None:
        """Test composing with matching segments."""
        composer = PromptComposer("Base prompt")
        composer.register(
            PromptSegment(
                id="task_guide",
                intent="system_task",
                content="Task-specific guidance",
            )
        )

        result = composer.compose("system_task")

        assert "Base prompt" in result
        assert "Task-specific guidance" in result

    def test_compose_multiple_segments(self) -> None:
        """Test composing with multiple matching segments."""
        composer = PromptComposer("Base")

        composer.register(PromptSegment(id="a", intent="task", priority=10, content="First"))
        composer.register(PromptSegment(id="b", intent="task", priority=20, content="Second"))

        result = composer.compose("task")

        # Should appear in priority order
        first_pos = result.find("First")
        second_pos = result.find("Second")
        assert first_pos < second_pos

    def test_compose_ignores_disabled_segments(self) -> None:
        """Test that disabled segments are not included."""
        composer = PromptComposer("Base")

        composer.register(
            PromptSegment(
                id="disabled",
                intent="task",
                content="Should not appear",
                enabled=False,
            )
        )

        result = composer.compose("task")
        assert "Should not appear" not in result

    def test_compose_with_context_substitution(self) -> None:
        """Test context variable substitution."""
        composer = PromptComposer("Base")

        composer.register(
            PromptSegment(
                id="contextual",
                intent="task",
                content="Working with {service_name} on {host}",
                requires_context=("service_name", "host"),
            )
        )

        result = composer.compose(
            "task",
            context={
                "service_name": "nginx",
                "host": "localhost",
            },
        )

        assert "Working with nginx on localhost" in result

    def test_compose_with_missing_context(self) -> None:
        """Test handling of missing context keys."""
        composer = PromptComposer("Base")

        composer.register(
            PromptSegment(
                id="contextual",
                intent="task",
                content="Value: {missing_key}",
                requires_context=("missing_key",),
            )
        )

        # Should not crash, uses empty string for missing keys
        result = composer.compose("task", context={})
        assert "Value: " in result


class TestPromptComposerComposeWithSegments:
    """Tests for compose_with_segments method."""

    def test_compose_specific_segments(self) -> None:
        """Test composing with specific segment IDs."""
        composer = PromptComposer("Base")

        composer.register(PromptSegment(id="a", intent="task", content="A"))
        composer.register(PromptSegment(id="b", intent="other", content="B"))
        composer.register(PromptSegment(id="c", intent="task", content="C"))

        result = composer.compose_with_segments("any", ["b"])

        assert "Base" in result
        assert "B" in result
        assert "A" not in result
        assert "C" not in result


class TestDefaultSegments:
    """Tests for default prompt segments."""

    def test_default_segments_exist(self) -> None:
        """Test that default segments are defined."""
        assert len(DEFAULT_SEGMENTS) > 0

    def test_default_segments_have_unique_ids(self) -> None:
        """Test that all default segments have unique IDs."""
        ids = [s.id for s in DEFAULT_SEGMENTS]
        assert len(ids) == len(set(ids))

    def test_reactive_function_segment(self) -> None:
        """Test reactive function segment properties."""
        assert REACTIVE_FUNCTION_SEGMENT.id == "reactive_function"
        assert REACTIVE_FUNCTION_SEGMENT.intent == "system_task"
        assert "trigger" in REACTIVE_FUNCTION_SEGMENT.content.lower()

    def test_capability_discovery_segment(self) -> None:
        """Test capability discovery segment properties."""
        assert CAPABILITY_DISCOVERY_SEGMENT.id == "capability_discovery"
        assert CAPABILITY_DISCOVERY_SEGMENT.intent == "system_question"

    def test_fixit_segment(self) -> None:
        """Test fixit segment properties."""
        assert FIXIT_SEGMENT.id == "fixit"
        assert FIXIT_SEGMENT.intent == "fixit"
        assert "command" in FIXIT_SEGMENT.content.lower()

    def test_incident_analysis_segment(self) -> None:
        """Test incident analysis segment properties."""
        assert INCIDENT_ANALYSIS_SEGMENT.id == "incident_analysis"
        assert INCIDENT_ANALYSIS_SEGMENT.intent == "navigation"


class TestGetComposer:
    """Tests for the composer singleton."""

    def test_get_composer_returns_composer(self) -> None:
        """Test that get_composer returns a PromptComposer."""
        composer = get_composer()
        assert isinstance(composer, PromptComposer)

    def test_get_composer_has_base_prompt(self) -> None:
        """Test that the composer has the base prompt."""
        composer = get_composer()
        assert composer.base_prompt == BASE_SYSTEM_PROMPT

    def test_get_composer_has_default_segments(self) -> None:
        """Test that default segments are registered."""
        composer = get_composer()

        # Check a few expected segments are registered
        assert composer.get_segment("reactive_function") is not None
        assert composer.get_segment("fixit") is not None
        assert composer.get_segment("docker_operations") is not None


class TestBaseSystemPrompt:
    """Tests for the base system prompt."""

    def test_base_prompt_exists(self) -> None:
        """Test that BASE_SYSTEM_PROMPT is defined."""
        assert BASE_SYSTEM_PROMPT is not None
        assert len(BASE_SYSTEM_PROMPT) > 0

    def test_base_prompt_mentions_elle(self) -> None:
        """Test that the base prompt mentions ELLE."""
        assert "ELLE" in BASE_SYSTEM_PROMPT

    def test_base_prompt_mentions_linux(self) -> None:
        """Test that the base prompt mentions Linux."""
        assert "Linux" in BASE_SYSTEM_PROMPT
