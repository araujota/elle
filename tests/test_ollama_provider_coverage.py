"""Coverage tests for elle.rag.providers.ollama_provider.

Targets uncovered paths:
- generate() with system prompt and json_mode
- generate() error paths (ConnectError, TimeoutException, HTTPStatusError)
- chat() with tools, json_mode
- chat() tool call parsing (including JSONDecodeError fallback)
- chat() error paths
- stream_chat() with tools, tool calls in final chunk
- stream_chat() JSONDecodeError on chunk, empty lines
- is_available() success and failure
- list_models() success and failure
- close()
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from elle.rag.providers.ollama_provider import OllamaProvider

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def provider():
    """Create an OllamaProvider with a mock client."""
    p = OllamaProvider(host="http://localhost:11434", timeout=30.0)
    return p


# =============================================================================
# generate() tests
# =============================================================================


class TestGenerate:
    """Tests for OllamaProvider.generate()."""

    def test_generate_basic(self, provider):
        """Basic generate should return ProviderResponse."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": "Hello world",
            "model": "qwen2.5:7b",
            "done": True,
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(provider._client, "post", return_value=mock_response):
            result = provider.generate(
                "Hello",
                model="qwen2.5:7b",
                max_tokens=100,
                temperature=0.7,
                timeout=30.0,
            )

        assert result.content == "Hello world"
        assert result.model == "qwen2.5:7b"
        assert result.done is True
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 5

    def test_generate_with_system_and_json_mode(self, provider):
        """Generate with system prompt and json_mode should include both in payload."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": '{"key": "val"}',
            "model": "test",
            "done": True,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(provider._client, "post", return_value=mock_response) as mock_post:
            result = provider.generate(
                "Make JSON",
                system="You are a JSON generator",
                model="test",
                max_tokens=50,
                temperature=0.0,
                timeout=10.0,
                json_mode=True,
            )

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs["json"] if "json" in call_kwargs.kwargs else call_kwargs[1]["json"]
        assert payload["system"] == "You are a JSON generator"
        assert payload["format"] == "json"
        assert result.content == '{"key": "val"}'

    def test_generate_connect_error(self, provider):
        """ConnectError should propagate."""
        with patch.object(provider._client, "post", side_effect=httpx.ConnectError("Connection refused")):
            with pytest.raises(httpx.ConnectError):
                provider.generate("Test", model="m", max_tokens=10, temperature=0.5, timeout=5.0)

    def test_generate_timeout_error(self, provider):
        """TimeoutException should propagate."""
        with patch.object(provider._client, "post", side_effect=httpx.ReadTimeout("Timed out")):
            with pytest.raises(httpx.TimeoutException):
                provider.generate("Test", model="m", max_tokens=10, temperature=0.5, timeout=5.0)

    def test_generate_http_status_error(self, provider):
        """HTTPStatusError should propagate."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        error = httpx.HTTPStatusError("Server error", request=MagicMock(), response=mock_response)

        with patch.object(provider._client, "post", side_effect=error):
            with pytest.raises(httpx.HTTPStatusError):
                provider.generate("Test", model="m", max_tokens=10, temperature=0.5, timeout=5.0)


# =============================================================================
# chat() tests
# =============================================================================


