"""Tests for OpenAI-compatible Pydantic models."""

import time

import pytest
from pydantic import ValidationError

from elle.daemon.api.openai_models import (
    ELLE_MODELS,
    MODEL_TO_MODE,
    ChatChoice,
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ExecutionMode,
    FunctionCall,
    ModelInfo,
    ModelsListResponse,
    PolicySummary,
    StreamChoice,
    ToolCall,
    UsageStats,
)


class TestExecutionMode:
    """Tests for ExecutionMode enum."""

    def test_readonly_mode(self):
        """Readonly mode should be defined."""
        assert ExecutionMode.READONLY.value == "readonly"

    def test_execute_mode(self):
        """Execute mode should be defined."""
        assert ExecutionMode.EXECUTE.value == "execute"

    def test_capabilities_only_mode(self):
        """Capabilities_only mode should be defined."""
        assert ExecutionMode.CAPABILITIES_ONLY.value == "capabilities_only"


class TestModelToMode:
    """Tests for model name to execution mode mapping."""

    def test_elle_maps_to_execute(self):
        """elle model should map to execute mode."""
        assert MODEL_TO_MODE["elle"] == ExecutionMode.EXECUTE

    def test_elle_readonly_maps_to_readonly(self):
        """elle.readonly model should map to readonly mode."""
        assert MODEL_TO_MODE["elle.readonly"] == ExecutionMode.READONLY

    def test_elle_capabilities_only_maps_correctly(self):
        """elle.capabilities_only should map to capabilities_only mode."""
        assert MODEL_TO_MODE["elle.capabilities_only"] == ExecutionMode.CAPABILITIES_ONLY


class TestChatMessage:
    """Tests for ChatMessage model."""

    def test_user_message(self):
        """User message should be valid."""
        msg = ChatMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.tool_calls is None

    def test_assistant_message(self):
        """Assistant message should be valid."""
        msg = ChatMessage(role="assistant", content="Hi there!")
        assert msg.role == "assistant"
        assert msg.content == "Hi there!"

    def test_system_message(self):
        """System message should be valid."""
        msg = ChatMessage(role="system", content="You are helpful")
        assert msg.role == "system"

    def test_tool_message(self):
        """Tool message should have tool_call_id."""
        msg = ChatMessage(
            role="tool",
            content="Result",
            tool_call_id="call_123",
        )
        assert msg.role == "tool"
        assert msg.tool_call_id == "call_123"

    def test_message_with_tool_calls(self):
        """Assistant message can have tool calls."""
        tool_call = ToolCall(
            id="call_123",
            type="function",
            function=FunctionCall(name="test", arguments="{}"),
        )
        msg = ChatMessage(
            role="assistant",
            content=None,
            tool_calls=(tool_call,),
        )
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1

    def test_invalid_role_rejected(self):
        """Invalid role should be rejected."""
        with pytest.raises(ValidationError):
            ChatMessage(role="invalid", content="test")

    def test_message_is_frozen(self):
        """ChatMessage should be immutable."""
        msg = ChatMessage(role="user", content="Hello")
        with pytest.raises(ValidationError):
            msg.content = "Changed"


class TestToolCall:
    """Tests for ToolCall model."""

    def test_basic_tool_call(self):
        """Basic tool call should be valid."""
        tc = ToolCall(
            id="call_abc123",
            type="function",
            function=FunctionCall(
                name="shell.execute",
                arguments='{"command": "ls"}',
            ),
        )
        assert tc.id == "call_abc123"
        assert tc.type == "function"
        assert tc.function.name == "shell.execute"

    def test_type_defaults_to_function(self):
        """Type should default to function."""
        tc = ToolCall(
            id="call_123",
            function=FunctionCall(name="test", arguments="{}"),
        )
        assert tc.type == "function"


class TestChatCompletionRequest:
    """Tests for ChatCompletionRequest model."""

    def test_basic_request(self):
        """Basic request should be valid."""
        request = ChatCompletionRequest(
            model="elle.readonly",
            messages=(ChatMessage(role="user", content="Hello"),),
        )
        assert request.model == "elle.readonly"
        assert len(request.messages) == 1
        assert request.stream is False

    def test_streaming_request(self):
        """Streaming request should be valid."""
        request = ChatCompletionRequest(
            model="elle",
            messages=(ChatMessage(role="user", content="Hello"),),
            stream=True,
        )
        assert request.stream is True

    def test_invalid_model_rejected(self):
        """Invalid model should be rejected."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4",
                messages=(ChatMessage(role="user", content="Hello"),),
            )

    def test_empty_messages_rejected(self):
        """Empty messages should be rejected."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="elle",
                messages=(),
            )

    def test_all_valid_models(self):
        """All valid model names should be accepted."""
        for model in ("elle", "elle.readonly", "elle.capabilities_only"):
            request = ChatCompletionRequest(
                model=model,
                messages=(ChatMessage(role="user", content="test"),),
            )
            assert request.model == model


