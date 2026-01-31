from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.cli.agentic.models import (
    AgenticIntent,
    CapabilityCall,
    ExecutionEvidence,
    ExecutionPlan,
    ExecutionResult,
    ParallelGroup,
)
from elle.cli.agentic.plan_executor import (
    PlanExecutor,
    _fallback_execution,
    get_plan_executor,
    reset_plan_executor,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_intent(**kwargs: Any) -> AgenticIntent:
    defaults: dict[str, Any] = {
        "information_needs": (),
        "action_requests": (),
        "goal_summary": "test goal",
        "confidence": 0.9,
    }
    defaults.update(kwargs)
    return AgenticIntent(**defaults)


def _make_call(**kwargs: Any) -> CapabilityCall:
    defaults: dict[str, Any] = {
        "capability": "service.status",
        "args": {"service": "nginx"},
        "purpose": "check status",
        "depends_on": (),
        "requires_confirmation": False,
        "is_read_only": True,
    }
    defaults.update(kwargs)
    return CapabilityCall(**defaults)


def _make_plan(
    calls: list[CapabilityCall] | None = None,
    requires_confirmation: bool = False,
) -> ExecutionPlan:
    intent = _make_intent()
    if calls is None:
        calls = [_make_call()]
    groups = [ParallelGroup(calls=tuple(calls))] if calls else []
    return ExecutionPlan(
        parallel_groups=tuple(groups),
        intent=intent,
        estimated_duration_ms=500,
        requires_confirmation=requires_confirmation,
    )


# =============================================================================
# Singleton tests
# =============================================================================


class TestSingleton:
    def setup_method(self) -> None:
        reset_plan_executor()

    def teardown_method(self) -> None:
        reset_plan_executor()

    def test_get_plan_executor_returns_instance(self) -> None:
        ex = get_plan_executor()
        assert isinstance(ex, PlanExecutor)

    def test_get_plan_executor_returns_same(self) -> None:
        a = get_plan_executor()
        b = get_plan_executor()
        assert a is b

    def test_reset_clears_singleton(self) -> None:
        a = get_plan_executor()
        reset_plan_executor()
        b = get_plan_executor()
        assert a is not b


# =============================================================================
# PlanExecutor init
# =============================================================================


class TestPlanExecutorInit:
    def test_defaults(self) -> None:
        pe = PlanExecutor()
        assert pe.max_parallel == 5
        assert pe.skip_confirmation_for_readonly is True
        assert pe.use_fallback is True
        assert pe._capability_executor is None

    def test_custom_params(self) -> None:
        pe = PlanExecutor(max_parallel=3, skip_confirmation_for_readonly=False, use_fallback=False)
        assert pe.max_parallel == 3
        assert pe.skip_confirmation_for_readonly is False
        assert pe.use_fallback is False

    def test_capability_executor_lazy_init_import_error(self) -> None:
        PlanExecutor()
        with patch.dict("sys.modules", {"elle.capabilities.executor": None}):
            with patch(
                "elle.cli.agentic.plan_executor.PlanExecutor.capability_executor",
                new_callable=lambda: property(lambda self: None),
            ):
                pass
        # Just verify the property returns None when import fails
        pe2 = PlanExecutor()
        with patch("builtins.__import__", side_effect=ImportError("test")):
            result = pe2.capability_executor
            assert result is None


# =============================================================================
# execute() - empty plan
# =============================================================================


class TestExecuteEmptyPlan:
    @pytest.mark.asyncio
    async def test_empty_plan_returns_immediately(self) -> None:
        pe = PlanExecutor()
        plan = ExecutionPlan(
            parallel_groups=(),
            intent=_make_intent(),
            estimated_duration_ms=0,
            requires_confirmation=False,
        )
        result = await pe.execute(plan)
        assert isinstance(result, ExecutionResult)
        assert result.evidence == ()
        assert result.total_duration_ms == 0


# =============================================================================
# execute() - confirmation
# =============================================================================


class TestExecuteConfirmation:
    @pytest.mark.asyncio
    async def test_plan_cancelled_by_user(self) -> None:
        pe = PlanExecutor()
        plan = _make_plan(requires_confirmation=True)
        callback = AsyncMock(return_value=False)

        result = await pe.execute(plan, confirm_callback=callback)
        assert len(result.evidence) == 1
        assert result.evidence[0].success is False
        assert "Cancelled" in (result.evidence[0].error or "")
        callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_plan_confirmed_by_user(self) -> None:
        pe = PlanExecutor(use_fallback=False)
        plan = _make_plan(requires_confirmation=True)
        callback = AsyncMock(return_value=True)

        with patch.object(type(pe), "capability_executor", new_callable=lambda: property(lambda self: None)):
            result = await pe.execute(plan, confirm_callback=callback)
            callback.assert_awaited_once()
            # After confirmation, group will be executed
            assert len(result.evidence) >= 1

    @pytest.mark.asyncio
    async def test_skip_confirmation_flag(self) -> None:
        pe = PlanExecutor(use_fallback=False)
        plan = _make_plan(requires_confirmation=True)
        callback = AsyncMock(return_value=True)

        with patch.object(type(pe), "capability_executor", new_callable=lambda: property(lambda self: None)):
            await pe.execute(plan, skip_confirmation=True, confirm_callback=callback)
            callback.assert_not_awaited()


# =============================================================================
# _execute_group
# =============================================================================


class TestExecuteGroup:
    @pytest.mark.asyncio
    async def test_empty_group_returns_empty(self) -> None:
        pe = PlanExecutor()
        group = ParallelGroup(calls=())
        evidence = await pe._execute_group(group)
        assert evidence == []

    @pytest.mark.asyncio
    async def test_exception_in_gather_converted_to_evidence(self) -> None:
        pe = PlanExecutor(use_fallback=False)
        call = _make_call(capability="nonexistent.cap")
        group = ParallelGroup(calls=(call,))

        with patch.object(type(pe), "capability_executor", new_callable=lambda: property(lambda self: None)):
            evidence = await pe._execute_group(group)
            assert len(evidence) == 1
            assert evidence[0].success is False


# =============================================================================
# _execute_call - fallback path
# =============================================================================


class TestExecuteCallFallback:
    @pytest.mark.asyncio
    async def test_fallback_used_for_known_capability(self) -> None:
        pe = PlanExecutor(use_fallback=True)
        call = _make_call(capability="service.status", args={"service": "sshd"})

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.stdout = "ActiveState=active"
        mock_result.stderr = ""

        with (
            patch.object(type(pe), "capability_executor", new_callable=lambda: property(lambda self: None)),
            patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result),
        ):
            ev = await pe._execute_call(call)
            assert ev.success is True
            assert ev.output == "ActiveState=active"

    @pytest.mark.asyncio
    async def test_unknown_capability_not_available(self) -> None:
        pe = PlanExecutor(use_fallback=True)
        call = _make_call(capability="magic.spell", args={})

        with patch.object(type(pe), "capability_executor", new_callable=lambda: property(lambda self: None)):
            ev = await pe._execute_call(call)
            assert ev.success is False
            assert "not available" in (ev.error or "")

    @pytest.mark.asyncio
    async def test_fallback_disabled(self) -> None:
        pe = PlanExecutor(use_fallback=False)
        call = _make_call(capability="service.status", args={"service": "nginx"})

        with patch.object(type(pe), "capability_executor", new_callable=lambda: property(lambda self: None)):
            ev = await pe._execute_call(call)
            assert ev.success is False

    @pytest.mark.asyncio
    async def test_fallback_exception_handled(self) -> None:
        pe = PlanExecutor(use_fallback=True)
        call = _make_call(capability="service.status", args={"service": "nginx"})

        with (
            patch.object(type(pe), "capability_executor", new_callable=lambda: property(lambda self: None)),
            patch("elle.cli.agentic.plan_executor._fallback_execution", side_effect=RuntimeError("boom")),
        ):
            ev = await pe._execute_call(call)
            assert ev.success is False


