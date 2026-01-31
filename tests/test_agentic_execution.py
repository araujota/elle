"""Tests for agent loop execution tracking models.

Covers the Pydantic models in ``elle.cli.agentic.execution``:
- LoopExecution
- ToolCallSummary
- StageTransition (from stages module)
- Serialization round-trips
- Edge cases (long input, unicode)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from elle.cli.agentic.execution import LoopExecution, ToolCallSummary
from elle.cli.agentic.stages import (
    LoopStage,
    StageTransition,
)

# =============================================================================
# Helpers
# =============================================================================

_NOW = datetime.now(timezone.utc)


def _make_execution(**overrides) -> LoopExecution:
    """Build a LoopExecution with sensible defaults, allowing overrides."""
    defaults = {
        "started_at": _NOW,
        "user_input": "check disk usage",
        "classified_intent": "system_question",
    }
    defaults.update(overrides)
    return LoopExecution(**defaults)


def _make_transition(**overrides) -> StageTransition:
    """Build a StageTransition with sensible defaults."""
    defaults = {
        "from_stage": LoopStage.OBSERVE,
        "to_stage": LoopStage.CLASSIFY,
        "timestamp": _NOW,
        "reason": "initial classification",
    }
    defaults.update(overrides)
    return StageTransition(**defaults)


def _make_tool_call(**overrides) -> ToolCallSummary:
    """Build a ToolCallSummary with sensible defaults."""
    defaults = {
        "tool": "search_man_vault",
        "reason": "look up systemd docs",
        "result_summary": "found 3 man pages",
    }
    defaults.update(overrides)
    return ToolCallSummary(**defaults)


# =============================================================================
# TestLoopExecution
# =============================================================================


class TestLoopExecution:
    """Tests for the LoopExecution model."""

    def test_creation_with_defaults(self) -> None:
        """LoopExecution with minimal required fields has correct defaults."""
        ex = _make_execution()

        assert ex.started_at == _NOW
        assert ex.user_input == "check disk usage"
        assert ex.classified_intent == "system_question"
        # Verify defaults
        assert ex.completed_at is None
        assert ex.current_stage == LoopStage.OBSERVE
        assert ex.stage_transitions == ()
        assert ex.hypotheses == ()
        assert ex.capabilities_evaluated == ()
        assert ex.capabilities_executed == ()
        assert ex.tool_calls == ()
        assert ex.total_tool_calls == 0
        assert ex.success is True
        assert ex.response == ""
        assert ex.error is None
        assert ex.incident_id is None
        assert ex.iterations == 1
        assert ex.total_duration_ms == 0
        assert ex.prompt_tokens is None
        assert ex.completion_tokens is None
        assert ex.classification_confidence == 0.0

    def test_execution_id_uniqueness(self) -> None:
        """Two LoopExecution instances receive different UUIDs."""
        ex1 = _make_execution()
        ex2 = _make_execution()

        assert ex1.execution_id != ex2.execution_id
        # Basic UUID-like format check (8-4-4-4-12 hex groups)
        assert len(ex1.execution_id) == 36
        assert ex1.execution_id.count("-") == 4

    def test_stage_transitions_recorded(self) -> None:
        """Stage transitions are stored as a tuple of StageTransition."""
        t1 = _make_transition()
        t2 = _make_transition(
            from_stage=LoopStage.CLASSIFY,
            to_stage=LoopStage.RETRIEVE,
            reason="classification complete",
        )
        ex = _make_execution(stage_transitions=(t1, t2))

        assert len(ex.stage_transitions) == 2
        assert ex.stage_transitions[0].from_stage == LoopStage.OBSERVE
        assert ex.stage_transitions[0].to_stage == LoopStage.CLASSIFY
        assert ex.stage_transitions[1].from_stage == LoopStage.CLASSIFY
        assert ex.stage_transitions[1].to_stage == LoopStage.RETRIEVE

    def test_tool_call_summary_capture(self) -> None:
        """tool_calls tuple is populated with ToolCallSummary instances."""
        tc1 = _make_tool_call()
        tc2 = _make_tool_call(
            tool="execute_capability",
            reason="restart nginx",
            result_summary="service restarted",
        )
        ex = _make_execution(tool_calls=(tc1, tc2), total_tool_calls=2)

        assert len(ex.tool_calls) == 2
        assert ex.tool_calls[0].tool == "search_man_vault"
        assert ex.tool_calls[1].tool == "execute_capability"
        assert ex.total_tool_calls == 2

    def test_completion_tracking(self) -> None:
        """completed_at and success are set when execution finishes."""
        completed = datetime.now(timezone.utc)
        ex = _make_execution(
            completed_at=completed,
            success=True,
            response="Disk usage is 42%.",
        )

        assert ex.completed_at == completed
        assert ex.success is True
        assert ex.response == "Disk usage is 42%."

    def test_total_tokens_property(self) -> None:
        """total_tokens returns prompt_tokens + completion_tokens."""
        ex = _make_execution(prompt_tokens=500, completion_tokens=200)
        assert ex.total_tokens == 700

        # When one side is None, total_tokens returns None
        ex_partial = _make_execution(prompt_tokens=500, completion_tokens=None)
        assert ex_partial.total_tokens is None

        # When both are None, total_tokens returns None
        ex_none = _make_execution()
        assert ex_none.total_tokens is None


# =============================================================================
# TestToolCallSummary
# =============================================================================


class TestToolCallSummary:
    """Tests for the ToolCallSummary model."""

    def test_success_recording(self) -> None:
        """ToolCallSummary records a successful call."""
        tc = _make_tool_call(success=True)

        assert tc.success is True
        assert tc.tool == "search_man_vault"
        assert tc.reason == "look up systemd docs"

    def test_failure_recording(self) -> None:
        """ToolCallSummary records a failed call."""
        tc = _make_tool_call(
            success=False,
            result_summary="capability not found",
        )

        assert tc.success is False
        assert tc.result_summary == "capability not found"

    def test_duration_tracking(self) -> None:
        """duration_ms is populated and stored."""
        tc = _make_tool_call(duration_ms=1234)

        assert tc.duration_ms == 1234

    def test_result_summary_content(self) -> None:
        """result_summary string is captured correctly."""
        summary = "Found 5 matching incidents from the last 24 hours"
        tc = _make_tool_call(result_summary=summary)

        assert tc.result_summary == summary


# =============================================================================
# TestStageTransitions
# =============================================================================


class TestStageTransitions:
    """Tests for the StageTransition model."""

    def test_valid_transition(self) -> None:
        """StageTransition from OBSERVE to CLASSIFY is valid."""
        t = _make_transition()

        assert t.from_stage == LoopStage.OBSERVE
        assert t.to_stage == LoopStage.CLASSIFY

    def test_transition_timestamps(self) -> None:
        """Timestamp field is present and stores the datetime."""
        t = _make_transition()

        assert t.timestamp == _NOW
        assert isinstance(t.timestamp, datetime)

    def test_transition_with_reason(self) -> None:
        """Reason string is captured on a transition."""
        reason = "user intent classified as system_task"
        t = _make_transition(reason=reason)

        assert t.reason == reason

    def test_transition_duration(self) -> None:
        """duration_ms tracks time spent in the source stage."""
        t = _make_transition(duration_ms=350)

        assert t.duration_ms == 350

    def test_transition_with_data(self) -> None:
        """Data dict is attached to the transition."""
        extra = {"intent": "system_question", "confidence": 0.95}
        t = _make_transition(data=extra)

        assert t.data == extra
        assert t.data["confidence"] == 0.95


# =============================================================================
# TestExecutionSerialization
# =============================================================================


class TestExecutionSerialization:
    """Tests for serialization and immutability of LoopExecution."""

    def test_model_to_dict(self) -> None:
        """LoopExecution.model_dump() produces a valid dict."""
        tc = _make_tool_call()
        transition = _make_transition()
        ex = _make_execution(
            tool_calls=(tc,),
            stage_transitions=(transition,),
            success=True,
            response="all good",
            total_tool_calls=1,
        )
        data = ex.model_dump()

        assert isinstance(data, dict)
        assert data["user_input"] == "check disk usage"
        assert data["classified_intent"] == "system_question"
        assert data["success"] is True
        assert data["response"] == "all good"
        assert len(data["tool_calls"]) == 1
        assert data["tool_calls"][0]["tool"] == "search_man_vault"
        assert len(data["stage_transitions"]) == 1
        assert data["stage_transitions"][0]["from_stage"] == "observe"

    def test_frozen_model_immutability(self) -> None:
        """Assigning to a field on a frozen model raises an error."""
        ex = _make_execution()

        with pytest.raises(ValidationError):
            ex.user_input = "something else"

        tc = _make_tool_call()
        with pytest.raises(ValidationError):
            tc.tool = "other_tool"

        t = _make_transition()
        with pytest.raises(ValidationError):
            t.reason = "changed reason"

    def test_optional_fields(self) -> None:
        """Optional fields default to None when not provided."""
        ex = _make_execution()

        assert ex.completed_at is None
        assert ex.error is None
        assert ex.incident_id is None
        assert ex.prompt_tokens is None
        assert ex.completion_tokens is None
        assert ex.chosen_hypothesis_id is None


# =============================================================================
# TestEdgeCases
# =============================================================================


class TestEdgeCases:
    """Edge-case tests for the execution models."""

    def test_very_long_user_input(self) -> None:
        """A 10 000-character user_input string is accepted."""
        long_input = "x" * 10_000
        ex = _make_execution(user_input=long_input)

        assert len(ex.user_input) == 10_000
        assert ex.user_input == long_input

    def test_unicode_in_classified_intent(self) -> None:
        """Unicode characters in classified_intent are stored correctly."""
        intent = "systeme_question_\u00e9\u00e8\u00ea_\u4f60\u597d_\U0001f680"
        ex = _make_execution(classified_intent=intent)

        assert ex.classified_intent == intent
        assert "\u00e9" in ex.classified_intent
        assert "\u4f60\u597d" in ex.classified_intent