class TestUsageStats:
    """Tests for UsageStats model."""

    def test_valid_usage(self):
        """Valid usage stats should work."""
        usage = UsageStats(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50
        assert usage.total_tokens == 150

    def test_negative_tokens_rejected(self):
        """Negative token counts should be rejected."""
        with pytest.raises(ValidationError):
            UsageStats(
                prompt_tokens=-1,
                completion_tokens=50,
                total_tokens=49,
            )


class TestPolicySummary:
    """Tests for PolicySummary model."""

    def test_policy_evaluated(self):
        """Policy summary with evaluation should work."""
        policy = PolicySummary(
            evaluated=True,
            effect="allow",
            message=None,
        )
        assert policy.evaluated is True
        assert policy.effect == "allow"

    def test_policy_blocked(self):
        """Policy blocking should include message."""
        policy = PolicySummary(
            evaluated=True,
            effect="deny",
            message="Operation not permitted in readonly mode",
        )
        assert policy.effect == "deny"
        assert policy.message is not None


class TestChatChoice:
    """Tests for ChatChoice model."""

    def test_basic_choice(self):
        """Basic choice should be valid."""
        choice = ChatChoice(
            index=0,
            message=ChatMessage(role="assistant", content="Hello!"),
            finish_reason="stop",
        )
        assert choice.index == 0
        assert choice.finish_reason == "stop"

    def test_tool_calls_finish_reason(self):
        """Tool calls finish reason should be valid."""
        choice = ChatChoice(
            index=0,
            message=ChatMessage(role="assistant", content=None),
            finish_reason="tool_calls",
        )
        assert choice.finish_reason == "tool_calls"


class TestChatCompletionResponse:
    """Tests for ChatCompletionResponse model."""

    def test_basic_response(self):
        """Basic response should be valid."""
        response = ChatCompletionResponse(
            model="elle.readonly",
            choices=(
                ChatChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Hello!"),
                    finish_reason="stop",
                ),
            ),
            usage=UsageStats(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
        )
        assert response.model == "elle.readonly"
        assert response.object == "chat.completion"
        assert len(response.choices) == 1

    def test_response_has_auto_id(self):
        """Response should have auto-generated ID."""
        response = ChatCompletionResponse(
            model="elle",
            choices=(
                ChatChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Hi"),
                    finish_reason="stop",
                ),
            ),
            usage=UsageStats(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            ),
        )
        assert response.id.startswith("chatcmpl-")

    def test_response_has_created_timestamp(self):
        """Response should have created timestamp."""
        before = int(time.time())
        response = ChatCompletionResponse(
            model="elle",
            choices=(
                ChatChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Hi"),
                    finish_reason="stop",
                ),
            ),
            usage=UsageStats(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            ),
        )
        after = int(time.time())
        assert before <= response.created <= after

    def test_response_with_elle_extensions(self):
        """Response can include ELLE-specific extensions."""
        response = ChatCompletionResponse(
            model="elle",
            choices=(
                ChatChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Hi"),
                    finish_reason="stop",
                ),
            ),
            usage=UsageStats(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            ),
            intent="system_question",
            policy_result=PolicySummary(evaluated=True, effect="allow"),
        )
        assert response.intent == "system_question"
        assert response.policy_result.evaluated is True


class TestStreamChoice:
    """Tests for StreamChoice model (streaming chunks)."""

    def test_content_delta(self):
        """Content delta should be valid."""
        choice = StreamChoice(
            index=0,
            delta=ChatMessage(role="assistant", content="Hello"),
            finish_reason=None,
        )
        assert choice.delta.content == "Hello"
        assert choice.finish_reason is None

    def test_final_chunk(self):
        """Final chunk should have finish_reason."""
        choice = StreamChoice(
            index=0,
            delta=ChatMessage(role="assistant", content=None),
            finish_reason="stop",
        )
        assert choice.finish_reason == "stop"


class TestChatCompletionChunk:
    """Tests for ChatCompletionChunk model (streaming)."""

    def test_basic_chunk(self):
        """Basic chunk should be valid."""
        chunk = ChatCompletionChunk(
            id="chatcmpl-abc123",
            created=int(time.time()),
            model="elle.readonly",
            choices=(
                StreamChoice(
                    index=0,
                    delta=ChatMessage(role="assistant", content="Hi"),
                    finish_reason=None,
                ),
            ),
        )
        assert chunk.object == "chat.completion.chunk"
        assert chunk.model == "elle.readonly"


class TestModelInfo:
    """Tests for ModelInfo model."""

    def test_basic_model_info(self):
        """Basic model info should be valid."""
        info = ModelInfo(
            id="elle",
            execution_mode="execute",
            description="Full execution mode",
        )
        assert info.id == "elle"
        assert info.object == "model"
        assert info.owned_by == "elle"
        assert info.execution_mode == "execute"


class TestModelsListResponse:
    """Tests for ModelsListResponse model."""

    def test_list_response(self):
        """Models list response should be valid."""
        response = ModelsListResponse(data=ELLE_MODELS)
        assert response.object == "list"
        assert len(response.data) == 3

    def test_contains_all_modes(self):
        """Should contain all three execution modes."""
        response = ModelsListResponse(data=ELLE_MODELS)
        model_ids = [m.id for m in response.data]
        assert "elle" in model_ids
        assert "elle.readonly" in model_ids
        assert "elle.capabilities_only" in model_ids


class TestElleModelsConstant:
    """Tests for ELLE_MODELS constant."""

    def test_three_models_defined(self):
        """Three models should be defined."""
        assert len(ELLE_MODELS) == 3

    def test_elle_model_has_execute_mode(self):
        """elle model should have execute mode."""
        elle = next(m for m in ELLE_MODELS if m.id == "elle")
        assert elle.execution_mode == "execute"

    def test_readonly_model_has_readonly_mode(self):
        """elle.readonly model should have readonly mode."""
        readonly = next(m for m in ELLE_MODELS if m.id == "elle.readonly")
        assert readonly.execution_mode == "readonly"

    def test_capabilities_only_has_correct_mode(self):
        """elle.capabilities_only should have correct mode."""
        caps = next(m for m in ELLE_MODELS if m.id == "elle.capabilities_only")
        assert caps.execution_mode == "capabilities_only"
