"""Tests for agent_commands.py - agent loop introspection commands."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_text_unchanged(self):
        from elle.cli.agent_commands import _truncate

        assert _truncate("hello", 10) == "hello"

    def test_exact_length_unchanged(self):
        from elle.cli.agent_commands import _truncate

        assert _truncate("hello", 5) == "hello"

    def test_long_text_truncated(self):
        from elle.cli.agent_commands import _truncate

        result = _truncate("hello world this is long", 10)
        assert result.endswith("...")
        assert len(result) == 10

    def test_newlines_replaced(self):
        from elle.cli.agent_commands import _truncate

        result = _truncate("line1\nline2\nline3", 50)
        assert "\n" not in result

    def test_whitespace_stripped(self):
        from elle.cli.agent_commands import _truncate

        result = _truncate("  hello  ", 50)
        assert result == "hello"


# ---------------------------------------------------------------------------
# _format_time
# ---------------------------------------------------------------------------


class TestFormatTime:
    def test_just_now(self):
        from elle.cli.agent_commands import _format_time

        now = datetime.now(timezone.utc).isoformat()
        result = _format_time(now)
        assert result == "just now"

    def test_minutes_ago(self):
        from elle.cli.agent_commands import _format_time

        t = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        result = _format_time(t)
        assert "m ago" in result

    def test_hours_ago(self):
        from elle.cli.agent_commands import _format_time

        t = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        result = _format_time(t)
        assert "h ago" in result

    def test_yesterday(self):
        from elle.cli.agent_commands import _format_time

        t = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        result = _format_time(t)
        assert result == "yesterday"

    def test_days_ago(self):
        from elle.cli.agent_commands import _format_time

        t = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
        result = _format_time(t)
        assert "d ago" in result

    def test_old_date(self):
        from elle.cli.agent_commands import _format_time

        result = _format_time("2020-06-15T10:00:00")
        assert "2020-06-15" in result

    def test_invalid_time(self):
        from elle.cli.agent_commands import _format_time

        result = _format_time("not-a-time")
        assert result == "not-a-time"

    def test_empty_string(self):
        from elle.cli.agent_commands import _format_time

        result = _format_time("")
        assert result == "unknown"


# ---------------------------------------------------------------------------
# _handle_help
# ---------------------------------------------------------------------------


class TestHandleHelp:
    def test_help_contains_commands(self):
        from elle.cli.agent_commands import _handle_help

        help_text = _handle_help()
        assert "agent last" in help_text
        assert "agent inspect" in help_text
        assert "agent stages" in help_text
        assert "agent hypotheses" in help_text
        assert "agent tools" in help_text


# ---------------------------------------------------------------------------
# _handle_stages
# ---------------------------------------------------------------------------


class TestHandleStages:
    @patch("elle.cli.agent_commands.explain_stages", create=True)
    def test_stages_delegates(self, _mock):
        # The function lazily imports explain_stages
        with patch.dict("sys.modules", {}):
            mock_stages = MagicMock()
            mock_stages.explain_stages.return_value = "STAGE_INFO"
            with patch.dict("sys.modules", {"elle.cli.agentic.stages": mock_stages}):
                from elle.cli.agent_commands import _handle_stages

                result = _handle_stages()
                assert result == "STAGE_INFO"


# ---------------------------------------------------------------------------
# handle_agent_command (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHandleAgentCommand:
    @patch("elle.cli.agent_commands._handle_last")
    async def test_empty_args_calls_last(self, mock_last):
        from elle.cli.agent_commands import handle_agent_command

        mock_last.return_value = "LAST_RESULTS"
        await handle_agent_command("")
        mock_last.assert_called_once_with("5")

    @patch("elle.cli.agent_commands._handle_last")
    async def test_last_without_number(self, mock_last):
        from elle.cli.agent_commands import handle_agent_command

        mock_last.return_value = "LAST_RESULTS"
        await handle_agent_command("last")
        mock_last.assert_called_once_with("5")

    async def test_help_subcommand(self):
        from elle.cli.agent_commands import handle_agent_command

        result = await handle_agent_command("help")
        assert "agent last" in result

    async def test_unknown_subcommand(self):
        from elle.cli.agent_commands import handle_agent_command

        result = await handle_agent_command("xyz")
        assert "Unknown agent subcommand" in result


# ---------------------------------------------------------------------------
# _handle_last (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHandleLast:
    async def test_invalid_number(self):
        from elle.cli.agent_commands import _handle_last

        result = await _handle_last("abc")
        assert "Invalid number" in result

    async def test_no_executions(self):
        from elle.cli.agent_commands import _handle_last

        mock_exec = MagicMock()
        mock_exec.get_execution_summaries.return_value = []
        with patch.dict("sys.modules", {"elle.cli.agentic.execution": mock_exec}):
            result = await _handle_last("5")
            assert "No agent executions" in result

    async def test_with_executions(self):
        from elle.cli.agent_commands import _handle_last

        summary = {
            "execution_id": "abcd1234efgh5678",
            "user_input": "Why is my CPU hot?",
            "classified_intent": "system_question",
            "success": True,
            "incident_id": "incident-001-abcdef12",
            "total_tool_calls": 3,
            "capabilities_executed": ["service.restart"],
            "started_at": datetime.now().isoformat(),
        }
        mock_exec = MagicMock()
        mock_exec.get_execution_summaries.return_value = [summary]
        with patch.dict("sys.modules", {"elle.cli.agentic.execution": mock_exec}):
            result = await _handle_last("5")
            assert "LAST 1 AGENT EXECUTIONS" in result
            assert "abcd1234" in result

    async def test_exception_handling(self):
        from elle.cli.agent_commands import _handle_last

        mock_exec = MagicMock()
        mock_exec.get_execution_summaries.side_effect = RuntimeError("db err")
        with patch.dict("sys.modules", {"elle.cli.agentic.execution": mock_exec}):
            result = await _handle_last("5")
            assert "Error" in result


# ---------------------------------------------------------------------------
# _handle_inspect (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHandleInspect:
    async def test_no_execution_id(self):
        from elle.cli.agent_commands import _handle_inspect

        result = await _handle_inspect("")
        assert "Usage" in result

    async def test_not_found(self):
        from elle.cli.agent_commands import _handle_inspect

        mock_store = MagicMock()
        mock_store.return_value.get.return_value = None
        mock_store.return_value.get_summary.return_value = []
        mock_exec = MagicMock()
        mock_exec.get_execution_store.return_value = mock_store.return_value
        with patch.dict("sys.modules", {"elle.cli.agentic.execution": mock_exec}):
            result = await _handle_inspect("nonexistent")
            assert "not found" in result

    async def test_exception_handling(self):
        from elle.cli.agent_commands import _handle_inspect

        mock_exec = MagicMock()
        mock_exec.get_execution_store.side_effect = RuntimeError("db err")
        with patch.dict("sys.modules", {"elle.cli.agentic.execution": mock_exec}):
            result = await _handle_inspect("abc123")
            assert "Error" in result


# ---------------------------------------------------------------------------
# _handle_hypotheses (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHandleHypotheses:
    async def test_no_execution_id(self):
        from elle.cli.agent_commands import _handle_hypotheses

        result = await _handle_hypotheses("")
        assert "Usage" in result

    async def test_not_found(self):
        from elle.cli.agent_commands import _handle_hypotheses

        mock_store = MagicMock()
        mock_store.return_value.get.return_value = None
        mock_store.return_value.get_summary.return_value = []
        mock_exec = MagicMock()
        mock_exec.get_execution_store.return_value = mock_store.return_value
        with patch.dict("sys.modules", {"elle.cli.agentic.execution": mock_exec}):
            result = await _handle_hypotheses("nonexistent")
            assert "not found" in result

    async def test_no_hypotheses(self):
        from elle.cli.agent_commands import _handle_hypotheses

        execution = SimpleNamespace(
            execution_id="abc12345",
            hypotheses=[],
        )
        mock_store = MagicMock()
        mock_store.return_value.get.return_value = execution
        mock_exec = MagicMock()
        mock_exec.get_execution_store.return_value = mock_store.return_value
        with patch.dict("sys.modules", {"elle.cli.agentic.execution": mock_exec}):
            result = await _handle_hypotheses("abc12345")
            assert "No hypotheses" in result

    async def test_with_hypotheses(self):
        from elle.cli.agent_commands import _handle_hypotheses

        h = SimpleNamespace(
            hypothesis_id="hyp-001-abcdef12",
            chosen=True,
            rejected=False,
            confidence=0.85,
            text="Restart the nginx service",
            source="incident_vault",
            supporting_evidence=["Previous incident had same symptoms"],
            capability_candidates=["service.restart"],
            rejected_reason=None,
        )
        execution = SimpleNamespace(
            execution_id="abc12345def67890",
            hypotheses=[h],
        )
        mock_store = MagicMock()
        mock_store.return_value.get.return_value = execution
        mock_exec = MagicMock()
        mock_exec.get_execution_store.return_value = mock_store.return_value
        with patch.dict("sys.modules", {"elle.cli.agentic.execution": mock_exec}):
            result = await _handle_hypotheses("abc12345")
            assert "CHOSEN" in result
            assert "85%" in result
            assert "service.restart" in result


# ---------------------------------------------------------------------------
# _handle_tools (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHandleTools:
    async def test_no_execution_id(self):
        from elle.cli.agent_commands import _handle_tools

        result = await _handle_tools("")
        assert "Usage" in result

    async def test_not_found(self):
        from elle.cli.agent_commands import _handle_tools

        mock_store = MagicMock()
        mock_store.return_value.get.return_value = None
        mock_store.return_value.get_summary.return_value = []
        mock_exec = MagicMock()
        mock_exec.get_execution_store.return_value = mock_store.return_value
        with patch.dict("sys.modules", {"elle.cli.agentic.execution": mock_exec}):
            result = await _handle_tools("nonexistent")
            assert "not found" in result

    async def test_no_tool_calls(self):
        from elle.cli.agent_commands import _handle_tools

        execution = SimpleNamespace(
            execution_id="abc12345",
            tool_calls=[],
        )
        mock_store = MagicMock()
        mock_store.return_value.get.return_value = execution
        mock_exec = MagicMock()
        mock_exec.get_execution_store.return_value = mock_store.return_value
        with patch.dict("sys.modules", {"elle.cli.agentic.execution": mock_exec}):
            result = await _handle_tools("abc12345")
            assert "No tool calls" in result

    async def test_with_tool_calls(self):
        from elle.cli.agent_commands import _handle_tools

        tc = SimpleNamespace(
            tool="search_man_vault",
            success=True,
            duration_ms=42,
            reason="Looking up docs",
            result_summary="Found 3 results",
        )
        execution = SimpleNamespace(
            execution_id="abc12345def67890",
            tool_calls=[tc],
        )
        mock_store = MagicMock()
        mock_store.return_value.get.return_value = execution
        mock_exec = MagicMock()
        mock_exec.get_execution_store.return_value = mock_store.return_value
        with patch.dict("sys.modules", {"elle.cli.agentic.execution": mock_exec}):
            result = await _handle_tools("abc12345")
            assert "search_man_vault" in result
            assert "42ms" in result


# ---------------------------------------------------------------------------
# _render_execution_detail
# ---------------------------------------------------------------------------


class TestRenderExecutionDetail:
    def test_minimal_execution(self):
        from elle.cli.agent_commands import _render_execution_detail

        execution = SimpleNamespace(
            execution_id="exec-001",
            started_at=datetime(2024, 3, 15, 10, 0, 0),
            total_duration_ms=150,
            success=True,
            iterations=1,
            user_input="Why is my system slow?",
            classified_intent="system_question",
            classification_confidence=0.92,
            stage_transitions=[],
            hypotheses=[],
            chosen_hypothesis=None,
            capabilities_evaluated=[],
            tool_calls=[],
            incident_id=None,
            response="Your system might be memory constrained.",
            error=None,
            total_tokens=500,
            prompt_tokens=300,
            completion_tokens=200,
        )
        result = _render_execution_detail(execution)
        assert "exec-001" in result
        assert "SUCCESS" in result
        assert "150ms" in result
        assert "system_question" in result
        assert "500" in result

    def test_execution_with_error(self):
        from elle.cli.agent_commands import _render_execution_detail

        execution = SimpleNamespace(
            execution_id="exec-002",
            started_at=datetime(2024, 3, 15, 10, 0, 0),
            total_duration_ms=50,
            success=False,
            iterations=1,
            user_input="crash it",
            classified_intent="system_task",
            classification_confidence=0.5,
            stage_transitions=[],
            hypotheses=[],
            chosen_hypothesis=None,
            capabilities_evaluated=[],
            tool_calls=[],
            incident_id=None,
            response=None,
            error="LLM timeout",
            total_tokens=None,
            prompt_tokens=None,
            completion_tokens=None,
        )
        result = _render_execution_detail(execution)
        assert "FAILED" in result
        assert "LLM timeout" in result

    def test_execution_with_incident(self):
        from elle.cli.agent_commands import _render_execution_detail

        execution = SimpleNamespace(
            execution_id="exec-003",
            started_at=datetime(2024, 3, 15, 10, 0, 0),
            total_duration_ms=200,
            success=True,
            iterations=2,
            user_input="Fix nginx",
            classified_intent="system_task",
            classification_confidence=0.88,
            stage_transitions=[],
            hypotheses=[],
            chosen_hypothesis=None,
            capabilities_evaluated=[],
            tool_calls=[],
            incident_id="inc-abc-123",
            response="Nginx restarted successfully",
            error=None,
            total_tokens=None,
            prompt_tokens=None,
            completion_tokens=None,
        )
        result = _render_execution_detail(execution)
        assert "inc-abc-123" in result

    def test_execution_with_stage_transitions(self):
        from elle.cli.agent_commands import _render_execution_detail

        trans = SimpleNamespace(
            from_stage=SimpleNamespace(value="OBSERVE"),
            to_stage=SimpleNamespace(value="CLASSIFY"),
            duration_ms=10,
            reason="Input received",
        )
        execution = SimpleNamespace(
            execution_id="exec-004",
            started_at=datetime(2024, 3, 15, 10, 0, 0),
            total_duration_ms=100,
            success=True,
            iterations=1,
            user_input="test",
            classified_intent="meta",
            classification_confidence=0.99,
            stage_transitions=[trans],
            hypotheses=[],
            chosen_hypothesis=None,
            capabilities_evaluated=[],
            tool_calls=[],
            incident_id=None,
            response="ok",
            error=None,
            total_tokens=None,
            prompt_tokens=None,
            completion_tokens=None,
        )
        result = _render_execution_detail(execution)
        assert "OBSERVE" in result
        assert "CLASSIFY" in result


# ---------------------------------------------------------------------------
# handle_agent_command_sync
# ---------------------------------------------------------------------------


class TestHandleAgentCommandSync:
    @patch("elle.cli.agent_commands.handle_agent_command")
    def test_sync_wrapper(self, mock_async):
        from elle.cli.agent_commands import handle_agent_command_sync

        async def fake_handler(args):
            return "SYNC_RESULT"

        mock_async.side_effect = fake_handler
        result = handle_agent_command_sync("help")
        assert "agent last" in result or result == "SYNC_RESULT"