class TestChat:
    """Tests for OllamaProvider.chat()."""

    def test_chat_basic(self, provider):
        """Basic chat should return ProviderResponse."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "Hi there", "role": "assistant"},
            "model": "qwen2.5:7b",
            "done": True,
            "prompt_eval_count": 20,
            "eval_count": 10,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(provider._client, "post", return_value=mock_response):
            result = provider.chat(
                [{"role": "user", "content": "Hello"}],
                model="qwen2.5:7b",
                max_tokens=100,
                temperature=0.7,
                timeout=30.0,
            )

        assert result.content == "Hi there"
        assert result.prompt_tokens == 20
        assert result.completion_tokens == 10

    def test_chat_with_tools(self, provider):
        """Chat with tools should include them in the payload."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "", "role": "assistant"},
            "model": "test",
            "done": True,
        }
        mock_response.raise_for_status = MagicMock()

        tools = [{"type": "function", "function": {"name": "test_tool"}}]

        with patch.object(provider._client, "post", return_value=mock_response) as mock_post:
            provider.chat(
                [{"role": "user", "content": "Use a tool"}],
                model="test",
                max_tokens=100,
                temperature=0.5,
                timeout=10.0,
                tools=tools,
            )

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs["json"] if "json" in call_kwargs.kwargs else call_kwargs[1]["json"]
        assert payload["tools"] == tools

    def test_chat_tool_call_parsing(self, provider):
        """Chat should parse tool calls from response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {
                "content": "",
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "get_info",
                            "arguments": '{"aspect": "resources"}',
                        },
                    }
                ],
            },
            "model": "test",
            "done": True,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(provider._client, "post", return_value=mock_response):
            result = provider.chat(
                [{"role": "user", "content": "Check resources"}],
                model="test",
                max_tokens=100,
                temperature=0.5,
                timeout=10.0,
            )

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "get_info"
        assert result.tool_calls[0]["arguments"] == {"aspect": "resources"}

    def test_chat_tool_call_dict_arguments(self, provider):
        """Chat should handle dict arguments directly (not JSON string)."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {
                "content": "",
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_2",
                        "function": {
                            "name": "shell",
                            "arguments": {"command": "ls"},  # Dict not string
                        },
                    }
                ],
            },
            "model": "test",
            "done": True,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(provider._client, "post", return_value=mock_response):
            result = provider.chat(
                [{"role": "user", "content": "List files"}],
                model="test",
                max_tokens=100,
                temperature=0.5,
                timeout=10.0,
            )

        assert result.tool_calls[0]["arguments"] == {"command": "ls"}

    def test_chat_tool_call_invalid_json(self, provider):
        """Chat should handle invalid JSON in tool call arguments."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {
                "content": "",
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_3",
                        "function": {
                            "name": "bad_tool",
                            "arguments": "{invalid json}}}",
                        },
                    }
                ],
            },
            "model": "test",
            "done": True,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(provider._client, "post", return_value=mock_response):
            result = provider.chat(
                [{"role": "user", "content": "Bad call"}],
                model="test",
                max_tokens=100,
                temperature=0.5,
                timeout=10.0,
            )

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["arguments"] == {}  # Fallback

    def test_chat_connect_error(self, provider):
        """ConnectError should propagate from chat."""
        with patch.object(provider._client, "post", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(httpx.ConnectError):
                provider.chat(
                    [{"role": "user", "content": "Hi"}],
                    model="m",
                    max_tokens=10,
                    temperature=0.5,
                    timeout=5.0,
                )

    def test_chat_timeout_error(self, provider):
        """TimeoutException should propagate from chat."""
        with patch.object(provider._client, "post", side_effect=httpx.ReadTimeout("timeout")):
            with pytest.raises(httpx.TimeoutException):
                provider.chat(
                    [{"role": "user", "content": "Hi"}],
                    model="m",
                    max_tokens=10,
                    temperature=0.5,
                    timeout=5.0,
                )


# =============================================================================
# stream_chat() tests
# =============================================================================


class TestStreamChat:
    """Tests for OllamaProvider.stream_chat()."""

    def test_stream_chat_basic(self, provider):
        """stream_chat should yield ProviderResponse chunks."""
        lines = [
            json.dumps({"message": {"content": "Hello"}, "model": "test", "done": False}),
            json.dumps(
                {
                    "message": {"content": " world"},
                    "model": "test",
                    "done": True,
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }
            ),
        ]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_lines.return_value = iter(lines)
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_response):
            chunks = list(
                provider.stream_chat(
                    [{"role": "user", "content": "Hi"}],
                    model="test",
                    max_tokens=100,
                    temperature=0.5,
                    timeout=30.0,
                )
            )

        assert len(chunks) == 2
        assert chunks[0].content == "Hello"
        assert chunks[0].done is False
        assert chunks[1].content == " world"
        assert chunks[1].done is True
        assert chunks[1].prompt_tokens == 10

    def test_stream_chat_empty_lines_skipped(self, provider):
        """stream_chat should skip empty lines."""
        lines = [
            "",
            json.dumps({"message": {"content": "OK"}, "model": "test", "done": True}),
            "",
        ]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_lines.return_value = iter(lines)
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_response):
            chunks = list(
                provider.stream_chat(
                    [{"role": "user", "content": "Hi"}],
                    model="test",
                    max_tokens=100,
                    temperature=0.5,
                    timeout=30.0,
                )
            )

        assert len(chunks) == 1

    def test_stream_chat_invalid_json_skipped(self, provider):
        """stream_chat should skip lines with invalid JSON."""
        lines = [
            "not json at all",
            json.dumps({"message": {"content": "Valid"}, "model": "test", "done": True}),
        ]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_lines.return_value = iter(lines)
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_response):
            chunks = list(
                provider.stream_chat(
                    [{"role": "user", "content": "Hi"}],
                    model="test",
                    max_tokens=100,
                    temperature=0.5,
                    timeout=30.0,
                )
            )

        assert len(chunks) == 1
        assert chunks[0].content == "Valid"

    def test_stream_chat_with_tool_calls(self, provider):
        """stream_chat should parse tool calls from final chunk."""
        lines = [
            json.dumps(
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "tc_1",
                                "function": {
                                    "name": "get_info",
                                    "arguments": '{"aspect": "resources"}',
                                },
                            }
                        ],
                    },
                    "model": "test",
                    "done": True,
                    "prompt_eval_count": 5,
                    "eval_count": 3,
                }
            ),
        ]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_lines.return_value = iter(lines)
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_response):
            chunks = list(
                provider.stream_chat(
                    [{"role": "user", "content": "Check info"}],
                    model="test",
                    max_tokens=100,
                    temperature=0.5,
                    timeout=30.0,
                )
            )

        assert len(chunks) == 1
        assert len(chunks[0].tool_calls) == 1
        assert chunks[0].tool_calls[0]["name"] == "get_info"

    def test_stream_chat_tool_call_invalid_json_args(self, provider):
        """stream_chat should handle invalid JSON in tool call args."""
        lines = [
            json.dumps(
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "tc_bad",
                                "function": {
                                    "name": "bad",
                                    "arguments": "{not valid json",
                                },
                            }
                        ],
                    },
                    "model": "test",
                    "done": True,
                }
            ),
        ]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_lines.return_value = iter(lines)
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_response):
            chunks = list(
                provider.stream_chat(
                    [{"role": "user", "content": "Bad tool"}],
                    model="test",
                    max_tokens=100,
                    temperature=0.5,
                    timeout=30.0,
                )
            )

        assert chunks[0].tool_calls[0]["arguments"] == {}

    def test_stream_chat_with_tools_in_payload(self, provider):
        """stream_chat with tools should include them in payload."""
        lines = [
            json.dumps({"message": {"content": "OK"}, "model": "test", "done": True}),
        ]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_lines.return_value = iter(lines)
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        tools = [{"type": "function", "function": {"name": "test"}}]

        with patch.object(provider._client, "stream", return_value=mock_response) as mock_stream:
            list(
                provider.stream_chat(
                    [{"role": "user", "content": "Use tool"}],
                    model="test",
                    max_tokens=100,
                    temperature=0.5,
                    timeout=30.0,
                    tools=tools,
                )
            )

        call_kwargs = mock_stream.call_args
        payload = call_kwargs.kwargs["json"] if "json" in call_kwargs.kwargs else call_kwargs[1]["json"]
        assert payload["tools"] == tools


# =============================================================================
# is_available() tests
# =============================================================================


class TestIsAvailable:
    """Tests for OllamaProvider.is_available()."""

    def test_available_returns_true(self, provider):
        """Should return True when Ollama responds with 200."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(provider._client, "get", return_value=mock_response):
            assert provider.is_available() is True

    def test_unavailable_returns_false(self, provider):
        """Should return False when Ollama responds with non-200."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch.object(provider._client, "get", return_value=mock_response):
            assert provider.is_available() is False

    def test_connection_error_returns_false(self, provider):
        """Should return False on connection error."""
        with patch.object(provider._client, "get", side_effect=httpx.ConnectError("refused")):
            assert provider.is_available() is False

    def test_timeout_returns_false(self, provider):
        """Should return False on timeout."""
        with patch.object(provider._client, "get", side_effect=httpx.ReadTimeout("timeout")):
            assert provider.is_available() is False


# =============================================================================
# list_models() tests
# =============================================================================


class TestListModels:
    """Tests for OllamaProvider.list_models()."""

    def test_list_models_success(self, provider):
        """Should return model names on success."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "models": [
                {"name": "qwen2.5:7b"},
                {"name": "llama3:8b"},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(provider._client, "get", return_value=mock_response):
            models = provider.list_models()

        assert models == ["qwen2.5:7b", "llama3:8b"]

    def test_list_models_empty(self, provider):
        """Should return empty list when no models."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"models": []}
        mock_response.raise_for_status = MagicMock()

        with patch.object(provider._client, "get", return_value=mock_response):
            models = provider.list_models()

        assert models == []

    def test_list_models_error(self, provider):
        """Should return empty list on error."""
        with patch.object(provider._client, "get", side_effect=httpx.ConnectError("refused")):
            models = provider.list_models()

        assert models == []


# =============================================================================
# close() and properties tests
# =============================================================================


class TestCloseAndProperties:
    """Tests for close() and provider properties."""

    def test_close_calls_client_close(self, provider):
        """close() should close the underlying httpx client."""
        with patch.object(provider._client, "close") as mock_close:
            provider.close()
            mock_close.assert_called_once()

    def test_provider_type(self, provider):
        """provider_type should return 'ollama'."""
        assert provider.provider_type == "ollama"

    def test_host_property(self, provider):
        """host should return the configured host."""
        assert provider.host == "http://localhost:11434"
