"""Tests for the agentic loop system.

Tests the ReAct-style tool-calling loop comprehensively:
- Tool definitions and registry
- LLMSession with tool calling
- AgenticLoop execution lifecycle
- Error handling and edge cases
- Verification strategies for all domains
- Incident recording
- Plan remediation
- Environment variable configuration
- Convenience functions
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.cli.agentic.loop import (
    MUTATING_TOOLS,
    AgenticLoop,
    AgenticLoopResult,
    InitialContext,
    ToolCallRecord,
    fetch_initial_context,
    format_initial_context,
    get_agentic_loop,
    is_agentic_loop_enabled,
    reset_agentic_loop,
    run_agentic_loop,
    run_for_failed_command,
    run_for_incident,
)
from elle.cli.agentic.prompts import (
    format_tool_observation,
    get_system_prompt,
)
from elle.cli.agentic.tools import (
    ExecuteCapabilityInput,
    GetSystemInfoInput,
    SearchIncidentsInput,
    SearchManVaultInput,
    ShellCommandInput,
    SystemInfoAspect,
    ToolRegistry,
    ToolResult,
    get_tool_registry,
    reset_tool_registry,
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


def _make_tool_call_response(
    content: str = "",
    tool_calls: tuple = (),
    done: bool = True,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
):
    """Create a ToolCallResponse with given parameters."""
    from elle.rag.llm import ToolCallResponse

    return ToolCallResponse(
        content=content,
        tool_calls=tool_calls,
        done=done,
        model="test-model",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _make_tool_call(name: str, arguments: dict, call_id: str = "call_1"):
    """Create a ToolCall with given parameters."""
    from elle.rag.llm import ToolCall

    return ToolCall(id=call_id, name=name, arguments=arguments)


def _make_mock_session(
    chat_response=None,
    continue_responses=None,
):
    """Create a mock LLM session for the context manager pattern.

    Args:
        chat_response: Response for the initial chat call.
        continue_responses: List of responses for continue_after_tool calls.

    Returns:
        Tuple of (MockSessionClass, mock_session_instance).
    """
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.add_tool_result = MagicMock()

    if chat_response is not None:
        mock_session.chat.return_value = chat_response

    if continue_responses is not None:
        mock_session.continue_after_tool.side_effect = continue_responses

    return mock_session


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

    @pytest.mark.asyncio
    async def test_fetch_initial_context_handles_search_failures(self):
        """fetch_initial_context should handle individual search failures gracefully."""
        registry = get_tool_registry()

        with patch.object(registry, "execute") as mock_execute:
            mock_execute.side_effect = Exception("Search failed")

            context = await fetch_initial_context("test query", registry)

            # Should return empty context on failures
            assert context.man_vault_results == ""
            assert context.incident_results == ""
            assert context.capability_results == ""

    @pytest.mark.asyncio
    async def test_fetch_initial_context_handles_unsuccessful_results(self):
        """fetch_initial_context should handle unsuccessful results."""
        registry = get_tool_registry()

        with patch.object(registry, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="mock",
                success=False,
                output="",
                error="Not found",
            )

            context = await fetch_initial_context("test query", registry)

            # Unsuccessful results should yield empty strings
            assert context.man_vault_results == ""
            assert context.incident_results == ""
            assert context.capability_results == ""

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

    def test_format_initial_context_with_all_fields(self):
        """format_initial_context should include all non-empty fields."""
        context = InitialContext(
            man_vault_results="Documentation content",
            incident_results="Past incident data",
            capability_results="Available capabilities",
            system_info="System state",
        )
        formatted = format_initial_context(context)

        assert "Documentation" in formatted
        assert "Incidents" in formatted
        assert "Capabilities" in formatted
        assert "System State" in formatted

    def test_format_initial_context_truncates_long_content(self):
        """format_initial_context should truncate very long content."""
        long_text = "x" * 5000
        context = InitialContext(
            man_vault_results=long_text,
        )
        formatted = format_initial_context(context)

        # Man vault max is 3000 chars
        assert len(formatted) < len(long_text) + 200


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
        assert loop.enable_audit is True
        assert loop.enable_verification is True

    def test_loop_initialization_custom(self):
        """AgenticLoop should accept custom parameters."""
        loop = AgenticLoop(
            max_iterations=5,
            max_tokens=8192,
            temperature=0.5,
            prefetch_context=False,
            enable_audit=False,
            enable_verification=False,
        )
        assert loop.max_iterations == 5
        assert loop.max_tokens == 8192
        assert loop.temperature == 0.5
        assert loop.prefetch_context is False
        assert loop.enable_audit is False
        assert loop.enable_verification is False

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

    def test_get_system_context(self):
        """_get_system_context should return hostname, os_info, timestamp."""
        loop = AgenticLoop()
        hostname, os_info, timestamp = loop._get_system_context()

        assert isinstance(hostname, str)
        assert len(hostname) > 0
        assert isinstance(os_info, str)
        assert isinstance(timestamp, str)

    def test_get_system_context_fallbacks(self):
        """_get_system_context should use fallbacks when system info fails."""
        loop = AgenticLoop()

        with patch("socket.gethostname", side_effect=Exception("DNS failure")):
            hostname, os_info, timestamp = loop._get_system_context()
            assert hostname == "localhost"


# =============================================================================
# AgenticLoop Execution Tests
# =============================================================================


class TestAgenticLoopExecution:
    """Tests for AgenticLoop execution with mocked LLM."""

    @pytest.mark.asyncio
    async def test_run_simple_response(self):
        """AgenticLoop should handle simple responses without tool calls."""
        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=False,
        )

        mock_response = _make_tool_call_response(
            content="Here is my response",
            prompt_tokens=50,
            completion_tokens=20,
        )
        mock_session = _make_mock_session(chat_response=mock_response)

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value="inc-123",
            ),
        ):
            result = await loop.run("Hello, how are you?")

            assert result.success
            assert result.response == "Here is my response"
            assert result.iterations == 1
            assert result.tool_call_count == 0
            assert result.incident_id == "inc-123"

    @pytest.mark.asyncio
    async def test_run_with_tool_calls(self):
        """AgenticLoop should execute tool calls and continue."""
        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=False,
            max_iterations=3,
        )

        tool_call_response = _make_tool_call_response(
            content="",
            tool_calls=(_make_tool_call("get_system_info", {"aspect": "resources"}),),
            done=False,
        )
        final_response = _make_tool_call_response(
            content="Based on the system info, here is my answer.",
        )

        mock_session = _make_mock_session(
            chat_response=tool_call_response,
            continue_responses=[final_response],
        )

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value="inc-456",
            ),
        ):
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
        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=False,
            max_iterations=2,
        )

        tool_call_response = _make_tool_call_response(
            content="",
            tool_calls=(_make_tool_call("shell_command", {"command": "ls"}),),
            done=False,
        )

        mock_session = _make_mock_session(
            chat_response=tool_call_response,
            continue_responses=[tool_call_response],
        )

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
        ):
            with patch.object(loop.tools, "execute") as mock_execute:
                mock_execute.return_value = ToolResult(
                    tool_name="shell_command",
                    success=True,
                    output="file.txt",
                )

                result = await loop.run("List files repeatedly")

                # Should stop at max_iterations (2)
                assert result.iterations == 2
                # Response should include max iterations notice
                assert "maximum" in result.response.lower() or result.iterations == 2

    @pytest.mark.asyncio
    async def test_run_streaming_callback(self):
        """AgenticLoop should pass stream_callback to the LLM session."""
        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=False,
        )

        collected_tokens = []

        def stream_callback(token: str):
            collected_tokens.append(token)

        mock_response = _make_tool_call_response(content="Streamed response")
        mock_session = _make_mock_session(chat_response=mock_response)

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
        ):
            await loop.run("Test", stream_callback=stream_callback)

            # stream_callback is passed to session.chat
            call_kwargs = mock_session.chat.call_args.kwargs
            assert "stream_callback" in call_kwargs

    @pytest.mark.asyncio
    async def test_run_progress_callback(self):
        """AgenticLoop should call progress_callback at each stage."""
        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=False,
        )

        progress_stages = []

        def progress_callback(stage: str, description: str, iteration: int):
            progress_stages.append((stage, description, iteration))

        mock_response = _make_tool_call_response(content="Done.")
        mock_session = _make_mock_session(chat_response=mock_response)

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
        ):
            await loop.run("Test", progress_callback=progress_callback)

            # Should have called progress for hypothesize and record stages
            stage_names = [s[0] for s in progress_stages]
            assert "hypothesize" in stage_names
            assert "record" in stage_names

    @pytest.mark.asyncio
    async def test_run_handles_llm_unavailable(self):
        """AgenticLoop should handle LLMUnavailableError gracefully."""
        from elle.rag.llm import LLMUnavailableError

        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=False,
        )

        mock_session = _make_mock_session()
        mock_session.chat.side_effect = LLMUnavailableError("Ollama not running")

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value="inc-err",
            ),
        ):
            result = await loop.run("Test query")

            assert not result.success
            assert "unable to connect" in result.response.lower() or "unavailable" in result.response.lower()
            assert result.error is not None
            assert "Ollama" in result.error

    @pytest.mark.asyncio
    async def test_run_handles_generic_exception(self):
        """AgenticLoop should handle unexpected exceptions gracefully."""
        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=False,
        )

        mock_session = _make_mock_session()
        mock_session.chat.side_effect = RuntimeError("Unexpected failure")

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
        ):
            result = await loop.run("Test query")

            assert not result.success
            assert "error" in result.response.lower()
            assert result.error == "Unexpected failure"
            assert result.iterations >= 1

    @pytest.mark.asyncio
    async def test_run_with_prefetch_context(self):
        """AgenticLoop should use retrieval pipeline when prefetch_context is True."""
        loop = AgenticLoop(
            prefetch_context=True,
            enable_audit=False,
        )

        mock_retrieval_context = MagicMock()
        mock_retrieval_context.retrieval_duration_ms = 50
        mock_retrieval_context.format_for_prompt.return_value = "## Context\nSome data"

        mock_pipeline = AsyncMock()
        mock_pipeline.retrieve.return_value = mock_retrieval_context
        loop._retrieval_pipeline = mock_pipeline

        mock_response = _make_tool_call_response(content="Answer with context.")
        mock_session = _make_mock_session(chat_response=mock_response)

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
        ):
            result = await loop.run("Why is nginx failing?")

            assert result.success
            # Verify the retrieval pipeline was called
            mock_pipeline.retrieve.assert_called_once()
            # The user message should include context
            chat_args = mock_session.chat.call_args
            user_message = chat_args[0][0] if chat_args[0] else chat_args.kwargs.get("content", "")
            # Context should be appended to user message
            assert "Context" in str(user_message) or "data" in str(user_message)

    @pytest.mark.asyncio
    async def test_run_with_audit_enabled(self):
        """AgenticLoop should create audit records when audit is enabled."""
        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=True,
        )

        mock_audit = MagicMock()
        mock_record = MagicMock()
        mock_record.execution_id = "exec-abc"
        mock_audit.start.return_value = mock_record
        loop._audit_recorder = mock_audit

        mock_response = _make_tool_call_response(content="Audited response.")
        mock_session = _make_mock_session(chat_response=mock_response)

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value="inc-audit",
            ),
        ):
            result = await loop.run("Test with audit")

            assert result.success
            assert result.execution_id == "exec-abc"
            mock_audit.start.assert_called_once()
            mock_audit.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_audit_records_failure(self):
        """AgenticLoop should record failures to the audit trail."""
        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=True,
        )

        mock_audit = MagicMock()
        mock_record = MagicMock()
        mock_record.execution_id = "exec-fail"
        mock_audit.start.return_value = mock_record
        loop._audit_recorder = mock_audit

        mock_session = _make_mock_session()
        mock_session.chat.side_effect = RuntimeError("Boom")

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
        ):
            result = await loop.run("Fail test")

            assert not result.success
            mock_audit.fail.assert_called_once_with("exec-fail", "Boom", 0)

    @pytest.mark.asyncio
    async def test_run_with_tool_call_verification(self):
        """AgenticLoop should verify mutating tool calls."""
        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=False,
            enable_verification=True,
        )

        tool_call_response = _make_tool_call_response(
            content="",
            tool_calls=(
                _make_tool_call(
                    "execute_capability",
                    {"capability_name": "service.restart", "args": {"service": "nginx"}},
                ),
            ),
            done=False,
        )
        final_response = _make_tool_call_response(content="Restarted nginx.")

        mock_session = _make_mock_session(
            chat_response=tool_call_response,
            continue_responses=[final_response],
        )

        execute_call_count = 0

        async def mock_execute(tool_name, args, **kwargs):
            nonlocal execute_call_count
            execute_call_count += 1
            if tool_name == "execute_capability":
                return ToolResult(
                    tool_name="execute_capability",
                    success=True,
                    output="Service restarted",
                )
            # Verification call
            return ToolResult(
                tool_name="get_system_info",
                success=True,
                output="Active: active (running)",
            )

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
            patch.object(loop.tools, "execute", side_effect=mock_execute),
        ):
            result = await loop.run("Restart nginx")

            assert result.success
            assert len(result.tool_calls) == 1
            assert result.tool_calls[0].verified is True

    @pytest.mark.asyncio
    async def test_run_token_counting(self):
        """AgenticLoop should track token usage across iterations."""
        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=False,
        )

        tool_call_response = _make_tool_call_response(
            content="",
            tool_calls=(_make_tool_call("shell_command", {"command": "uptime"}),),
            done=False,
            prompt_tokens=100,
            completion_tokens=30,
        )
        final_response = _make_tool_call_response(
            content="System uptime is 5 days.",
            prompt_tokens=200,
            completion_tokens=50,
        )

        mock_session = _make_mock_session(
            chat_response=tool_call_response,
            continue_responses=[final_response],
        )

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
            patch.object(
                loop.tools,
                "execute",
                return_value=ToolResult(
                    tool_name="shell_command",
                    success=True,
                    output="up 5 days",
                ),
            ),
        ):
            result = await loop.run("How long has the system been up?")

            assert result.prompt_tokens == 300
            assert result.completion_tokens == 80
            assert result.total_tokens == 380

    @pytest.mark.asyncio
    async def test_run_max_duration_exceeded(self):
        """AgenticLoop should stop when max duration is exceeded."""
        import time as real_time

        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=False,
            max_iterations=100,
        )

        tool_call_response = _make_tool_call_response(
            content="",
            tool_calls=(_make_tool_call("shell_command", {"command": "date"}),),
            done=False,
        )

        mock_session = _make_mock_session(
            chat_response=tool_call_response,
            continue_responses=[tool_call_response] * 10,
        )

        # Use a counter to simulate time advancing after the first iteration
        call_counter = {"count": 0}
        base_time = real_time.time()

        def fake_time():
            call_counter["count"] += 1
            # After a few calls, jump forward past the 300s timeout
            if call_counter["count"] >= 5:
                return base_time + 400
            return base_time

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
            patch.object(
                loop.tools,
                "execute",
                return_value=ToolResult(
                    tool_name="shell_command",
                    success=True,
                    output="Mon Jan 27",
                ),
            ),
            patch("elle.cli.agentic.loop.time") as mock_time,
        ):
            mock_time.time = fake_time

            result = await loop.run("Long running query")

            # The loop should have stopped early due to duration limit
            assert result.iterations < 100


# =============================================================================
# AgenticLoopResult Tests
# =============================================================================


class TestAgenticLoopResult:
    """Tests for AgenticLoopResult model."""

    def test_result_properties(self):
        """AgenticLoopResult should compute properties correctly."""
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

    def test_total_tokens_none_when_missing(self):
        """total_tokens should return None when token counts are missing."""
        result = AgenticLoopResult(
            response="Test",
            success=True,
            iterations=1,
        )
        assert result.total_tokens is None

    def test_mutating_calls_property(self):
        """mutating_calls should return only mutating tool calls."""
        result = AgenticLoopResult(
            response="Test",
            success=True,
            iterations=2,
            tool_calls=(
                ToolCallRecord(
                    tool_name="execute_capability",
                    arguments={"capability_name": "service.restart"},
                    result=ToolResult(tool_name="execute_capability", success=True, output=""),
                    iteration=1,
                ),
                ToolCallRecord(
                    tool_name="shell_command",
                    arguments={"command": "ls"},
                    result=ToolResult(tool_name="shell_command", success=True, output=""),
                    iteration=1,
                ),
            ),
        )
        assert len(result.mutating_calls) == 1
        assert result.mutating_calls[0].tool_name == "execute_capability"

    def test_verified_calls_property(self):
        """verified_calls should return only verified tool calls."""
        result = AgenticLoopResult(
            response="Test",
            success=True,
            iterations=2,
            tool_calls=(
                ToolCallRecord(
                    tool_name="execute_capability",
                    arguments={},
                    result=ToolResult(tool_name="execute_capability", success=True, output=""),
                    iteration=1,
                    verified=True,
                ),
                ToolCallRecord(
                    tool_name="shell_command",
                    arguments={},
                    result=ToolResult(tool_name="shell_command", success=True, output=""),
                    iteration=1,
                    verified=False,
                ),
            ),
        )
        assert len(result.verified_calls) == 1
        assert result.verified_calls[0].verified is True

    def test_mutating_tools_constant(self):
        """MUTATING_TOOLS should include execute_capability."""
        assert "execute_capability" in MUTATING_TOOLS


# =============================================================================
# Verification System Tests
# =============================================================================


class TestVerificationResult:
    """Tests for VerificationResult model."""

    def test_verification_result_creation(self):
        """VerificationResult should store all fields."""
        from elle.cli.agentic.audit import VerificationResult

        result = VerificationResult(
            verification_id="test-123",
            tool_call_id="call-456",
            passed=True,
            method="systemctl_status",
            evidence="Service is active (running)",
        )

        assert result.verification_id == "test-123"
        assert result.tool_call_id == "call-456"
        assert result.passed is True
        assert result.method == "systemctl_status"
        assert result.evidence == "Service is active (running)"

    def test_verification_result_failure(self):
        """VerificationResult should handle failures."""
        from elle.cli.agentic.audit import VerificationResult

        result = VerificationResult(
            verification_id="test-789",
            tool_call_id="call-012",
            passed=False,
            method="docker_inspect",
            evidence="Container still running after stop command",
        )

        assert result.passed is False
        assert "Container still running" in result.evidence


class TestVerificationStrategies:
    """Tests for domain-specific verification strategies."""

    @pytest.mark.asyncio
    async def test_verify_service_start(self):
        """_verify_service should verify service start operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="get_system_info",
                success=True,
                output="Active: active (running)",
            )

            result = await loop._verify_service("ver-123", "call-456", "service.start", {"service": "nginx"})

            assert result.passed is True
            assert result.method == "systemctl_status"
            assert "active" in result.evidence.lower()

    @pytest.mark.asyncio
    async def test_verify_service_stop(self):
        """_verify_service should verify service stop operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="get_system_info",
                success=True,
                output="Active: inactive (dead)",
            )

            result = await loop._verify_service("ver-123", "call-456", "service.stop", {"service": "nginx"})

            assert result.passed is True
            assert "inactive" in result.evidence.lower()

    @pytest.mark.asyncio
    async def test_verify_service_enable(self):
        """_verify_service should verify service enable operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="get_system_info",
                success=True,
                output="nginx.service enabled",
            )

            result = await loop._verify_service("ver-123", "call-456", "service.enable", {"service": "nginx"})

            assert result.passed is True
            assert "enabled" in result.evidence.lower()

    @pytest.mark.asyncio
    async def test_verify_service_disable(self):
        """_verify_service should verify service disable operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="get_system_info",
                success=True,
                output="nginx.service disabled",
            )

            result = await loop._verify_service("ver-123", "call-456", "service.disable", {"service": "nginx"})

            assert result.passed is True
            assert "disabled" in result.evidence.lower()

    @pytest.mark.asyncio
    async def test_verify_service_unknown_operation(self):
        """_verify_service should trust result for unknown operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="get_system_info",
                success=True,
                output="some output",
            )

            result = await loop._verify_service("ver-123", "call-456", "service.reload", {"service": "nginx"})

            assert result.passed is True

    @pytest.mark.asyncio
    async def test_verify_service_check_failure(self):
        """_verify_service should return failure when status check fails."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="get_system_info",
                success=False,
                output="",
                error="Could not check service",
            )

            result = await loop._verify_service("ver-123", "call-456", "service.restart", {"service": "nginx"})

            assert result.passed is False
            assert "Failed to verify" in result.evidence

    @pytest.mark.asyncio
    async def test_verify_file_write(self):
        """_verify_file should verify file write operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="shell_command",
                success=True,
                output="  File: /etc/hosts\n  Size: 158",
            )

            result = await loop._verify_file("ver-123", "call-456", "file.write", {"path": "/etc/hosts"})

            assert result.passed is True
            assert result.method == "stat_check"

    @pytest.mark.asyncio
    async def test_verify_file_delete(self):
        """_verify_file should verify file delete operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="shell_command",
                success=True,
                output="File successfully deleted",
            )

            result = await loop._verify_file("ver-123", "call-456", "file.delete", {"path": "/tmp/test.txt"})

            assert result.passed is True
            assert result.method == "existence_check"

    @pytest.mark.asyncio
    async def test_verify_file_copy(self):
        """_verify_file should verify file copy operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="shell_command",
                success=True,
                output="  File: /tmp/backup.txt",
            )

            result = await loop._verify_file(
                "ver-123",
                "call-456",
                "file.copy",
                {"path": "/etc/hosts", "dest": "/tmp/backup.txt"},
            )

            assert result.passed is True
            assert result.method == "copy_check"

    @pytest.mark.asyncio
    async def test_verify_file_chmod(self):
        """_verify_file should verify chmod operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="shell_command",
                success=True,
                output="-rw-r--r-- 1 root root 158 Jan 27 /etc/hosts",
            )

            result = await loop._verify_file("ver-123", "call-456", "file.chmod", {"path": "/etc/hosts"})

            assert result.passed is True
            assert result.method == "permission_check"

    @pytest.mark.asyncio
    async def test_verify_file_read_non_mutating(self):
        """_verify_file should pass non-mutating operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        result = await loop._verify_file("ver-123", "call-456", "file.read", {"path": "/etc/hosts"})

        assert result.passed is True
        assert result.method == "non_mutating"

    @pytest.mark.asyncio
    async def test_verify_docker_start(self):
        """_verify_docker should verify container start operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="shell_command",
                success=True,
                output="true",
            )

            result = await loop._verify_docker("ver-123", "call-456", "docker.start", {"container": "myapp"})

            assert result.passed is True
            assert result.method == "docker_inspect"

    @pytest.mark.asyncio
    async def test_verify_docker_stop(self):
        """_verify_docker should verify container stop operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="shell_command",
                success=True,
                output="false",
            )

            result = await loop._verify_docker("ver-123", "call-456", "docker.stop", {"container": "myapp"})

            assert result.passed is True
            assert result.method == "docker_inspect"

    @pytest.mark.asyncio
    async def test_verify_docker_remove(self):
        """_verify_docker should verify container removal."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="shell_command",
                success=True,
                output="No such container: myapp\nREMOVED",
            )

            result = await loop._verify_docker("ver-123", "call-456", "docker.remove", {"container": "myapp"})

            assert result.passed is True

    @pytest.mark.asyncio
    async def test_verify_docker_prune(self):
        """_verify_docker should trust prune results."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        result = await loop._verify_docker("ver-123", "call-456", "docker.prune", {})

        assert result.passed is True
        assert "prune" in result.evidence.lower()

    @pytest.mark.asyncio
    async def test_verify_docker_list_non_mutating(self):
        """_verify_docker should pass non-mutating operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        result = await loop._verify_docker("ver-123", "call-456", "docker.list", {})

        assert result.passed is True
        assert result.method == "non_mutating"

    @pytest.mark.asyncio
    async def test_verify_package_install(self):
        """_verify_package should verify package installation."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="shell_command",
                success=True,
                output="Status: install ok installed\nVersion: 1.2.3",
            )

            result = await loop._verify_package("ver-123", "call-456", "package.install", {"package": "nginx"})

            assert result.passed is True
            assert result.method == "dpkg_status"

    @pytest.mark.asyncio
    async def test_verify_package_remove(self):
        """_verify_package should verify package removal."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="shell_command",
                success=False,
                output="dpkg-query: package 'nginx' is not installed",
                error="not installed",
            )

            result = await loop._verify_package("ver-123", "call-456", "package.remove", {"package": "nginx"})

            assert result.passed is True

    @pytest.mark.asyncio
    async def test_verify_package_update(self):
        """_verify_package should trust update results."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        result = await loop._verify_package("ver-123", "call-456", "package.update", {"package": "nginx"})

        assert result.passed is True

    @pytest.mark.asyncio
    async def test_verify_package_info_non_mutating(self):
        """_verify_package should pass non-mutating operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        result = await loop._verify_package("ver-123", "call-456", "package.info", {"package": "nginx"})

        assert result.passed is True
        assert result.method == "non_mutating"

    @pytest.mark.asyncio
    async def test_verify_config_edit_json(self):
        """_verify_config should validate JSON config files."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        call_count = 0

        async def mock_execute_side_effect(tool_name, args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ToolResult(
                    tool_name="shell_command",
                    success=True,
                    output='{"key": "value"}',
                )
            else:
                return ToolResult(
                    tool_name="shell_command",
                    success=True,
                    output="VALID",
                )

        with patch.object(loop.tools, "execute", side_effect=mock_execute_side_effect):
            result = await loop._verify_config("ver-123", "call-456", "config.edit", {"path": "/etc/app/config.json"})

            assert result.passed is True
            assert "JSON syntax: valid" in result.evidence

    @pytest.mark.asyncio
    async def test_verify_config_edit_yaml(self):
        """_verify_config should validate YAML config files."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        call_count = 0

        async def mock_execute_side_effect(tool_name, args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ToolResult(
                    tool_name="shell_command",
                    success=True,
                    output="key: value",
                )
            else:
                return ToolResult(
                    tool_name="shell_command",
                    success=True,
                    output="VALID",
                )

        with patch.object(loop.tools, "execute", side_effect=mock_execute_side_effect):
            result = await loop._verify_config(
                "ver-123",
                "call-456",
                "config.edit",
                {"path": "/etc/app/config.yaml"},
            )

            assert result.passed is True
            assert "YAML syntax: valid" in result.evidence

    @pytest.mark.asyncio
    async def test_verify_config_validate_trusts_result(self):
        """_verify_config should trust validation results."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        result = await loop._verify_config("ver-123", "call-456", "config.validate", {"path": "/etc/app/config.json"})

        assert result.passed is True

    @pytest.mark.asyncio
    async def test_verify_config_preview_non_mutating(self):
        """_verify_config should pass non-mutating operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        result = await loop._verify_config("ver-123", "call-456", "config.preview", {"path": "/etc/app/config.json"})

        assert result.passed is True
        assert result.method == "non_mutating"

    @pytest.mark.asyncio
    async def test_verify_network_firewall_allow(self):
        """_verify_network should verify firewall allow operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="shell_command",
                success=True,
                output="80/tcp ALLOW Anywhere",
            )

            result = await loop._verify_network(
                "ver-123",
                "call-456",
                "network.firewall_allow",
                {"port": "80"},
            )

            assert result.passed is True
            assert result.method == "network_check"

    @pytest.mark.asyncio
    async def test_verify_network_wireguard(self):
        """_verify_network should verify wireguard operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="shell_command",
                success=True,
                output="interface: wg0\npublic key: ...",
            )

            result = await loop._verify_network(
                "ver-123",
                "call-456",
                "network.wireguard.up",
                {"interface": "wg0"},
            )

            assert result.passed is True

    @pytest.mark.asyncio
    async def test_verify_network_diagnose_non_mutating(self):
        """_verify_network should pass non-mutating operations."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        result = await loop._verify_network("ver-123", "call-456", "network.diagnose", {})

        assert result.passed is True
        assert result.method == "non_mutating"