# =============================================================================
# _execute_via_capability_executor
# =============================================================================


class TestExecuteViaCapabilityExecutor:
    @pytest.mark.asyncio
    async def test_capability_not_in_registry(self) -> None:
        pe = PlanExecutor()
        pe._capability_executor = MagicMock()

        mock_registry = MagicMock()
        mock_registry.get.return_value = None
        call = _make_call()

        with patch("elle.capabilities.get_registry", return_value=mock_registry):
            ev = await pe._execute_via_capability_executor(call)
            assert ev is None

    @pytest.mark.asyncio
    async def test_capability_execution_success(self) -> None:
        pe = PlanExecutor()

        mock_cap = MagicMock()
        mock_cap.spec.input_schema_name = None

        mock_evidence_obj = MagicMock()
        mock_evidence_obj.verification_passed = True
        mock_evidence_obj.verification_details = "all good"

        mock_cap_result = MagicMock()
        mock_cap_result.success = True
        mock_cap_result.output = "running"
        mock_cap_result.error = None
        mock_cap_result.warnings = ()
        mock_cap_result.evidence = mock_evidence_obj

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value=mock_cap_result)
        pe._capability_executor = mock_executor

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_cap
        call = _make_call()

        with patch("elle.capabilities.get_registry", return_value=mock_registry):
            ev = await pe._execute_via_capability_executor(call)
            assert ev is not None
            assert ev.success is True

    @pytest.mark.asyncio
    async def test_capability_execution_exception(self) -> None:
        pe = PlanExecutor()

        mock_cap = MagicMock()
        mock_cap.spec.input_schema_name = None

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(side_effect=RuntimeError("explode"))
        pe._capability_executor = mock_executor

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_cap
        call = _make_call()

        with patch("elle.capabilities.get_registry", return_value=mock_registry):
            ev = await pe._execute_via_capability_executor(call)
            assert ev is not None
            assert ev.success is False
            assert "explode" in (ev.error or "")


