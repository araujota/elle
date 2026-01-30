from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.cli.agentic.models import (
    CapabilityCall,
    GatheredEvidence,
    GatherPlan,
    GatherResult,
    InformationNeed,
)
from elle.cli.agentic.executor import (
    READ_ONLY_CAPABILITIES,
    GatherExecutor,
    get_executor,
    reset_executor,
)


# =============================================================================
# Helpers
# =============================================================================


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


def _make_need(**kwargs: Any) -> InformationNeed:
    defaults: dict[str, Any] = {
        "category": "service",
        "target": "nginx",
        "aspects": ("status",),
    }
    defaults.update(kwargs)
    return InformationNeed(**defaults)


def _make_plan(calls: list[CapabilityCall] | None = None, needs: list[InformationNeed] | None = None) -> GatherPlan:
    if calls is None:
        calls = [_make_call()]
    if needs is None:
        needs = [_make_need()]
    return GatherPlan(
        needs=tuple(needs),
        calls=tuple(calls),
        estimated_duration_ms=500,
    )


# =============================================================================
# Singleton
# =============================================================================


class TestSingleton:
    def setup_method(self) -> None:
        reset_executor()

    def teardown_method(self) -> None:
        reset_executor()

    def test_get_executor_creates(self) -> None:
        ex = get_executor()
        assert isinstance(ex, GatherExecutor)

    def test_get_executor_same_instance(self) -> None:
        a = get_executor()
        b = get_executor()
        assert a is b

    def test_reset_clears(self) -> None:
        a = get_executor()
        reset_executor()
        b = get_executor()
        assert a is not b


# =============================================================================
# GatherExecutor init
# =============================================================================


class TestGatherExecutorInit:
    def test_defaults(self) -> None:
        ex = GatherExecutor()
        assert ex.allowed_capabilities is READ_ONLY_CAPABILITIES
        assert ex.max_parallel == 5

    def test_custom_capabilities(self) -> None:
        custom = frozenset({"my.cap"})
        ex = GatherExecutor(allowed_capabilities=custom)
        assert ex.allowed_capabilities is custom


# =============================================================================
# execute - empty plan
# =============================================================================


class TestExecuteEmptyPlan:
    @pytest.mark.asyncio
    async def test_empty_calls(self) -> None:
        ex = GatherExecutor()
        plan = GatherPlan(needs=(), calls=(), estimated_duration_ms=0)
        result = await ex.execute(plan)
        assert isinstance(result, GatherResult)
        assert result.sufficient is False
        assert "No capabilities to execute" in result.missing


# =============================================================================
# execute - with calls
# =============================================================================


class TestExecuteWithCalls:
    @pytest.mark.asyncio
    async def test_disallowed_capability(self) -> None:
        ex = GatherExecutor(allowed_capabilities=frozenset({"allowed.cap"}))
        call = _make_call(capability="forbidden.cap")
        plan = _make_plan(calls=[call])

        result = await ex.execute(plan)
        assert len(result.evidence) == 1
        assert result.evidence[0].success is False
        assert "not allowed" in (result.evidence[0].error or "")

    @pytest.mark.asyncio
    async def test_successful_execution(self) -> None:
        ex = GatherExecutor()

        mock_run = AsyncMock(return_value=("status output", None))
        with patch.object(ex, "_run_capability", mock_run):
            call = _make_call(capability="service.status")
            plan = _make_plan(calls=[call])
            result = await ex.execute(plan)

        assert result.sufficient is True
        assert result.evidence[0].success is True
        assert result.evidence[0].output == "status output"

    @pytest.mark.asyncio
    async def test_failed_execution(self) -> None:
        ex = GatherExecutor()

        mock_run = AsyncMock(return_value=(None, "connection refused"))
        with patch.object(ex, "_run_capability", mock_run):
            call = _make_call(capability="service.status")
            plan = _make_plan(calls=[call])
            result = await ex.execute(plan)

        assert result.evidence[0].success is False
        assert len(result.missing) == 1

    @pytest.mark.asyncio
    async def test_exception_in_execution(self) -> None:
        ex = GatherExecutor()

        mock_run = AsyncMock(side_effect=RuntimeError("crash"))
        with patch.object(ex, "_run_capability", mock_run):
            call = _make_call(capability="service.status")
            plan = _make_plan(calls=[call])
            result = await ex.execute(plan)

        assert result.evidence[0].success is False
        assert "crash" in (result.evidence[0].error or "")

    @pytest.mark.asyncio
    async def test_mixed_success_failure(self) -> None:
        ex = GatherExecutor()
        call_count = {"n": 0}

        async def mock_run(cap_name: str, args: dict) -> tuple:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return ("data", None)
            return (None, "failed")

        with patch.object(ex, "_run_capability", side_effect=mock_run):
            calls = [
                _make_call(capability="service.status"),
                _make_call(capability="service.logs", args={"service": "nginx", "lines": "50"}),
            ]
            plan = _make_plan(calls=calls)
            result = await ex.execute(plan)

        assert result.sufficient is True  # at least one succeeded
        assert len(result.missing) == 1


