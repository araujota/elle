"""Tests for the agentic loop system.

Tests the new ReAct-style tool-calling loop:
- Tool definitions and registry
- LLMSession with tool calling
- AgenticLoop execution
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.cli.agentic.tools import (
    ExecuteCapabilityInput,
    GetSystemInfoInput,
    ListCapabilitiesInput,
    SearchIncidentsInput,
    SearchManVaultInput,
    ShellCommandInput,
    SystemInfoAspect,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    get_tool_registry,
    reset_tool_registry,
)
from elle.cli.agentic.loop import (
    AgenticLoop,
    AgenticLoopResult,
    InitialContext,
    fetch_initial_context,
    format_initial_context,
    get_agentic_loop,
    is_agentic_loop_enabled,
    reset_agentic_loop,
)
from elle.cli.agentic.prompts import (
    format_tool_observation,
    get_system_prompt,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset all singletons before and after each test."""
    reset_tool_registry()
    reset_agentic_loop()
    yield
    reset_tool_registry()
    reset_agentic_loop()


@pytest.fixture
def tool_registry():
    """Get a fresh tool registry."""
    return get_tool_registry()


# =============================================================================
# Tool Registry Tests
# =============================================================================


class TestToolRegistry:
    """Tests for ToolRegistry."""

    def test_registry_has_builtin_tools(self, tool_registry: ToolRegistry):
        """Registry should have all built-in tools."""
        expected_tools = [
            "search_man_vault",
            "search_incidents",
            "execute_capability",
            "get_system_info",
            "shell_command",
            "list_capabilities",
        ]
        for name in expected_tools:
            assert tool_registry.get(name) is not None, f"Missing tool: {name}"

    def test_list_specs_returns_all_tools(self, tool_registry: ToolRegistry):
        """list_specs should return all tool specifications."""
        specs = tool_registry.list_specs()
        assert len(specs) >= 6
        names = {s.name for s in specs}
        assert "search_man_vault" in names
        assert "execute_capability" in names

    def test_to_ollama_tools_format(self, tool_registry: ToolRegistry):
        """to_ollama_tools should return Ollama-compatible format."""
        tools = tool_registry.to_ollama_tools()
        assert len(tools) >= 6

        # Check structure
        for tool in tools:
            assert "type" in tool
            assert tool["type"] == "function"
            assert "function" in tool
            func = tool["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func

    def test_get_unknown_tool_returns_none(self, tool_registry: ToolRegistry):
        """get() should return None for unknown tools."""
        assert tool_registry.get("nonexistent_tool") is None

    @pytest.mark.asyncio
    async def test_execute_unknown_tool_returns_error(self, tool_registry: ToolRegistry):
        """execute() should return error for unknown tools."""
        result = await tool_registry.execute("nonexistent_tool", {})
        assert not result.success
        assert "Unknown tool" in result.error


# =============================================================================
# Tool Input Model Tests
# =============================================================================


class TestToolInputModels:
    """Tests for tool input Pydantic models."""

    def test_search_man_vault_input_defaults(self):
        """SearchManVaultInput should have sensible defaults."""
        inp = SearchManVaultInput(query="test")
        assert inp.query == "test"
        assert inp.k == 5
        assert inp.search_type == "hybrid"
        assert inp.command is None

    def test_search_incidents_input_defaults(self):
        """SearchIncidentsInput should have sensible defaults."""
        inp = SearchIncidentsInput(query="nginx failing")
        assert inp.query == "nginx failing"
        assert inp.k == 3
        assert inp.include_actions is True
        assert inp.domain is None

    def test_execute_capability_input_defaults(self):
        """ExecuteCapabilityInput should have sensible defaults."""
        inp = ExecuteCapabilityInput(capability_name="service.restart")
        assert inp.capability_name == "service.restart"
        assert inp.args == {}
        assert inp.skip_confirmation is False

    def test_get_system_info_input_aspects(self):
        """GetSystemInfoInput should accept all aspects."""
        for aspect in SystemInfoAspect:
            inp = GetSystemInfoInput(aspect=aspect)
            assert inp.aspect == aspect

    def test_shell_command_input_timeout_bounds(self):
        """ShellCommandInput should enforce timeout bounds."""
        # Valid timeout
        inp = ShellCommandInput(command="ls", timeout=60.0)
        assert inp.timeout == 60.0

        # Timeout should be clamped by Pydantic validation
        with pytest.raises(Exception):
            ShellCommandInput(command="ls", timeout=0.5)  # Below minimum

        with pytest.raises(Exception):
            ShellCommandInput(command="ls", timeout=200.0)  # Above maximum


# =============================================================================
# Tool Execution Tests (Mocked)
# =============================================================================


class TestToolExecution:
    """Tests for tool execution with mocked backends."""

    @pytest.mark.asyncio
    async def test_search_man_vault_execution(self, tool_registry: ToolRegistry):
        """search_man_vault should call retriever and format results."""
        with patch("elle.daemon.manvault.retriever.search") as mock_search:
            # Mock ManSnippet-like objects
            # Note: 'name' is a special MagicMock kwarg, so we configure_mock it
            mock_result = MagicMock()
            mock_result.configure_mock(
                name="systemctl",
                section="1",
                snippet="systemctl is used to control systemd...",
                score=0.9,
                match_section="DESCRIPTION",
            )
            mock_search.return_value = [mock_result]

            result = await tool_registry.execute(
                "search_man_vault",
                {"query": "restart service"},
            )

            assert result.success
            assert "systemctl" in result.output or "documentation" in result.output.lower()

    @pytest.mark.asyncio
    async def test_shell_command_blocks_dangerous(self, tool_registry: ToolRegistry):
        """shell_command should block dangerous commands."""
        dangerous_commands = [
            "rm -rf /",
            "sudo rm -rf /home",
            "mkfs.ext4 /dev/sda",
            "dd if=/dev/zero of=/dev/sda",
        ]

        for cmd in dangerous_commands:
            result = await tool_registry.execute(
                "shell_command",
                {"command": cmd},
            )
            assert not result.success, f"Should block: {cmd}"
            assert "blocked" in result.error.lower(), f"Should say blocked: {cmd}"

    @pytest.mark.asyncio
    async def test_get_system_info_aspects(self, tool_registry: ToolRegistry):
        """get_system_info should handle all aspects."""
        with patch("elle.cli.subprocess_runner.run_safe") as mock_run:
            mock_run.return_value = MagicMock(
                success=True,
                stdout="test output",
                stderr="",
            )

            for aspect in ["resources", "services", "listeners", "kernel"]:
                result = await tool_registry.execute(
                    "get_system_info",
                    {"aspect": aspect},
                )
                assert result.success, f"Failed for aspect: {aspect}"


# =============================================================================
# Initial Context Tests
# =============================================================================


class TestInitialContext:
    """Tests for initial context fetching and formatting."""

    @pytest.mark.asyncio
    async def test_fetch_initial_context_parallel(self):
        """fetch_initial_context should run searches in parallel."""
        registry = get_tool_registry()

        with patch.object(registry, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="mock",
                success=True,
                output="test output",
            )

            context = await fetch_initial_context("test query", registry)

            # Should have called execute three times (man vault + incidents + capabilities)
            assert mock_execute.call_count == 3
            assert context.duration_ms >= 0

    def test_format_initial_context_empty(self):
        """format_initial_context should handle empty context."""
        context = InitialContext()
        formatted = format_initial_context(context)
        assert formatted == ""

    def test_format_initial_context_with_data(self):
        """format_initial_context should format non-empty context."""
        context = InitialContext(
            man_vault_results="Some documentation",
            incident_results="Similar incident found",
        )
        formatted = format_initial_context(context)

        assert "Documentation" in formatted
        assert "Some documentation" in formatted
        assert "Incidents" in formatted
        assert "Similar incident" in formatted


# =============================================================================
# Prompt Tests
# =============================================================================


class TestPrompts:
    """Tests for prompt generation."""

    def test_get_system_prompt_includes_context(self):
        """System prompt should include hostname and OS info."""
        prompt = get_system_prompt(
            hostname="test-host",
            os_info="Ubuntu 24.04",
        )

        assert "test-host" in prompt
        assert "Ubuntu 24.04" in prompt
        assert "ELLE" in prompt
        assert "tools" in prompt.lower()

    def test_format_tool_observation_success(self):
        """format_tool_observation should format successful results."""
        obs = format_tool_observation(
            "search_man_vault",
            "Found 3 results",
            success=True,
        )

        assert "search_man_vault" in obs
        assert "SUCCESS" in obs
        assert "Found 3 results" in obs

    def test_format_tool_observation_failure(self):
        """format_tool_observation should format failed results."""
        obs = format_tool_observation(
            "shell_command",
            "Error: command not found",
            success=False,
        )

        assert "shell_command" in obs
        assert "FAILED" in obs
        assert "Error: command not found" in obs


# =============================================================================
# AgenticLoop Tests
# =============================================================================


class TestAgenticLoop:
    """Tests for the AgenticLoop class."""

    def test_loop_initialization_defaults(self):
        """AgenticLoop should have sensible defaults."""
        loop = AgenticLoop()
        assert loop.max_iterations == 10
        assert loop.max_tokens == 32768
        assert loop.temperature == 0.7
        assert loop.prefetch_context is True
        assert loop.tools is not None

    def test_loop_initialization_custom(self):
        """AgenticLoop should accept custom parameters."""
        loop = AgenticLoop(
            max_iterations=5,
            max_tokens=8192,
            temperature=0.5,
            prefetch_context=False,
        )
        assert loop.max_iterations == 5
        assert loop.max_tokens == 8192
        assert loop.temperature == 0.5
        assert loop.prefetch_context is False

    def test_singleton_pattern(self):
        """get_agentic_loop should return same instance."""
        loop1 = get_agentic_loop()
        loop2 = get_agentic_loop()
        assert loop1 is loop2

    def test_reset_singleton(self):
        """reset_agentic_loop should clear the singleton."""
        loop1 = get_agentic_loop()
        reset_agentic_loop()
        loop2 = get_agentic_loop()
        assert loop1 is not loop2


class TestAgenticLoopExecution:
    """Tests for AgenticLoop execution with mocked LLM."""

    @pytest.mark.asyncio
    async def test_run_simple_response(self):
        """AgenticLoop should handle simple responses without tool calls."""
        loop = AgenticLoop(prefetch_context=False)

        # Mock the LLM session
        with patch("elle.rag.llm.LLMSession") as MockSession:
            mock_session = AsyncMock()
            mock_session.__aenter__.return_value = mock_session
            mock_session.__aexit__.return_value = None

            # Mock response without tool calls
            from elle.rag.llm import ToolCallResponse
            mock_response = ToolCallResponse(
                content="Here is my response",
                tool_calls=(),
                done=True,
                model="test-model",
            )
            mock_session.chat.return_value = mock_response
            MockSession.return_value = mock_session

            result = await loop.run("Hello, how are you?")

            assert result.success
            assert result.response == "Here is my response"
            assert result.iterations == 1
            assert result.tool_call_count == 0

    @pytest.mark.asyncio
    async def test_run_with_tool_calls(self):
        """AgenticLoop should execute tool calls and continue."""
        loop = AgenticLoop(prefetch_context=False, max_iterations=3)

        with patch("elle.rag.llm.LLMSession") as MockSession:
            mock_session = AsyncMock()
            mock_session.__aenter__.return_value = mock_session
            mock_session.__aexit__.return_value = None

            from elle.rag.llm import ToolCall, ToolCallResponse

            # First response: tool call
            tool_call_response = ToolCallResponse(
                content="",
                tool_calls=(
                    ToolCall(
                        id="call_1",
                        name="get_system_info",
                        arguments={"aspect": "resources"},
                    ),
                ),
                done=False,
                model="test-model",
            )

            # Second response: final answer
            final_response = ToolCallResponse(
                content="Based on the system info, here is my answer.",
                tool_calls=(),
                done=True,
                model="test-model",
            )

            mock_session.chat.return_value = tool_call_response
            mock_session.continue_after_tool.return_value = final_response
            mock_session.add_tool_result = MagicMock()
            MockSession.return_value = mock_session

            # Mock tool execution
            with patch.object(loop.tools, "execute") as mock_execute:
                mock_execute.return_value = ToolResult(
                    tool_name="get_system_info",
                    success=True,
                    output="Memory: 16GB available",
                )

                result = await loop.run("What are my system resources?")

                assert result.success
                assert result.iterations == 2
                assert result.tool_call_count == 1
                assert result.tool_calls[0].tool_name == "get_system_info"

    @pytest.mark.asyncio
    async def test_run_respects_max_iterations(self):
        """AgenticLoop should stop at max_iterations."""
        loop = AgenticLoop(prefetch_context=False, max_iterations=2)

        with patch("elle.rag.llm.LLMSession") as MockSession:
            mock_session = AsyncMock()
            mock_session.__aenter__.return_value = mock_session
            mock_session.__aexit__.return_value = None

            from elle.rag.llm import ToolCall, ToolCallResponse

            # Always return tool calls (would loop forever without limit)
            tool_call_response = ToolCallResponse(
                content="",
                tool_calls=(
                    ToolCall(id="call_1", name="shell_command", arguments={"command": "ls"}),
                ),
                done=False,
                model="test-model",
            )

            mock_session.chat.return_value = tool_call_response
            mock_session.continue_after_tool.return_value = tool_call_response
            mock_session.add_tool_result = MagicMock()
            MockSession.return_value = mock_session

            with patch.object(loop.tools, "execute") as mock_execute:
                mock_execute.return_value = ToolResult(
                    tool_name="shell_command",
                    success=True,
                    output="file.txt",
                )

                result = await loop.run("List files repeatedly")

                # Should stop at max_iterations (2)
                assert result.iterations == 2

    @pytest.mark.asyncio
    async def test_run_streaming_callback(self):
        """AgenticLoop should call stream_callback with tokens."""
        loop = AgenticLoop(prefetch_context=False)

        collected_tokens = []

        def stream_callback(token: str):
            collected_tokens.append(token)

        with patch("elle.rag.llm.LLMSession") as MockSession:
            mock_session = AsyncMock()
            mock_session.__aenter__.return_value = mock_session
            mock_session.__aexit__.return_value = None

            from elle.rag.llm import ToolCallResponse
            mock_response = ToolCallResponse(
                content="Streamed response",
                tool_calls=(),
                done=True,
                model="test-model",
            )
            mock_session.chat.return_value = mock_response
            MockSession.return_value = mock_session

            await loop.run("Test", stream_callback=stream_callback)

            # stream_callback is passed to session.chat, which is mocked
            # So we verify it was passed correctly
            call_kwargs = mock_session.chat.call_args.kwargs
            assert "stream_callback" in call_kwargs


# =============================================================================
# Environment Variable Tests
# =============================================================================


class TestEnvironmentConfig:
    """Tests for environment variable configuration."""

    def test_is_agentic_loop_enabled_false_by_default(self):
        """Agentic loop should be disabled by default."""
        import os
        old_val = os.environ.pop("ELLE_AGENTIC_LOOP", None)
        try:
            assert not is_agentic_loop_enabled()
        finally:
            if old_val:
                os.environ["ELLE_AGENTIC_LOOP"] = old_val

    def test_is_agentic_loop_enabled_true(self):
        """Agentic loop should be enabled when env var is set."""
        import os
        old_val = os.environ.get("ELLE_AGENTIC_LOOP")
        try:
            for val in ["1", "true", "yes", "TRUE", "Yes"]:
                os.environ["ELLE_AGENTIC_LOOP"] = val
                assert is_agentic_loop_enabled(), f"Should be enabled for: {val}"
        finally:
            if old_val:
                os.environ["ELLE_AGENTIC_LOOP"] = old_val
            else:
                os.environ.pop("ELLE_AGENTIC_LOOP", None)


# =============================================================================
# ToolResult Tests
# =============================================================================


class TestToolResult:
    """Tests for ToolResult model."""

    def test_tool_result_creation(self):
        """ToolResult should store all fields."""
        result = ToolResult(
            tool_name="test_tool",
            success=True,
            output="test output",
            duration_ms=100,
        )

        assert result.tool_name == "test_tool"
        assert result.success is True
        assert result.output == "test output"
        assert result.duration_ms == 100
        assert result.error is None

    def test_tool_result_with_error(self):
        """ToolResult should store error information."""
        result = ToolResult(
            tool_name="test_tool",
            success=False,
            output="",
            error="Something went wrong",
        )

        assert result.success is False
        assert result.error == "Something went wrong"


# =============================================================================
# AgenticLoopResult Tests
# =============================================================================


class TestAgenticLoopResult:
    """Tests for AgenticLoopResult model."""

    def test_result_properties(self):
        """AgenticLoopResult should compute properties correctly."""
        from elle.cli.agentic.loop import ToolCallRecord

        result = AgenticLoopResult(
            response="Test response",
            success=True,
            iterations=3,
            total_duration_ms=1500,
            tool_calls=(
                ToolCallRecord(
                    tool_name="tool1",
                    arguments={},
                    result=ToolResult(tool_name="tool1", success=True, output=""),
                    iteration=1,
                ),
                ToolCallRecord(
                    tool_name="tool2",
                    arguments={},
                    result=ToolResult(tool_name="tool2", success=True, output=""),
                    iteration=2,
                ),
            ),
            prompt_tokens=100,
            completion_tokens=50,
        )

        assert result.tool_call_count == 2
        assert result.total_tokens == 150
        assert result.iterations == 3