# =============================================================================
# _build_input
# =============================================================================


class TestBuildInput:
    def test_no_schema_name(self) -> None:
        pe = PlanExecutor()
        cap = MagicMock()
        cap.spec.input_schema_name = None
        result = pe._build_input(cap, {"foo": "bar"})
        assert result == {"foo": "bar"}

    def test_schema_import_fails(self) -> None:
        pe = PlanExecutor()
        cap = MagicMock()
        cap.spec.input_schema_name = "SomeInput"
        type(cap).__module__ = "nonexistent.module"
        result = pe._build_input(cap, {"x": 1})
        assert result == {"x": 1}


# =============================================================================
# _format_output
# =============================================================================


class TestFormatOutput:
    def test_none(self) -> None:
        pe = PlanExecutor()
        assert pe._format_output(None) == ""

    def test_string(self) -> None:
        pe = PlanExecutor()
        assert pe._format_output("hello") == "hello"

    def test_model_dump(self) -> None:
        pe = PlanExecutor()
        obj = MagicMock()
        obj.model_dump.return_value = {"status": "ok"}
        result = pe._format_output(obj)
        assert "status" in result

    def test_dict(self) -> None:
        pe = PlanExecutor()
        assert '"a"' in pe._format_output({"a": 1})

    def test_other(self) -> None:
        pe = PlanExecutor()
        assert pe._format_output(42) == "42"


# =============================================================================
# _format_plan_preview
# =============================================================================


class TestFormatPlanPreview:
    def test_single_group(self) -> None:
        pe = PlanExecutor()
        plan = _make_plan()
        preview = pe._format_plan_preview(plan)
        assert "test goal" in preview
        assert "service.status" in preview

    def test_multiple_groups(self) -> None:
        pe = PlanExecutor()
        call1 = _make_call(capability="service.status")
        call2 = _make_call(capability="service.restart", requires_confirmation=True)
        groups = [
            ParallelGroup(calls=(call1,)),
            ParallelGroup(calls=(call2,)),
        ]
        plan = ExecutionPlan(
            parallel_groups=tuple(groups),
            intent=_make_intent(),
            estimated_duration_ms=1000,
            requires_confirmation=True,
        )
        preview = pe._format_plan_preview(plan)
        assert "Group 1" in preview
        assert "Group 2" in preview
        assert "[requires confirmation]" in preview


# =============================================================================
# _format_dry_run_preview
# =============================================================================


