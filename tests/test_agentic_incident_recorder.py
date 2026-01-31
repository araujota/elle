from __future__ import annotations

from unittest.mock import MagicMock, patch

from elle.cli.agentic.incident_recorder import (
    RecordingResult,
    _build_provenance,
    _build_rationale,
    _build_summary,
    _calculate_confidence,
    _generate_title,
    _infer_domain,
    _with_retry,
    get_or_create_incident_for_input,
    record_arm_action,
    record_execution_to_incident,
)

# =============================================================================
# RecordingResult
# =============================================================================


class TestRecordingResult:
    def test_success_is_truthy(self) -> None:
        r = RecordingResult(success=True, incident_id="abc")
        assert bool(r) is True

    def test_failure_is_falsy(self) -> None:
        r = RecordingResult(success=False, error="boom")
        assert bool(r) is False

    def test_defaults(self) -> None:
        r = RecordingResult(success=True)
        assert r.incident_id is None
        assert r.error is None
        assert r.recoverable is True


# =============================================================================
# _with_retry
# =============================================================================


class TestWithRetry:
    def test_success_first_try(self) -> None:
        result = _with_retry(lambda: "id-123", max_retries=2)
        assert result.success is True
        assert result.incident_id == "id-123"

    def test_failure_all_retries(self) -> None:
        calls = {"count": 0}

        def fail():
            calls["count"] += 1
            raise RuntimeError("db locked")

        result = _with_retry(fail, max_retries=1, base_delay=0.001)
        assert result.success is False
        assert "db locked" in (result.error or "")
        assert calls["count"] == 2  # initial + 1 retry

    def test_success_on_retry(self) -> None:
        attempts = {"n": 0}

        def eventually_succeed():
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise RuntimeError("fail")
            return "id-456"

        result = _with_retry(eventually_succeed, max_retries=2, base_delay=0.001)
        assert result.success is True
        assert result.incident_id == "id-456"


# =============================================================================
# _infer_domain
# =============================================================================


class TestInferDomain:
    def test_from_service_capability(self) -> None:
        assert _infer_domain("check nginx", "system_task", ["service.restart"]) == "service"

    def test_from_docker_capability(self) -> None:
        assert _infer_domain("test", "system_task", ["docker.list"]) == "docker"

    def test_from_network_capability(self) -> None:
        assert _infer_domain("test", "system_task", ["network.listeners"]) == "net"

    def test_from_file_capability(self) -> None:
        assert _infer_domain("test", "system_task", ["file.read"]) == "fs"

    def test_from_config_capability(self) -> None:
        assert _infer_domain("test", "system_task", ["config.edit"]) == "fs"

    def test_from_package_capability(self) -> None:
        assert _infer_domain("test", "system_task", ["package.install"]) == "pkg"

    def test_from_network_keyword(self) -> None:
        assert _infer_domain("check network dns", "system_question", []) == "net"

    def test_from_docker_keyword(self) -> None:
        assert _infer_domain("list docker containers", "system_question", []) == "docker"

    def test_from_service_keyword(self) -> None:
        assert _infer_domain("restart the service", "system_task", []) == "service"

    def test_from_disk_keyword(self) -> None:
        assert _infer_domain("disk space full", "system_question", []) == "disk"

    def test_from_memory_keyword(self) -> None:
        assert _infer_domain("memory usage oom", "system_question", []) == "oom"

    def test_from_package_keyword(self) -> None:
        assert _infer_domain("install package", "system_task", []) == "pkg"

    def test_from_auth_keyword(self) -> None:
        assert _infer_domain("fix permission denied", "system_task", []) == "auth"

    def test_default_other(self) -> None:
        assert _infer_domain("hello world", "meta", []) == "other"


# =============================================================================
# _generate_title
# =============================================================================


