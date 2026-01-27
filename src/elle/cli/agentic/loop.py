"""AgenticLoop - Core ReAct-style tool-calling loop.

Implements a tight reasoning loop where the LLM:
1. Receives query + unified retrieval context (incidents, docs, capabilities)
2. Reasons about what to do
3. Calls tools to gather information or take action
4. Observes results
5. Verifies mutating operations
6. Iterates until it produces a final response
7. Records complete audit trail

CRITICAL: Uses a single KV cache for the entire run cycle.
- One LLM session is opened at the start
- Tool calls append observations to the same context
- KV cache is preserved across tool call iterations
- Only closed when the run completes

This module now integrates with:
- UnifiedRetrievalPipeline: Parallel retrieval from incidents, man vault, capabilities
- AuditRecorder: Complete audit trail recording
- Verification: Outcome verification for mutating operations
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from elle.cli.agentic.prompts import (
    format_tool_observation,
    get_system_prompt,
)
from elle.cli.agentic.tools import (
    ConfirmCallback,
    ToolRegistry,
    ToolResult,
    get_tool_registry,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

# Environment variable to enable/disable the agentic loop
AGENTIC_LOOP_ENABLED_VAR = "ELLE_AGENTIC_LOOP"

# Mutating tools that require verification
MUTATING_TOOLS = frozenset(["execute_capability"])


def is_agentic_loop_enabled() -> bool:
    """Check if the agentic loop is enabled via environment variable."""
    return os.environ.get(AGENTIC_LOOP_ENABLED_VAR, "").lower() in ("1", "true", "yes")


# =============================================================================
# Result Models
# =============================================================================


class ToolCallRecord(BaseModel):
    """Record of a tool call during the loop."""

    model_config = ConfigDict(frozen=True)

    tool_name: str
    arguments: dict[str, Any]
    result: ToolResult
    iteration: int
    verified: bool = Field(default=False, description="Whether outcome was verified")


class AgenticLoopResult(BaseModel):
    """Result from running the agentic loop."""

    model_config = ConfigDict(frozen=True)

    # Final response text
    response: str = Field(description="Final response from the LLM")

    # Execution metadata
    success: bool = Field(default=True, description="Whether the loop completed successfully")
    iterations: int = Field(default=1, ge=1, description="Number of iterations")
    total_duration_ms: int = Field(default=0, ge=0, description="Total execution time")

    # Tool usage
    tool_calls: tuple[ToolCallRecord, ...] = Field(
        default_factory=tuple,
        description="All tool calls made during the loop"
    )

    # Token usage
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    # Audit trail
    execution_id: str | None = Field(default=None, description="Audit execution ID")

    # Incident linkage
    incident_id: str | None = Field(default=None, description="Linked incident report ID")

    # Error information
    error: str | None = None

    @property
    def tool_call_count(self) -> int:
        """Total number of tool calls."""
        return len(self.tool_calls)

    @property
    def total_tokens(self) -> int | None:
        """Total tokens used."""
        if self.prompt_tokens is not None and self.completion_tokens is not None:
            return self.prompt_tokens + self.completion_tokens
        return None

    @property
    def mutating_calls(self) -> tuple[ToolCallRecord, ...]:
        """Get all mutating tool calls."""
        return tuple(tc for tc in self.tool_calls if tc.tool_name in MUTATING_TOOLS)

    @property
    def verified_calls(self) -> tuple[ToolCallRecord, ...]:
        """Get all verified tool calls."""
        return tuple(tc for tc in self.tool_calls if tc.verified)


# =============================================================================
# Initial Context Fetching (Legacy - for backwards compatibility)
# =============================================================================


@dataclass
class InitialContext:
    """Context gathered before the main LLM interaction."""

    man_vault_results: str = ""
    incident_results: str = ""
    system_info: str = ""
    capability_results: str = ""
    duration_ms: int = 0


async def fetch_initial_context(
    user_input: str,
    tools: ToolRegistry,
) -> InitialContext:
    """Pre-fetch relevant context before starting the LLM loop.

    Runs Man Vault, Incident Vault, and Capability searches in parallel.

    Args:
        user_input: The user's input.
        tools: Tool registry for executing searches.

    Returns:
        InitialContext with search results.
    """
    start_time = time.time()
    context = InitialContext()

    # Create search tasks
    async def search_man_vault() -> str:
        try:
            result = await tools.execute(
                "search_man_vault",
                {"query": user_input, "k": 3},
            )
            return result.output if result.success else ""
        except Exception as e:
            logger.debug(f"Initial man vault search failed: {e}")
            return ""

    async def search_incidents() -> str:
        try:
            result = await tools.execute(
                "search_incidents",
                {"query": user_input, "k": 2},
            )
            return result.output if result.success else ""
        except Exception as e:
            logger.debug(f"Initial incident search failed: {e}")
            return ""

    async def search_capabilities() -> str:
        try:
            result = await tools.execute(
                "search_capabilities",
                {"query": user_input, "k": 3},
            )
            return result.output if result.success else ""
        except Exception as e:
            logger.debug(f"Initial capability search failed: {e}")
            return ""

    # Run searches in parallel
    results = await asyncio.gather(
        search_man_vault(),
        search_incidents(),
        search_capabilities(),
        return_exceptions=True,
    )

    context.man_vault_results = results[0] if isinstance(results[0], str) else ""
    context.incident_results = results[1] if isinstance(results[1], str) else ""
    context.capability_results = results[2] if isinstance(results[2], str) else ""
    context.duration_ms = int((time.time() - start_time) * 1000)

    return context


def format_initial_context(context: InitialContext) -> str:
    """Format initial context for inclusion in the LLM prompt.

    Args:
        context: Initial context to format.

    Returns:
        Formatted context string.
    """
    parts = []

    if context.incident_results:
        parts.append("## Similar Past Incidents\n")
        parts.append(context.incident_results[:2000])
        parts.append("\n")

    if context.capability_results:
        parts.append("## Available Capabilities\n")
        parts.append(context.capability_results[:1500])
        parts.append("\n")

    if context.man_vault_results:
        parts.append("## Relevant Documentation\n")
        parts.append(context.man_vault_results[:3000])
        parts.append("\n")

    if context.system_info:
        parts.append("## Current System State\n")
        parts.append(context.system_info[:1000])
        parts.append("\n")

    return "\n".join(parts) if parts else ""


# =============================================================================
# AgenticLoop
# =============================================================================


class AgenticLoop:
    """ReAct-style tool-calling loop with persistent KV cache.

    CRITICAL: Uses a single KV cache for the entire run cycle.
    - One LLM session is opened at the start
    - Tool calls append observations to the same context
    - KV cache is preserved across tool call iterations
    - Only closed when the run completes

    Flow:
    1. Create AgenticInput and start audit record
    2. Run unified retrieval (incidents, docs, capabilities)
    3. Open LLM session with system prompt + tools + retrieval context
    4. Stream LLM response
    5. On tool call: execute, verify if mutating, append observation
    6. Continue streaming from existing KV cache position
    7. On final response: close session, complete audit, return

    Usage:
        loop = AgenticLoop()
        result = await loop.run(
            "Why is nginx failing?",
            stream_callback=print,
        )

        # Or with AgenticInput:
        from elle.cli.agentic.unified_input import AgenticInput
        input = AgenticInput.from_user_message("restart nginx")
        result = await loop.run_with_input(input, stream_callback=print)
    """

    def __init__(
        self,
        tools: ToolRegistry | None = None,
        max_iterations: int = 10,
        max_tokens: int = 32768,
        temperature: float = 0.7,
        prefetch_context: bool = True,
        enable_audit: bool = True,
        enable_verification: bool = True,
    ) -> None:
        """Initialize the agentic loop.

        Args:
            tools: Tool registry (uses default if None).
            max_iterations: Maximum tool call iterations.
            max_tokens: Maximum tokens per response.
            temperature: LLM temperature.
            prefetch_context: Whether to pre-fetch Man Vault/Incident context.
            enable_audit: Whether to record audit trails.
            enable_verification: Whether to verify mutating operations.
        """
        self.tools = tools or get_tool_registry()
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.prefetch_context = prefetch_context
        self.enable_audit = enable_audit
        self.enable_verification = enable_verification

        self._llm = None
        self._retrieval_pipeline = None
        self._audit_recorder = None

    @property
    def llm(self):
        """Lazy-load LLM instance."""
        if self._llm is None:
            from elle.rag.llm import get_llm
            self._llm = get_llm()
        return self._llm

    @property
    def retrieval_pipeline(self):
        """Lazy-load retrieval pipeline."""
        if self._retrieval_pipeline is None:
            from elle.cli.agentic.retrieval_pipeline import get_retrieval_pipeline
            self._retrieval_pipeline = get_retrieval_pipeline()
        return self._retrieval_pipeline

    @property
    def audit_recorder(self):
        """Lazy-load audit recorder."""
        if self._audit_recorder is None:
            from elle.cli.agentic.audit import get_audit_recorder
            self._audit_recorder = get_audit_recorder()
        return self._audit_recorder

    def _get_system_context(self) -> tuple[str, str, str]:
        """Get system context for the prompt.

        Returns:
            Tuple of (hostname, os_info, timestamp).
        """
        try:
            hostname = socket.gethostname()
        except Exception:
            hostname = "localhost"

        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        os_info = line.split("=")[1].strip().strip('"')
                        break
                else:
                    os_info = "Ubuntu 24.04 LTS"
        except Exception:
            os_info = "Ubuntu 24.04 LTS"

        return hostname, os_info, datetime.now(UTC).isoformat()

    async def run(
        self,
        user_input: str,
        *,
        stream_callback: Callable[[str], None] | None = None,
        confirm_callback: ConfirmCallback | None = None,
        session_id: str | None = None,
    ) -> AgenticLoopResult:
        """Run the agentic loop for a user input string.

        This is the main entry point for string inputs. For AgenticInput,
        use run_with_input().

        Args:
            user_input: The user's input (question or task).
            stream_callback: Optional callback for streaming response tokens.
            confirm_callback: Optional callback for confirming mutating operations.
            session_id: Optional session ID for audit linking.

        Returns:
            AgenticLoopResult with the final response and execution metadata.
        """
        from elle.cli.agentic.unified_input import AgenticInput

        # Create AgenticInput from string
        agentic_input = AgenticInput.from_user_message(
            user_input,
            session_id=session_id,
        )

        return await self.run_with_input(
            agentic_input,
            stream_callback=stream_callback,
            confirm_callback=confirm_callback,
        )

    async def run_with_input(
        self,
        input: Any,  # AgenticInput, but avoid circular import
        *,
        stream_callback: Callable[[str], None] | None = None,
        confirm_callback: ConfirmCallback | None = None,
    ) -> AgenticLoopResult:
        """Run the agentic loop for an AgenticInput.

        This is the unified entry point that handles all trigger types:
        - User messages
        - Daemon incidents
        - Command failures

        Args:
            input: AgenticInput to process.
            stream_callback: Optional callback for streaming response tokens.
            confirm_callback: Optional callback for confirming mutating operations.

        Returns:
            AgenticLoopResult with the final response and execution metadata.
        """
        from elle.cli.agentic.audit import ToolCallRecord as AuditToolCallRecord
        from elle.rag.llm import LLMSession, LLMUnavailableError

        start_time = time.time()
        tool_call_records: list[ToolCallRecord] = []
        iterations = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        execution_id: str | None = None

        # Start audit record
        if self.enable_audit:
            audit_record = self.audit_recorder.start(input)
            execution_id = audit_record.execution_id

        try:
            # 1. Run unified retrieval
            retrieval_context = None
            if self.prefetch_context:
                retrieval_context = await self.retrieval_pipeline.retrieve(input)
                logger.debug(f"Retrieval took {retrieval_context.retrieval_duration_ms}ms")

                # Add to audit
                if self.enable_audit:
                    self.audit_recorder.add_retrieval(execution_id, retrieval_context)

            # 2. Build system prompt
            hostname, os_info, _ = self._get_system_context()
            system_prompt = get_system_prompt(
                hostname=hostname,
                os_info=os_info,
            )

            # 3. Build initial user message with retrieval context
            user_message = input.query_text
            if retrieval_context:
                context_str = retrieval_context.format_for_prompt()
                if context_str:
                    user_message = f"{input.query_text}\n\n---\n\n{context_str}"

            # 4. Create LLM session with tools
            ollama_tools = self.tools.to_ollama_tools()

            async with LLMSession(
                self.llm,
                system_prompt=system_prompt,
                tools=ollama_tools,
                keep_alive="30m",
            ) as session:
                # 5. Initial LLM call
                response = await session.chat(
                    user_message,
                    stream_callback=stream_callback,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )

                if response.prompt_tokens:
                    total_prompt_tokens += response.prompt_tokens
                if response.completion_tokens:
                    total_completion_tokens += response.completion_tokens

                collected_response = response.content
                iterations = 1

                # 6. Tool call loop with verification
                while response.has_tool_calls and iterations < self.max_iterations:
                    iterations += 1
                    logger.debug(
                        f"Iteration {iterations}: {len(response.tool_calls)} tool calls"
                    )

                    # Execute all tool calls
                    for tool_call in response.tool_calls:
                        logger.debug(
                            f"Executing tool: {tool_call.name} with args {tool_call.arguments}"
                        )

                        # Execute the tool
                        result = await self.tools.execute(
                            tool_call.name,
                            tool_call.arguments,
                            confirm_callback=confirm_callback,
                        )

                        # Verify mutating operations
                        verified = False
                        if self.enable_verification and tool_call.name in MUTATING_TOOLS:
                            verified = await self._verify_outcome(tool_call, result)

                        # Record the call
                        call_record = ToolCallRecord(
                            tool_name=tool_call.name,
                            arguments=tool_call.arguments,
                            result=result,
                            iteration=iterations,
                            verified=verified,
                        )
                        tool_call_records.append(call_record)

                        # Add to audit
                        if self.enable_audit:
                            audit_call = AuditToolCallRecord.from_tool_result(
                                tool_call.name,
                                tool_call.arguments,
                                result,
                                iterations,
                            )
                            self.audit_recorder.add_tool_call(execution_id, audit_call)

                        # Add tool result to session
                        tool_id = tool_call.id or f"call_{len(tool_call_records)}"
                        observation = format_tool_observation(
                            tool_call.name,
                            result.output if result.success else f"Error: {result.error}",
                            result.success,
                        )
                        session.add_tool_result(tool_id, observation)

                    # Stream continuation notification
                    if stream_callback:
                        stream_callback("\n")

                    # Continue from where we left off (KV cache preserved)
                    response = await session.continue_after_tool(
                        stream_callback=stream_callback,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                    )

                    if response.prompt_tokens:
                        total_prompt_tokens += response.prompt_tokens
                    if response.completion_tokens:
                        total_completion_tokens += response.completion_tokens

                    collected_response = response.content

            # Build final result
            duration_ms = int((time.time() - start_time) * 1000)

            # Complete audit
            if self.enable_audit:
                self.audit_recorder.complete(
                    execution_id,
                    collected_response,
                    success=True,
                    iterations=iterations,
                )

            # Record to incident vault - ESSENTIAL for the Spine architecture
            incident_id = self._record_to_incident(
                execution_id=execution_id,
                input=input,
                response=collected_response,
                success=True,
                iterations=iterations,
                duration_ms=duration_ms,
                tool_call_records=tool_call_records,
                retrieval_context=retrieval_context,
            )

            return AgenticLoopResult(
                response=collected_response,
                success=True,
                iterations=iterations,
                total_duration_ms=duration_ms,
                tool_calls=tuple(tool_call_records),
                prompt_tokens=total_prompt_tokens if total_prompt_tokens else None,
                completion_tokens=total_completion_tokens if total_completion_tokens else None,
                execution_id=execution_id,
                incident_id=incident_id,
            )

        except LLMUnavailableError as e:
            duration_ms = int((time.time() - start_time) * 1000)

            if self.enable_audit:
                self.audit_recorder.fail(execution_id, str(e), iterations)

            # Record failure to incident vault
            incident_id = self._record_to_incident(
                execution_id=execution_id,
                input=input,
                response="LLM service unavailable",
                success=False,
                iterations=iterations or 1,
                duration_ms=duration_ms,
                tool_call_records=tool_call_records,
                error=str(e),
            )

            return AgenticLoopResult(
                response="I'm unable to connect to the LLM service. Please ensure Ollama is running.",
                success=False,
                iterations=iterations or 1,
                total_duration_ms=duration_ms,
                tool_calls=tuple(tool_call_records),
                error=str(e),
                execution_id=execution_id,
                incident_id=incident_id,
            )

        except Exception as e:
            logger.exception("Agentic loop failed")
            duration_ms = int((time.time() - start_time) * 1000)

            if self.enable_audit:
                self.audit_recorder.fail(execution_id, str(e), iterations)

            # Record failure to incident vault
            incident_id = self._record_to_incident(
                execution_id=execution_id,
                input=input,
                response=f"Error: {e}",
                success=False,
                iterations=iterations or 1,
                duration_ms=duration_ms,
                tool_call_records=tool_call_records,
                error=str(e),
            )

            return AgenticLoopResult(
                response=f"An error occurred: {e}",
                success=False,
                iterations=iterations or 1,
                total_duration_ms=duration_ms,
                tool_calls=tuple(tool_call_records),
                error=str(e),
                execution_id=execution_id,
                incident_id=incident_id,
            )

    def _record_to_incident(
        self,
        execution_id: str | None,
        input: Any,
        response: str,
        success: bool,
        iterations: int,
        duration_ms: int,
        tool_call_records: list[ToolCallRecord],
        retrieval_context: Any | None = None,
        error: str | None = None,
    ) -> str | None:
        """Record the execution to the incident vault.

        This is ESSENTIAL for the Spine architecture:
        DAEMON -> SIGNALS -> INCIDENT REPORT -> AGENT LOOP -> CAPABILITIES -> OUTCOME -> INCIDENT MEMORY

        Every agent execution creates a permanent record that:
        - Enables pattern matching against past decisions
        - Provides complete audit trail
        - Allows learning from outcomes

        Args:
            execution_id: The loop's execution ID.
            input: The AgenticInput that triggered this execution.
            response: Final response text.
            success: Whether execution succeeded.
            iterations: Number of loop iterations.
            duration_ms: Total execution time.
            tool_call_records: List of tool call records.
            retrieval_context: Retrieval results for provenance.
            error: Error message if failed.

        Returns:
            The incident ID, or None if recording failed.
        """
        try:
            from elle.cli.agentic.incident_recorder import record_execution_to_incident

            # Extract data from input
            user_input = input.query_text if hasattr(input, "query_text") else str(input)
            classified_intent = getattr(input, "classified_intent", "unknown")
            existing_incident_id = getattr(input, "incident_id", None)

            # Convert tool call records to dicts
            tool_calls = [
                {
                    "tool_name": tc.tool_name,
                    "arguments": tc.arguments,
                    "result": {
                        "success": tc.result.success,
                        "output": tc.result.output[:2000] if tc.result.output else "",
                        "error": tc.result.error,
                        "duration_ms": tc.result.duration_ms,
                    },
                    "iteration": tc.iteration,
                    "verified": tc.verified,
                }
                for tc in tool_call_records
            ]

            # Extract capabilities executed
            capabilities_executed = [
                tc.arguments.get("capability_name", "")
                for tc in tool_call_records
                if tc.tool_name == "execute_capability"
            ]

            return record_execution_to_incident(
                execution_id=execution_id or "unknown",
                user_input=user_input,
                classified_intent=classified_intent,
                response=response,
                success=success,
                iterations=iterations,
                total_duration_ms=duration_ms,
                tool_calls=tool_calls,
                retrieval_context=retrieval_context,
                capabilities_executed=capabilities_executed,
                error=error,
                existing_incident_id=existing_incident_id,
            )

        except Exception as e:
            logger.warning(f"Failed to record execution to incident: {e}")
            return None

    async def _verify_outcome(self, tool_call: Any, result: ToolResult) -> bool:
        """Verify the outcome of a mutating operation.

        Args:
            tool_call: The tool call that was executed.
            result: Result from the tool execution.

        Returns:
            True if verification passed, False otherwise.
        """
        if not result.success:
            return False

        # For execute_capability, check if the operation succeeded
        if tool_call.name == "execute_capability":
            capability_name = tool_call.arguments.get("capability_name", "")

            # Service operations - verify service state
            if capability_name.startswith("service."):
                try:
                    # Quick verification check
                    verify_result = await self.tools.execute(
                        "get_system_info",
                        {"aspect": "services", "filter": capability_name.split(".")[-1]},
                    )
                    if verify_result.success:
                        # Check if the expected state is present
                        if capability_name.endswith(".restart") or capability_name.endswith(".start"):
                            return "active" in verify_result.output.lower()
                        elif capability_name.endswith(".stop"):
                            return "inactive" in verify_result.output.lower()
                except Exception as e:
                    logger.debug(f"Verification failed: {e}")
                    return False

        # Default: assume verified if result was successful
        return result.success


# =============================================================================
# Module-level singleton
# =============================================================================

_loop: AgenticLoop | None = None


def get_agentic_loop() -> AgenticLoop:
    """Get the shared AgenticLoop instance.

    Returns:
        The AgenticLoop singleton.
    """
    global _loop
    if _loop is None:
        _loop = AgenticLoop()
    return _loop


def reset_agentic_loop() -> None:
    """Reset the shared AgenticLoop instance."""
    global _loop
    _loop = None


# =============================================================================
# Convenience functions for engine integration
# =============================================================================


async def run_agentic_loop(
    user_input: str,
    *,
    stream_callback: Callable[[str], None] | None = None,
    confirm_callback: ConfirmCallback | None = None,
    session_id: str | None = None,
) -> AgenticLoopResult:
    """Run the agentic loop for a user input.

    Convenience function that uses the shared loop instance.

    Args:
        user_input: The user's input.
        stream_callback: Optional streaming callback.
        confirm_callback: Optional confirmation callback.
        session_id: Optional session ID for audit.

    Returns:
        AgenticLoopResult with the response and metadata.
    """
    loop = get_agentic_loop()
    return await loop.run(
        user_input,
        stream_callback=stream_callback,
        confirm_callback=confirm_callback,
        session_id=session_id,
    )


async def run_for_incident(
    incident_id: str,
    *,
    stream_callback: Callable[[str], None] | None = None,
    confirm_callback: ConfirmCallback | None = None,
    auto_triggered: bool = False,
) -> AgenticLoopResult:
    """Run the agentic loop for a daemon incident.

    Args:
        incident_id: ID of the incident to handle.
        stream_callback: Optional streaming callback.
        confirm_callback: Optional confirmation callback.
        auto_triggered: Whether this was auto-triggered by daemon.

    Returns:
        AgenticLoopResult with the response and metadata.
    """
    from elle.cli.agentic.unified_input import AgenticInput

    input = AgenticInput.from_incident(
        incident_id,
        auto_triggered=auto_triggered,
    )

    loop = get_agentic_loop()
    return await loop.run_with_input(
        input,
        stream_callback=stream_callback,
        confirm_callback=confirm_callback,
    )


async def run_for_failed_command(
    command: str,
    exit_code: int,
    *,
    stdout: str = "",
    stderr: str = "",
    cwd: str = "",
    stream_callback: Callable[[str], None] | None = None,
    confirm_callback: ConfirmCallback | None = None,
) -> AgenticLoopResult:
    """Run the agentic loop for a failed command (fixit).

    Args:
        command: The command that failed.
        exit_code: Exit code of the failed command.
        stdout: Standard output from the command.
        stderr: Standard error from the command.
        cwd: Working directory when command ran.
        stream_callback: Optional streaming callback.
        confirm_callback: Optional confirmation callback.

    Returns:
        AgenticLoopResult with the response and metadata.
    """
    from elle.cli.agentic.unified_input import AgenticInput

    input = AgenticInput.from_failed_command(
        command,
        exit_code,
        stdout=stdout,
        stderr=stderr,
        cwd=cwd,
    )

    loop = get_agentic_loop()
    return await loop.run_with_input(
        input,
        stream_callback=stream_callback,
        confirm_callback=confirm_callback,
    )