# =============================================================================
# _run_capability
# =============================================================================


class TestRunCapability:
    @pytest.mark.asyncio
    async def test_registry_capability_success(self) -> None:
        ex = GatherExecutor()

        mock_cap = MagicMock()
        mock_cap.spec.input_schema_name = None
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.output = "running"
        mock_cap.run.return_value = mock_result

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_cap

        with patch("elle.capabilities.get_registry", return_value=mock_registry):
            output, error = await ex._run_capability("service.status", {"service": "nginx"})
            assert output == "running"
            assert error is None

    @pytest.mark.asyncio
    async def test_registry_capability_failure(self) -> None:
        ex = GatherExecutor()

        mock_cap = MagicMock()
        mock_cap.spec.input_schema_name = None
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error = "service not found"
        mock_cap.run.return_value = mock_result

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_cap

        with patch("elle.capabilities.get_registry", return_value=mock_registry):
            output, error = await ex._run_capability("service.status", {"service": "missing"})
            assert output is None
            assert "not found" in (error or "")

    @pytest.mark.asyncio
    async def test_registry_not_found_falls_back(self) -> None:
        ex = GatherExecutor()

        mock_registry = MagicMock()
        mock_registry.get.return_value = None

        mock_fallback = AsyncMock(return_value=("fallback data", None))

        with patch("elle.capabilities.get_registry", return_value=mock_registry), \
             patch.object(ex, "_fallback_execution", mock_fallback):
            output, error = await ex._run_capability("service.status", {"service": "nginx"})
            assert output == "fallback data"

    @pytest.mark.asyncio
    async def test_import_error_falls_back(self) -> None:
        ex = GatherExecutor()

        mock_fallback = AsyncMock(return_value=("fb data", None))

        with patch("elle.capabilities.get_registry", side_effect=ImportError("no module")), \
             patch.object(ex, "_fallback_execution", mock_fallback):
            output, error = await ex._run_capability("service.status", {})
            assert output == "fb data"

    @pytest.mark.asyncio
    async def test_generic_exception(self) -> None:
        ex = GatherExecutor()

        with patch("elle.capabilities.get_registry", side_effect=RuntimeError("unexpected")):
            output, error = await ex._run_capability("service.status", {})
            assert output is None
            assert "unexpected" in (error or "")


# =============================================================================
# _build_input
# =============================================================================


class TestBuildInput:
    def test_no_schema(self) -> None:
        ex = GatherExecutor()
        cap = MagicMock()
        cap.spec.input_schema_name = None
        result = ex._build_input(cap, {"key": "val"})
        assert result == {"key": "val"}

    def test_import_fails(self) -> None:
        ex = GatherExecutor()
        cap = MagicMock()
        cap.spec.input_schema_name = "MyInput"
        type(cap).__module__ = "nonexistent.module"
        result = ex._build_input(cap, {"x": 1})
        assert result == {"x": 1}


# =============================================================================
# _format_output
# =============================================================================


class TestFormatOutput:
    def test_none(self) -> None:
        ex = GatherExecutor()
        assert ex._format_output(None) == ""

    def test_string(self) -> None:
        ex = GatherExecutor()
        assert ex._format_output("hello") == "hello"

    def test_model_dump(self) -> None:
        ex = GatherExecutor()
        obj = MagicMock()
        obj.model_dump.return_value = {"key": "value"}
        result = ex._format_output(obj)
        assert "key" in result

    def test_dict(self) -> None:
        ex = GatherExecutor()
        result = ex._format_output({"a": 1})
        assert '"a"' in result

    def test_other(self) -> None:
        ex = GatherExecutor()
        assert ex._format_output(123) == "123"