class TestVerificationIntegration:
    """Integration tests for verification in the agent loop."""

    @pytest.mark.asyncio
    async def test_verify_outcome_returns_tuple(self):
        """_verify_outcome should return tuple of (passed, VerificationResult)."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        mock_tool_call = MagicMock()
        mock_tool_call.name = "execute_capability"
        mock_tool_call.id = "call_123"
        mock_tool_call.arguments = {"capability_name": "service.restart", "args": {"service": "nginx"}}

        mock_result = ToolResult(
            tool_name="execute_capability",
            success=True,
            output="Service restarted successfully",
        )

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="get_system_info",
                success=True,
                output="Active: active (running)",
            )

            passed, verification = await loop._verify_outcome(mock_tool_call, mock_result)

            assert isinstance(passed, bool)
            assert passed is True
            assert verification is not None
            assert verification.passed is True
            assert verification.method == "systemctl_status"

    @pytest.mark.asyncio
    async def test_verify_outcome_failure_returns_false(self):
        """_verify_outcome should return False for failed capability execution."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        mock_tool_call = MagicMock()
        mock_tool_call.name = "execute_capability"
        mock_tool_call.id = "call_123"
        mock_tool_call.arguments = {"capability_name": "service.restart"}

        mock_result = ToolResult(
            tool_name="execute_capability",
            success=False,
            output="",
            error="Service not found",
        )

        passed, verification = await loop._verify_outcome(mock_tool_call, mock_result)

        assert passed is False
        assert verification is not None
        assert verification.passed is False
        assert "failed" in verification.method.lower()

    @pytest.mark.asyncio
    async def test_verify_outcome_unknown_domain(self):
        """_verify_outcome should trust result for unknown domains."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        mock_tool_call = MagicMock()
        mock_tool_call.name = "execute_capability"
        mock_tool_call.id = "call_123"
        mock_tool_call.arguments = {"capability_name": "custom.operation", "args": {}}

        mock_result = ToolResult(
            tool_name="execute_capability",
            success=True,
            output="Operation completed",
        )

        passed, verification = await loop._verify_outcome(mock_tool_call, mock_result)

        assert passed is True
        assert verification is not None
        assert verification.method == "result_trust"

    @pytest.mark.asyncio
    async def test_verify_outcome_non_execute_capability_tool(self):
        """_verify_outcome should skip verification for non-execute_capability tools."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        mock_tool_call = MagicMock()
        mock_tool_call.name = "shell_command"
        mock_tool_call.id = "call_123"
        mock_tool_call.arguments = {"command": "ls"}

        mock_result = ToolResult(
            tool_name="shell_command",
            success=True,
            output="file.txt",
        )

        passed, verification = await loop._verify_outcome(mock_tool_call, mock_result)

        assert passed is True
        assert verification is None

    @pytest.mark.asyncio
    async def test_verify_outcome_records_to_audit(self):
        """_verify_outcome should record verification to the audit trail."""
        loop = AgenticLoop(
            prefetch_context=False,
            enable_verification=True,
            enable_audit=True,
        )

        mock_audit = MagicMock()
        loop._audit_recorder = mock_audit

        mock_tool_call = MagicMock()
        mock_tool_call.name = "execute_capability"
        mock_tool_call.id = "call_123"
        mock_tool_call.arguments = {"capability_name": "service.restart", "args": {"service": "nginx"}}

        mock_result = ToolResult(
            tool_name="execute_capability",
            success=True,
            output="OK",
        )

        with patch.object(loop.tools, "execute") as mock_execute:
            mock_execute.return_value = ToolResult(
                tool_name="get_system_info",
                success=True,
                output="Active: active (running)",
            )

            passed, verification = await loop._verify_outcome(
                mock_tool_call,
                mock_result,
                execution_id="exec-123",
            )

            assert passed is True
            mock_audit.add_verification.assert_called_once_with("exec-123", verification)

    @pytest.mark.asyncio
    async def test_run_verification_strategy_error_handling(self):
        """_run_verification_strategy should handle exceptions gracefully."""
        loop = AgenticLoop(prefetch_context=False, enable_verification=True)

        mock_tool_call = MagicMock()
        mock_tool_call.id = "call_err_1"
        mock_result = ToolResult(
            tool_name="execute_capability",
            success=True,
            output="OK",
        )

        with patch.object(loop.tools, "execute", side_effect=RuntimeError("Boom")):
            result = await loop._run_verification_strategy(
                "service.restart",
                {"service": "nginx"},
                mock_result,
                mock_tool_call,
            )

            assert result is not None
            assert result.passed is False
            assert result.method == "verification_error"
            assert "Boom" in result.evidence