class TestFormatDryRunPreview:
    def test_basic_preview(self) -> None:
        pe = PlanExecutor()
        call = _make_call()
        dry_run = MagicMock()
        dry_run.preview_text = "Will restart"
        dry_run.would_modify = ["/etc/nginx/nginx.conf"]

        preview = pe._format_dry_run_preview(call, dry_run)
        assert "service.status" in preview
        assert "Will restart" in preview
        assert "/etc/nginx/nginx.conf" in preview

    def test_no_preview_attributes(self) -> None:
        pe = PlanExecutor()
        call = _make_call()
        dry_run = object()  # no preview_text or would_modify
        preview = pe._format_dry_run_preview(call, dry_run)
        assert "service.status" in preview


# =============================================================================
# _should_stop_execution
# =============================================================================


class TestShouldStopExecution:
    def test_stop_on_non_readonly_failure(self) -> None:
        pe = PlanExecutor()
        call = _make_call(is_read_only=False)
        ev = ExecutionEvidence(
            capability="service.restart",
            args={},
            success=False,
            error="failed",
            duration_ms=10,
        )
        group = ParallelGroup(calls=(call,))
        assert pe._should_stop_execution([ev], group) is True

    def test_no_stop_on_readonly_failure(self) -> None:
        pe = PlanExecutor()
        call = _make_call(is_read_only=True)
        ev = ExecutionEvidence(
            capability="service.status",
            args={},
            success=False,
            error="timeout",
            duration_ms=10,
        )
        group = ParallelGroup(calls=(call,))
        assert pe._should_stop_execution([ev], group) is False

    def test_no_stop_on_success(self) -> None:
        pe = PlanExecutor()
        call = _make_call(is_read_only=False)
        ev = ExecutionEvidence(
            capability="service.restart",
            args={},
            success=True,
            duration_ms=10,
        )
        group = ParallelGroup(calls=(call,))
        assert pe._should_stop_execution([ev], group) is False


# =============================================================================
# _fallback_execution
# =============================================================================


