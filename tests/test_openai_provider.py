from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from elle.rag.providers.base import ProviderResponse
from elle.rag.providers.openai_provider import OpenAIProvider

# =============================================================================
# Helpers
# =============================================================================


def _make_chat_response(
    content: str = "Hello!",
    model: str = "gpt-4o",
    tool_calls: list[dict[str, Any]] | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> dict[str, Any]:
    """Build a realistic /v1/chat/completions JSON response."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _make_stream_lines(
    content_fragments: list[str],
    model: str = "gpt-4o",
    tool_call_deltas: list[dict[str, Any]] | None = None,
    include_usage: bool = False,
) -> list[str]:
    """Build SSE lines as they would appear from a streaming response."""
    lines: list[str] = []
    for i, fragment in enumerate(content_fragments):
        is_last = i == len(content_fragments) - 1
        delta: dict[str, Any] = {"content": fragment}
        if tool_call_deltas and i < len(tool_call_deltas):
            delta["tool_calls"] = [tool_call_deltas[i]]
        chunk: dict[str, Any] = {
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": "stop" if is_last else None,
                }
            ],
        }
        if is_last and include_usage:
            chunk["usage"] = {
                "prompt_tokens": 10,
                "completion_tokens": 5,
            }
        lines.append(f"data: {json.dumps(chunk)}")
    lines.append("data: [DONE]")
    return lines


def _make_models_response(model_ids: list[str] | None = None) -> dict[str, Any]:
    """Build a /v1/models list response."""
    if model_ids is None:
        model_ids = ["gpt-4o", "gpt-4o-mini"]
    return {
        "object": "list",
        "data": [{"id": mid, "object": "model"} for mid in model_ids],
    }


# =============================================================================
# Construction
# =============================================================================


class TestOpenAIProviderInit:
    """Tests for __init__ and host normalization."""

    def test_host_stored_without_trailing_slash(self) -> None:
        provider = OpenAIProvider(host="http://localhost:8080/")
        assert provider._host == "http://localhost:8080"

    def test_host_strips_trailing_v1(self) -> None:
        provider = OpenAIProvider(host="http://localhost:8080/v1")
        assert provider._host == "http://localhost:8080"

    def test_host_strips_trailing_v1_with_slash(self) -> None:
        provider = OpenAIProvider(host="http://localhost:8080/v1/")
        assert provider._host == "http://localhost:8080"

    def test_default_model(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        assert provider._default_model == "gpt-4o"

    def test_custom_default_model(self) -> None:
        provider = OpenAIProvider(host="http://localhost", default_model="gpt-3.5-turbo")
        assert provider._default_model == "gpt-3.5-turbo"

    def test_api_key_stored(self) -> None:
        provider = OpenAIProvider(host="http://localhost", api_key="sk-test123")
        assert provider._api_key == "sk-test123"

    def test_timeout_stored(self) -> None:
        provider = OpenAIProvider(host="http://localhost", timeout=60.0)
        assert provider._timeout == 60.0

    def test_authorization_header_set_when_api_key_given(self) -> None:
        provider = OpenAIProvider(host="http://localhost", api_key="sk-test")
        auth = provider._client.headers.get("authorization")
        assert auth == "Bearer sk-test"

    def test_no_authorization_header_when_api_key_empty(self) -> None:
        provider = OpenAIProvider(host="http://localhost", api_key="")
        auth = provider._client.headers.get("authorization")
        assert auth is None

    def test_content_type_header_always_set(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        ct = provider._client.headers.get("content-type")
        assert ct == "application/json"


# =============================================================================
# Properties
# =============================================================================


class TestOpenAIProviderProperties:
    """Tests for read-only properties."""

    def test_provider_type(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        assert provider.provider_type == "openai"

    def test_host_property(self) -> None:
        provider = OpenAIProvider(host="http://example.com")
        assert provider.host == "http://example.com"


# =============================================================================
# _build_url
# =============================================================================


class TestBuildUrl:
    """Tests for internal URL construction."""

    def test_build_url_chat_completions(self) -> None:
        provider = OpenAIProvider(host="http://localhost:8080")
        assert provider._build_url("/chat/completions") == "http://localhost:8080/v1/chat/completions"

    def test_build_url_models(self) -> None:
        provider = OpenAIProvider(host="https://api.openai.com")
        assert provider._build_url("/models") == "https://api.openai.com/v1/models"

    def test_build_url_after_host_v1_strip(self) -> None:
        provider = OpenAIProvider(host="http://localhost:8080/v1")
        url = provider._build_url("/chat/completions")
        assert url == "http://localhost:8080/v1/chat/completions"


# =============================================================================
# generate()
# =============================================================================


class TestGenerate:
    """Tests for the generate() method, which wraps chat()."""

    def test_generate_delegates_to_chat(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        expected = ProviderResponse(content="ok", model="gpt-4o", done=True)
        provider.chat = MagicMock(return_value=expected)

        result = provider.generate(
            "Hello",
            model="gpt-4o",
            max_tokens=100,
            temperature=0.7,
            timeout=30.0,
        )

        assert result is expected
        call_args = provider.chat.call_args
        messages = call_args.kwargs["messages"]
        assert len(messages) == 1
        assert messages[0] == {"role": "user", "content": "Hello"}

    def test_generate_with_system_prompt(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        expected = ProviderResponse(content="ok", model="gpt-4o", done=True)
        provider.chat = MagicMock(return_value=expected)

        provider.generate(
            "Hello",
            system="Be helpful",
            model="gpt-4o",
            max_tokens=100,
            temperature=0.7,
            timeout=30.0,
        )

        call_args = provider.chat.call_args
        messages = call_args.kwargs["messages"]
        assert len(messages) == 2
        assert messages[0] == {"role": "system", "content": "Be helpful"}
        assert messages[1] == {"role": "user", "content": "Hello"}

    def test_generate_passes_json_mode(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        provider.chat = MagicMock(return_value=ProviderResponse(content="{}", model="m", done=True))

        provider.generate(
            "Give JSON",
            model="gpt-4o",
            max_tokens=100,
            temperature=0.0,
            timeout=30.0,
            json_mode=True,
        )

        assert provider.chat.call_args.kwargs["json_mode"] is True

    def test_generate_without_system_has_single_message(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        provider.chat = MagicMock(return_value=ProviderResponse(content="x", model="m", done=True))

        provider.generate(
            "prompt",
            system=None,
            model="gpt-4o",
            max_tokens=50,
            temperature=0.5,
            timeout=10.0,
        )

        messages = provider.chat.call_args.kwargs["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"


# =============================================================================
# chat()
# =============================================================================


class TestChat:
    """Tests for the chat() method."""

    def _mock_post(self, provider: OpenAIProvider, response_data: dict[str, Any]) -> MagicMock:
        mock_response = MagicMock()
        mock_response.json.return_value = response_data
        mock_response.raise_for_status.return_value = None
        provider._client.post = MagicMock(return_value=mock_response)
        return mock_response

    def test_basic_chat(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        self._mock_post(provider, _make_chat_response(content="Hi there"))

        result = provider.chat(
            messages=[{"role": "user", "content": "Hello"}],
            model="gpt-4o",
            max_tokens=100,
            temperature=0.7,
            timeout=30.0,
        )

        assert isinstance(result, ProviderResponse)
        assert result.content == "Hi there"
        assert result.model == "gpt-4o"
        assert result.done is True
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 5
        assert result.duration_ms is not None
        assert result.duration_ms >= 0

    def test_chat_json_mode_sets_response_format(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        self._mock_post(provider, _make_chat_response(content='{"key": "val"}'))

        provider.chat(
            messages=[{"role": "user", "content": "json please"}],
            model="gpt-4o",
            max_tokens=100,
            temperature=0.0,
            timeout=30.0,
            json_mode=True,
        )

        call_kwargs = provider._client.post.call_args
        payload = call_kwargs.kwargs["json"]
        assert payload["response_format"] == {"type": "json_object"}

    def test_chat_without_json_mode_no_response_format(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        self._mock_post(provider, _make_chat_response())

        provider.chat(
            messages=[{"role": "user", "content": "Hello"}],
            model="gpt-4o",
            max_tokens=100,
            temperature=0.7,
            timeout=30.0,
            json_mode=False,
        )

        payload = provider._client.post.call_args.kwargs["json"]
        assert "response_format" not in payload

    def test_chat_with_tools(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        self._mock_post(provider, _make_chat_response())

        tools = [{"type": "function", "function": {"name": "test_tool"}}]
        provider.chat(
            messages=[{"role": "user", "content": "Use tool"}],
            model="gpt-4o",
            max_tokens=100,
            temperature=0.0,
            timeout=30.0,
            tools=tools,
        )

        payload = provider._client.post.call_args.kwargs["json"]
        assert payload["tools"] == tools

    def test_chat_without_tools(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        self._mock_post(provider, _make_chat_response())

        provider.chat(
            messages=[{"role": "user", "content": "No tools"}],
            model="gpt-4o",
            max_tokens=100,
            temperature=0.0,
            timeout=30.0,
        )

        payload = provider._client.post.call_args.kwargs["json"]
        assert "tools" not in payload

    def test_chat_stream_is_false(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        self._mock_post(provider, _make_chat_response())

        provider.chat(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            max_tokens=10,
            temperature=0.0,
            timeout=10.0,
        )

        payload = provider._client.post.call_args.kwargs["json"]
        assert payload["stream"] is False

    def test_chat_with_tool_calls_in_response(self) -> None:
        tool_calls_data = [
            {
                "id": "call_abc",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location": "NYC"}',
                },
            }
        ]
        response_data = _make_chat_response(
            content="",
            tool_calls=tool_calls_data,
        )
        provider = OpenAIProvider(host="http://localhost")
        self._mock_post(provider, response_data)

        result = provider.chat(
            messages=[{"role": "user", "content": "Weather?"}],
            model="gpt-4o",
            max_tokens=100,
            temperature=0.0,
            timeout=30.0,
        )

        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert tc["id"] == "call_abc"
        assert tc["name"] == "get_weather"
        assert tc["arguments"] == {"location": "NYC"}

    def test_chat_tool_call_with_invalid_json_arguments(self) -> None:
        tool_calls_data = [
            {
                "id": "call_bad",
                "type": "function",
                "function": {
                    "name": "broken",
                    "arguments": "not valid json{{{",
                },
            }
        ]
        response_data = _make_chat_response(content="", tool_calls=tool_calls_data)
        provider = OpenAIProvider(host="http://localhost")
        self._mock_post(provider, response_data)

        result = provider.chat(
            messages=[{"role": "user", "content": "x"}],
            model="gpt-4o",
            max_tokens=10,
            temperature=0.0,
            timeout=10.0,
        )

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["arguments"] == {}

    def test_chat_tool_call_with_dict_arguments(self) -> None:
        """When arguments is already a dict (not a JSON string)."""
        tool_calls_data = [
            {
                "id": "call_dict",
                "type": "function",
                "function": {
                    "name": "direct_dict",
                    "arguments": {"key": "value"},
                },
            }
        ]
        response_data = _make_chat_response(content="", tool_calls=tool_calls_data)
        provider = OpenAIProvider(host="http://localhost")
        self._mock_post(provider, response_data)

        result = provider.chat(
            messages=[{"role": "user", "content": "x"}],
            model="gpt-4o",
            max_tokens=10,
            temperature=0.0,
            timeout=10.0,
        )

        assert result.tool_calls[0]["arguments"] == {"key": "value"}

    def test_chat_empty_choices(self) -> None:
        response_data = {
            "model": "gpt-4o",
            "choices": [],
            "usage": {"prompt_tokens": 5, "completion_tokens": 0},
        }
        provider = OpenAIProvider(host="http://localhost")
        self._mock_post(provider, response_data)

        result = provider.chat(
            messages=[{"role": "user", "content": "x"}],
            model="gpt-4o",
            max_tokens=10,
            temperature=0.0,
            timeout=10.0,
        )

        assert result.content == ""
        assert result.tool_calls == []

    def test_chat_missing_choices_key(self) -> None:
        response_data = {
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 5, "completion_tokens": 0},
        }
        provider = OpenAIProvider(host="http://localhost")
        self._mock_post(provider, response_data)

        result = provider.chat(
            messages=[{"role": "user", "content": "x"}],
            model="gpt-4o",
            max_tokens=10,
            temperature=0.0,
            timeout=10.0,
        )

        assert result.content == ""

    def test_chat_null_content_returns_empty_string(self) -> None:
        response_data = _make_chat_response(content=None)
        # content=None: the helper sets it, but we override
        response_data["choices"][0]["message"]["content"] = None
        provider = OpenAIProvider(host="http://localhost")
        self._mock_post(provider, response_data)

        result = provider.chat(
            messages=[{"role": "user", "content": "x"}],
            model="gpt-4o",
            max_tokens=10,
            temperature=0.0,
            timeout=10.0,
        )

        assert result.content == ""

    def test_chat_connect_error_raises(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        provider._client.post = MagicMock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(httpx.ConnectError):
            provider.chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=5.0,
            )

    def test_chat_timeout_error_raises(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        provider._client.post = MagicMock(side_effect=httpx.ReadTimeout("timed out"))

        with pytest.raises(httpx.TimeoutException):
            provider.chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=5.0,
            )

    def test_chat_http_status_error_raises(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=MagicMock(),
            response=MagicMock(),
        )
        provider._client.post = MagicMock(return_value=mock_response)

        with pytest.raises(httpx.HTTPStatusError):
            provider.chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=5.0,
            )

    def test_chat_url_is_correct(self) -> None:
        provider = OpenAIProvider(host="http://myhost:9000")
        self._mock_post(provider, _make_chat_response())

        provider.chat(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            max_tokens=10,
            temperature=0.0,
            timeout=10.0,
        )

        call_args = provider._client.post.call_args
        url = call_args.args[0]
        assert url == "http://myhost:9000/v1/chat/completions"

    def test_chat_no_usage_in_response(self) -> None:
        response_data = _make_chat_response()
        del response_data["usage"]
        provider = OpenAIProvider(host="http://localhost")
        self._mock_post(provider, response_data)

        result = provider.chat(
            messages=[{"role": "user", "content": "x"}],
            model="gpt-4o",
            max_tokens=10,
            temperature=0.0,
            timeout=10.0,
        )

        assert result.prompt_tokens is None
        assert result.completion_tokens is None

    def test_chat_model_fallback_from_response(self) -> None:
        response_data = _make_chat_response(model="gpt-4o-2024-05-13")
        provider = OpenAIProvider(host="http://localhost")
        self._mock_post(provider, response_data)

        result = provider.chat(
            messages=[{"role": "user", "content": "x"}],
            model="gpt-4o",
            max_tokens=10,
            temperature=0.0,
            timeout=10.0,
        )

        assert result.model == "gpt-4o-2024-05-13"


# =============================================================================
# stream_chat()
# =============================================================================


class TestStreamChat:
    """Tests for the stream_chat() SSE streaming method."""

    def _setup_stream(self, provider: OpenAIProvider, lines: list[str]) -> MagicMock:
        """Configure the provider's _client.stream context manager."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.iter_lines.return_value = iter(lines)

        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_response)
        mock_cm.__exit__ = MagicMock(return_value=False)

        provider._client.stream = MagicMock(return_value=mock_cm)
        return mock_response

    def test_stream_chat_basic(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        lines = _make_stream_lines(["Hello", " world"])
        self._setup_stream(provider, lines)

        chunks = list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "Hi"}],
                model="gpt-4o",
                max_tokens=100,
                temperature=0.7,
                timeout=30.0,
            )
        )

        assert len(chunks) == 2
        assert chunks[0].content == "Hello"
        assert chunks[0].done is False
        assert chunks[1].content == " world"
        assert chunks[1].done is True

    def test_stream_chat_done_flag_on_last_chunk(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        lines = _make_stream_lines(["a", "b", "c"])
        self._setup_stream(provider, lines)

        chunks = list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
            )
        )

        for chunk in chunks[:-1]:
            assert chunk.done is False
        assert chunks[-1].done is True

    def test_stream_chat_skips_empty_lines(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        lines = [
            "",
            "data: "
            + json.dumps(
                {
                    "model": "m",
                    "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
                }
            ),
            "",
            "data: [DONE]",
        ]
        self._setup_stream(provider, lines)

        chunks = list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
            )
        )

        assert len(chunks) == 1
        assert chunks[0].content == "ok"

    def test_stream_chat_handles_sse_prefix(self) -> None:
        """Lines with 'data: ' prefix have it stripped correctly."""
        provider = OpenAIProvider(host="http://localhost")
        chunk_json = json.dumps(
            {
                "model": "gpt-4o",
                "choices": [{"index": 0, "delta": {"content": "test"}, "finish_reason": "stop"}],
            }
        )
        lines = [f"data: {chunk_json}", "data: [DONE]"]
        self._setup_stream(provider, lines)

        chunks = list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="gpt-4o",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
            )
        )

        assert chunks[0].content == "test"

    def test_stream_chat_line_without_data_prefix(self) -> None:
        """Lines without 'data: ' prefix are treated as raw JSON."""
        provider = OpenAIProvider(host="http://localhost")
        chunk_json = json.dumps(
            {
                "model": "m",
                "choices": [{"index": 0, "delta": {"content": "raw"}, "finish_reason": "stop"}],
            }
        )
        lines = [chunk_json, "data: [DONE]"]
        self._setup_stream(provider, lines)

        chunks = list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
            )
        )

        assert chunks[0].content == "raw"

    def test_stream_chat_skips_invalid_json(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        valid_json = json.dumps(
            {
                "model": "m",
                "choices": [{"index": 0, "delta": {"content": "valid"}, "finish_reason": "stop"}],
            }
        )
        lines = ["data: {bad json!!!", f"data: {valid_json}", "data: [DONE]"]
        self._setup_stream(provider, lines)

        chunks = list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
            )
        )

        assert len(chunks) == 1
        assert chunks[0].content == "valid"

    def test_stream_chat_with_tools_in_payload(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        lines = _make_stream_lines(["ok"])
        self._setup_stream(provider, lines)

        tools = [{"type": "function", "function": {"name": "mytool"}}]
        list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
                tools=tools,
            )
        )

        call_kwargs = provider._client.stream.call_args.kwargs
        assert call_kwargs["json"]["tools"] == tools

    def test_stream_chat_without_tools_no_key(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        lines = _make_stream_lines(["ok"])
        self._setup_stream(provider, lines)

        list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
            )
        )

        payload = provider._client.stream.call_args.kwargs["json"]
        assert "tools" not in payload

    def test_stream_chat_stream_is_true(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        lines = _make_stream_lines(["ok"])
        self._setup_stream(provider, lines)

        list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
            )
        )

        payload = provider._client.stream.call_args.kwargs["json"]
        assert payload["stream"] is True

    def test_stream_chat_with_tool_call_deltas(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        args_fragment = '{"loc'
        tool_delta = {
            "id": "call_123",
            "function": {"name": "get_weather", "arguments": args_fragment},
        }
        lines = _make_stream_lines([""], tool_call_deltas=[tool_delta])
        self._setup_stream(provider, lines)

        chunks = list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
            )
        )

        assert len(chunks) == 1
        assert len(chunks[0].tool_calls) == 1
        assert chunks[0].tool_calls[0]["id"] == "call_123"
        assert chunks[0].tool_calls[0]["name"] == "get_weather"

    def test_stream_chat_duration_ms_only_on_done(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        lines = _make_stream_lines(["first", "last"])
        self._setup_stream(provider, lines)

        chunks = list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
            )
        )

        assert chunks[0].duration_ms is None
        assert chunks[1].duration_ms is not None
        assert chunks[1].duration_ms >= 0

    def test_stream_chat_usage_on_final_chunk(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        lines = _make_stream_lines(["text"], include_usage=True)
        self._setup_stream(provider, lines)

        chunks = list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
            )
        )

        assert chunks[0].prompt_tokens == 10
        assert chunks[0].completion_tokens == 5

    def test_stream_chat_done_stops_at_done_marker(self) -> None:
        """[DONE] marker should stop iteration, ignoring later lines."""
        provider = OpenAIProvider(host="http://localhost")
        extra_chunk = json.dumps(
            {
                "model": "m",
                "choices": [{"index": 0, "delta": {"content": "should not appear"}, "finish_reason": None}],
            }
        )
        lines = _make_stream_lines(["visible"]) + [f"data: {extra_chunk}"]
        self._setup_stream(provider, lines)

        chunks = list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
            )
        )

        contents = [c.content for c in chunks]
        assert "should not appear" not in contents

    def test_stream_chat_empty_choices(self) -> None:
        """Chunk with empty choices list still yields a response."""
        provider = OpenAIProvider(host="http://localhost")
        chunk_json = json.dumps(
            {
                "model": "m",
                "choices": [],
            }
        )
        done_chunk = json.dumps(
            {
                "model": "m",
                "choices": [{"index": 0, "delta": {"content": "end"}, "finish_reason": "stop"}],
            }
        )
        lines = [f"data: {chunk_json}", f"data: {done_chunk}", "data: [DONE]"]
        self._setup_stream(provider, lines)

        chunks = list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
            )
        )

        assert any(c.content == "" for c in chunks)

    def test_stream_chat_null_content_in_delta(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        chunk_json = json.dumps(
            {
                "model": "m",
                "choices": [{"index": 0, "delta": {"content": None}, "finish_reason": "stop"}],
            }
        )
        lines = [f"data: {chunk_json}", "data: [DONE]"]
        self._setup_stream(provider, lines)

        chunks = list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
            )
        )

        assert chunks[0].content == ""

    def test_stream_chat_url_is_correct(self) -> None:
        provider = OpenAIProvider(host="http://myhost:5000")
        lines = _make_stream_lines(["ok"])
        self._setup_stream(provider, lines)

        list(
            provider.stream_chat(
                messages=[{"role": "user", "content": "x"}],
                model="m",
                max_tokens=10,
                temperature=0.0,
                timeout=10.0,
            )
        )

        call_args = provider._client.stream.call_args
        assert call_args.args[1] == "http://myhost:5000/v1/chat/completions"