class TestGenerateTitle:
    def test_question_without_mark(self) -> None:
        title = _generate_title("why is nginx slow", "system_question")
        assert title.startswith("Question:")

    def test_question_with_mark(self) -> None:
        title = _generate_title("why is nginx slow?", "system_question")
        assert title == "why is nginx slow?"

    def test_task(self) -> None:
        title = _generate_title("restart nginx", "system_task")
        assert title.startswith("Task:")

    def test_truncation(self) -> None:
        title = _generate_title("x" * 200, "meta")
        assert len(title) <= 103  # 97 + "..."
        assert title.endswith("...")

    def test_other_intent(self) -> None:
        title = _generate_title("hello world", "meta")
        assert title == "hello world"


# =============================================================================
# _build_summary
# =============================================================================


class TestBuildSummary:
    def test_success_with_capabilities(self) -> None:
        summary = _build_summary(
            user_input="restart nginx",
            response="done",
            success=True,
            tool_calls=[{"tool_name": "a"}],
            capabilities_executed=["service.restart"],
        )
        assert "restart nginx" in summary
        assert "service.restart" in summary
        assert "successfully" in summary

    def test_failure(self) -> None:
        summary = _build_summary(
            user_input="test",
            response="error",
            success=False,
            tool_calls=[],
            capabilities_executed=[],
        )
        assert "errors" in summary

    def test_long_input_truncated(self) -> None:
        summary = _build_summary(
            user_input="z" * 200,
            response="ok",
            success=True,
            tool_calls=[],
            capabilities_executed=[],
        )
        assert "..." in summary

    def test_with_tool_calls(self) -> None:
        summary = _build_summary(
            user_input="check system",
            response="ok",
            success=True,
            tool_calls=[{"tool_name": "a"}, {"tool_name": "b"}],
            capabilities_executed=[],
        )
        assert "2 tool calls" in summary


# =============================================================================
# _build_rationale
# =============================================================================


class TestBuildRationale:
    def test_empty(self) -> None:
        assert _build_rationale(None, []) == "Executed via agentic loop."

    def test_with_hypotheses(self) -> None:
        hyps = [
            {"text": "nginx is overloaded", "chosen": True},
            {"text": "disk full", "chosen": False},
        ]
        rationale = _build_rationale(hyps, [])
        assert "nginx is overloaded" in rationale
        assert "2 hypotheses" in rationale

    def test_with_tool_calls(self) -> None:
        tools = [
            {"tool_name": "search_man_vault"},
            {"tool_name": "execute_capability"},
            {"tool_name": "search_man_vault"},  # duplicate
        ]
        rationale = _build_rationale(None, tools)
        assert "search_man_vault" in rationale

    def test_both(self) -> None:
        hyps = [{"text": "test", "chosen": False}]
        tools = [{"tool_name": "shell_command"}]
        rationale = _build_rationale(hyps, tools)
        assert "1 hypotheses" in rationale
        assert "shell_command" in rationale


# =============================================================================
# _calculate_confidence
# =============================================================================


class TestCalculateConfidence:
    def test_no_context_success(self) -> None:
        conf = _calculate_confidence(None, True, 1)
        # Should have at least LLM base confidence
        assert hasattr(conf, "overall")
        assert conf.from_llm > 0

    def test_no_context_failure(self) -> None:
        conf = _calculate_confidence(None, False, 1)
        assert conf.from_llm < 0.5

    def test_high_iterations_penalty(self) -> None:
        conf = _calculate_confidence(None, True, 10)
        assert conf.from_llm <= 0.5

    def test_with_retrieval_context(self) -> None:
        ctx = MagicMock()
        ctx.man_vault_hits = 3
        ctx.incident_vault_hits = 2
        conf = _calculate_confidence(ctx, True, 1)
        assert conf.from_man_vault > 0
        assert conf.from_incident_vault > 0

    def test_exception_returns_fallback(self) -> None:
        # When ConfidenceBreakdown raises, the except block catches and returns a fallback
        with patch(
            "elle.daemon.incidents.models.ConfidenceBreakdown",
            side_effect=[Exception("fail"), MagicMock(overall=0.5, from_llm=0.5)],
        ):
            conf = _calculate_confidence(None, True, 1)
        # Either the normal path or fallback should return an object with overall
        assert conf is not None


