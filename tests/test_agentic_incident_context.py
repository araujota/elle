"""Tests for elle.cli.agentic.incident_context -- Mandatory incident recording.

Covers:
- Enums: RecordingMode, ActionKind
- Models: ActionRecord, IncidentOutcome
- IncidentContext: properties, factory methods, record_action, set_outcome
- Lifecycle: _enter/_exit, auto-summary, confidence calculation
- Error handling: recording mode REQUIRED vs BEST_EFFORT vs DISABLED
- Helper functions: _infer_domain_from_input, _infer_domain_from_function, _generate_title
- IncidentRecordingError exception
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.agentic.incident_context import (
    ActionKind,
    ActionRecord,
    IncidentContext,
    IncidentOutcome,
    IncidentRecordingError,
    RecordingMode,
    _generate_title,
    _infer_domain_from_function,
    _infer_domain_from_input,
)

# =============================================================================
# Enums
# =============================================================================


class TestRecordingMode:
    def test_required(self) -> None:
        assert RecordingMode.REQUIRED.value == "required"

    def test_best_effort(self) -> None:
        assert RecordingMode.BEST_EFFORT.value == "best_effort"

    def test_disabled(self) -> None:
        assert RecordingMode.DISABLED.value == "disabled"


class TestActionKind:
    def test_all_kinds(self) -> None:
        assert ActionKind.CAPABILITY.value == "capability"
        assert ActionKind.SHELL.value == "shell"
        assert ActionKind.VERIFY.value == "verify"
        assert ActionKind.ARM.value == "arm"
        assert ActionKind.OTHER.value == "other"


# =============================================================================
# Models
# =============================================================================


class TestActionRecord:
    def test_basic_creation(self) -> None:
        record = ActionRecord(
            kind="capability",
            command="service.restart",
            payload={"service": "nginx"},
            success=True,
            output="restarted",
        )
        assert record.kind == "capability"
        assert record.command == "service.restart"
        assert record.success is True
        assert record.output == "restarted"
        assert record.error is None
        assert record.duration_ms == 0

    def test_failed_action(self) -> None:
        record = ActionRecord(
            kind="shell",
            command="systemctl restart nginx",
            success=False,
            error="permission denied",
            duration_ms=50,
        )
        assert record.success is False
        assert record.error == "permission denied"

    def test_frozen(self) -> None:
        record = ActionRecord(kind="other", command="test")
        with pytest.raises(Exception):
            record.kind = "changed"  # type: ignore[misc]


class TestIncidentOutcome:
    def test_defaults(self) -> None:
        outcome = IncidentOutcome()
        assert outcome.success is False
        assert outcome.summary == ""
        assert outcome.status == "open"
        assert outcome.outcome == "no_change"

    def test_successful_outcome(self) -> None:
        outcome = IncidentOutcome(
            success=True,
            summary="Fixed the issue",
            status="resolved",
            outcome="improved",
        )
        assert outcome.success is True
        assert outcome.status == "resolved"


# =============================================================================
# IncidentContext - properties
# =============================================================================


class TestIncidentContextProperties:
    def test_incident_id_default(self) -> None:
        ctx = IncidentContext()
        assert ctx.incident_id == ""

    def test_incident_id_set(self) -> None:
        ctx = IncidentContext(incident_id="inc-123")
        assert ctx.incident_id == "inc-123"

    def test_is_recording_enabled(self) -> None:
        ctx = IncidentContext(recording_mode=RecordingMode.BEST_EFFORT)
        assert ctx.is_recording is True

    def test_is_recording_required(self) -> None:
        ctx = IncidentContext(recording_mode=RecordingMode.REQUIRED)
        assert ctx.is_recording is True

    def test_is_recording_disabled(self) -> None:
        ctx = IncidentContext(recording_mode=RecordingMode.DISABLED)
        assert ctx.is_recording is False

    def test_actions_empty(self) -> None:
        ctx = IncidentContext()
        assert ctx.actions == []

    def test_actions_returns_copy(self) -> None:
        ctx = IncidentContext()
        actions1 = ctx.actions
        actions2 = ctx.actions
        assert actions1 is not actions2


# =============================================================================
# IncidentContext - set_outcome
# =============================================================================


class TestIncidentContextSetOutcome:
    def test_success_defaults(self) -> None:
        ctx = IncidentContext()
        ctx.set_outcome(success=True, summary="All good")
        assert ctx._outcome.success is True
        assert ctx._outcome.status == "resolved"
        assert ctx._outcome.outcome == "improved"
        assert ctx._outcome.summary == "All good"

    def test_failure_defaults(self) -> None:
        ctx = IncidentContext()
        ctx.set_outcome(success=False, summary="Failed")
        assert ctx._outcome.success is False
        assert ctx._outcome.status == "open"
        assert ctx._outcome.outcome == "no_change"

    def test_explicit_status(self) -> None:
        ctx = IncidentContext()
        ctx.set_outcome(success=True, status="mitigated", outcome="partial")
        assert ctx._outcome.status == "mitigated"
        assert ctx._outcome.outcome == "partial"


# =============================================================================
# IncidentContext - record_action
# =============================================================================


class TestIncidentContextRecordAction:
    @pytest.mark.asyncio
    async def test_record_action_disabled(self) -> None:
        ctx = IncidentContext(recording_mode=RecordingMode.DISABLED)
        await ctx.record_action(kind="shell", command="echo hello")
        assert len(ctx._actions) == 0

    @pytest.mark.asyncio
    async def test_record_action_enabled(self) -> None:
        ctx = IncidentContext(recording_mode=RecordingMode.BEST_EFFORT)
        await ctx.record_action(
            kind="capability",
            command="service.restart",
            payload={"service": "nginx"},
            success=True,
            output="restarted",
            duration_ms=100,
        )
        assert len(ctx._actions) == 1
        assert ctx._actions[0].kind == "capability"
        assert ctx._actions[0].command == "service.restart"

    @pytest.mark.asyncio
    async def test_record_action_with_incident_id(self) -> None:
        ctx = IncidentContext(
            incident_id="inc-123",
            recording_mode=RecordingMode.BEST_EFFORT,
        )
        with patch("elle.daemon.incidents.store.append_action") as mock_append:
            await ctx.record_action(
                kind="shell",
                command="ls",
                success=True,
                output="file.txt",
            )
            mock_append.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_action_store_failure_best_effort(self, caplog: Any) -> None:
        ctx = IncidentContext(
            incident_id="inc-123",
            recording_mode=RecordingMode.BEST_EFFORT,
        )
        with patch("elle.daemon.incidents.store.append_action", side_effect=RuntimeError("DB")):
            with caplog.at_level(logging.WARNING):
                await ctx.record_action(kind="shell", command="ls")
        assert "Failed to append action" in caplog.text
        # Action is still stored locally
        assert len(ctx._actions) == 1

    @pytest.mark.asyncio
    async def test_record_action_store_failure_required(self) -> None:
        ctx = IncidentContext(
            incident_id="inc-123",
            recording_mode=RecordingMode.REQUIRED,
        )
        with patch("elle.daemon.incidents.store.append_action", side_effect=RuntimeError("DB")):
            with pytest.raises(IncidentRecordingError):
                await ctx.record_action(kind="shell", command="ls")

    @pytest.mark.asyncio
    async def test_record_action_truncates_output(self) -> None:
        ctx = IncidentContext(
            incident_id="inc-123",
            recording_mode=RecordingMode.BEST_EFFORT,
        )
        long_output = "x" * 10000
        with patch("elle.daemon.incidents.store.append_action") as mock_append:
            await ctx.record_action(
                kind="shell",
                command="verbose_cmd",
                output=long_output,
                success=True,
            )
            call_kwargs = mock_append.call_args[1]
            assert len(call_kwargs["stdout"]) <= 5000

    @pytest.mark.asyncio
    async def test_record_multiple_actions(self) -> None:
        ctx = IncidentContext(recording_mode=RecordingMode.BEST_EFFORT)
        await ctx.record_action(kind="shell", command="cmd1")
        await ctx.record_action(kind="capability", command="cmd2")
        await ctx.record_action(kind="verify", command="cmd3")
        assert len(ctx._actions) == 3


# =============================================================================
# IncidentContext - _enter/_exit lifecycle
# =============================================================================


class TestIncidentContextLifecycle:
    @pytest.mark.asyncio
    async def test_enter_disabled_generates_tracking_id(self) -> None:
        ctx = IncidentContext(recording_mode=RecordingMode.DISABLED)
        await ctx._enter()
        assert ctx._incident_id is not None
        assert ctx._incident_id != ""

    @pytest.mark.asyncio
    async def test_enter_creates_new_incident(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "new-inc-1"

        with patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident):
            ctx = IncidentContext(
                title="test incident",
                recording_mode=RecordingMode.BEST_EFFORT,
            )
            await ctx._enter()
            assert ctx._incident_id == "new-inc-1"
            assert ctx._incident == mock_incident

    @pytest.mark.asyncio
    async def test_enter_with_existing_incident(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "existing-inc"

        with patch("elle.daemon.incidents.store.get_incident", return_value=mock_incident):
            ctx = IncidentContext(
                incident_id="existing-inc",
                recording_mode=RecordingMode.BEST_EFFORT,
            )
            await ctx._enter()
            assert ctx._incident == mock_incident

    @pytest.mark.asyncio
    async def test_enter_existing_incident_not_found_creates_new(self) -> None:
        mock_new_incident = MagicMock()
        mock_new_incident.incident_id = "new-inc-2"

        with patch("elle.daemon.incidents.store.get_incident", return_value=None):
            with patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_new_incident):
                ctx = IncidentContext(
                    incident_id="missing-inc",
                    recording_mode=RecordingMode.BEST_EFFORT,
                )
                await ctx._enter()
                assert ctx._incident_id == "new-inc-2"

    @pytest.mark.asyncio
    async def test_enter_failure_best_effort_continues(self, caplog: Any) -> None:
        with patch("elle.daemon.incidents.store.create_incident_draft", side_effect=RuntimeError("DB")):
            ctx = IncidentContext(
                recording_mode=RecordingMode.BEST_EFFORT,
            )
            with caplog.at_level(logging.WARNING):
                await ctx._enter()
            assert ctx._incident_id is not None  # Tracking ID generated

    @pytest.mark.asyncio
    async def test_enter_failure_required_raises(self) -> None:
        with patch("elle.daemon.incidents.store.create_incident_draft", side_effect=RuntimeError("DB")):
            ctx = IncidentContext(
                recording_mode=RecordingMode.REQUIRED,
            )
            with pytest.raises(IncidentRecordingError):
                await ctx._enter()

    @pytest.mark.asyncio
    async def test_exit_finalized_is_noop(self) -> None:
        ctx = IncidentContext(recording_mode=RecordingMode.BEST_EFFORT)
        ctx._finalized = True
        await ctx._exit()
        # Should not raise

    @pytest.mark.asyncio
    async def test_exit_disabled_is_noop(self) -> None:
        ctx = IncidentContext(recording_mode=RecordingMode.DISABLED)
        await ctx._exit()
        assert ctx._finalized is True

    @pytest.mark.asyncio
    async def test_exit_no_incident_id_is_noop(self) -> None:
        ctx = IncidentContext(recording_mode=RecordingMode.BEST_EFFORT)
        ctx._incident_id = None
        await ctx._exit()
        assert ctx._finalized is True

    @pytest.mark.asyncio
    async def test_exit_updates_incident(self) -> None:
        with patch("elle.daemon.incidents.store.update_incident") as mock_update:
            ctx = IncidentContext(recording_mode=RecordingMode.BEST_EFFORT)
            ctx._incident_id = "inc-exit"
            ctx.set_outcome(success=True, summary="All done")
            await ctx._exit()
            mock_update.assert_called_once()
            call_kwargs = mock_update.call_args
            assert call_kwargs[0][0] == "inc-exit"

    @pytest.mark.asyncio
    async def test_exit_failure_best_effort_continues(self, caplog: Any) -> None:
        with patch("elle.daemon.incidents.store.update_incident", side_effect=RuntimeError("DB")):
            ctx = IncidentContext(recording_mode=RecordingMode.BEST_EFFORT)
            ctx._incident_id = "inc-exit-fail"
            with caplog.at_level(logging.WARNING):
                await ctx._exit()
            assert "Failed to finalize incident" in caplog.text

    @pytest.mark.asyncio
    async def test_exit_failure_required_raises(self) -> None:
        with patch("elle.daemon.incidents.store.update_incident", side_effect=RuntimeError("DB")):
            ctx = IncidentContext(recording_mode=RecordingMode.REQUIRED)
            ctx._incident_id = "inc-exit-fail"
            with pytest.raises(IncidentRecordingError):
                await ctx._exit()


# =============================================================================
# IncidentContext - auto summary and confidence
# =============================================================================


class TestAutoSummaryAndConfidence:
    def test_build_auto_summary_empty(self) -> None:
        ctx = IncidentContext()
        summary = ctx._build_auto_summary()
        assert "Encountered errors" in summary

    def test_build_auto_summary_with_title(self) -> None:
        ctx = IncidentContext(title="My Task")
        summary = ctx._build_auto_summary()
        assert "My Task" in summary

    def test_build_auto_summary_with_actions(self) -> None:
        ctx = IncidentContext(title="")
        ctx._actions = [
            ActionRecord(kind="shell", command="cmd1", success=True),
            ActionRecord(kind="shell", command="cmd2", success=False),
        ]
        summary = ctx._build_auto_summary()
        assert "2 actions" in summary
        assert "1 successful" in summary
        assert "1 failed" in summary

    def test_build_auto_summary_success(self) -> None:
        ctx = IncidentContext()
        ctx._outcome = IncidentOutcome(success=True)
        summary = ctx._build_auto_summary()
        assert "successfully" in summary

    def test_calculate_confidence_no_actions(self) -> None:
        ctx = IncidentContext()
        assert ctx._calculate_confidence() == 0.5

    def test_calculate_confidence_all_success(self) -> None:
        ctx = IncidentContext()
        ctx._actions = [
            ActionRecord(kind="shell", command="cmd1", success=True),
            ActionRecord(kind="shell", command="cmd2", success=True),
        ]
        ctx._outcome = IncidentOutcome(success=True)
        conf = ctx._calculate_confidence()
        # 0.5 base + 1.0 * 0.3 (100% success) + 0.2 (success outcome) = 1.0
        assert conf == 1.0

    def test_calculate_confidence_partial_success(self) -> None:
        ctx = IncidentContext()
        ctx._actions = [
            ActionRecord(kind="shell", command="cmd1", success=True),
            ActionRecord(kind="shell", command="cmd2", success=False),
        ]
        ctx._outcome = IncidentOutcome(success=False)
        conf = ctx._calculate_confidence()
        # 0.5 + 0.5 * 0.3 = 0.65
        assert abs(conf - 0.65) < 0.01

    def test_calculate_confidence_all_failed(self) -> None:
        ctx = IncidentContext()
        ctx._actions = [
            ActionRecord(kind="shell", command="cmd1", success=False),
        ]
        ctx._outcome = IncidentOutcome(success=False)
        conf = ctx._calculate_confidence()
        # 0.5 + 0.0 * 0.3 + 0 = 0.5
        assert conf == 0.5

    def test_calculate_confidence_capped_at_1(self) -> None:
        ctx = IncidentContext()
        ctx._actions = [
            ActionRecord(kind="shell", command="cmd1", success=True),
        ]
        ctx._outcome = IncidentOutcome(success=True)
        conf = ctx._calculate_confidence()
        assert conf <= 1.0


# =============================================================================
# IncidentContext - factory methods (async context managers)
# =============================================================================


class TestFactoryMethods:
    @pytest.mark.asyncio
    async def test_for_user_task(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "ut-inc"

        with patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident):
            with patch("elle.daemon.incidents.store.update_incident"):
                async with IncidentContext.for_user_task(
                    user_input="restart nginx",
                    classified_intent="system_task",
                ) as ctx:
                    assert ctx.incident_id == "ut-inc"
                    assert ctx.is_recording is True

    @pytest.mark.asyncio
    async def test_for_user_task_question(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "q-inc"

        with patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident):
            with patch("elle.daemon.incidents.store.update_incident"):
                async with IncidentContext.for_user_task(
                    user_input="why is my disk full?",
                    classified_intent="system_question",
                ) as ctx:
                    assert ctx._trigger_source == "user_query"

    @pytest.mark.asyncio
    async def test_for_user_task_disabled_recording(self) -> None:
        async with IncidentContext.for_user_task(
            user_input="test",
            classified_intent="system_task",
            recording_mode=RecordingMode.DISABLED,
        ) as ctx:
            assert ctx.is_recording is False
            assert ctx.incident_id != ""  # Tracking ID still generated

    @pytest.mark.asyncio
    async def test_for_reactive_function(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "rf-inc"

        with patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident):
            with patch("elle.daemon.incidents.store.update_incident"):
                async with IncidentContext.for_reactive_function(
                    function_id="func-1",
                    function_name="docker_cleanup",
                    trigger_event={"type": "schedule"},
                ) as ctx:
                    assert ctx.incident_id == "rf-inc"
                    assert ctx._function_id == "func-1"
                    assert ctx._trigger_event == {"type": "schedule"}

    @pytest.mark.asyncio
    async def test_for_capability(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "cap-inc"

        with patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident):
            with patch("elle.daemon.incidents.store.update_incident"):
                async with IncidentContext.for_capability(
                    capability_name="service.restart",
                ) as ctx:
                    assert ctx._domain == "service"

    @pytest.mark.asyncio
    async def test_for_capability_with_parent(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "parent-inc"

        with patch("elle.daemon.incidents.store.get_incident", return_value=mock_incident):
            with patch("elle.daemon.incidents.store.update_incident"):
                async with IncidentContext.for_capability(
                    capability_name="file.write",
                    parent_incident_id="parent-inc",
                ) as ctx:
                    assert ctx._parent_incident_id == "parent-inc"

    @pytest.mark.asyncio
    async def test_for_capability_no_dot_in_name(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "nodot-inc"

        with patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident):
            with patch("elle.daemon.incidents.store.update_incident"):
                async with IncidentContext.for_capability(
                    capability_name="reboot",
                ) as ctx:
                    assert ctx._domain == "other"


# =============================================================================
# Helper functions
# =============================================================================


class TestInferDomainFromInput:
    def test_network_keywords(self) -> None:
        for kw in ["network", "dns", "firewall", "port", "ping", "curl", "ip", "connection"]:
            assert _infer_domain_from_input(f"check {kw} status", "system_task") == "net"

    def test_docker_keywords(self) -> None:
        for kw in ["docker", "container", "compose", "image"]:
            assert _infer_domain_from_input(f"restart {kw}", "system_task") == "docker"

    def test_service_keywords(self) -> None:
        for kw in ["service", "systemd", "restart", "nginx", "apache"]:
            assert _infer_domain_from_input(f"check {kw}", "system_task") == "service"

    def test_disk_keywords(self) -> None:
        for kw in ["disk", "storage", "mount", "space", "full"]:
            assert _infer_domain_from_input(f"check {kw}", "system_task") == "disk"

    def test_oom_keywords(self) -> None:
        for kw in ["memory", "oom", "ram", "swap"]:
            assert _infer_domain_from_input(f"check {kw}", "system_task") == "oom"

    def test_pkg_keywords(self) -> None:
        for kw in ["package", "install", "apt", "dpkg", "upgrade"]:
            assert _infer_domain_from_input(f"run {kw}", "system_task") == "pkg"

    def test_auth_keywords(self) -> None:
        for kw in ["permission", "sudo", "password", "login", "ssh"]:
            assert _infer_domain_from_input(f"check {kw}", "system_task") == "auth"

    def test_fs_keywords(self) -> None:
        for kw in ["file", "config", "edit", "delete", "copy"]:
            assert _infer_domain_from_input(f"do {kw}", "system_task") == "fs"

    def test_unknown_returns_other(self) -> None:
        assert _infer_domain_from_input("what time is it?", "system_question") == "other"

    def test_case_insensitive(self) -> None:
        assert _infer_domain_from_input("Check DOCKER status", "system_task") == "docker"


class TestInferDomainFromFunction:
    def test_docker(self) -> None:
        assert _infer_domain_from_function("docker_cleanup") == "docker"

    def test_container(self) -> None:
        assert _infer_domain_from_function("container_monitor") == "docker"

    def test_service(self) -> None:
        assert _infer_domain_from_function("service_restart") == "service"

    def test_systemd(self) -> None:
        assert _infer_domain_from_function("systemd_watcher") == "service"

    def test_disk(self) -> None:
        assert _infer_domain_from_function("disk_monitor") == "disk"

    def test_storage(self) -> None:
        assert _infer_domain_from_function("storage_cleanup") == "disk"

    def test_network(self) -> None:
        assert _infer_domain_from_function("network_check") == "net"

    def test_net_short(self) -> None:
        assert _infer_domain_from_function("net_monitor") == "net"

    def test_memory(self) -> None:
        assert _infer_domain_from_function("memory_guard") == "oom"

    def test_oom(self) -> None:
        assert _infer_domain_from_function("oom_killer_watch") == "oom"

    def test_unknown(self) -> None:
        assert _infer_domain_from_function("custom_function") == "other"

    def test_case_insensitive(self) -> None:
        assert _infer_domain_from_function("DOCKER_cleanup") == "docker"


class TestGenerateTitle:
    def test_basic_task(self) -> None:
        title = _generate_title("restart nginx", "system_task")
        assert title == "Task: restart nginx"

    def test_question_with_mark(self) -> None:
        title = _generate_title("why is it failing?", "system_question")
        assert title == "why is it failing?"

    def test_question_without_mark(self) -> None:
        title = _generate_title("what is wrong", "system_question")
        assert title == "Question: what is wrong"

    def test_truncation(self) -> None:
        long_input = "a" * 200
        title = _generate_title(long_input, "system_task")
        # Truncated to 100 then "Task: " prepended
        assert len(title) <= 106  # "Task: " + 100

    def test_strips_whitespace(self) -> None:
        title = _generate_title("  restart nginx  ", "system_task")
        assert title == "Task: restart nginx"

    def test_other_intent(self) -> None:
        title = _generate_title("just some text", "shell_passthrough")
        assert title == "just some text"


# =============================================================================
# IncidentRecordingError
# =============================================================================


class TestIncidentRecordingError:
    def test_is_exception(self) -> None:
        err = IncidentRecordingError("test error")
        assert isinstance(err, Exception)
        assert str(err) == "test error"


# =============================================================================
# _handle_recording_error
# =============================================================================


class TestHandleRecordingError:
    def test_required_mode_raises(self) -> None:
        ctx = IncidentContext(recording_mode=RecordingMode.REQUIRED)
        with pytest.raises(IncidentRecordingError, match="test error"):
            ctx._handle_recording_error("test error")

    def test_best_effort_mode_logs(self, caplog: Any) -> None:
        ctx = IncidentContext(recording_mode=RecordingMode.BEST_EFFORT)
        with caplog.at_level(logging.WARNING):
            ctx._handle_recording_error("test warning")
        assert "test warning" in caplog.text
