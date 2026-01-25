"""Tests for Ollama client configuration with keep_alive and num_ctx."""

import pytest

from elle.rag.ollama_client import (
    DEFAULT_HOST,
    DEFAULT_KEEP_ALIVE,
    DEFAULT_MAX_TOKENS,
    DEFAULT_NUM_CTX,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT,
    OllamaConfig,
)


class TestOllamaConfig:
    """Tests for OllamaConfig model."""

    def test_default_values(self) -> None:
        """Test that OllamaConfig has expected defaults."""
        config = OllamaConfig()

        assert config.host == DEFAULT_HOST
        assert config.timeout == DEFAULT_TIMEOUT
        assert config.max_tokens == DEFAULT_MAX_TOKENS
        assert config.temperature == DEFAULT_TEMPERATURE
        assert config.keep_alive == DEFAULT_KEEP_ALIVE
        assert config.num_ctx == DEFAULT_NUM_CTX

    def test_custom_keep_alive(self) -> None:
        """Test custom keep_alive values."""
        # Indefinite keep-alive
        config = OllamaConfig(keep_alive="-1")
        assert config.keep_alive == "-1"

        # Time-based keep-alive
        config = OllamaConfig(keep_alive="30m")
        assert config.keep_alive == "30m"

        config = OllamaConfig(keep_alive="1h")
        assert config.keep_alive == "1h"

    def test_custom_num_ctx(self) -> None:
        """Test custom num_ctx values."""
        config = OllamaConfig(num_ctx=2048)
        assert config.num_ctx == 2048

        config = OllamaConfig(num_ctx=8192)
        assert config.num_ctx == 8192

        config = OllamaConfig(num_ctx=16384)
        assert config.num_ctx == 16384

    def test_frozen_config(self) -> None:
        """Test that OllamaConfig is immutable."""
        config = OllamaConfig()

        with pytest.raises(Exception):  # Pydantic raises ValidationError
            config.keep_alive = "10m"

        with pytest.raises(Exception):
            config.num_ctx = 8192

    def test_full_custom_config(self) -> None:
        """Test fully customized config."""
        config = OllamaConfig(
            host="http://localhost:11435",
            timeout=60.0,
            max_tokens=500,
            temperature=0.5,
            keep_alive="1h",
            num_ctx=4096,
        )

        assert config.host == "http://localhost:11435"
        assert config.timeout == 60.0
        assert config.max_tokens == 500
        assert config.temperature == 0.5
        assert config.keep_alive == "1h"
        assert config.num_ctx == 4096


class TestOllamaConfigConstants:
    """Tests for default constants."""

    def test_default_keep_alive_value(self) -> None:
        """Test that default keep_alive is reasonable."""
        assert DEFAULT_KEEP_ALIVE == "5m"

    def test_default_num_ctx_value(self) -> None:
        """Test that default num_ctx is reasonable."""
        assert DEFAULT_NUM_CTX == 4096