# =============================================================================
# _fallback_execution
# =============================================================================


class TestFallbackExecution:
    @pytest.mark.asyncio
    async def test_service_status_missing(self) -> None:
        ex = GatherExecutor()
        output, error = await ex._fallback_execution("service.status", {})
        assert output is None
        assert "Missing" in (error or "")

    @pytest.mark.asyncio
    async def test_service_status_success(self) -> None:
        ex = GatherExecutor()
        mock_result = MagicMock(success=True, stdout="active", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await ex._fallback_execution("service.status", {"service": "sshd"})
            assert output == "active"

    @pytest.mark.asyncio
    async def test_service_logs_missing(self) -> None:
        ex = GatherExecutor()
        output, error = await ex._fallback_execution("service.logs", {})
        assert "Missing" in (error or "")

    @pytest.mark.asyncio
    async def test_file_read_missing(self) -> None:
        ex = GatherExecutor()
        output, error = await ex._fallback_execution("file.read", {})
        assert "Missing" in (error or "")

    @pytest.mark.asyncio
    async def test_file_read_success(self, tmp_path: Any) -> None:
        ex = GatherExecutor()
        f = tmp_path / "test.txt"
        f.write_text("content")
        output, error = await ex._fallback_execution("file.read", {"path": str(f)})
        assert output == "content"

    @pytest.mark.asyncio
    async def test_package_info_missing(self) -> None:
        ex = GatherExecutor()
        output, error = await ex._fallback_execution("package.info", {})
        assert "Missing" in (error or "")

    @pytest.mark.asyncio
    async def test_docker_list(self) -> None:
        ex = GatherExecutor()
        mock_result = MagicMock(success=True, stdout="containers list", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await ex._fallback_execution("docker.list", {})
            assert output is not None

    @pytest.mark.asyncio
    async def test_docker_inspect_missing(self) -> None:
        ex = GatherExecutor()
        output, error = await ex._fallback_execution("docker.inspect", {})
        assert "Missing" in (error or "")

    @pytest.mark.asyncio
    async def test_docker_logs_missing(self) -> None:
        ex = GatherExecutor()
        output, error = await ex._fallback_execution("docker.logs", {})
        assert "Missing" in (error or "")

    @pytest.mark.asyncio
    async def test_network_listeners(self) -> None:
        ex = GatherExecutor()
        mock_result = MagicMock(success=True, stdout="LISTEN 8080", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await ex._fallback_execution("network.listeners", {})
            assert output is not None

    @pytest.mark.asyncio
    async def test_system_info(self) -> None:
        ex = GatherExecutor()
        mock_result = MagicMock(success=True, stdout="Linux", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await ex._fallback_execution("system.info", {})
            assert output is not None

    @pytest.mark.asyncio
    async def test_system_resources(self) -> None:
        ex = GatherExecutor()
        mock_result = MagicMock(success=True, stdout="Mem: 16G", stderr="")
        with patch("elle.cli.subprocess_runner.run_safe", return_value=mock_result):
            output, error = await ex._fallback_execution("system.resources", {})
            assert output is not None

    @pytest.mark.asyncio
    async def test_unknown_capability(self) -> None:
        ex = GatherExecutor()
        output, error = await ex._fallback_execution("unknown.cap", {})
        assert output is None
        assert "Unknown" in (error or "")


# =============================================================================
# READ_ONLY_CAPABILITIES constant
# =============================================================================


class TestReadOnlyCapabilities:
    def test_contains_expected(self) -> None:
        assert "service.status" in READ_ONLY_CAPABILITIES
        assert "file.read" in READ_ONLY_CAPABILITIES
        assert "docker.list" in READ_ONLY_CAPABILITIES
        assert "network.listeners" in READ_ONLY_CAPABILITIES
        assert "system.info" in READ_ONLY_CAPABILITIES

    def test_does_not_contain_mutating(self) -> None:
        assert "service.restart" not in READ_ONLY_CAPABILITIES
        assert "file.write" not in READ_ONLY_CAPABILITIES