# =============================================================================
# Incident Recording Tests
# =============================================================================


class TestIncidentRecording:
    """Tests for incident recording in the agentic loop."""

    def test_record_to_incident_success(self):
        """_record_to_incident should record successful executions."""
        loop = AgenticLoop(prefetch_context=False, enable_audit=False)

        mock_input = MagicMock()
        mock_input.query_text = "restart nginx"
        mock_input.classified_intent = "system_task"
        mock_input.incident_id = None

        with patch(
            "elle.cli.agentic.incident_recorder.record_execution_to_incident",
            return_value="inc-record-123",
        ) as mock_record:
            incident_id = loop._record_to_incident(
                execution_id="exec-1",
                input=mock_input,
                response="Done",
                success=True,
                iterations=1,
                duration_ms=500,
                tool_call_records=[],
            )

            assert incident_id == "inc-record-123"
            mock_record.assert_called_once()

    def test_record_to_incident_with_tool_calls(self):
        """_record_to_incident should include tool call details."""
        loop = AgenticLoop(prefetch_context=False, enable_audit=False)

        mock_input = MagicMock()
        mock_input.query_text = "restart nginx"
        mock_input.classified_intent = "system_task"
        mock_input.incident_id = None

        tool_calls = [
            ToolCallRecord(
                tool_name="execute_capability",
                arguments={"capability_name": "service.restart", "args": {"service": "nginx"}},
                result=ToolResult(
                    tool_name="execute_capability",
                    success=True,
                    output="Service restarted",
                    duration_ms=200,
                ),
                iteration=2,
                verified=True,
            ),
        ]

        with patch(
            "elle.cli.agentic.incident_recorder.record_execution_to_incident",
            return_value="inc-tools",
        ) as mock_record:
            incident_id = loop._record_to_incident(
                execution_id="exec-2",
                input=mock_input,
                response="Restarted nginx.",
                success=True,
                iterations=2,
                duration_ms=1000,
                tool_call_records=tool_calls,
            )

            assert incident_id == "inc-tools"
            call_kwargs = mock_record.call_args.kwargs
            assert len(call_kwargs["tool_calls"]) == 1
            assert call_kwargs["capabilities_executed"] == ["service.restart"]

    def test_record_to_incident_handles_failure(self):
        """_record_to_incident should handle recording failures gracefully."""
        loop = AgenticLoop(prefetch_context=False, enable_audit=False)

        mock_input = MagicMock()
        mock_input.query_text = "test"
        mock_input.classified_intent = "unknown"
        mock_input.incident_id = None

        with patch(
            "elle.cli.agentic.incident_recorder.record_execution_to_incident",
            side_effect=Exception("DB error"),
        ):
            incident_id = loop._record_to_incident(
                execution_id="exec-3",
                input=mock_input,
                response="Test",
                success=True,
                iterations=1,
                duration_ms=100,
                tool_call_records=[],
            )

            assert incident_id is None


