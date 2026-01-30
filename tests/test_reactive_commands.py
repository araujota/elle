from __future__ import annotations

"""Tests for the ELLE reactive function commands."""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.reactive_commands import (
    Colors,
    _react_help,
    handle_reactive_command,
    is_reactive_command,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_session():
    """Create a mock session for command handlers."""
    return MagicMock()


def _make_mock_function(
    name="test-function",
    enabled=True,
    description="Test reactive function",
    trigger_type="event",
    event_category="disk",
    event_source=None,
    event_severity=None,
    event_match=None,
    schedule_cron=None,
    schedule_timezone="UTC",
    condition=None,
    actions=None,
    tags=None,
    source_prompt=None,
    func_id="func-123",
):
    """Create a mock ReactiveFunction."""
    func = MagicMock()
    func.id = func_id
    func.name = name
    func.enabled = enabled
    func.description = description
    func.created_at = datetime(2024, 6, 1, 12, 0)
    func.updated_at = datetime(2024, 6, 2, 14, 30)
    func.tags = tags or []
    func.source_prompt = source_prompt

    # Trigger
    func.trigger.type = trigger_type
    func.trigger.event = None
    func.trigger.schedule = None

    if trigger_type == "event":
        func.trigger.event = MagicMock()
        func.trigger.event.category = event_category
        func.trigger.event.source = event_source
        func.trigger.event.severity = event_severity
        func.trigger.event.match = event_match
    elif trigger_type == "schedule":
        func.trigger.schedule = MagicMock()
        func.trigger.schedule.cron = schedule_cron or "0 */4 * * *"
        func.trigger.schedule.timezone = schedule_timezone

    # Condition
    func.condition = condition

    # Actions
    if actions is None:
        action = MagicMock()
        action.capability = "docker.prune"
        action.input = {"images": True}
        action.on_failure = "stop"
        func.actions = [action]
    else:
        func.actions = actions

    # Policy
    func.policy = MagicMock()
    func.policy.max_frequency = "1h"
    func.policy.max_daily_executions = 10
    func.policy.require_confirmation = False
    func.policy.allowed_hours = None
    func.policy.escalate_on_failure = False

    return func


# =============================================================================
# Colors tests
# =============================================================================


class TestColors:
    def test_reset_code(self):
        assert Colors.RESET == "\033[0m"

    def test_bold_code(self):
        assert Colors.BOLD == "\033[1m"

    def test_color_codes_exist(self):
        assert Colors.RED
        assert Colors.GREEN
        assert Colors.YELLOW
        assert Colors.CYAN
        assert Colors.BLUE
        assert Colors.MAGENTA
        assert Colors.DIM

    def test_bold_color_codes(self):
        assert Colors.BOLD_RED
        assert Colors.BOLD_GREEN
        assert Colors.BOLD_YELLOW


# =============================================================================
# is_reactive_command tests
# =============================================================================


class TestIsReactiveCommand:
    def test_react_command(self):
        assert is_reactive_command("/react") is True

    def test_react_with_subcommand(self):
        assert is_reactive_command("/react list") is True

    def test_react_with_create(self):
        assert is_reactive_command("/react create 'when disk > 90%'") is True

    def test_reactive_prefix(self):
        assert is_reactive_command("reactive") is True

    def test_reactive_with_args(self):
        assert is_reactive_command("reactive list") is True

    def test_not_reactive(self):
        assert is_reactive_command("help") is False

    def test_not_reactive_partial(self):
        assert is_reactive_command("/reactivate") is False

    def test_case_insensitive(self):
        assert is_reactive_command("/REACT list") is True

    def test_leading_whitespace(self):
        assert is_reactive_command("  /react list") is True


# =============================================================================
# _react_help tests
# =============================================================================


class TestReactHelp:
    def test_returns_help_text(self):
        help_text = _react_help()
        assert "Reactive Functions" in help_text

    def test_lists_commands(self):
        help_text = _react_help()
        assert "list" in help_text
        assert "show" in help_text
        assert "create" in help_text
        assert "enable" in help_text
        assert "disable" in help_text
        assert "delete" in help_text
        assert "history" in help_text
        assert "test" in help_text

    def test_includes_examples(self):
        help_text = _react_help()
        assert "disk" in help_text or "docker" in help_text


# =============================================================================
# handle_reactive_command routing tests
# =============================================================================


class TestHandleReactiveCommandRouting:
    def test_react_alone_returns_help(self, mock_session):
        output, success = handle_reactive_command("/react", mock_session)
        assert success is True
        assert "Reactive Functions" in output

    def test_react_help_subcommand(self, mock_session):
        output, success = handle_reactive_command("/react help", mock_session)
        assert success is True
        assert "Reactive Functions" in output

    def test_unknown_subcommand(self, mock_session):
        output, success = handle_reactive_command("/react unknown", mock_session)
        assert success is False
        assert "Unknown subcommand" in output

    def test_list_subcommand(self, mock_session):
        with patch("elle.reactive.store.list_functions", return_value=[]):
            output, success = handle_reactive_command("/react list", mock_session)
            assert success is True

    def test_ls_alias(self, mock_session):
        with patch("elle.reactive.store.list_functions", return_value=[]):
            output, success = handle_reactive_command("/react ls", mock_session)
            assert success is True

    def test_show_subcommand(self, mock_session):
        output, success = handle_reactive_command("/react show", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_create_subcommand_no_args(self, mock_session):
        output, success = handle_reactive_command("/react create", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_enable_subcommand_no_args(self, mock_session):
        output, success = handle_reactive_command("/react enable", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_disable_subcommand_no_args(self, mock_session):
        output, success = handle_reactive_command("/react disable", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_delete_subcommand_no_args(self, mock_session):
        output, success = handle_reactive_command("/react delete", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_rm_alias_no_args(self, mock_session):
        output, success = handle_reactive_command("/react rm", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_history_subcommand_no_args(self, mock_session):
        output, success = handle_reactive_command("/react history", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_test_subcommand_no_args(self, mock_session):
        output, success = handle_reactive_command("/react test", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_dry_run_alias_no_args(self, mock_session):
        output, success = handle_reactive_command("/react dry-run", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_new_alias_no_args(self, mock_session):
        output, success = handle_reactive_command("/react new", mock_session)
        assert success is False
        assert "Usage:" in output


# =============================================================================
# _react_list tests
# =============================================================================


class TestReactList:
    def test_empty_list(self, mock_session):
        with patch("elle.reactive.store.list_functions", return_value=[]):
            output, success = handle_reactive_command("/react list", mock_session)
            assert success is True
            assert "No reactive functions" in output

    def test_list_with_event_function(self, mock_session):
        func = _make_mock_function(
            name="docker-cleanup",
            enabled=True,
            description="Clean docker images when disk is full",
            trigger_type="event",
            event_category="disk",
        )

        with patch("elle.reactive.store.list_functions", return_value=[func]):
            output, success = handle_reactive_command("/react list", mock_session)
            assert success is True
            assert "docker-cleanup" in output
            assert "enabled" in output

    def test_list_with_schedule_function(self, mock_session):
        func = _make_mock_function(
            name="daily-check",
            enabled=False,
            description="Daily system health check",
            trigger_type="schedule",
            schedule_cron="0 6 * * *",
        )

        with patch("elle.reactive.store.list_functions", return_value=[func]):
            output, success = handle_reactive_command("/react list", mock_session)
            assert success is True
            assert "daily-check" in output
            assert "disabled" in output

    def test_list_with_long_description_truncated(self, mock_session):
        func = _make_mock_function(
            name="test-func",
            description="A" * 100,
        )

        with patch("elle.reactive.store.list_functions", return_value=[func]):
            output, success = handle_reactive_command("/react list", mock_session)
            assert success is True
            assert "..." in output

    def test_list_exception(self, mock_session):
        with patch("elle.reactive.store.list_functions", side_effect=RuntimeError("DB error")):
            output, success = handle_reactive_command("/react list", mock_session)
            assert success is False
            assert "Error" in output


# =============================================================================
# _react_show tests
# =============================================================================


class TestReactShow:
    def test_show_no_name(self, mock_session):
        output, success = handle_reactive_command("/react show", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_show_not_found(self, mock_session):
        with patch("elle.reactive.store.get_function_by_name", return_value=None):
            output, success = handle_reactive_command("/react show nonexistent", mock_session)
            assert success is False
            assert "not found" in output

    def test_show_event_function(self, mock_session):
        func = _make_mock_function(
            name="docker-cleanup",
            description="Clean docker resources",
            trigger_type="event",
            event_category="disk",
            event_source="probe",
            event_severity="warning",
            tags=["disk", "docker"],
            source_prompt="when disk > 90% clean docker images",
        )

        with patch("elle.reactive.store.get_function_by_name", return_value=func):
            output, success = handle_reactive_command("/react show docker-cleanup", mock_session)
            assert success is True
            assert "docker-cleanup" in output
            assert "enabled" in output
            assert "event" in output
            assert "disk" in output or "Tags:" in output

    def test_show_schedule_function(self, mock_session):
        func = _make_mock_function(
            name="daily-check",
            trigger_type="schedule",
            schedule_cron="0 6 * * *",
            schedule_timezone="US/Eastern",
        )

        with patch("elle.reactive.store.get_function_by_name", return_value=func):
            output, success = handle_reactive_command("/react show daily-check", mock_session)
            assert success is True
            assert "daily-check" in output
            assert "0 6 * * *" in output
            assert "US/Eastern" in output

    def test_show_disabled_function(self, mock_session):
        func = _make_mock_function(name="disabled-func", enabled=False)

        with patch("elle.reactive.store.get_function_by_name", return_value=func):
            output, success = handle_reactive_command("/react show disabled-func", mock_session)
            assert success is True
            assert "disabled" in output

    def test_show_with_condition(self, mock_session):
        condition = MagicMock()
        condition.expression = {"field": "disk.usage", "op": "gt", "value": 90}
        func = _make_mock_function(name="cond-func", condition=condition)

        with patch("elle.reactive.store.get_function_by_name", return_value=func):
            output, success = handle_reactive_command("/react show cond-func", mock_session)
            assert success is True
            assert "Condition:" in output

    def test_show_with_policy_details(self, mock_session):
        func = _make_mock_function(name="policy-func")
        func.policy.require_confirmation = True
        func.policy.allowed_hours = (8, 22)
        func.policy.escalate_on_failure = True

        with patch("elle.reactive.store.get_function_by_name", return_value=func):
            output, success = handle_reactive_command("/react show policy-func", mock_session)
            assert success is True
            assert "confirmation" in output.lower()
            assert "8-22" in output

    def test_show_with_source_prompt(self, mock_session):
        func = _make_mock_function(
            name="prompted-func",
            source_prompt="when disk > 90% clean docker",
        )

        with patch("elle.reactive.store.get_function_by_name", return_value=func):
            output, success = handle_reactive_command("/react show prompted-func", mock_session)
            assert success is True
            assert "when disk > 90%" in output

    def test_show_exception(self, mock_session):
        with patch("elle.reactive.store.get_function_by_name", side_effect=RuntimeError("DB error")):
            output, success = handle_reactive_command("/react show test-func", mock_session)
            assert success is False
            assert "Error" in output


# =============================================================================
# _react_enable tests
# =============================================================================


class TestReactEnable:
    def test_enable_no_name(self, mock_session):
        output, success = handle_reactive_command("/react enable", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_enable_not_found(self, mock_session):
        with patch("elle.reactive.store.get_function_by_name", return_value=None):
            output, success = handle_reactive_command("/react enable nonexistent", mock_session)
            assert success is False
            assert "not found" in output

    def test_enable_success(self, mock_session):
        func = _make_mock_function(name="test-func", enabled=False)

        with (
            patch("elle.reactive.store.get_function_by_name", return_value=func),
            patch("elle.reactive.store.update_function") as mock_update,
        ):
            output, success = handle_reactive_command("/react enable test-func", mock_session)
            assert success is True
            assert "Enabled" in output
            mock_update.assert_called_once_with(func.id, enabled=True)

    def test_enable_already_enabled(self, mock_session):
        func = _make_mock_function(name="test-func", enabled=True)

        with patch("elle.reactive.store.get_function_by_name", return_value=func):
            output, success = handle_reactive_command("/react enable test-func", mock_session)
            assert success is True
            assert "already enabled" in output

    def test_enable_exception(self, mock_session):
        with patch("elle.reactive.store.get_function_by_name", side_effect=RuntimeError("DB error")):
            output, success = handle_reactive_command("/react enable test-func", mock_session)
            assert success is False
            assert "Error" in output


# =============================================================================
# _react_disable tests
# =============================================================================


class TestReactDisable:
    def test_disable_no_name(self, mock_session):
        output, success = handle_reactive_command("/react disable", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_disable_not_found(self, mock_session):
        with patch("elle.reactive.store.get_function_by_name", return_value=None):
            output, success = handle_reactive_command("/react disable nonexistent", mock_session)
            assert success is False
            assert "not found" in output

    def test_disable_success(self, mock_session):
        func = _make_mock_function(name="test-func", enabled=True)

        with (
            patch("elle.reactive.store.get_function_by_name", return_value=func),
            patch("elle.reactive.store.update_function") as mock_update,
        ):
            output, success = handle_reactive_command("/react disable test-func", mock_session)
            assert success is True
            assert "Disabled" in output
            mock_update.assert_called_once_with(func.id, enabled=False)

    def test_disable_already_disabled(self, mock_session):
        func = _make_mock_function(name="test-func", enabled=False)

        with patch("elle.reactive.store.get_function_by_name", return_value=func):
            output, success = handle_reactive_command("/react disable test-func", mock_session)
            assert success is True
            assert "already disabled" in output

    def test_disable_exception(self, mock_session):
        with patch("elle.reactive.store.get_function_by_name", side_effect=RuntimeError("DB error")):
            output, success = handle_reactive_command("/react disable test-func", mock_session)
            assert success is False
            assert "Error" in output


# =============================================================================
# _react_delete tests
# =============================================================================


class TestReactDelete:
    def test_delete_no_name(self, mock_session):
        output, success = handle_reactive_command("/react delete", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_delete_not_found(self, mock_session):
        with patch("elle.reactive.store.get_function_by_name", return_value=None):
            output, success = handle_reactive_command("/react delete nonexistent", mock_session)
            assert success is False
            assert "not found" in output

    def test_delete_confirmed(self, mock_session):
        func = _make_mock_function(name="test-func", func_id="func-123")

        with (
            patch("elle.reactive.store.get_function_by_name", return_value=func),
            patch("elle.reactive.store.delete_function") as mock_del,
            patch("builtins.input", return_value="y"),
            patch("elle.cli.agentic.incident_recorder.record_arm_action"),
        ):
            output, success = handle_reactive_command("/react delete test-func", mock_session)
            assert success is True
            assert "Deleted" in output
            mock_del.assert_called_once_with("func-123")

    def test_delete_cancelled(self, mock_session):
        func = _make_mock_function(name="test-func")

        with (
            patch("elle.reactive.store.get_function_by_name", return_value=func),
            patch("builtins.input", return_value="n"),
        ):
            output, success = handle_reactive_command("/react delete test-func", mock_session)
            assert success is False
            assert "Cancelled" in output

    def test_delete_eof_cancels(self, mock_session):
        func = _make_mock_function(name="test-func")

        with (
            patch("elle.reactive.store.get_function_by_name", return_value=func),
            patch("builtins.input", side_effect=EOFError),
        ):
            output, success = handle_reactive_command("/react delete test-func", mock_session)
            assert success is False
            assert "Cancelled" in output

    def test_delete_keyboard_interrupt(self, mock_session):
        func = _make_mock_function(name="test-func")

        with (
            patch("elle.reactive.store.get_function_by_name", return_value=func),
            patch("builtins.input", side_effect=KeyboardInterrupt),
        ):
            output, success = handle_reactive_command("/react delete test-func", mock_session)
            assert success is False
            assert "Cancelled" in output

    def test_delete_exception(self, mock_session):
        with patch("elle.reactive.store.get_function_by_name", side_effect=RuntimeError("DB error")):
            output, success = handle_reactive_command("/react delete test-func", mock_session)
            assert success is False
            assert "Error" in output


# =============================================================================
# _react_history tests
# =============================================================================


class TestReactHistory:
    def test_history_no_name(self, mock_session):
        output, success = handle_reactive_command("/react history", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_history_not_found(self, mock_session):
        with patch("elle.reactive.store.get_function_by_name", return_value=None):
            output, success = handle_reactive_command("/react history nonexistent", mock_session)
            assert success is False
            assert "not found" in output

    def test_history_empty(self, mock_session):
        func = _make_mock_function(name="test-func")

        with (
            patch("elle.reactive.store.get_function_by_name", return_value=func),
            patch("elle.reactive.store.get_execution_history", return_value=[]),
        ):
            output, success = handle_reactive_command("/react history test-func", mock_session)
            assert success is True
            assert "No executions" in output

    def test_history_with_records(self, mock_session):
        func = _make_mock_function(name="test-func")

        record = MagicMock()
        record.triggered_at = datetime(2024, 6, 15, 10, 30, 0)
        record.success = True
        record.execution_time_ms = 150
        record.condition_result = True
        record.condition_explanation = ""
        record.actions_executed = ["docker.prune"]
        record.error = None

        with (
            patch("elle.reactive.store.get_function_by_name", return_value=func),
            patch("elle.reactive.store.get_execution_history", return_value=[record]),
        ):
            output, success = handle_reactive_command("/react history test-func", mock_session)
            assert success is True
            assert "Execution History" in output
            assert "success" in output
            assert "150ms" in output

    def test_history_with_failed_record(self, mock_session):
        func = _make_mock_function(name="test-func")

        record = MagicMock()
        record.triggered_at = datetime(2024, 6, 15, 10, 30, 0)
        record.success = False
        record.execution_time_ms = 50
        record.condition_result = True
        record.condition_explanation = ""
        record.actions_executed = []
        record.error = "Connection timeout"

        with (
            patch("elle.reactive.store.get_function_by_name", return_value=func),
            patch("elle.reactive.store.get_execution_history", return_value=[record]),
        ):
            output, success = handle_reactive_command("/react history test-func", mock_session)
            assert success is True
            assert "failed" in output
            assert "Connection timeout" in output

    def test_history_condition_not_met(self, mock_session):
        func = _make_mock_function(name="test-func")

        record = MagicMock()
        record.triggered_at = datetime(2024, 6, 15, 10, 30, 0)
        record.success = True
        record.execution_time_ms = 10
        record.condition_result = False
        record.condition_explanation = "Disk usage below threshold"
        record.actions_executed = []
        record.error = None

        with (
            patch("elle.reactive.store.get_function_by_name", return_value=func),
            patch("elle.reactive.store.get_execution_history", return_value=[record]),
        ):
            output, success = handle_reactive_command("/react history test-func", mock_session)
            assert success is True
            assert "Condition not met" in output
            assert "Disk usage below threshold" in output

    def test_history_exception(self, mock_session):
        with patch("elle.reactive.store.get_function_by_name", side_effect=RuntimeError("DB error")):
            output, success = handle_reactive_command("/react history test-func", mock_session)
            assert success is False
            assert "Error" in output


# =============================================================================
# _react_test tests
# =============================================================================


class TestReactTest:
    def test_test_no_name(self, mock_session):
        output, success = handle_reactive_command("/react test", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_test_not_found(self, mock_session):
        with patch("elle.reactive.store.get_function_by_name", return_value=None):
            output, success = handle_reactive_command("/react test nonexistent", mock_session)
            assert success is False
            assert "not found" in output

    def test_test_condition_passes(self, mock_session):
        func = _make_mock_function(name="test-func")

        mock_engine = MagicMock()
        mock_engine.dry_run = MagicMock()

        loop = asyncio.new_event_loop()
        future = loop.create_future()
        future.set_result((True, "Disk usage is 95%", {"state": {"disk_usage": 95}}))
        mock_engine.dry_run.return_value = future

        with (
            patch("elle.reactive.store.get_function_by_name", return_value=func),
            patch("elle.reactive.engine.get_engine", return_value=mock_engine),
            patch("asyncio.get_event_loop", return_value=loop),
        ):
            output, success = handle_reactive_command("/react test test-func", mock_session)
            assert success is True
            assert "Dry-run" in output
            assert "PASS" in output

        loop.close()

    def test_test_condition_fails(self, mock_session):
        func = _make_mock_function(name="test-func")

        mock_engine = MagicMock()
        mock_engine.dry_run = MagicMock()

        loop = asyncio.new_event_loop()
        future = loop.create_future()
        future.set_result((False, "Disk usage is only 50%", {"state": {}}))
        mock_engine.dry_run.return_value = future

        with (
            patch("elle.reactive.store.get_function_by_name", return_value=func),
            patch("elle.reactive.engine.get_engine", return_value=mock_engine),
            patch("asyncio.get_event_loop", return_value=loop),
        ):
            output, success = handle_reactive_command("/react test test-func", mock_session)
            assert success is True
            assert "Dry-run" in output
            assert "FAIL" in output

        loop.close()

    def test_test_exception(self, mock_session):
        with patch("elle.reactive.store.get_function_by_name", side_effect=RuntimeError("DB error")):
            output, success = handle_reactive_command("/react test test-func", mock_session)
            assert success is False
            assert "Error" in output


# =============================================================================
# _react_create tests
# =============================================================================


class TestReactCreate:
    def test_create_no_prompt(self, mock_session):
        output, success = handle_reactive_command("/react create", mock_session)
        assert success is False
        assert "Usage:" in output

    def test_create_confirmed(self, mock_session):
        func = _make_mock_function(
            name="disk-cleanup",
            description="Clean up disk when full",
        )
        func.model_copy = MagicMock(return_value=func)

        # Use a coroutine mock for compile
        async def mock_compile(prompt):
            return func

        mock_compiler = MagicMock()
        mock_compiler.compile = mock_compile
        mock_compiler.suggest_improvements.return_value = []

        with (
            patch("elle.reactive.compiler.get_compiler", return_value=mock_compiler),
            patch("elle.reactive.store.create_function"),
            patch("elle.reactive.store.get_function_by_name", return_value=None),
            patch("builtins.input", return_value="y"),
            patch("builtins.print"),
            patch("elle.cli.agentic.incident_recorder.record_arm_action"),
        ):
            output, success = handle_reactive_command(
                '/react create "when disk > 90% clean docker"',
                mock_session,
            )
            assert success is True
            assert "Created" in output

    def test_create_edit_mode(self, mock_session):
        func = _make_mock_function(
            name="disk-cleanup",
            description="Clean up disk when full",
        )
        func.model_copy = MagicMock(return_value=func)

        async def mock_compile(prompt):
            return func

        mock_compiler = MagicMock()
        mock_compiler.compile = mock_compile
        mock_compiler.suggest_improvements.return_value = []

        with (
            patch("elle.reactive.compiler.get_compiler", return_value=mock_compiler),
            patch("elle.reactive.store.create_function"),
            patch("elle.reactive.store.get_function_by_name", return_value=None),
            patch("builtins.input", return_value="e"),
            patch("builtins.print"),
            patch("elle.cli.agentic.incident_recorder.record_arm_action"),
        ):
            output, success = handle_reactive_command(
                '/react create "test prompt"',
                mock_session,
            )
            assert success is True
            assert "Created" in output
            assert "delete" in output  # Hints about editing via delete+recreate

    def test_create_cancelled(self, mock_session):
        func = _make_mock_function(name="disk-cleanup")
        func.model_copy = MagicMock(return_value=func)

        async def mock_compile(prompt):
            return func

        mock_compiler = MagicMock()
        mock_compiler.compile = mock_compile
        mock_compiler.suggest_improvements.return_value = []

        with (
            patch("elle.reactive.compiler.get_compiler", return_value=mock_compiler),
            patch("elle.reactive.store.get_function_by_name", return_value=None),
            patch("builtins.input", return_value="n"),
            patch("builtins.print"),
        ):
            output, success = handle_reactive_command(
                '/react create "test prompt"',
                mock_session,
            )
            assert success is False
            assert "Cancelled" in output

    def test_create_eof_cancels(self, mock_session):
        func = _make_mock_function(name="disk-cleanup")
        func.model_copy = MagicMock(return_value=func)

        async def mock_compile(prompt):
            return func

        mock_compiler = MagicMock()
        mock_compiler.compile = mock_compile
        mock_compiler.suggest_improvements.return_value = []

        with (
            patch("elle.reactive.compiler.get_compiler", return_value=mock_compiler),
            patch("elle.reactive.store.get_function_by_name", return_value=None),
            patch("builtins.input", side_effect=EOFError),
            patch("builtins.print"),
        ):
            output, success = handle_reactive_command(
                '/react create "test prompt"',
                mock_session,
            )
            assert success is False
            assert "Cancelled" in output

    def test_create_duplicate_name(self, mock_session):
        func = _make_mock_function(name="disk-cleanup", description="Clean disk")
        func.model_copy = MagicMock(return_value=func)

        async def mock_compile(prompt):
            return func

        mock_compiler = MagicMock()
        mock_compiler.compile = mock_compile
        mock_compiler.suggest_improvements.return_value = []

        existing = _make_mock_function(name="disk-cleanup")

        call_count = [0]

        def mock_get_by_name(name):
            call_count[0] += 1
            if call_count[0] <= 1:
                return existing  # First call: name exists
            return None  # Subsequent calls: unique name ok

        with (
            patch("elle.reactive.compiler.get_compiler", return_value=mock_compiler),
            patch("elle.reactive.store.create_function"),
            patch("elle.reactive.store.get_function_by_name", side_effect=mock_get_by_name),
            patch("builtins.input", return_value="y"),
            patch("builtins.print"),
            patch("elle.cli.agentic.incident_recorder.record_arm_action"),
        ):
            output, success = handle_reactive_command(
                '/react create "test prompt"',
                mock_session,
            )
            assert success is True
            # model_copy should have been called to rename
            func.model_copy.assert_called_once()

    def test_create_with_suggestions(self, mock_session):
        func = _make_mock_function(name="disk-cleanup", description="Clean disk")
        func.model_copy = MagicMock(return_value=func)

        async def mock_compile(prompt):
            return func

        mock_compiler = MagicMock()
        mock_compiler.compile = mock_compile
        mock_compiler.suggest_improvements.return_value = [
            "Add a max_frequency policy to prevent too frequent runs"
        ]

        with (
            patch("elle.reactive.compiler.get_compiler", return_value=mock_compiler),
            patch("elle.reactive.store.create_function"),
            patch("elle.reactive.store.get_function_by_name", return_value=None),
            patch("builtins.input", return_value="y"),
            patch("builtins.print") as mock_print,
            patch("elle.cli.agentic.incident_recorder.record_arm_action"),
        ):
            output, success = handle_reactive_command(
                '/react create "test prompt"',
                mock_session,
            )
            assert success is True
            # Verify suggestions were printed
            all_printed = " ".join(str(c) for c in mock_print.call_args_list)
            assert "Suggestions" in all_printed

    def test_create_exception(self, mock_session):
        with patch(
            "elle.reactive.compiler.get_compiler",
            side_effect=RuntimeError("Compiler error"),
        ):
            output, success = handle_reactive_command(
                '/react create "test prompt"',
                mock_session,
            )
            assert success is False
            assert "Error" in output