# =============================================================================
# _build_provenance
# =============================================================================


class TestBuildProvenance:
    def test_no_context(self) -> None:
        prov = _build_provenance(None, [])
        assert prov.primary_source == "llm_only"

    def test_with_man_vault(self) -> None:
        ctx = MagicMock()
        ctx.man_vault_sources = ["systemctl.1", "journalctl.1"]
        ctx.incident_ids = []
        prov = _build_provenance(ctx, [])
        assert prov.primary_source == "man_vault"

    def test_with_incidents(self) -> None:
        ctx = MagicMock()
        ctx.man_vault_sources = []
        ctx.incident_ids = ["inc-1", "inc-2"]
        # Make sure man_vault_sources is empty to not set man_vault first
        prov = _build_provenance(ctx, [])
        assert prov.primary_source == "incident_vault"

    def test_tool_calls_man_vault(self) -> None:
        prov = _build_provenance(None, [{"tool_name": "search_man_vault"}])
        assert prov.primary_source == "man_vault"

    def test_tool_calls_incidents(self) -> None:
        prov = _build_provenance(None, [{"tool_name": "search_incidents"}])
        assert prov.primary_source == "incident_vault"


# =============================================================================
# record_execution_to_incident
# =============================================================================


class TestRecordExecutionToIncident:
    def test_creates_new_incident(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-new"

        with (
            patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident) as mock_create,
            patch("elle.daemon.incidents.store.get_incident"),
            patch("elle.daemon.incidents.store.update_incident") as mock_update,
            patch("elle.daemon.incidents.store.append_action") as mock_append,
        ):
            result = record_execution_to_incident(
                execution_id="exec-1",
                user_input="check nginx status",
                classified_intent="system_question",
                response="nginx is running",
                success=True,
                iterations=1,
                total_duration_ms=500,
                tool_calls=[
                    {
                        "tool_name": "execute_capability",
                        "arguments": {"capability_name": "service.status"},
                        "result": {"output": "running", "success": True, "duration_ms": 100},
                        "iteration": 1,
                        "verified": True,
                    },
                ],
            )
            assert result == "inc-new"
            mock_create.assert_called_once()
            mock_update.assert_called_once()
            mock_append.assert_called_once()

    def test_updates_existing_incident(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-exist"

        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=mock_incident),
            patch("elle.daemon.incidents.store.create_incident_draft") as mock_create,
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action"),
        ):
            result = record_execution_to_incident(
                execution_id="exec-2",
                user_input="test",
                classified_intent="system_task",
                response="done",
                success=True,
                iterations=1,
                total_duration_ms=100,
                tool_calls=[],
                existing_incident_id="inc-exist",
            )
            assert result == "inc-exist"
            mock_create.assert_not_called()

    def test_existing_not_found_creates_new(self) -> None:
        mock_new = MagicMock()
        mock_new.incident_id = "inc-new2"

        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=None),
            patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_new),
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action"),
        ):
            result = record_execution_to_incident(
                execution_id="exec-3",
                user_input="test",
                classified_intent="system_task",
                response="done",
                success=True,
                iterations=1,
                total_duration_ms=100,
                tool_calls=[],
                existing_incident_id="inc-gone",
            )
            assert result == "inc-new2"

    def test_records_shell_tool_call(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-shell"

        with (
            patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident),
            patch("elle.daemon.incidents.store.get_incident"),
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action") as mock_append,
        ):
            record_execution_to_incident(
                execution_id="exec-4",
                user_input="run df",
                classified_intent="system_task",
                response="done",
                success=True,
                iterations=1,
                total_duration_ms=50,
                tool_calls=[
                    {
                        "tool_name": "shell_command",
                        "arguments": {"command": "df -h"},
                        "result": {"output": "Filesystem...", "success": True},
                    },
                ],
            )
            args = mock_append.call_args
            assert args.kwargs["kind"] == "shell"

    def test_records_verify_tool_call(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-verify"

        with (
            patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident),
            patch("elle.daemon.incidents.store.get_incident"),
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action") as mock_append,
        ):
            record_execution_to_incident(
                execution_id="exec-5",
                user_input="search for info",
                classified_intent="system_question",
                response="found",
                success=True,
                iterations=1,
                total_duration_ms=50,
                tool_calls=[
                    {
                        "tool_name": "search_man_vault",
                        "arguments": {"query": "nginx"},
                        "result": "some result string",
                    },
                ],
            )
            args = mock_append.call_args
            assert args.kwargs["kind"] == "verify"

    def test_exception_returns_none(self) -> None:
        with patch("elle.daemon.incidents.store.create_incident_draft", side_effect=Exception("db error")):
            result = record_execution_to_incident(
                execution_id="exec-err",
                user_input="test",
                classified_intent="system_task",
                response="fail",
                success=False,
                iterations=1,
                total_duration_ms=10,
                tool_calls=[],
            )
            assert result is None


