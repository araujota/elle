"""Tests for Ollama client configuration and LLM config loading."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from elle.rag.llm_config import (
    ElleLLMConfig,
    LLMFallbackConfig,
    LLMProviderConfig,
    LLMProviderType,
    _load_toml,
    _merge_dict,
    load_elle_llm_config,
)
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


# ============================================================================
# LLMProviderType enum
# ============================================================================


class TestLLMProviderType:
    """Tests for the LLMProviderType enum."""

    def test_ollama_value(self) -> None:
        assert LLMProviderType.OLLAMA.value == "ollama"

    def test_openai_value(self) -> None:
        assert LLMProviderType.OPENAI.value == "openai"

    def test_string_enum(self) -> None:
        assert str(LLMProviderType.OLLAMA) == "LLMProviderType.OLLAMA"


# ============================================================================
# LLMProviderConfig
# ============================================================================


class TestLLMProviderConfig:
    """Tests for LLMProviderConfig model."""

    def test_defaults(self) -> None:
        cfg = LLMProviderConfig()
        assert cfg.type == LLMProviderType.OLLAMA
        assert cfg.host == "http://localhost:11434"
        assert cfg.model == "qwen2.5:7b-instruct-q8_0"
        assert cfg.api_key == ""
        assert cfg.timeout == 120.0
        assert cfg.max_tokens == 4096
        assert cfg.temperature == 0.7

    def test_custom_values(self) -> None:
        cfg = LLMProviderConfig(
            type=LLMProviderType.OPENAI,
            host="http://remote:8080",
            model="gpt-4",
            api_key="sk-test",
            timeout=60.0,
            max_tokens=2048,
            temperature=0.3,
        )
        assert cfg.type == LLMProviderType.OPENAI
        assert cfg.host == "http://remote:8080"
        assert cfg.model == "gpt-4"
        assert cfg.api_key == "sk-test"

    def test_frozen(self) -> None:
        cfg = LLMProviderConfig()
        with pytest.raises(Exception):
            cfg.host = "other"

    def test_api_key_hidden_in_repr(self) -> None:
        cfg = LLMProviderConfig(api_key="secret-key")
        assert "secret-key" not in repr(cfg)


# ============================================================================
# LLMFallbackConfig
# ============================================================================


class TestLLMFallbackConfig:
    """Tests for LLMFallbackConfig model."""

    def test_defaults(self) -> None:
        cfg = LLMFallbackConfig()
        assert cfg.enabled is True
        assert cfg.host == "http://localhost:11434"
        assert cfg.model == "qwen2.5:7b-instruct-q8_0"
        assert cfg.retry_interval == 60.0

    def test_custom_values(self) -> None:
        cfg = LLMFallbackConfig(
            enabled=False,
            host="http://backup:11434",
            model="llama3.2:3b",
            retry_interval=120.0,
        )
        assert cfg.enabled is False
        assert cfg.retry_interval == 120.0

    def test_frozen(self) -> None:
        cfg = LLMFallbackConfig()
        with pytest.raises(Exception):
            cfg.enabled = False


# ============================================================================
# ElleLLMConfig
# ============================================================================


class TestElleLLMConfig:
    """Tests for ElleLLMConfig model."""

    def test_defaults(self) -> None:
        cfg = ElleLLMConfig()
        assert isinstance(cfg.provider, LLMProviderConfig)
        assert isinstance(cfg.fallback, LLMFallbackConfig)

    def test_custom_sub_configs(self) -> None:
        cfg = ElleLLMConfig(
            provider=LLMProviderConfig(host="http://custom:1234"),
            fallback=LLMFallbackConfig(enabled=False),
        )
        assert cfg.provider.host == "http://custom:1234"
        assert cfg.fallback.enabled is False


# ============================================================================
# _load_toml()
# ============================================================================


class TestLoadToml:
    """Tests for the _load_toml helper."""

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        result = _load_toml(tmp_path / "nope.toml")
        assert result == {}

    def test_valid_toml(self, tmp_path: Path) -> None:
        f = tmp_path / "test.toml"
        f.write_text('[llm]\nmodel = "test"\n')
        result = _load_toml(f)
        assert result["llm"]["model"] == "test"

    def test_invalid_toml(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.toml"
        f.write_text("[[[[invalid")
        result = _load_toml(f)
        assert result == {}


# ============================================================================
# _merge_dict()
# ============================================================================


class TestMergeDict:
    """Tests for the _merge_dict helper."""

    def test_simple_merge(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _merge_dict(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self) -> None:
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 99, "z": 100}}
        result = _merge_dict(base, override)
        assert result == {"a": {"x": 1, "y": 99, "z": 100}, "b": 3}

    def test_base_not_mutated(self) -> None:
        base = {"a": {"x": 1}}
        override = {"a": {"x": 2}}
        _merge_dict(base, override)
        assert base["a"]["x"] == 1

    def test_empty_base(self) -> None:
        result = _merge_dict({}, {"a": 1})
        assert result == {"a": 1}

    def test_empty_override(self) -> None:
        result = _merge_dict({"a": 1}, {})
        assert result == {"a": 1}

    def test_override_replaces_non_dict(self) -> None:
        result = _merge_dict({"a": "string"}, {"a": {"nested": True}})
        assert result == {"a": {"nested": True}}


# ============================================================================
# load_elle_llm_config()
# ============================================================================


class TestLoadElleLLMConfig:
    """Tests for the full config loader."""

    def test_defaults_when_no_files(self) -> None:
        """No files exist, no env vars -- all defaults."""
        with patch("elle.rag.llm_config._load_toml", return_value={}):
            cfg = load_elle_llm_config()
        assert cfg.provider.type == LLMProviderType.OLLAMA
        assert cfg.provider.host == "http://localhost:11434"
        assert cfg.fallback.enabled is True

    def test_system_config_loaded(self, tmp_path: Path) -> None:
        """System config supplies provider settings."""
        toml_data = {
            "llm": {
                "provider": {"host": "http://sys:1234", "model": "sys-model"},
            }
        }
        with patch("elle.rag.llm_config._load_toml", side_effect=[toml_data, {}, {}]):
            cfg = load_elle_llm_config()
        assert cfg.provider.host == "http://sys:1234"
        assert cfg.provider.model == "sys-model"

    def test_user_config_overrides_system(self) -> None:
        """User config overrides system config."""
        sys_data = {"llm": {"provider": {"host": "http://sys:1234"}}}
        user_data = {"llm": {"provider": {"host": "http://user:5678"}}}
        with patch("elle.rag.llm_config._load_toml", side_effect=[sys_data, user_data, {}]):
            cfg = load_elle_llm_config()
        assert cfg.provider.host == "http://user:5678"

    def test_explicit_config_path(self, tmp_path: Path) -> None:
        """Explicit config path overrides everything."""
        explicit_data = {
            "llm": {
                "provider": {"type": "openai", "api_key": "sk-test"},
                "fallback": {"enabled": False},
            }
        }
        with patch("elle.rag.llm_config._load_toml", side_effect=[{}, {}, explicit_data]):
            cfg = load_elle_llm_config(config_path=tmp_path / "custom.toml")
        assert cfg.provider.type == LLMProviderType.OPENAI
        assert cfg.provider.api_key == "sk-test"
        assert cfg.fallback.enabled is False

    def test_env_provider_type(self) -> None:
        """ELLE_LLM_PROVIDER_TYPE env var overrides config."""
        with (
            patch("elle.rag.llm_config._load_toml", return_value={}),
            patch.dict(os.environ, {"ELLE_LLM_PROVIDER_TYPE": "openai"}, clear=False),
        ):
            cfg = load_elle_llm_config()
        assert cfg.provider.type == LLMProviderType.OPENAI

    def test_env_provider_host(self) -> None:
        """ELLE_LLM_PROVIDER_HOST env var overrides config."""
        with (
            patch("elle.rag.llm_config._load_toml", return_value={}),
            patch.dict(os.environ, {"ELLE_LLM_PROVIDER_HOST": "http://env:9999"}, clear=False),
        ):
            cfg = load_elle_llm_config()
        assert cfg.provider.host == "http://env:9999"

    def test_env_provider_model(self) -> None:
        """ELLE_LLM_PROVIDER_MODEL env var overrides config."""
        with (
            patch("elle.rag.llm_config._load_toml", return_value={}),
            patch.dict(os.environ, {"ELLE_LLM_PROVIDER_MODEL": "custom-model"}, clear=False),
        ):
            cfg = load_elle_llm_config()
        assert cfg.provider.model == "custom-model"

    def test_env_api_key(self) -> None:
        """ELLE_LLM_API_KEY env var overrides config."""
        with (
            patch("elle.rag.llm_config._load_toml", return_value={}),
            patch.dict(os.environ, {"ELLE_LLM_API_KEY": "sk-env"}, clear=False),
        ):
            cfg = load_elle_llm_config()
        assert cfg.provider.api_key == "sk-env"

    def test_env_fallback_enabled_true(self) -> None:
        """ELLE_LLM_FALLBACK_ENABLED=true enables fallback."""
        with (
            patch("elle.rag.llm_config._load_toml", return_value={}),
            patch.dict(os.environ, {"ELLE_LLM_FALLBACK_ENABLED": "true"}, clear=False),
        ):
            cfg = load_elle_llm_config()
        assert cfg.fallback.enabled is True

    def test_env_fallback_enabled_false(self) -> None:
        """ELLE_LLM_FALLBACK_ENABLED=false disables fallback."""
        with (
            patch("elle.rag.llm_config._load_toml", return_value={}),
            patch.dict(os.environ, {"ELLE_LLM_FALLBACK_ENABLED": "false"}, clear=False),
        ):
            cfg = load_elle_llm_config()
        assert cfg.fallback.enabled is False

    def test_env_fallback_enabled_1(self) -> None:
        """ELLE_LLM_FALLBACK_ENABLED=1 enables fallback."""
        with (
            patch("elle.rag.llm_config._load_toml", return_value={}),
            patch.dict(os.environ, {"ELLE_LLM_FALLBACK_ENABLED": "1"}, clear=False),
        ):
            cfg = load_elle_llm_config()
        assert cfg.fallback.enabled is True

    def test_env_fallback_enabled_yes(self) -> None:
        """ELLE_LLM_FALLBACK_ENABLED=yes enables fallback."""
        with (
            patch("elle.rag.llm_config._load_toml", return_value={}),
            patch.dict(os.environ, {"ELLE_LLM_FALLBACK_ENABLED": "yes"}, clear=False),
        ):
            cfg = load_elle_llm_config()
        assert cfg.fallback.enabled is True

    def test_all_provider_fields_from_toml(self) -> None:
        """All provider fields read from TOML."""
        data = {
            "llm": {
                "provider": {
                    "type": "openai",
                    "host": "http://custom:8080",
                    "model": "gpt-4",
                    "api_key": "key123",
                    "timeout": 90.0,
                    "max_tokens": 8192,
                    "temperature": 0.5,
                },
            }
        }
        with patch("elle.rag.llm_config._load_toml", side_effect=[data, {}, {}]):
            cfg = load_elle_llm_config()
        assert cfg.provider.type == LLMProviderType.OPENAI
        assert cfg.provider.host == "http://custom:8080"
        assert cfg.provider.model == "gpt-4"
        assert cfg.provider.api_key == "key123"
        assert cfg.provider.timeout == 90.0
        assert cfg.provider.max_tokens == 8192
        assert cfg.provider.temperature == 0.5

    def test_all_fallback_fields_from_toml(self) -> None:
        """All fallback fields read from TOML."""
        data = {
            "llm": {
                "fallback": {
                    "enabled": False,
                    "host": "http://fallback:11434",
                    "model": "llama3.2:3b",
                    "retry_interval": 120.0,
                },
            }
        }
        with patch("elle.rag.llm_config._load_toml", side_effect=[data, {}, {}]):
            cfg = load_elle_llm_config()
        assert cfg.fallback.enabled is False
        assert cfg.fallback.host == "http://fallback:11434"
        assert cfg.fallback.model == "llama3.2:3b"
        assert cfg.fallback.retry_interval == 120.0

    def test_no_llm_section(self) -> None:
        """TOML file with no [llm] section defaults everything."""
        data = {"other": {"key": "val"}}
        with patch("elle.rag.llm_config._load_toml", side_effect=[data, {}, {}]):
            cfg = load_elle_llm_config()
        assert cfg.provider.type == LLMProviderType.OLLAMA
        assert cfg.fallback.enabled is True
