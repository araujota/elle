"""Engine adapter for OpenAI-compatible API.

Wraps Engine.process() for async API use with execution mode enforcement.
Every request goes through intent classification before any action is taken.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from elle.cli.engine import Engine, EngineResult, get_engine
from elle.cli.terminal.classifier import IntentClassifier, get_classifier
from elle.cli.terminal.intent import Intent, IntentResult
from elle.common.session import Session, create_session
from elle.daemon.api.auth import AuthContext
from elle.daemon.api.openai_models import (
    ChatChoice,
    ChatCompletionResponse,
    ChatMessage,
    ExecutionMode,
    FunctionCall,
    PolicySummary,
    ToolCall,
    UsageStats,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Commands that are considered mutating (blocked in readonly mode)
MUTATING_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    # File operations
    re.compile(r"^rm\b"),
    re.compile(r"^mv\b"),
    re.compile(r"^cp\b"),
    re.compile(r"^mkdir\b"),
    re.compile(r"^rmdir\b"),
    re.compile(r"^touch\b"),
    re.compile(r"^chmod\b"),
    re.compile(r"^chown\b"),
    re.compile(r"^ln\b"),
    # Package management
    re.compile(r"^apt\b"),
    re.compile(r"^apt-get\b"),
    re.compile(r"^dpkg\b"),
    re.compile(r"^snap\b"),
    re.compile(r"^pip\b"),
    re.compile(r"^npm\b"),
    # Service management
    re.compile(r"^systemctl\s+(start|stop|restart|enable|disable|reload|mask|unmask)\b"),
    re.compile(r"^service\s+\w+\s+(start|stop|restart)\b"),
    # Docker
    re.compile(r"^docker\s+(run|start|stop|rm|kill|exec|build|push|pull)\b"),
    re.compile(r"^docker-compose\s+(up|down|start|stop|rm)\b"),
    # Git (write operations)
    re.compile(r"^git\s+(commit|push|pull|merge|rebase|reset|checkout|branch\s+-[dD]|stash|clean)\b"),
    # Network
    re.compile(r"^ufw\b"),
    re.compile(r"^iptables\b"),
    # User management
    re.compile(r"^useradd\b"),
    re.compile(r"^userdel\b"),
    re.compile(r"^usermod\b"),
    re.compile(r"^passwd\b"),
    # Disk operations
    re.compile(r"^mount\b"),
    re.compile(r"^umount\b"),
    re.compile(r"^mkfs\b"),
    re.compile(r"^fdisk\b"),
)


def _is_mutating_command(command: str) -> bool:
    """Check if a command would mutate system state.

    Args:
        command: The shell command to check.

    Returns:
        True if the command is considered mutating.
    """
    command = command.strip()
    return any(pattern.search(command) for pattern in MUTATING_COMMAND_PATTERNS)


@dataclass(frozen=True)
class EngineResponse:
    """Response from engine processing."""

    content: str
    intent: str
    success: bool
    tool_calls: tuple[ToolCall, ...] | None = None
    policy_evaluated: bool = False
    policy_effect: str | None = None
    policy_message: str | None = None


class EngineAdapter:
    """Adapter for using Engine in async API context.

    Wraps Engine.process() for async execution and enforces
    execution mode restrictions:

    - READONLY: Can read state and propose plans but cannot execute mutations
    - EXECUTE: Full execution with policy gates
    - CAPABILITIES_ONLY: Returns tool_calls without execution
    """

    def __init__(
        self,
        engine: Engine | None = None,
        classifier: IntentClassifier | None = None,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        """Initialize the engine adapter.

        Args:
            engine: Engine instance. Uses shared instance if not provided.
            classifier: Intent classifier. Uses shared instance if not provided.
            executor: Thread pool for running sync engine in async context.
        """
        self._engine = engine
        self._classifier = classifier
        self._executor = executor or ThreadPoolExecutor(max_workers=4)

    @property
    def engine(self) -> Engine:
        """Get the engine (lazy initialization)."""
        if self._engine is None:
            self._engine = get_engine()
        return self._engine

    @property
    def classifier(self) -> IntentClassifier:
        """Get the classifier (lazy initialization)."""
        if self._classifier is None:
            self._classifier = get_classifier()
        return self._classifier

    async def process_chat(
        self,
        messages: tuple[ChatMessage, ...],
        mode: ExecutionMode,
        auth: AuthContext,
    ) -> EngineResponse:
        """Process a chat completion request through the engine.

        All requests go through intent classification first. The execution
        mode then determines what actions are allowed.

        Args:
            messages: The conversation messages.
            mode: Execution mode (readonly, execute, capabilities_only).
            auth: Authentication context.

        Returns:
            EngineResponse with the result.
        """
        # Extract the user input from messages
        user_input = self._extract_user_input(messages)
        if not user_input:
            return EngineResponse(
                content="No user message found in the request.",
                intent="meta",
                success=False,
            )

        # Create session with conversation context
        session = self._create_session_from_messages(messages)

        # Run classification (always allowed, even in readonly)
        intent_result = await self._classify_async(user_input, session)

        # Handle based on execution mode
        if mode == ExecutionMode.READONLY:
            return await self._handle_readonly(user_input, intent_result, session)
        elif mode == ExecutionMode.CAPABILITIES_ONLY:
            return await self._handle_capabilities_only(user_input, intent_result, session)
        else:
            return await self._handle_execute(user_input, intent_result, session)

    async def process_chat_streaming(
        self,
        messages: tuple[ChatMessage, ...],
        mode: ExecutionMode,
        auth: AuthContext,
    ) -> AsyncIterator[str]:
        """Process a chat completion request with streaming output.

        Yields content chunks as they become available.

        Args:
            messages: The conversation messages.
            mode: Execution mode.
            auth: Authentication context.

        Yields:
            String content chunks.
        """
        # For now, get the full response and yield it in chunks
        # A more sophisticated implementation could stream from the LLM
        response = await self.process_chat(messages, mode, auth)

        # Yield content in small chunks to simulate streaming
        content = response.content
        chunk_size = 50  # Characters per chunk
        for i in range(0, len(content), chunk_size):
            yield content[i : i + chunk_size]
            await asyncio.sleep(0.01)  # Small delay to simulate streaming

    def _extract_user_input(self, messages: tuple[ChatMessage, ...]) -> str:
        """Extract the most recent user message.

        Args:
            messages: Conversation messages.

        Returns:
            The user's input text, or empty string if not found.
        """
        # Find the last user message
        for msg in reversed(messages):
            if msg.role == "user" and msg.content:
                return msg.content.strip()
        return ""

    def _create_session_from_messages(
        self,
        messages: tuple[ChatMessage, ...],
    ) -> Session:
        """Create an Engine session from conversation context.

        Extracts relevant context from previous messages to populate
        the session's command history.

        Args:
            messages: Conversation messages.

        Returns:
            A Session object with context from the conversation.
        """
        session = create_session()

        # Extract any command history from assistant messages
        # (In a real implementation, we might track commands executed
        # in the conversation and their results)
        history: list[str] = []
        _last_cmd: str | None = None  # TODO: Extract from conversation
        _last_exit: int | None = None  # TODO: Extract from conversation

        for msg in messages:
            if msg.role == "assistant" and msg.content:
                # Try to extract any shell commands that were executed
                # This is a simplified heuristic
                content = msg.content
                if "$ " in content or "`" in content:
                    # Might contain a command
                    pass  # Could extract commands here

        # Return session with extracted history
        if history:
            for cmd in history:
                session = session.with_command_result(cmd, exit_code=0)

        return session

    async def _classify_async(
        self,
        text: str,
        session: Session,
    ) -> IntentResult:
        """Run intent classification asynchronously.

        Args:
            text: User input to classify.
            session: Session context.

        Returns:
            IntentResult from classification.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self.classifier.classify,
            text,
            session,
        )

    async def _process_engine_async(
        self,
        user_input: str,
        session: Session,
    ) -> EngineResult:
        """Run engine processing asynchronously.

        Args:
            user_input: User input to process.
            session: Session context.

        Returns:
            EngineResult from processing.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            lambda: self.engine.process(user_input, session, stream_output=False),
        )

    async def _handle_readonly(
        self,
        user_input: str,
        intent_result: IntentResult,
        session: Session,
    ) -> EngineResponse:
        """Handle a request in readonly mode.

        Readonly mode allows:
        - system_question: Reading system state, getting explanations
        - navigation: Viewing status, logs, incidents
        - meta: Help, about, version
        - system_task: Generates plans but does NOT execute them
        - shell_passthrough: Only for non-mutating commands

        Args:
            user_input: The user's input.
            intent_result: Classification result.
            session: Session context.

        Returns:
            EngineResponse with the result.
        """
        intent = intent_result.intent

        # Always allowed in readonly
        if intent in (Intent.SYSTEM_QUESTION, Intent.NAVIGATION, Intent.META):
            result = await self._process_engine_async(user_input, session)
            return EngineResponse(
                content=result.output,
                intent=intent.value,
                success=result.success,
            )

        # System tasks - return plan only, no execution
        if intent == Intent.SYSTEM_TASK:
            # We could generate a plan without executing, but for now
            # we just explain what would happen
            content = (
                f"[READONLY MODE] Task request: {user_input}\n\n"
                "In readonly mode, I can analyze this request but cannot "
                "execute any system changes. Switch to 'elle' model for "
                "full execution capabilities.\n\n"
                f"Intent classified as: {intent.value}\n"
                f"Confidence: {intent_result.confidence:.2f}"
            )
            return EngineResponse(
                content=content,
                intent=intent.value,
                success=True,
                policy_evaluated=True,
                policy_effect="readonly_blocked",
                policy_message="Execution blocked in readonly mode",
            )

        # Shell commands - check if mutating
        if intent == Intent.SHELL_PASSTHROUGH:
            command = user_input
            if command.startswith("/sh "):
                command = command[4:]
            elif command.startswith("!"):
                command = command[1:]

            if _is_mutating_command(command):
                return EngineResponse(
                    content=(
                        f"[READONLY MODE] Command blocked: {command}\n\n"
                        "This command would modify system state and is not "
                        "allowed in readonly mode. Switch to 'elle' model "
                        "for full execution capabilities."
                    ),
                    intent=intent.value,
                    success=False,
                    policy_evaluated=True,
                    policy_effect="readonly_blocked",
                    policy_message="Mutating command blocked in readonly mode",
                )

            # Non-mutating command - allow it
            result = await self._process_engine_async(user_input, session)
            return EngineResponse(
                content=result.output,
                intent=intent.value,
                success=result.success,
            )

        # GUI tasks and fixit - blocked in readonly
        if intent in (Intent.GUI_TASK, Intent.FIXIT):
            return EngineResponse(
                content=(
                    f"[READONLY MODE] {intent.value} blocked.\n\n"
                    "This operation is not available in readonly mode. "
                    "Switch to 'elle' model for full capabilities."
                ),
                intent=intent.value,
                success=False,
                policy_evaluated=True,
                policy_effect="readonly_blocked",
            )

        # Fallback - process but warn
        result = await self._process_engine_async(user_input, session)
        return EngineResponse(
            content=result.output,
            intent=intent.value,
            success=result.success,
        )

    async def _handle_capabilities_only(
        self,
        user_input: str,
        intent_result: IntentResult,
        session: Session,
    ) -> EngineResponse:
        """Handle a request in capabilities_only mode.

        Returns tool_calls for capabilities without executing them.
        The client can then execute the capabilities externally.

        Args:
            user_input: The user's input.
            intent_result: Classification result.
            session: Session context.

        Returns:
            EngineResponse with tool_calls.
        """
        intent = intent_result.intent

        # For tasks, convert to capability tool calls
        if intent == Intent.SYSTEM_TASK:
            # Generate a capability representation
            tool_calls = self._generate_capability_tool_calls(user_input, intent_result)
            content = f"[CAPABILITIES_ONLY MODE] Task: {user_input}\n\nThe following capabilities would be invoked:"
            return EngineResponse(
                content=content,
                intent=intent.value,
                success=True,
                tool_calls=tool_calls,
            )

        # For shell commands, wrap in a shell capability
        if intent == Intent.SHELL_PASSTHROUGH:
            command = user_input
            if command.startswith("/sh "):
                command = command[4:]
            elif command.startswith("!"):
                command = command[1:]

            tool_call = ToolCall(
                id=f"call_{uuid.uuid4().hex[:8]}",
                type="function",
                function=FunctionCall(
                    name="shell.execute",
                    arguments=json.dumps({"command": command}),
                ),
            )
            return EngineResponse(
                content=f"[CAPABILITIES_ONLY] Shell command: {command}",
                intent=intent.value,
                success=True,
                tool_calls=(tool_call,),
            )

        # For other intents, process normally but don't execute mutations
        result = await self._process_engine_async(user_input, session)
        return EngineResponse(
            content=result.output,
            intent=intent.value,
            success=result.success,
        )

    async def _handle_execute(
        self,
        user_input: str,
        intent_result: IntentResult,
        session: Session,
    ) -> EngineResponse:
        """Handle a request in full execute mode.

        Processes through the full engine pipeline with policy gates.

        Args:
            user_input: The user's input.
            intent_result: Classification result.
            session: Session context.

        Returns:
            EngineResponse with the result.
        """
        # Full execution through engine
        result = await self._process_engine_async(user_input, session)

        return EngineResponse(
            content=result.output,
            intent=intent_result.intent.value,
            success=result.success,
            policy_evaluated=True,
            policy_effect="allow" if result.success else "executed_with_errors",
        )

    def _generate_capability_tool_calls(
        self,
        user_input: str,
        intent_result: IntentResult,
    ) -> tuple[ToolCall, ...]:
        """Generate tool calls for a task request.

        Converts a natural language task into capability tool calls
        that would be executed.

        Args:
            user_input: The task request.
            intent_result: Classification result with entities.

        Returns:
            Tuple of ToolCall objects representing the capabilities.
        """
        # This is a simplified implementation. A full implementation
        # would use the planner to generate a proper plan and convert
        # each step to a capability.
        entities = intent_result.entities or []

        tool_calls: list[ToolCall] = []

        # Try to infer capabilities from keywords
        lower = user_input.lower()

        if "restart" in lower and any(e for e in entities if e not in ("restart",)):
            # Service restart
            service = next((e for e in entities if e not in ("restart",)), "unknown")
            tool_calls.append(
                ToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    type="function",
                    function=FunctionCall(
                        name="service.restart",
                        arguments=json.dumps({"service": service}),
                    ),
                )
            )

        elif "install" in lower:
            # Package install
            package = next((e for e in entities if e not in ("install",)), "unknown")
            tool_calls.append(
                ToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    type="function",
                    function=FunctionCall(
                        name="package.install",
                        arguments=json.dumps({"package": package}),
                    ),
                )
            )

        # If no specific capability was inferred, create a generic task
        if not tool_calls:
            tool_calls.append(
                ToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    type="function",
                    function=FunctionCall(
                        name="system.task",
                        arguments=json.dumps(
                            {
                                "description": user_input,
                                "entities": entities,
                            }
                        ),
                    ),
                )
            )

        return tuple(tool_calls)


def format_completion_response(
    response: EngineResponse,
    model: str,
    completion_id: str | None = None,
) -> ChatCompletionResponse:
    """Format an EngineResponse as a ChatCompletionResponse.

    Args:
        response: The engine response.
        model: The model name used.
        completion_id: Optional completion ID. Generated if not provided.

    Returns:
        Formatted ChatCompletionResponse.
    """
    # Create the assistant message
    message = ChatMessage(
        role="assistant",
        content=response.content,
        tool_calls=response.tool_calls,
    )

    # Determine finish reason
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"] = "stop"
    if response.tool_calls:
        finish_reason = "tool_calls"

    # Create choice
    choice = ChatChoice(
        index=0,
        message=message,
        finish_reason=finish_reason,
    )

    # Estimate token usage (simplified)
    prompt_tokens = len(response.content.split()) * 2  # Rough estimate
    completion_tokens = len(response.content.split())
    usage = UsageStats(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )

    # Policy summary
    policy_result = None
    if response.policy_evaluated:
        policy_result = PolicySummary(
            evaluated=True,
            effect=response.policy_effect,
            message=response.policy_message,
        )

    return ChatCompletionResponse(
        model=model,
        choices=(choice,),
        usage=usage,
        intent=response.intent,
        policy_result=policy_result,
    )


# Module-level adapter instance
_adapter: EngineAdapter | None = None


def get_adapter() -> EngineAdapter:
    """Get the shared engine adapter instance."""
    global _adapter
    if _adapter is None:
        _adapter = EngineAdapter()
    return _adapter