# =============================================================================
# get_or_create_incident_for_input
# =============================================================================


class TestGetOrCreateIncidentForInput:
    def test_creates_incident(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-auto"

        with patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident):
            result = get_or_create_incident_for_input("test input", "system_task")
            assert result == "inc-auto"

    def test_returns_uuid_on_failure(self) -> None:
        with patch("elle.daemon.incidents.store.create_incident_draft", side_effect=Exception("fail")):
            result = get_or_create_incident_for_input("test", "meta")
            # Should be a UUID string
            assert len(result) > 10


# =============================================================================
# record_arm_action
# =============================================================================


class TestRecordArmAction:
    def test_success(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "arm-1"

        with (
            patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident),
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action"),
        ):
            result = record_arm_action(
                arm_name="package_learning",
                action="learn_package",
                target="ffmpeg",
                success=True,
                details={"capabilities_generated": 5},
            )
            assert result == "arm-1"

    def test_failure_action(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "arm-2"

        with (
            patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident),
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action"),
        ):
            result = record_arm_action(
                arm_name="reactive_functions",
                action="add_function",
                target="my_func",
                success=False,
                error="validation failed",
            )
            assert result == "arm-2"

    def test_retry_on_failure(self) -> None:
        calls = {"n": 0}
        mock_incident = MagicMock()
        mock_incident.incident_id = "arm-retry"

        def side_effect(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("db busy")
            return mock_incident

        with (
            patch("elle.daemon.incidents.store.create_incident_draft", side_effect=side_effect),
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action"),
        ):
            result = record_arm_action(
                arm_name="mobile_gateway",
                action="pair",
                target="phone1",
                success=True,
            )
            assert result == "arm-retry"

    def test_all_retries_fail(self) -> None:
        with patch("elle.daemon.incidents.store.create_incident_draft", side_effect=RuntimeError("fail")):
            result = record_arm_action(
                arm_name="package_learning",
                action="learn",
                target="pkg",
                success=True,
            )
            assert result is None

    def test_arm_domain_mapping(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "arm-domain"

        with (
            patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident) as mock_create,
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action"),
        ):
            record_arm_action(
                arm_name="gui_mapping",
                action="map_window",
                target="firefox",
                success=True,
            )
            create_call = mock_create.call_args
            assert create_call.kwargs["domain"] == "gui"

    def test_execute_action_kind(self) -> None:
        mock_incident = MagicMock()
        mock_incident.incident_id = "arm-exec"

        with (
            patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident),
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action") as mock_append,
        ):
            record_arm_action(
                arm_name="package_learning",
                action="execute_something",
                target="test",
                success=True,
            )
            args = mock_append.call_args
            assert args.kwargs["kind"] == "capability"