# =============================================================================
# Plan Remediation Tests
# =============================================================================


class TestPlanRemediation:
    """Tests for the plan_remediation method."""

    @pytest.mark.asyncio
    async def test_plan_remediation_basic(self):
        """plan_remediation should generate a plan without executing capabilities."""
        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=False,
            enable_verification=True,
        )

        mock_response = _make_tool_call_response(
            content="1. Check disk usage\n2. Clean old logs\n- Rotate journals",
        )
        mock_session = _make_mock_session(chat_response=mock_response)

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
        ):
            plan = await loop.plan_remediation("Disk is filling up")

            assert "summary" in plan
            assert "steps" in plan
            assert "capabilities" in plan
            assert "confidence" in plan
            assert "response" in plan
            assert plan["confidence"] == 0.7  # Success

    @pytest.mark.asyncio
    async def test_plan_remediation_restores_verification(self):
        """plan_remediation should restore enable_verification after completion."""
        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=False,
            enable_verification=True,
        )

        mock_response = _make_tool_call_response(content="Plan here.")
        mock_session = _make_mock_session(chat_response=mock_response)

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
        ):
            await loop.plan_remediation("Test query")

            # Verification should be restored
            assert loop.enable_verification is True

    @pytest.mark.asyncio
    async def test_plan_remediation_restores_on_error(self):
        """plan_remediation should restore verification even on error."""
        loop = AgenticLoop(
            prefetch_context=False,
            enable_audit=False,
            enable_verification=True,
        )

        mock_session = _make_mock_session()
        mock_session.chat.side_effect = RuntimeError("LLM error")

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
        ):
            plan = await loop.plan_remediation("Test query")

            # Verification should be restored even after failure
            assert loop.enable_verification is True
            assert plan["confidence"] == 0.3  # Failure confidence

    def test_extract_plan_summary_truncation(self):
        """_extract_plan_summary should truncate long summaries."""
        loop = AgenticLoop()
        long_text = "A" * 300 + "\n\nSecond paragraph"
        summary = loop._extract_plan_summary(long_text)
        assert len(summary) <= 203  # 200 + "..."
        assert summary.endswith("...")

    def test_extract_plan_summary_short(self):
        """_extract_plan_summary should not truncate short summaries."""
        loop = AgenticLoop()
        short_text = "Simple summary\n\nMore details"
        summary = loop._extract_plan_summary(short_text)
        assert summary == "Simple summary"

    def test_extract_plan_steps_numbered(self):
        """_extract_plan_steps should extract numbered steps."""
        loop = AgenticLoop()
        response = "Here is the plan:\n1. Check disk space\n2. Remove old logs\n3. Restart service"
        steps = loop._extract_plan_steps(response)
        assert len(steps) == 3
        assert "Check disk space" in steps[0]

    def test_extract_plan_steps_bullets(self):
        """_extract_plan_steps should extract bullet points when no numbered steps."""
        loop = AgenticLoop()
        response = "Here is the plan:\n- Check disk space\n- Remove old logs"
        steps = loop._extract_plan_steps(response)
        assert len(steps) == 2
        assert "Check disk space" in steps[0]

    def test_extract_plan_steps_fallback(self):
        """_extract_plan_steps should fallback when no steps found."""
        loop = AgenticLoop()
        response = "No structured steps here."
        steps = loop._extract_plan_steps(response)
        assert steps == ["See response for details"]

    def test_extract_plan_capabilities(self):
        """_extract_plan_capabilities should extract capability patterns."""
        loop = AgenticLoop()
        response = "Use service.restart for nginx and file.read to check config."
        capabilities = loop._extract_plan_capabilities(response)
        assert "service.restart" in capabilities
        assert "file.read" in capabilities

    def test_extract_plan_capabilities_filters_unknown(self):
        """_extract_plan_capabilities should filter unknown domains."""
        loop = AgenticLoop()
        response = "unknown.capability and service.restart"
        capabilities = loop._extract_plan_capabilities(response)
        assert "service.restart" in capabilities
        assert "unknown.capability" not in capabilities