# =============================================================================
# is_available()
# =============================================================================


class TestIsAvailable:
    """Tests for the is_available() health check."""

    def test_available_on_200(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        provider._client.get = MagicMock(return_value=mock_resp)

        assert provider.is_available() is True

    def test_not_available_on_non_200(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        provider._client.get = MagicMock(return_value=mock_resp)

        assert provider.is_available() is False

    def test_not_available_on_request_error(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        provider._client.get = MagicMock(side_effect=httpx.ConnectError("refused"))

        assert provider.is_available() is False

    def test_not_available_on_timeout_error(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        provider._client.get = MagicMock(side_effect=httpx.ReadTimeout("timeout"))

        assert provider.is_available() is False

    def test_is_available_custom_timeout(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        provider._client.get = MagicMock(return_value=mock_resp)

        provider.is_available(timeout=10.0)

        call_kwargs = provider._client.get.call_args
        assert call_kwargs.kwargs["timeout"] == 10.0

    def test_is_available_url_is_models_endpoint(self) -> None:
        provider = OpenAIProvider(host="http://localhost:8080")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        provider._client.get = MagicMock(return_value=mock_resp)

        provider.is_available()

        call_args = provider._client.get.call_args
        assert call_args.args[0] == "http://localhost:8080/v1/models"


# =============================================================================
# list_models()
# =============================================================================


class TestListModels:
    """Tests for the list_models() method."""

    def test_list_models_success(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_models_response(["gpt-4o", "gpt-3.5-turbo"])
        mock_resp.raise_for_status.return_value = None
        provider._client.get = MagicMock(return_value=mock_resp)

        models = provider.list_models()

        assert models == ["gpt-4o", "gpt-3.5-turbo"]

    def test_list_models_empty(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"object": "list", "data": []}
        mock_resp.raise_for_status.return_value = None
        provider._client.get = MagicMock(return_value=mock_resp)

        models = provider.list_models()

        assert models == []

    def test_list_models_on_request_error(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        provider._client.get = MagicMock(side_effect=httpx.ConnectError("refused"))

        models = provider.list_models()

        assert models == []

    def test_list_models_on_timeout_error(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        provider._client.get = MagicMock(side_effect=httpx.ReadTimeout("timeout"))

        models = provider.list_models()

        assert models == []

    def test_list_models_missing_id_field(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "object": "list",
            "data": [{"object": "model"}, {"id": "gpt-4o", "object": "model"}],
        }
        mock_resp.raise_for_status.return_value = None
        provider._client.get = MagicMock(return_value=mock_resp)

        models = provider.list_models()

        assert models == ["", "gpt-4o"]

    def test_list_models_missing_data_key(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"object": "list"}
        mock_resp.raise_for_status.return_value = None
        provider._client.get = MagicMock(return_value=mock_resp)

        models = provider.list_models()

        assert models == []


# =============================================================================
# close()
# =============================================================================


class TestClose:
    """Tests for the close() method."""

    def test_close_calls_client_close(self) -> None:
        provider = OpenAIProvider(host="http://localhost")
        provider._client.close = MagicMock()

        provider.close()

        provider._client.close.assert_called_once()