class TestFallbackExecution:
    @pytest.mark.asyncio
    async def test_service_status_missing_service(self) -> None:
        output, error = await _fallback_execution("service.status", {})
        assert output is None
        assert "Missing" in (error or "")

    @pytest.mark.asyncio
    async def test_service_status_success(self) -> None:
        mock_result = MagicMock(success=True, stdout="ActiveState=active", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await _fallback_execution("service.status", {"service": "nginx"})
            assert output == "ActiveState=active"
            assert error is None

    @pytest.mark.asyncio
    async def test_service_status_failure(self) -> None:
        mock_result = MagicMock(success=False, stdout="", stderr="not found")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await _fallback_execution("service.status", {"service": "nginx"})
            assert output is None
            assert error is not None

    @pytest.mark.asyncio
    async def test_service_logs_missing_service(self) -> None:
        output, error = await _fallback_execution("service.logs", {})
        assert output is None
        assert "Missing" in (error or "")

    @pytest.mark.asyncio
    async def test_service_logs_success(self) -> None:
        mock_result = MagicMock(success=True, stdout="log data", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await _fallback_execution("service.logs", {"service": "sshd"})
            assert output == "log data"

    @pytest.mark.asyncio
    async def test_file_read_missing_path(self) -> None:
        output, error = await _fallback_execution("file.read", {})
        assert output is None
        assert "Missing" in (error or "")

    @pytest.mark.asyncio
    async def test_file_read_success(self, tmp_path: Any) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        output, error = await _fallback_execution("file.read", {"path": str(f)})
        assert output == "hello"
        assert error is None

    @pytest.mark.asyncio
    async def test_file_read_not_found(self) -> None:
        output, error = await _fallback_execution("file.read", {"path": "/nonexistent/file"})
        assert output is None
        assert error is not None

    @pytest.mark.asyncio
    async def test_file_stat_missing_path(self) -> None:
        output, error = await _fallback_execution("file.stat", {})
        assert output is None
        assert "Missing" in (error or "")

    @pytest.mark.asyncio
    async def test_file_stat_success(self) -> None:
        mock_result = MagicMock(success=True, stdout="File: /etc/hosts", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await _fallback_execution("file.stat", {"path": "/etc/hosts"})
            assert output is not None

    @pytest.mark.asyncio
    async def test_package_info_missing(self) -> None:
        output, error = await _fallback_execution("package.info", {})
        assert output is None
        assert "Missing" in (error or "")

    @pytest.mark.asyncio
    async def test_package_info_success(self) -> None:
        mock_result = MagicMock(success=True, stdout="Version: 1.0", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await _fallback_execution("package.info", {"package": "nginx"})
            assert output is not None

    @pytest.mark.asyncio
    async def test_docker_list(self) -> None:
        mock_result = MagicMock(success=True, stdout="NAMES\tSTATUS", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await _fallback_execution("docker.list", {})
            assert output is not None

    @pytest.mark.asyncio
    async def test_docker_inspect_missing(self) -> None:
        output, error = await _fallback_execution("docker.inspect", {})
        assert output is None
        assert "Missing" in (error or "")

    @pytest.mark.asyncio
    async def test_docker_inspect_success(self) -> None:
        mock_result = MagicMock(success=True, stdout="running - 2024", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await _fallback_execution("docker.inspect", {"name": "myapp"})
            assert output is not None

    @pytest.mark.asyncio
    async def test_docker_logs_missing(self) -> None:
        output, error = await _fallback_execution("docker.logs", {})
        assert output is None
        assert "Missing" in (error or "")

    @pytest.mark.asyncio
    async def test_docker_logs_success(self) -> None:
        mock_result = MagicMock(success=True, stdout="log output", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await _fallback_execution("docker.logs", {"name": "myapp"})
            assert output is not None

    @pytest.mark.asyncio
    async def test_network_listeners(self) -> None:
        mock_result = MagicMock(success=True, stdout="tcp LISTEN", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await _fallback_execution("network.listeners", {})
            assert output is not None

    @pytest.mark.asyncio
    async def test_network_connections(self) -> None:
        mock_result = MagicMock(success=True, stdout="tcp ESTAB", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await _fallback_execution("network.connections", {})
            assert output is not None

    @pytest.mark.asyncio
    async def test_system_info(self) -> None:
        mock_result = MagicMock(success=True, stdout="Linux box", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await _fallback_execution("system.info", {})
            assert output is not None

    @pytest.mark.asyncio
    async def test_system_resources(self) -> None:
        mock_result = MagicMock(success=True, stdout="Mem: 16G", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await _fallback_execution("system.resources", {})
            assert output is not None

    @pytest.mark.asyncio
    async def test_unknown_capability(self) -> None:
        output, error = await _fallback_execution("does.not.exist", {})
        assert output is None
        assert "Unknown" in (error or "")


# =============================================================================
# Full execute flow
# =============================================================================


class TestExecuteFlow:
    @pytest.mark.asyncio
    async def test_stop_on_failure(self) -> None:
        pe = PlanExecutor(use_fallback=False)
        call1 = _make_call(capability="service.restart", is_read_only=False)
        call2 = _make_call(capability="service.status")
        groups = [
            ParallelGroup(calls=(call1,)),
            ParallelGroup(calls=(call2,)),
        ]
        plan = ExecutionPlan(
            parallel_groups=tuple(groups),
            intent=_make_intent(),
            estimated_duration_ms=1000,
            requires_confirmation=False,
        )
        with patch.object(type(pe), "capability_executor", new_callable=lambda: property(lambda self: None)):
            result = await pe.execute(plan, skip_confirmation=True)
            # Should stop after first group fails
            assert result.failure_count >= 1

    @pytest.mark.asyncio
    async def test_confirm_wrapper_no_callback_readonly(self) -> None:
        pe = PlanExecutor()
        mock_cap = MagicMock()
        mock_cap.spec.input_schema_name = None

        mock_cap_result = MagicMock()
        mock_cap_result.success = True
        mock_cap_result.output = "ok"
        mock_cap_result.error = None
        mock_cap_result.warnings = ()
        mock_cap_result.evidence = None

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value=mock_cap_result)
        pe._capability_executor = mock_executor

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_cap
        call = _make_call(requires_confirmation=True, is_read_only=True)

        with patch("elle.capabilities.get_registry", return_value=mock_registry):
            ev = await pe._execute_via_capability_executor(call, confirm_callback=None)
            assert ev is not None
