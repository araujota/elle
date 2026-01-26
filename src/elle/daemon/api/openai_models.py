"""OpenAI-compatible Pydantic models for chat completions API.

Defines request/response models that follow the OpenAI chat completions
API specification, enabling standard clients to interact with ELLE.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExecutionMode(str, Enum):
    """Execution modes for the chat completions API.

    Each mode maps to a specific model name and controls what
    operations ELLE is allowed to perform.
    """

    READONLY = "readonly"  # Read system state, propose plans, NO mutations
    EXECUTE = "execute"  # Full execution with policy gates
    CAPABILITIES_ONLY = "capabilities_only"  # Returns tool_calls without execution


# Map model names to execution modes
MODEL_TO_MODE: dict[str, ExecutionMode] = {
    "elle": ExecutionMode.EXECUTE,
    "elle.readonly": ExecutionMode.READONLY,
    "elle.capabilities_only": ExecutionMode.CAPABILITIES_ONLY,
}

# Valid model names
ModelName = Literal["elle", "elle.readonly", "elle.capabilities_only"]


class FunctionCall(BaseModel):
    """Function call within a tool call."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Name of the function to call")
    arguments: str = Field(description="JSON-encoded arguments for the function")


class ToolCall(BaseModel):
    """Tool call in assistant message."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Unique ID for this tool call")
    type: Literal["function"] = "function"
    function: FunctionCall = Field(description="The function call details")


class ChatMessage(BaseModel):
    """Single message in the conversation.

    Follows OpenAI's message format with role, content, and optional
    tool call fields.
    """

    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant", "tool"] = Field(
        description="Role of the message sender"
    )
    content: str | None = Field(
        default=None,
        description="Text content of the message",
    )
    tool_calls: tuple[ToolCall, ...] | None = Field(
        default=None,
        description="Tool calls made by the assistant",
    )
    tool_call_id: str | None = Field(
        default=None,
        description="ID of the tool call this message is responding to",
    )
    name: str | None = Field(
        default=None,
        description="Name of the tool for tool messages",
    )


class ChatCompletionRequest(BaseModel):
    """Request for POST /v1/chat/completions.

    Follows OpenAI's chat completions request format but with
    intentionally locked-down settings. Dangerous parameters like
    temperature, system prompt override, and custom tools are NOT exposed.
    """

    model_config = ConfigDict(frozen=True)

    model: ModelName = Field(
        description="Model ID determining execution mode"
    )
    messages: tuple[ChatMessage, ...] = Field(
        description="Conversation history (at least one message required)",
    )

    @field_validator("messages")
    @classmethod
    def messages_not_empty(cls, v: tuple[ChatMessage, ...]) -> tuple[ChatMessage, ...]:
        """Ensure at least one message is provided."""
        if not v:
            raise ValueError("At least one message is required")
        return v
    stream: bool = Field(
        default=False,
        description="Whether to stream the response via SSE",
    )

    # Note: The following OpenAI parameters are intentionally NOT exposed:
    # - temperature: Fixed at ELLE's configured value
    # - top_p, frequency_penalty, presence_penalty: Not exposed
    # - system prompt override: ELLE's classification prompts are internal
    # - tools/functions: Only ELLE capabilities available
    # - max_tokens: Managed internally


class UsageStats(BaseModel):
    """Token usage statistics for the completion."""

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = Field(ge=0, description="Tokens in the prompt")
    completion_tokens: int = Field(ge=0, description="Tokens in the completion")
    total_tokens: int = Field(ge=0, description="Total tokens used")


class PolicySummary(BaseModel):
    """Summary of policy evaluation for the request.

    ELLE extension to standard OpenAI response.
    """

    model_config = ConfigDict(frozen=True)

    evaluated: bool = Field(description="Whether policy was evaluated")
    effect: str | None = Field(
        default=None,
        description="Policy effect (allow, deny, require_confirmation, etc.)",
    )
    message: str | None = Field(
        default=None,
        description="Policy message if blocked or requires action",
    )


class ChatChoice(BaseModel):
    """Single choice in the completion response."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0, description="Index of this choice")
    message: ChatMessage = Field(description="The assistant's message")
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"] | None = Field(
        default=None,
        description="Reason the generation stopped",
    )


class ChatCompletionResponse(BaseModel):
    """Response for POST /v1/chat/completions.

    Follows OpenAI's chat completions response format with
    ELLE-specific extensions for intent and policy information.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(
        default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:24]}",
        description="Unique identifier for this completion",
    )
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(
        default_factory=lambda: int(time.time()),
        description="Unix timestamp of when the completion was created",
    )
    model: str = Field(description="Model used for the completion")
    choices: tuple[ChatChoice, ...] = Field(
        description="List of completion choices (at least one required)",
    )

    @field_validator("choices")
    @classmethod
    def choices_not_empty(cls, v: tuple[ChatChoice, ...]) -> tuple[ChatChoice, ...]:
        """Ensure at least one choice is provided."""
        if not v:
            raise ValueError("At least one choice is required")
        return v
    usage: UsageStats = Field(description="Token usage statistics")

    # ELLE extensions
    intent: str | None = Field(
        default=None,
        description="Classified intent for the request",
    )
    policy_result: PolicySummary | None = Field(
        default=None,
        description="Policy evaluation result",
    )


class StreamChoice(BaseModel):
    """Single choice in a streaming chunk."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0, description="Index of this choice")
    delta: ChatMessage = Field(description="Partial message content")
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"] | None = Field(
        default=None,
        description="Reason the generation stopped (only in final chunk)",
    )


class ChatCompletionChunk(BaseModel):
    """Streaming chunk for POST /v1/chat/completions with stream=true."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Completion ID (same across all chunks)")
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int = Field(description="Unix timestamp")
    model: str = Field(description="Model used")
    choices: tuple[StreamChoice, ...] = Field(description="Partial choices")


class ModelInfo(BaseModel):
    """Information about an available model."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Model identifier")
    object: Literal["model"] = "model"
    created: int = Field(
        default_factory=lambda: int(time.time()),
        description="Unix timestamp of model creation",
    )
    owned_by: str = Field(
        default="elle",
        description="Organization that owns the model",
    )

    # ELLE extensions
    execution_mode: str = Field(
        description="Execution mode for this model (readonly, execute, capabilities_only)",
    )
    description: str = Field(
        default="",
        description="Human-readable description of the model",
    )


class ModelsListResponse(BaseModel):
    """Response for GET /v1/models."""

    model_config = ConfigDict(frozen=True)

    object: Literal["list"] = "list"
    data: tuple[ModelInfo, ...] = Field(description="Available models")


# Predefined model info for the three ELLE modes
ELLE_MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(
        id="elle",
        execution_mode="execute",
        description="Full execution with policy gates. High-risk actions need confirmation.",
    ),
    ModelInfo(
        id="elle.readonly",
        execution_mode="readonly",
        description="Read-only mode. Can read state and propose plans but cannot change.",
    ),
    ModelInfo(
        id="elle.capabilities_only",
        execution_mode="capabilities_only",
        description="Returns capability tool_calls without execution. Execute externally.",
    ),
)
