"""Tests for agentic tool definitions and execution.

Covers all seven tool functions in elle.cli.agentic.tools:
  - search_man_vault
  - search_incidents
  - execute_capability
  - get_system_info (all 7 aspects + filter branches)
  - shell_command (blocked commands, success, error, stderr)
  - list_capabilities (domain filter, search filter, fallback)
  - search_capabilities (success, empty, error/fallback)
Plus the ToolRegistry class (register, get, list_specs, to_ollama_tools,
execute dispatch, unknown tool, unhandled tool name, and exception handling)
and module-level singletons (get_tool_registry, reset_tool_registry).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.cli.agentic.tools import (
    EXECUTE_CAPABILITY_SPEC,
    GET_SYSTEM_INFO_SPEC,
    LIST_CAPABILITIES_SPEC,
    SEARCH_CAPABILITIES_SPEC,
    SEARCH_INCIDENTS_SPEC,
    SEARCH_MAN_VAULT_SPEC,
    SHELL_COMMAND_SPEC,
    ExecuteCapabilityInput,
    GetSystemInfoInput,
    ListCapabilitiesInput,
    SearchCapabilitiesInput,
    SearchIncidentsInput,
    SearchManVaultInput,
    ShellCommandInput,
    SystemInfoAspect,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    execute_capability,
    get_system_info,
    get_tool_registry,
    list_capabilities,
    reset_tool_registry,
    search_capabilities,
    search_incidents,
    search_man_vault,
    shell_command,
)

# Patch target prefixes for lazy imports inside tool functions
_MAN_VAULT = "elle.cli.agentic.tools.search"  # patched via module import
_INCIDENTS = "elle.cli.agentic.tools.get_prior_art"


# =============================================================================
# Helpers
# =============================================================================


def _make_subprocess_result(
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    timed_out: bool = False,
    denied: bool = False,
) -> MagicMock:
    """Build a mock SubprocessResult with the given fields."""
    mock = MagicMock()
    mock.stdout = stdout
    mock.stderr = stderr
    mock.exit_code = exit_code
    mock.timed_out = timed_out
    mock.denied = denied
    mock.success = exit_code == 0 and not timed_out and not denied
    return mock


def _make_man_snippet(
    name: str = "ls",
    section: str = "1",
    snippet: str = "list directory contents",
    score: float = 0.85,
    match_section: str | None = "DESCRIPTION",
) -> MagicMock:
    """Build a mock ManSnippet-like object."""
    mock = MagicMock()
    mock.name = name
    mock.section = section
    mock.snippet = snippet
    mock.score = score
    mock.match_section = match_section
    return mock


def _make_prior_art_dict(
    incident_id: str = "inc-001",
    title: str = "disk full",
    summary: str = "disk was full",
    outcome: str = "improved",
    outcome_weight: float = 0.9,
    decision: str | None = "cleaned /tmp",
    root_cause: str | None = "log rotation disabled",
    successful_actions: list[dict[str, Any]] | None = None,
    days_ago: int = 3,
) -> dict[str, Any]:
    """Build a prior-art dict as returned by get_prior_art."""
    return {
        "incident_id": incident_id,
        "title": title,
        "summary": summary,
        "outcome": outcome,
        "outcome_weight": outcome_weight,
        "decision": decision,
        "root_cause": root_cause,
        "successful_actions": successful_actions or [],
        "days_ago": days_ago,
    }


def _make_capability_search_result(
    capability_name: str = "service.restart",
    domain: str = "service",
    description: str = "Restart a systemd service",
    risk_level: str = "medium",
    score: float = 0.92,
    success_rate: float = 0.95,
    is_approved: bool = True,
    source_command: str | None = None,
) -> MagicMock:
    """Build a mock CapabilitySearchResult."""
    mock = MagicMock()
    mock.capability_name = capability_name
    mock.domain = domain
    mock.description = description
    mock.risk_level = risk_level
    mock.score = score
    mock.success_rate = success_rate
    mock.is_approved = is_approved
    mock.source_command = source_command
    return mock


def _make_cap_spec(
    name: str = "service.restart",
    domain: str = "service",
    risk: str = "medium",
    summary: str = "Restart a systemd service",
) -> MagicMock:
    """Build a mock CapabilitySpec for the registry."""
    mock = MagicMock()
    mock.name = name
    mock.domain = domain
    mock.risk = risk
    mock.summary = summary
    return mock


# =============================================================================
# TestSearchManVault
# =============================================================================


class TestSearchManVault:
    """Tests for the search_man_vault tool function."""

    @pytest.mark.asyncio
    @patch("elle.daemon.manvault.retriever.search")
    async def test_success_with_results(self, mock_search: MagicMock) -> None:
        """Successful search returns formatted snippets."""
        mock_search.return_value = [
            _make_man_snippet(name="systemctl", section="1", snippet="Control systemd", score=0.95),
            _make_man_snippet(name="journalctl", section="1", snippet="Query journal", score=0.80),
        ]
        args = SearchManVaultInput(query="service management", k=5, search_type="hybrid")
        result = await search_man_vault(args)

        assert result.success is True
        assert result.tool_name == "search_man_vault"
        assert "2 relevant documentation snippets" in result.output
        assert "systemctl" in result.output
        assert "journalctl" in result.output
        assert result.error is None
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    @patch("elle.daemon.manvault.retriever.search")
    async def test_empty_results(self, mock_search: MagicMock) -> None:
        """Empty search returns 'no documentation' message."""
        mock_search.return_value = []
        args = SearchManVaultInput(query="nonexistent topic")
        result = await search_man_vault(args)

        assert result.success is True
        assert "No documentation found" in result.output

    @pytest.mark.asyncio
    @patch("elle.daemon.manvault.retriever.search")
    async def test_with_command_filter(self, mock_search: MagicMock) -> None:
        """Command filter is passed through to the retriever."""
        mock_search.return_value = [_make_man_snippet(name="ls")]
        args = SearchManVaultInput(query="list files", command="ls")
        await search_man_vault(args)

        mock_search.assert_called_once_with(query="list files", k=5, name="ls", search_type="hybrid")

    @pytest.mark.asyncio
    @patch("elle.daemon.manvault.retriever.search", side_effect=RuntimeError("DB unavailable"))
    async def test_exception_returns_failure(self, mock_search: MagicMock) -> None:
        """Exception during search returns a failure ToolResult."""
        args = SearchManVaultInput(query="test")
        result = await search_man_vault(args)

        assert result.success is False
        assert result.error == "DB unavailable"
        assert result.output == ""

    @pytest.mark.asyncio
    @patch("elle.daemon.manvault.retriever.search")
    async def test_snippet_without_match_section(self, mock_search: MagicMock) -> None:
        """Snippet with match_section=None renders 'general' in output."""
        mock_search.return_value = [_make_man_snippet(match_section=None)]
        args = SearchManVaultInput(query="test")
        result = await search_man_vault(args)

        assert result.success is True
        assert "[general]" in result.output


# =============================================================================
# TestSearchIncidents
# =============================================================================


class TestSearchIncidents:
    """Tests for the search_incidents tool function."""

    @pytest.mark.asyncio
    @patch("elle.daemon.incidents.retriever.get_prior_art")
    async def test_success_with_results(self, mock_get: MagicMock) -> None:
        """Successful search returns formatted incidents."""
        mock_get.return_value = [
            _make_prior_art_dict(
                title="OOM kill",
                root_cause="memory leak",
                decision="increased swap",
                successful_actions=[{"command": "swapon /swapfile"}],
            )
        ]
        args = SearchIncidentsInput(query="memory issues", k=3)
        result = await search_incidents(args)

        assert result.success is True
        assert "1 similar past incidents" in result.output
        assert "OOM kill" in result.output
        assert "memory leak" in result.output
        assert "increased swap" in result.output
        assert "swapon /swapfile" in result.output

    @pytest.mark.asyncio
    @patch("elle.daemon.incidents.retriever.get_prior_art")
    async def test_empty_results(self, mock_get: MagicMock) -> None:
        """Empty search returns 'no similar' message."""
        mock_get.return_value = []
        args = SearchIncidentsInput(query="no match")
        result = await search_incidents(args)

        assert result.success is True
        assert "No similar past incidents" in result.output

    @pytest.mark.asyncio
    @patch("elle.daemon.incidents.retriever.get_prior_art")
    async def test_domain_filter_passed(self, mock_get: MagicMock) -> None:
        """Domain filter is forwarded to get_prior_art."""
        mock_get.return_value = []
        args = SearchIncidentsInput(query="disk full", domain="disk")
        await search_incidents(args)

        mock_get.assert_called_once_with(query="disk full", domain="disk", k=3, include_actions=True)

    @pytest.mark.asyncio
    @patch("elle.daemon.incidents.retriever.get_prior_art", side_effect=RuntimeError("DB down"))
    async def test_exception_returns_failure(self, mock_get: MagicMock) -> None:
        """Exception returns a failure ToolResult."""
        args = SearchIncidentsInput(query="fail")
        result = await search_incidents(args)

        assert result.success is False
        assert result.error == "DB down"

    @pytest.mark.asyncio
    @patch("elle.daemon.incidents.retriever.get_prior_art")
    async def test_incident_without_optional_fields(self, mock_get: MagicMock) -> None:
        """Incidents missing decision/root_cause/actions still format correctly."""
        mock_get.return_value = [_make_prior_art_dict(decision=None, root_cause=None, successful_actions=[])]
        args = SearchIncidentsInput(query="test")
        result = await search_incidents(args)

        assert result.success is True
        assert "Root Cause" not in result.output
        assert "Decision" not in result.output


# =============================================================================
# TestExecuteCapability
# =============================================================================


class TestExecuteCapability:
    """Tests for the execute_capability tool function."""

    @pytest.mark.asyncio
    @patch("elle.cli.agentic.plan_executor.get_plan_executor")
    async def test_success_with_evidence(self, mock_get_exec: MagicMock) -> None:
        """Successful execution returns output from evidence."""
        ev = MagicMock()
        ev.success = True
        ev.output_text = "nginx restarted"
        ev.output = "nginx restarted"  # Must be truthy for output_text branch
        ev.error = None

        exec_result = MagicMock()
        exec_result.evidence = [ev]

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value=exec_result)
        mock_get_exec.return_value = mock_executor

        args = ExecuteCapabilityInput(capability_name="service.restart", args={"service": "nginx"})
        result = await execute_capability(args)

        assert result.success is True
        assert "nginx restarted" in result.output
        assert result.tool_name == "execute_capability"

    @pytest.mark.asyncio
    @patch("elle.cli.agentic.plan_executor.get_plan_executor")
    async def test_success_with_output_fallback(self, mock_get_exec: MagicMock) -> None:
        """When output_text is None, falls back to str(output)."""
        ev = MagicMock()
        ev.success = True
        ev.output_text = None
        ev.output = {"status": "active"}
        ev.error = None

        exec_result = MagicMock()
        exec_result.evidence = [ev]

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value=exec_result)
        mock_get_exec.return_value = mock_executor

        args = ExecuteCapabilityInput(capability_name="service.status", args={"service": "nginx"})
        result = await execute_capability(args)

        assert result.success is True
        assert "status" in result.output

    @pytest.mark.asyncio
    @patch("elle.cli.agentic.plan_executor.get_plan_executor")
    async def test_evidence_with_error(self, mock_get_exec: MagicMock) -> None:
        """Evidence with an error returns the error message."""
        ev = MagicMock()
        ev.success = False
        ev.output_text = None
        ev.output = None
        ev.error = "service not found"

        exec_result = MagicMock()
        exec_result.evidence = [ev]

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value=exec_result)
        mock_get_exec.return_value = mock_executor

        args = ExecuteCapabilityInput(capability_name="service.restart", args={"service": "missing"})
        result = await execute_capability(args)

        assert result.success is False
        assert "service not found" in result.output

    @pytest.mark.asyncio
    @patch("elle.cli.agentic.plan_executor.get_plan_executor")
    async def test_no_evidence(self, mock_get_exec: MagicMock) -> None:
        """Empty evidence list returns 'No execution result'."""
        exec_result = MagicMock()
        exec_result.evidence = []

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value=exec_result)
        mock_get_exec.return_value = mock_executor

        args = ExecuteCapabilityInput(capability_name="service.restart", args={})
        result = await execute_capability(args)

        assert result.success is False
        assert result.error == "No execution result"

    @pytest.mark.asyncio
    @patch("elle.cli.agentic.plan_executor.get_plan_executor", side_effect=RuntimeError("Import fail"))
    async def test_exception_returns_failure(self, mock_get_exec: MagicMock) -> None:
        """Exception during execution returns failure ToolResult."""
        args = ExecuteCapabilityInput(capability_name="service.restart", args={})
        result = await execute_capability(args)

        assert result.success is False
        assert "Import fail" in (result.error or "")

    @pytest.mark.asyncio
    @patch("elle.cli.agentic.plan_executor.get_plan_executor")
    async def test_read_only_detection(self, mock_get_exec: MagicMock) -> None:
        """Capabilities ending in read-only suffixes get is_read_only=True."""
        ev = MagicMock()
        ev.success = True
        ev.output_text = "ok"
        ev.output = None
        ev.error = None

        exec_result = MagicMock()
        exec_result.evidence = [ev]

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value=exec_result)
        mock_get_exec.return_value = mock_executor

        args = ExecuteCapabilityInput(capability_name="service.status", args={})
        result = await execute_capability(args)

        assert result.success is True
        # Verify the CapabilityCall was built with is_read_only=True
        call_kwargs = mock_executor.execute.call_args
        plan = call_kwargs[0][0]
        cap_call = plan.parallel_groups[0].calls[0]
        assert cap_call.is_read_only is True

    @pytest.mark.asyncio
    @patch("elle.cli.agentic.plan_executor.get_plan_executor")
    async def test_evidence_completed_successfully_fallback(self, mock_get_exec: MagicMock) -> None:
        """When evidence has no output_text and output is falsy, returns 'Completed successfully'."""
        ev = MagicMock()
        ev.success = True
        ev.output_text = None
        ev.output = None
        ev.error = None

        exec_result = MagicMock()
        exec_result.evidence = [ev]

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value=exec_result)
        mock_get_exec.return_value = mock_executor

        args = ExecuteCapabilityInput(capability_name="service.restart", args={})
        result = await execute_capability(args)

        assert result.success is True
        assert result.output == "Completed successfully"


# =============================================================================
# TestGetSystemInfo
# =============================================================================


class TestGetSystemInfo:
    """Tests for the get_system_info tool function, all 7 aspects."""

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_resources_success(self, mock_run: MagicMock) -> None:
        """Resources aspect returns stdout on success."""
        mock_run.return_value = _make_subprocess_result(stdout="Mem: 16Gi")
        args = GetSystemInfoInput(aspect=SystemInfoAspect.RESOURCES)
        result = await get_system_info(args)

        assert result.success is True
        assert "Mem: 16Gi" in result.output

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_resources_failure(self, mock_run: MagicMock) -> None:
        """Resources aspect returns error on failure."""
        mock_run.return_value = _make_subprocess_result(stderr="command not found", exit_code=1)
        args = GetSystemInfoInput(aspect=SystemInfoAspect.RESOURCES)
        result = await get_system_info(args)

        assert result.success is True  # tool itself succeeds
        assert "Error:" in result.output

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_services_without_filter(self, mock_run: MagicMock) -> None:
        """Services aspect without filter lists running/failed units."""
        mock_run.return_value = _make_subprocess_result(stdout="nginx.service active running")
        args = GetSystemInfoInput(aspect=SystemInfoAspect.SERVICES)
        result = await get_system_info(args)

        assert result.success is True
        assert "nginx.service" in result.output
        # Verify the command used list-units
        call_args = mock_run.call_args
        assert "list-units" in call_args[0][0]

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_services_with_filter(self, mock_run: MagicMock) -> None:
        """Services aspect with filter checks specific service status."""
        mock_run.return_value = _make_subprocess_result(stdout="active (running)")
        args = GetSystemInfoInput(aspect=SystemInfoAspect.SERVICES, filter="nginx")
        result = await get_system_info(args)

        assert result.success is True
        call_args = mock_run.call_args
        assert "systemctl status nginx" in call_args[0][0]

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_listeners(self, mock_run: MagicMock) -> None:
        """Listeners aspect returns network listener info."""
        mock_run.return_value = _make_subprocess_result(stdout="*:80  *:443")
        args = GetSystemInfoInput(aspect=SystemInfoAspect.LISTENERS)
        result = await get_system_info(args)

        assert result.success is True
        assert ":80" in result.output

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_containers_without_filter(self, mock_run: MagicMock) -> None:
        """Containers aspect without filter lists all containers."""
        mock_run.return_value = _make_subprocess_result(stdout="my_container  Up 2 hours")
        args = GetSystemInfoInput(aspect=SystemInfoAspect.CONTAINERS)
        result = await get_system_info(args)

        assert result.success is True
        call_args = mock_run.call_args
        assert "docker ps" in call_args[0][0]

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_containers_with_filter(self, mock_run: MagicMock) -> None:
        """Containers aspect with filter inspects specific container."""
        mock_run.return_value = _make_subprocess_result(stdout='{"Name":"web"}')
        args = GetSystemInfoInput(aspect=SystemInfoAspect.CONTAINERS, filter="web")
        result = await get_system_info(args)

        assert result.success is True
        call_args = mock_run.call_args
        assert "docker inspect web" in call_args[0][0]

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_packages(self, mock_run: MagicMock) -> None:
        """Packages aspect returns dpkg log tail."""
        mock_run.return_value = _make_subprocess_result(stdout="install nginx 1.22")
        args = GetSystemInfoInput(aspect=SystemInfoAspect.PACKAGES)
        result = await get_system_info(args)

        assert result.success is True
        assert "install nginx" in result.output

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_kernel(self, mock_run: MagicMock) -> None:
        """Kernel aspect returns uname and os-release info."""
        mock_run.return_value = _make_subprocess_result(stdout="Linux 5.15.0-generic")
        args = GetSystemInfoInput(aspect=SystemInfoAspect.KERNEL)
        result = await get_system_info(args)

        assert result.success is True
        assert "Linux 5.15" in result.output

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_recent_events_without_filter(self, mock_run: MagicMock) -> None:
        """Recent events without filter queries journalctl warnings."""
        mock_run.return_value = _make_subprocess_result(stdout="Jan 01 warning: oom")
        args = GetSystemInfoInput(aspect=SystemInfoAspect.RECENT_EVENTS)
        result = await get_system_info(args)

        assert result.success is True
        call_args = mock_run.call_args
        assert "-p warning" in call_args[0][0]

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_recent_events_with_filter(self, mock_run: MagicMock) -> None:
        """Recent events with filter queries specific unit."""
        mock_run.return_value = _make_subprocess_result(stdout="nginx log entry")
        args = GetSystemInfoInput(aspect=SystemInfoAspect.RECENT_EVENTS, filter="nginx")
        result = await get_system_info(args)

        assert result.success is True
        call_args = mock_run.call_args
        assert "-u nginx" in call_args[0][0]

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe", side_effect=OSError("No such file"))
    async def test_exception_returns_failure(self, mock_run: MagicMock) -> None:
        """Exception from run_safe returns failure ToolResult."""
        args = GetSystemInfoInput(aspect=SystemInfoAspect.RESOURCES)
        result = await get_system_info(args)

        assert result.success is False
        assert "No such file" in (result.error or "")


# =============================================================================
# TestShellCommand
# =============================================================================


class TestShellCommand:
    """Tests for the shell_command tool function."""

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_success(self, mock_run: MagicMock) -> None:
        """Successful command returns stdout."""
        mock_run.return_value = _make_subprocess_result(stdout="total 42\n-rw-r--r-- foo")
        args = ShellCommandInput(command="ls -la /tmp")
        result = await shell_command(args)

        assert result.success is True
        assert "total 42" in result.output
        assert result.error is None

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_with_stderr(self, mock_run: MagicMock) -> None:
        """Successful command with stderr appends stderr to output."""
        mock_run.return_value = _make_subprocess_result(stdout="ok", stderr="deprecation warning")
        args = ShellCommandInput(command="some-cmd")
        result = await shell_command(args)

        assert result.success is True
        assert "[stderr]:" in result.output
        assert "deprecation warning" in result.output

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_command_failure(self, mock_run: MagicMock) -> None:
        """Failed command returns success=False and error."""
        mock_run.return_value = _make_subprocess_result(stdout="", stderr="file not found", exit_code=1)
        args = ShellCommandInput(command="cat /nonexistent")
        result = await shell_command(args)

        assert result.success is False
        assert result.error == "file not found"

    @pytest.mark.asyncio
    async def test_blocked_rm_rf(self) -> None:
        """rm -rf / is blocked by centralized denylist."""
        args = ShellCommandInput(command="rm -rf /")
        result = await shell_command(args)

        assert result.success is False
        assert "blocked for safety" in (result.error or "").lower() or "denied" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_blocked_sudo(self) -> None:
        """sudo is blocked."""
        args = ShellCommandInput(command="sudo apt update")
        result = await shell_command(args)

        assert result.success is False
        assert "blocked for safety" in (result.error or "")

    @pytest.mark.asyncio
    async def test_blocked_fork_bomb(self) -> None:
        """Fork bomb :(){ pattern is blocked."""
        args = ShellCommandInput(command=":(){ :|:& };:")
        result = await shell_command(args)

        assert result.success is False
        assert "blocked for safety" in (result.error or "")

    @pytest.mark.asyncio
    async def test_blocked_mkfs(self) -> None:
        """mkfs command is blocked."""
        args = ShellCommandInput(command="mkfs.ext4 /dev/sda1")
        result = await shell_command(args)

        assert result.success is False
        assert "blocked for safety" in (result.error or "")

    @pytest.mark.asyncio
    async def test_blocked_dd_of_dev(self) -> None:
        """dd of=/dev is blocked."""
        args = ShellCommandInput(command="dd if=/dev/zero of=/dev/sda bs=512 count=1")
        result = await shell_command(args)

        assert result.success is False
        assert "blocked for safety" in (result.error or "")

    @pytest.mark.asyncio
    async def test_blocked_shutdown(self) -> None:
        """shutdown command is blocked."""
        args = ShellCommandInput(command="shutdown -h now")
        result = await shell_command(args)

        assert result.success is False
        assert "blocked for safety" in (result.error or "")

    @pytest.mark.asyncio
    async def test_blocked_case_insensitive(self) -> None:
        """Blocked commands are checked case-insensitively."""
        args = ShellCommandInput(command="SUDO apt install nginx")
        result = await shell_command(args)

        assert result.success is False
        assert "blocked for safety" in (result.error or "")

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe", side_effect=OSError("exec fail"))
    async def test_exception_returns_failure(self, mock_run: MagicMock) -> None:
        """Exception during run_safe returns failure ToolResult."""
        args = ShellCommandInput(command="echo hello")
        result = await shell_command(args)

        assert result.success is False
        assert "exec fail" in (result.error or "")


# =============================================================================
# TestListCapabilities
# =============================================================================


class TestListCapabilities:
    """Tests for the list_capabilities tool function."""

    @pytest.mark.asyncio
    @patch("elle.capabilities.get_registry")
    async def test_list_all(self, mock_get_reg: MagicMock) -> None:
        """List all capabilities without filters."""
        caps = [
            _make_cap_spec(name="service.restart", domain="service", risk="medium"),
            _make_cap_spec(name="file.read", domain="file", risk="none", summary="Read a file"),
        ]
        mock_registry = MagicMock()
        mock_registry.list_all.return_value = caps
        mock_get_reg.return_value = mock_registry

        args = ListCapabilitiesInput()
        result = await list_capabilities(args)

        assert result.success is True
        assert "2 capabilities" in result.output
        assert "file.read" in result.output
        assert "[R]" in result.output  # file.read is read-only
        mock_registry.list_all.assert_called_once()

    @pytest.mark.asyncio
    @patch("elle.capabilities.get_registry")
    async def test_filter_by_domain(self, mock_get_reg: MagicMock) -> None:
        """Filter by domain calls list_by_domain."""
        caps = [_make_cap_spec(name="service.restart", domain="service")]
        mock_registry = MagicMock()
        mock_registry.list_by_domain.return_value = caps
        mock_get_reg.return_value = mock_registry

        args = ListCapabilitiesInput(domain="service")
        result = await list_capabilities(args)

        assert result.success is True
        mock_registry.list_by_domain.assert_called_once()

    @pytest.mark.asyncio
    @patch("elle.capabilities.get_registry")
    async def test_search_filter_matches(self, mock_get_reg: MagicMock) -> None:
        """Search filter matches by name or description."""
        caps = [
            _make_cap_spec(name="service.restart", summary="Restart a service"),
            _make_cap_spec(name="file.read", summary="Read a file"),
        ]
        mock_registry = MagicMock()
        mock_registry.list_all.return_value = caps
        mock_get_reg.return_value = mock_registry

        args = ListCapabilitiesInput(search="restart")
        result = await list_capabilities(args)

        assert result.success is True
        assert "service.restart" in result.output
        assert "file.read" not in result.output

    @pytest.mark.asyncio
    @patch("elle.capabilities.get_registry")
    async def test_search_filter_no_match(self, mock_get_reg: MagicMock) -> None:
        """Search filter with no matches returns appropriate message."""
        caps = [_make_cap_spec(name="service.restart")]
        mock_registry = MagicMock()
        mock_registry.list_all.return_value = caps
        mock_get_reg.return_value = mock_registry

        args = ListCapabilitiesInput(search="nonexistent_thing")
        result = await list_capabilities(args)

        assert result.success is True
        assert "No capabilities found" in result.output

    @pytest.mark.asyncio
    @patch("elle.capabilities.get_registry", side_effect=RuntimeError("Registry unavailable"))
    async def test_fallback_on_error(self, mock_get_reg: MagicMock) -> None:
        """Exception triggers fallback capability list."""
        args = ListCapabilitiesInput()
        result = await list_capabilities(args)

        # Fallback still returns success=True with a static list
        assert result.success is True
        assert "registry unavailable" in result.output.lower()
        assert "service.status" in result.output
        assert "file.read" in result.output

    @pytest.mark.asyncio
    @patch("elle.capabilities.get_registry")
    async def test_read_only_vs_write_markers(self, mock_get_reg: MagicMock) -> None:
        """Read-only capabilities get [R], mutating get [W]."""
        caps = [
            _make_cap_spec(name="service.status"),  # read-only
            _make_cap_spec(name="service.restart"),  # mutating
        ]
        mock_registry = MagicMock()
        mock_registry.list_all.return_value = caps
        mock_get_reg.return_value = mock_registry

        args = ListCapabilitiesInput()
        result = await list_capabilities(args)

        assert result.success is True
        # Verify both markers appear
        assert "[R]" in result.output
        assert "[W]" in result.output


# =============================================================================
# TestSearchCapabilities
# =============================================================================


class TestSearchCapabilities:
    """Tests for the search_capabilities tool function."""

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.retriever.search_capabilities")
    async def test_success_with_results(self, mock_search: MagicMock) -> None:
        """Successful search returns formatted capability matches."""
        mock_search.return_value = [
            _make_capability_search_result(
                capability_name="service.restart",
                score=0.95,
                source_command="systemctl restart",
            ),
        ]
        args = SearchCapabilitiesInput(query="restart web server")
        result = await search_capabilities(args)

        assert result.success is True
        assert "service.restart" in result.output
        assert "[approved]" in result.output
        assert "systemctl restart" in result.output

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.retriever.search_capabilities")
    async def test_unapproved_marker(self, mock_search: MagicMock) -> None:
        """Unapproved capabilities show [pending approval] marker."""
        mock_search.return_value = [
            _make_capability_search_result(is_approved=False),
        ]
        args = SearchCapabilitiesInput(query="restart", include_unapproved=True)
        result = await search_capabilities(args)

        assert result.success is True
        assert "[pending approval]" in result.output

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.retriever.search_capabilities")
    async def test_empty_results(self, mock_search: MagicMock) -> None:
        """Empty results return a helpful message."""
        mock_search.return_value = []
        args = SearchCapabilitiesInput(query="something obscure")
        result = await search_capabilities(args)

        assert result.success is True
        assert "No capabilities found" in result.output
        assert "list_capabilities" in result.output

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.retriever.search_capabilities", side_effect=RuntimeError("DB error"))
    async def test_exception_returns_fallback(self, mock_search: MagicMock) -> None:
        """Exception returns fallback help text."""
        args = SearchCapabilitiesInput(query="restart")
        result = await search_capabilities(args)

        assert result.success is False
        assert "DB error" in (result.error or "")
        assert "service.restart" in result.output  # fallback suggestions

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.retriever.search_capabilities")
    async def test_domain_filter_passed(self, mock_search: MagicMock) -> None:
        """Domain filter is forwarded to the retriever."""
        mock_search.return_value = []
        args = SearchCapabilitiesInput(query="restart", domain="service", k=3)
        await search_capabilities(args)

        mock_search.assert_called_once_with(
            query="restart",
            domain="service",
            k=3,
            search_type="hybrid",
            include_unapproved=False,
        )


# =============================================================================
# TestToolRegistry
# =============================================================================


class TestToolRegistry:
    """Tests for the ToolRegistry class."""

    def test_default_tools_registered(self) -> None:
        """Registry initializes with all 7 built-in tools."""
        registry = ToolRegistry()
        specs = registry.list_specs()
        names = {s.name for s in specs}

        assert "search_man_vault" in names
        assert "search_incidents" in names
        assert "execute_capability" in names
        assert "get_system_info" in names
        assert "shell_command" in names
        assert "list_capabilities" in names
        assert "search_capabilities" in names
        assert len(names) == 7

    def test_get_existing_tool(self) -> None:
        """get() returns (spec, handler) for a registered tool."""
        registry = ToolRegistry()
        result = registry.get("search_man_vault")

        assert result is not None
        spec, handler = result
        assert spec.name == "search_man_vault"
        assert callable(handler)

    def test_get_missing_tool(self) -> None:
        """get() returns None for an unregistered tool."""
        registry = ToolRegistry()
        assert registry.get("nonexistent_tool") is None

    def test_register_custom_tool(self) -> None:
        """register() adds a custom tool."""
        registry = ToolRegistry()

        async def custom_handler(args: Any) -> ToolResult:
            return ToolResult(tool_name="custom", success=True, output="ok")

        spec = ToolSpec(
            name="custom",
            description="A custom tool",
            parameters={"type": "object", "properties": {}},
        )
        registry.register(spec, custom_handler)

        assert registry.get("custom") is not None
        assert len(registry.list_specs()) == 8

    def test_to_ollama_tools_format(self) -> None:
        """to_ollama_tools() returns properly formatted tool definitions."""
        registry = ToolRegistry()
        ollama_tools = registry.to_ollama_tools()

        assert len(ollama_tools) == 7
        for tool in ollama_tools:
            assert tool["type"] == "function"
            assert "function" in tool
            assert "name" in tool["function"]
            assert "description" in tool["function"]
            assert "parameters" in tool["function"]

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self) -> None:
        """execute() with an unknown tool name returns error."""
        registry = ToolRegistry()
        result = await registry.execute("nonexistent", {})

        assert result.success is False
        assert "Unknown tool" in (result.error or "")
        assert result.tool_name == "nonexistent"

    @pytest.mark.asyncio
    @patch("elle.daemon.manvault.retriever.search")
    async def test_execute_search_man_vault(self, mock_search: MagicMock) -> None:
        """execute() dispatches search_man_vault correctly."""
        mock_search.return_value = []
        registry = ToolRegistry()
        result = await registry.execute("search_man_vault", {"query": "test"})

        assert result.success is True
        assert result.tool_name == "search_man_vault"

    @pytest.mark.asyncio
    @patch("elle.daemon.incidents.retriever.get_prior_art")
    async def test_execute_search_incidents(self, mock_get: MagicMock) -> None:
        """execute() dispatches search_incidents correctly."""
        mock_get.return_value = []
        registry = ToolRegistry()
        result = await registry.execute("search_incidents", {"query": "test"})

        assert result.success is True
        assert result.tool_name == "search_incidents"

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_execute_shell_command(self, mock_run: MagicMock) -> None:
        """execute() dispatches shell_command correctly."""
        mock_run.return_value = _make_subprocess_result(stdout="hello")
        registry = ToolRegistry()
        result = await registry.execute("shell_command", {"command": "echo hello"})

        assert result.success is True

    @pytest.mark.asyncio
    @patch("elle.cli.subprocess_runner.run_safe")
    async def test_execute_get_system_info(self, mock_run: MagicMock) -> None:
        """execute() dispatches get_system_info correctly."""
        mock_run.return_value = _make_subprocess_result(stdout="Linux 5.15")
        registry = ToolRegistry()
        result = await registry.execute("get_system_info", {"aspect": "kernel"})

        assert result.success is True

    @pytest.mark.asyncio
    @patch("elle.capabilities.get_registry")
    async def test_execute_list_capabilities(self, mock_get_reg: MagicMock) -> None:
        """execute() dispatches list_capabilities correctly."""
        mock_registry = MagicMock()
        mock_registry.list_all.return_value = []
        mock_get_reg.return_value = mock_registry

        registry = ToolRegistry()
        result = await registry.execute("list_capabilities", {})

        assert result.success is True

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.retriever.search_capabilities")
    async def test_execute_search_capabilities(self, mock_search: MagicMock) -> None:
        """execute() dispatches search_capabilities correctly."""
        mock_search.return_value = []
        registry = ToolRegistry()
        result = await registry.execute("search_capabilities", {"query": "restart"})

        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_validation_error(self) -> None:
        """execute() with invalid args returns error from Pydantic validation."""
        registry = ToolRegistry()
        # Missing required 'query' field for search_man_vault
        result = await registry.execute("search_man_vault", {})

        assert result.success is False
        assert result.error is not None


# =============================================================================
# TestToolRegistrySingleton
# =============================================================================


class TestToolRegistrySingleton:
    """Tests for module-level singleton functions."""

    def setup_method(self) -> None:
        """Reset the singleton before each test."""
        reset_tool_registry()

    def teardown_method(self) -> None:
        """Reset after each test."""
        reset_tool_registry()

    def test_get_tool_registry_creates_singleton(self) -> None:
        """get_tool_registry creates a new registry on first call."""
        reg = get_tool_registry()
        assert isinstance(reg, ToolRegistry)

    def test_get_tool_registry_returns_same_instance(self) -> None:
        """get_tool_registry returns the same instance on subsequent calls."""
        reg1 = get_tool_registry()
        reg2 = get_tool_registry()
        assert reg1 is reg2

    def test_reset_tool_registry(self) -> None:
        """reset_tool_registry clears the singleton."""
        reg1 = get_tool_registry()
        reset_tool_registry()
        reg2 = get_tool_registry()
        assert reg1 is not reg2


# =============================================================================
# TestToolSpecs
# =============================================================================


class TestToolSpecs:
    """Tests for the static ToolSpec definitions."""

    def test_search_man_vault_spec(self) -> None:
        """SEARCH_MAN_VAULT_SPEC has correct attributes."""
        assert SEARCH_MAN_VAULT_SPEC.name == "search_man_vault"
        assert SEARCH_MAN_VAULT_SPEC.is_mutating is False
        assert "query" in SEARCH_MAN_VAULT_SPEC.parameters["required"]

    def test_search_incidents_spec(self) -> None:
        """SEARCH_INCIDENTS_SPEC has correct attributes."""
        assert SEARCH_INCIDENTS_SPEC.name == "search_incidents"
        assert SEARCH_INCIDENTS_SPEC.is_mutating is False

    def test_execute_capability_spec(self) -> None:
        """EXECUTE_CAPABILITY_SPEC is marked as mutating."""
        assert EXECUTE_CAPABILITY_SPEC.name == "execute_capability"
        assert EXECUTE_CAPABILITY_SPEC.is_mutating is True

    def test_get_system_info_spec(self) -> None:
        """GET_SYSTEM_INFO_SPEC has correct attributes."""
        assert GET_SYSTEM_INFO_SPEC.name == "get_system_info"
        assert GET_SYSTEM_INFO_SPEC.is_mutating is False

    def test_shell_command_spec(self) -> None:
        """SHELL_COMMAND_SPEC has correct attributes."""
        assert SHELL_COMMAND_SPEC.name == "shell_command"
        assert SHELL_COMMAND_SPEC.is_mutating is False

    def test_list_capabilities_spec(self) -> None:
        """LIST_CAPABILITIES_SPEC has correct attributes."""
        assert LIST_CAPABILITIES_SPEC.name == "list_capabilities"
        assert LIST_CAPABILITIES_SPEC.is_mutating is False

    def test_search_capabilities_spec(self) -> None:
        """SEARCH_CAPABILITIES_SPEC has correct attributes."""
        assert SEARCH_CAPABILITIES_SPEC.name == "search_capabilities"
        assert SEARCH_CAPABILITIES_SPEC.is_mutating is False


# =============================================================================
# TestBlockedCommands
# =============================================================================


class TestBlockedCommands:
    """Tests for centralized denylist enforcement via check_denylist()."""

    def test_check_denylist_blocks_dangerous_commands(self) -> None:
        """check_denylist rejects critical unsafe patterns."""
        from elle.cli.subprocess_runner import check_denylist

        for cmd in ("rm -rf /", "sudo rm", "mkfs /dev/sda", "shutdown now"):
            denied, reason, explanation = check_denylist(cmd)
            assert denied, f"Expected '{cmd}' to be denied"

    def test_check_denylist_allows_safe_commands(self) -> None:
        """check_denylist allows harmless read-only commands."""
        from elle.cli.subprocess_runner import check_denylist

        denied, _, _ = check_denylist("ls -la")
        assert not denied


# =============================================================================
# TestToolResult
# =============================================================================


class TestToolResult:
    """Tests for the ToolResult model."""

    def test_default_values(self) -> None:
        """ToolResult has sensible defaults."""
        result = ToolResult(tool_name="test", success=True, output="ok")
        assert result.error is None
        assert result.duration_ms == 0
        assert result.timestamp is not None

    def test_frozen(self) -> None:
        """ToolResult is immutable (frozen)."""
        result = ToolResult(tool_name="test", success=True, output="ok")
        with pytest.raises(Exception):
            result.tool_name = "modified"  # type: ignore[misc]


# =============================================================================
# TestInputModels
# =============================================================================


class TestInputModels:
    """Tests for Pydantic input model validation."""

    def test_search_man_vault_defaults(self) -> None:
        """SearchManVaultInput has correct defaults."""
        inp = SearchManVaultInput(query="test")
        assert inp.k == 5
        assert inp.command is None
        assert inp.search_type == "hybrid"

    def test_search_incidents_defaults(self) -> None:
        """SearchIncidentsInput has correct defaults."""
        inp = SearchIncidentsInput(query="test")
        assert inp.k == 3
        assert inp.domain is None
        assert inp.include_actions is True

    def test_execute_capability_defaults(self) -> None:
        """ExecuteCapabilityInput has correct defaults."""
        inp = ExecuteCapabilityInput(capability_name="service.restart")
        assert inp.args == {}
        assert inp.skip_confirmation is False

    def test_shell_command_defaults(self) -> None:
        """ShellCommandInput has correct defaults."""
        inp = ShellCommandInput(command="echo hi")
        assert inp.timeout == 30.0

    def test_list_capabilities_defaults(self) -> None:
        """ListCapabilitiesInput has correct defaults."""
        inp = ListCapabilitiesInput()
        assert inp.domain is None
        assert inp.search is None

    def test_search_capabilities_defaults(self) -> None:
        """SearchCapabilitiesInput has correct defaults."""
        inp = SearchCapabilitiesInput(query="test")
        assert inp.k == 5
        assert inp.domain is None
        assert inp.include_unapproved is False

    def test_system_info_aspect_enum(self) -> None:
        """SystemInfoAspect enum has all 7 values."""
        aspects = list(SystemInfoAspect)
        assert len(aspects) == 7
        assert SystemInfoAspect.RESOURCES.value == "resources"
        assert SystemInfoAspect.CONTAINERS.value == "containers"