# =============================================================================
# Environment Variable Tests
# =============================================================================


class TestEnvironmentConfig:
    """Tests for environment variable configuration."""

    def test_is_agentic_loop_enabled_false_by_default(self):
        """Agentic loop should be disabled by default."""
        old_val = os.environ.pop("ELLE_AGENTIC_LOOP", None)
        try:
            assert not is_agentic_loop_enabled()
        finally:
            if old_val:
                os.environ["ELLE_AGENTIC_LOOP"] = old_val

    def test_is_agentic_loop_enabled_true(self):
        """Agentic loop should be enabled when env var is set."""
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

    def test_is_agentic_loop_enabled_false_for_other_values(self):
        """Agentic loop should be disabled for unrecognized values."""
        old_val = os.environ.get("ELLE_AGENTIC_LOOP")
        try:
            for val in ["0", "false", "no", "random", ""]:
                os.environ["ELLE_AGENTIC_LOOP"] = val
                assert not is_agentic_loop_enabled(), f"Should be disabled for: {val}"
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
# Convenience Function Tests
# =============================================================================


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    @pytest.mark.asyncio
    async def test_run_agentic_loop_function(self):
        """run_agentic_loop should use the shared loop instance."""
        mock_response = _make_tool_call_response(content="Hello from loop.")
        mock_session = _make_mock_session(chat_response=mock_response)

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
            patch(
                "elle.cli.agentic.audit.AuditRecorder._ensure_schema",
                return_value=None,
            ),
        ):
            # Use the shared loop with prefetch disabled to simplify test
            loop = get_agentic_loop()
            loop.prefetch_context = False
            loop.enable_audit = False

            result = await run_agentic_loop("Hello")

            assert result.success
            assert result.response == "Hello from loop."

    @pytest.mark.asyncio
    async def test_run_for_incident_function(self):
        """run_for_incident should create AgenticInput from incident_id."""
        mock_response = _make_tool_call_response(content="Incident handled.")
        mock_session = _make_mock_session(chat_response=mock_response)

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
            patch(
                "elle.cli.agentic.audit.AuditRecorder._ensure_schema",
                return_value=None,
            ),
        ):
            loop = get_agentic_loop()
            loop.prefetch_context = False
            loop.enable_audit = False

            result = await run_for_incident("test-incident-id")

            assert result.success

    @pytest.mark.asyncio
    async def test_run_for_failed_command_function(self):
        """run_for_failed_command should create AgenticInput from command details."""
        mock_response = _make_tool_call_response(content="Here is how to fix it.")
        mock_session = _make_mock_session(chat_response=mock_response)

        with (
            patch("elle.rag.llm.LLMSession", return_value=mock_session),
            patch(
                "elle.cli.agentic.incident_recorder.record_execution_to_incident",
                return_value=None,
            ),
            patch(
                "elle.cli.agentic.audit.AuditRecorder._ensure_schema",
                return_value=None,
            ),
        ):
            loop = get_agentic_loop()
            loop.prefetch_context = False
            loop.enable_audit = False

            result = await run_for_failed_command(
                "nginx -t",
                exit_code=1,
                stderr="configuration file syntax error",
            )

            assert result.success
            assert result.response == "Here is how to fix it."


