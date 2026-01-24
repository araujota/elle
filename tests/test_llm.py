"""Tests for the general-purpose LLM interface."""

import json
from unittest.mock import MagicMock, patch

import pytest

from elle.rag.llm import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT,
    FALLBACK_MODELS,
    LLM,
    LLMConfig,
    LLMError,
    LLMJSONError,
    LLMResponse,
    LLMTimeoutError,
    LLMUnavailableError,
    Message,
    get_llm,
    reset_llm,
)


class TestLLMConfig:
    """Tests for LLMConfig."""

    def test_default_values(self):
        """Test that config has sensible defaults."""
        config = LLMConfig()

        assert config.host == "http://localhost:11434"
        assert config.model == DEFAULT_MODEL
        assert config.timeout == DEFAULT_TIMEOUT
        assert config.max_tokens == DEFAULT_MAX_TOKENS
        assert config.temperature == DEFAULT_TEMPERATURE

    def test_custom_values(self):
        """Test custom configuration."""
        config = LLMConfig(
            host="http://custom:8080",
            model="custom-model",
            timeout=60.0,
            max_tokens=1024,
            temperature=0.5,
        )

        assert config.host == "http://custom:8080"
        assert config.model == "custom-model"
        assert config.timeout == 60.0
        assert config.max_tokens == 1024
        assert config.temperature == 0.5

    def test_config_is_frozen(self):
        """Test that config is immutable."""
        config = LLMConfig()
        with pytest.raises(Exception):
            config.temperature = 0.9


class TestLLMResponse:
    """Tests for LLMResponse."""

    def test_basic_response(self):
        """Test creating a basic response."""
        response = LLMResponse(
            content="Hello, world!",
            model="qwen2.5:7b-instruct",
        )

        assert response.content == "Hello, world!"
        assert response.model == "qwen2.5:7b-instruct"
        assert response.done is True

    def test_total_tokens(self):
        """Test total token calculation."""
        response = LLMResponse(
            content="Test",
            model="test",
            prompt_tokens=100,
            completion_tokens=50,
        )

        assert response.total_tokens == 150

    def test_total_tokens_none(self):
        """Test total_tokens when counts are None."""
        response = LLMResponse(content="Test", model="test")
        assert response.total_tokens is None


class TestMessage:
    """Tests for Message model."""

    def test_user_message(self):
        """Test creating a user message."""
        msg = Message(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_system_message(self):
        """Test creating a system message."""
        msg = Message(role="system", content="You are helpful")
        assert msg.role == "system"

    def test_assistant_message(self):
        """Test creating an assistant message."""
        msg = Message(role="assistant", content="Hi there!")
        assert msg.role == "assistant"


class TestDefaultSettings:
    """Tests for default settings."""

    def test_default_model(self):
        """Test default model is Qwen2.5-7B-Instruct."""
        assert DEFAULT_MODEL == "qwen2.5:7b-instruct"

    def test_default_timeout(self):
        """Test default timeout is 2 minutes."""
        assert DEFAULT_TIMEOUT == 120.0

    def test_default_max_tokens(self):
        """Test default max tokens is reasonable for general use."""
        assert DEFAULT_MAX_TOKENS == 2048

    def test_default_temperature(self):
        """Test default temperature is balanced."""
        assert DEFAULT_TEMPERATURE == 0.7

    def test_fallback_models_exist(self):
        """Test that fallback models are defined."""
        assert len(FALLBACK_MODELS) > 0
        assert all(isinstance(m, str) for m in FALLBACK_MODELS)


class TestExceptions:
    """Tests for LLM exceptions."""

    def test_llm_error(self):
        """Test base LLMError."""
        error = LLMError("Something went wrong", status_code=500)
        assert error.message == "Something went wrong"
        assert error.status_code == 500
        assert str(error) == "Something went wrong"

    def test_llm_unavailable_error(self):
        """Test LLMUnavailableError."""
        error = LLMUnavailableError("Ollama not running")
        assert isinstance(error, LLMError)

    def test_llm_timeout_error(self):
        """Test LLMTimeoutError."""
        error = LLMTimeoutError("Request timed out")
        assert isinstance(error, LLMError)

    def test_llm_json_error(self):
        """Test LLMJSONError with raw content."""
        error = LLMJSONError("Invalid JSON", raw_content="not json")
        assert error.raw_content == "not json"
        assert isinstance(error, LLMError)


class TestLLMJSONParsing:
    """Tests for JSON parsing logic."""

    def test_parse_clean_json(self):
        """Test parsing clean JSON."""
        llm = LLM()
        result = llm._parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_with_markdown_block(self):
        """Test parsing JSON wrapped in markdown code block."""
        llm = LLM()
        result = llm._parse_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_parse_json_with_plain_block(self):
        """Test parsing JSON wrapped in plain code block."""
        llm = LLM()
        result = llm._parse_json('```\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_parse_json_with_whitespace(self):
        """Test parsing JSON with surrounding whitespace."""
        llm = LLM()
        result = llm._parse_json('  \n{"key": "value"}\n  ')
        assert result == {"key": "value"}

    def test_parse_invalid_json_raises(self):
        """Test that invalid JSON raises JSONDecodeError."""
        llm = LLM()
        with pytest.raises(json.JSONDecodeError):
            llm._parse_json("not valid json")


class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_llm_returns_same_instance(self):
        """Test that get_llm returns the same instance."""
        reset_llm()  # Start fresh

        llm1 = get_llm()
        llm2 = get_llm()
        assert llm1 is llm2

        reset_llm()  # Cleanup

    def test_reset_llm_creates_new_instance(self):
        """Test that reset_llm creates a new instance."""
        reset_llm()

        llm1 = get_llm()
        reset_llm()
        llm2 = get_llm()

        assert llm1 is not llm2

        reset_llm()  # Cleanup


class TestLLMContextManager:
    """Tests for context manager support."""

    def test_context_manager(self):
        """Test LLM can be used as context manager."""
        with LLM() as llm:
            assert llm is not None
        # Should not raise after exit


class TestLLMConfigIntegration:
    """Tests for LLM configuration integration."""

    def test_llm_uses_config(self):
        """Test that LLM uses provided config."""
        config = LLMConfig(
            model="custom-model",
            max_tokens=512,
            temperature=0.3,
        )
        llm = LLM(config)

        assert llm.config.model == "custom-model"
        assert llm.config.max_tokens == 512
        assert llm.config.temperature == 0.3

    def test_llm_default_config(self):
        """Test that LLM creates default config if none provided."""
        llm = LLM()

        assert llm.config is not None
        assert llm.config.model == DEFAULT_MODEL
