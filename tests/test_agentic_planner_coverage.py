"""Tests for elle.cli.agentic.planner - targeting uncovered lines.

Covers:
  - Lines 248-253: LLM planning branch in plan() when use_llm_planning=True
                    and intent.is_mixed (success + exception paths)
  - Line 463:      _plan_with_llm() early return when LLM unavailable
                    (exercises via plan() integration path)
  - Line 531:      _plan_with_llm() successful return of parsed plan
  - Lines 567-568: JSON decode fallback with regex that also fails
  - Lines 589-590: Individual call parse error in _parse_llm_plan()
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.agentic.models import (
    ActionRequest,
    ActionType,
    AgenticIntent,
    InformationNeed,
)
from elle.cli.agentic.planner import CapabilityPlanner


# =============================================================================
# Helpers
# =============================================================================


def _make_need(**kwargs: Any) -> InformationNeed:
    defaults: dict[str, Any] = {
        "category": "service",
        "target": "nginx",
        "aspects": ("status",),
    }
    defaults.update(kwargs)
    return InformationNeed(**defaults)


def _make_action(**kwargs: Any) -> ActionRequest:
    defaults: dict[str, Any] = {
        "action_type": ActionType.RESTART,
        "target": "nginx",
        "domain": "service",
    }
    defaults.update(kwargs)
    return ActionRequest(**defaults)


def _make_mixed_intent(**kwargs: Any) -> AgenticIntent:
    """Create an intent that has both info needs and actions (is_mixed=True)."""
    defaults: dict[str, Any] = {
        "information_needs": (_make_need(),),
        "action_requests": (_make_action(),),
        "goal_summary": "check and restart nginx",
        "confidence": 0.9,
    }
    defaults.update(kwargs)
    intent = AgenticIntent(**defaults)
    assert intent.is_mixed is True
    return intent


# =============================================================================
# Lines 248-253: LLM planning branch in plan()
# =============================================================================


class TestPlanLlmBranch:
    """Cover the LLM planning branch inside plan() (lines 247-253)."""

    async def test_llm_planning_replaces_rule_calls(self) -> None:
        """When use_llm_planning=True and intent.is_mixed, successful LLM
        plan replaces the rule-based calls (lines 248-251)."""
        planner = CapabilityPlanner(use_llm_planning=True)
        intent = _make_mixed_intent()

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        llm_response_json = json.dumps({
            "calls": [
                {
                    "capability": "service.status",
                    "args": {"service": "nginx"},
                    "purpose": "Check status first",
                    "depends_on": [],
                },
                {
                    "capability": "service.restart",
                    "args": {"service": "nginx"},
                    "purpose": "Restart if needed",
                    "depends_on": [0],
                },
            ]
        })
        mock_llm.generate.return_value = MagicMock(content=llm_response_json)

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            plan = await planner.plan(intent, user_input="check and restart nginx")

        # LLM plan was used, verify it contains the expected calls
        caps = [c.capability for c in plan.all_calls]
        assert "service.status" in caps
        assert "service.restart" in caps
        mock_llm.generate.assert_called_once()

    async def test_llm_planning_exception_falls_back_to_rules(self) -> None:
        """When _plan_with_llm raises an exception that escapes its own
        try/except, the outer except in plan() (lines 252-253) catches
        it and falls back to rule-based planning."""
        from unittest.mock import AsyncMock

        planner = CapabilityPlanner(use_llm_planning=True)
        intent = _make_mixed_intent()

        # Patch _plan_with_llm directly so the exception escapes
        # the inner try/except in _plan_with_llm and hits the outer
        # try/except in plan() at lines 252-253.
        planner._plan_with_llm = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("unexpected crash")
        )

        plan = await planner.plan(intent, user_input="check and restart nginx")

        # Should still get a valid plan from the rule-based fallback
        assert plan.total_calls >= 1

    async def test_llm_planning_returns_none_keeps_rules(self) -> None:
        """When _plan_with_llm returns None (LLM unavailable), rule-based
        calls are kept (the if llm_calls: branch is False)."""
        planner = CapabilityPlanner(use_llm_planning=True)
        intent = _make_mixed_intent()

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = False

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            plan = await planner.plan(intent, user_input="check and restart nginx")

        # Rule-based plan should still be present
        assert plan.total_calls >= 1

    async def test_llm_planning_skipped_when_not_mixed(self) -> None:
        """When intent is not mixed, LLM planning is never called even
        if use_llm_planning=True."""
        planner = CapabilityPlanner(use_llm_planning=True)
        intent = AgenticIntent(
            information_needs=(_make_need(),),
            action_requests=(),
            goal_summary="just check status",
            confidence=0.9,
        )
        assert intent.is_mixed is False

        mock_llm = MagicMock()

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            plan = await planner.plan(intent, user_input="check nginx")

        mock_llm.is_available.assert_not_called()


# =============================================================================
# Line 531: _plan_with_llm() successful return
# =============================================================================


class TestPlanWithLlmSuccess:
    """Cover the successful return path in _plan_with_llm (line 531)."""

    async def test_successful_llm_plan(self) -> None:
        """LLM returns valid JSON -> parsed calls are returned."""
        planner = CapabilityPlanner(use_llm_planning=True)
        intent = _make_mixed_intent()

        valid_json = json.dumps({
            "calls": [
                {
                    "capability": "service.status",
                    "args": {"service": "nginx"},
                    "purpose": "Check nginx status",
                },
            ]
        })

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate.return_value = MagicMock(content=valid_json)

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            result = await planner._plan_with_llm(intent, "check nginx")

        assert result is not None
        assert len(result) == 1
        assert result[0].capability == "service.status"


# =============================================================================
# Lines 567-568: JSON decode fallback with regex that also fails
# =============================================================================


class TestBuildParallelGroupsContinue:
    """Cover the 'continue' on line 463 in _build_parallel_groups.

    This is hit when the while loop iterates a second time and encounters
    calls that were already assigned in a previous iteration.
    """

    def test_reverse_dependency_triggers_continue(self) -> None:
        """Call B (index 0) depends on A (index 1, no deps).

        While-loop pass 1:
          - B (index 0): dep (1) not in assigned -> skip
          - A (index 1): no deps -> assigned={1}, group=[A]
        While-loop pass 2:
          - B (index 0): dep (1) is in assigned -> assigned, group=[B]
          - A (index 1): already assigned -> continue (LINE 463)
        """
        from elle.cli.agentic.models import CapabilityCall

        planner = CapabilityPlanner()
        calls = [
            CapabilityCall(capability="b", args={}, purpose="b", depends_on=(1,)),
            CapabilityCall(capability="a", args={}, purpose="a", depends_on=()),
        ]
        groups = planner._build_parallel_groups(calls)

        # Both calls should be placed
        total = sum(len(g.calls) for g in groups)
        assert total == 2
        # Two groups: [A] then [B]
        assert len(groups) == 2
        assert groups[0].calls[0].capability == "a"
        assert groups[1].calls[0].capability == "b"


class TestParseLlmPlanRegexFallback:
    """Cover the regex JSON extraction fallback paths (lines 562-568)."""

    def test_invalid_json_with_regex_match_that_also_fails(self) -> None:
        """Content has braces but the extracted JSON is still invalid,
        triggering the inner JSONDecodeError (lines 567-568)."""
        planner = CapabilityPlanner()
        # This has braces so regex will match, but contents are not valid JSON
        content = "Some text { not: valid json, missing quotes } more text"
        result = planner._parse_llm_plan(content)
        assert result is None

    def test_invalid_json_no_braces(self) -> None:
        """Content has no braces at all -> regex finds nothing -> return None.
        This covers line 570 (else: return None)."""
        planner = CapabilityPlanner()
        content = "no json here at all, just plain text"
        result = planner._parse_llm_plan(content)
        assert result is None


# =============================================================================
# Lines 589-590: Individual call parse error handler
# =============================================================================


class TestParseLlmPlanCallError:
    """Cover the per-call exception handler (lines 589-590)."""

    def test_call_with_bad_depends_on_type(self) -> None:
        """A call entry that causes a Pydantic/type error in CapabilityCall
        construction triggers the except branch (lines 589-590)."""
        planner = CapabilityPlanner()
        content = json.dumps({
            "calls": [
                {
                    "capability": "service.status",
                    "args": {"service": "nginx"},
                    "purpose": "check",
                    "depends_on": "not_a_list",  # Should be a list -> tuple() fails
                },
                {
                    "capability": "service.restart",
                    "args": {"service": "nginx"},
                    "purpose": "restart",
                    "depends_on": [],
                },
            ]
        })
        result = planner._parse_llm_plan(content)
        # The first call fails to parse, but the second should succeed
        assert result is not None
        assert len(result) == 1
        assert result[0].capability == "service.restart"

    def test_all_calls_fail_to_parse(self) -> None:
        """All call entries fail to parse -> returns None (empty list)."""
        planner = CapabilityPlanner()
        content = json.dumps({
            "calls": [
                {
                    "capability": "service.status",
                    "args": {"service": "nginx"},
                    "depends_on": "bad",
                },
            ]
        })
        result = planner._parse_llm_plan(content)
        assert result is None