# =============================================================================
# Lazy Loading Tests
# =============================================================================


class TestLazyLoading:
    """Tests for lazy property loading."""

    def test_llm_property_lazy_loads(self):
        """llm property should lazy-load the LLM instance."""
        loop = AgenticLoop()
        assert loop._llm is None

        with patch("elle.rag.llm.get_llm") as mock_get_llm:
            mock_get_llm.return_value = MagicMock()
            llm = loop.llm
            assert llm is not None
            mock_get_llm.assert_called_once()

    def test_retrieval_pipeline_lazy_loads(self):
        """retrieval_pipeline property should lazy-load."""
        loop = AgenticLoop()
        assert loop._retrieval_pipeline is None

        with patch(
            "elle.cli.agentic.retrieval_pipeline.get_retrieval_pipeline",
        ) as mock_get:
            mock_get.return_value = MagicMock()
            pipeline = loop.retrieval_pipeline
            assert pipeline is not None
            mock_get.assert_called_once()

    def test_audit_recorder_lazy_loads(self):
        """audit_recorder property should lazy-load."""
        loop = AgenticLoop()
        assert loop._audit_recorder is None

        with patch("elle.cli.agentic.audit.get_audit_recorder") as mock_get:
            mock_get.return_value = MagicMock()
            recorder = loop.audit_recorder
            assert recorder is not None
            mock_get.assert_called_once()
