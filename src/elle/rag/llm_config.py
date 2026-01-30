"""LLM configuration loading from elle.toml.

Provides Pydantic models for LLM provider configuration and a
loader that reads the [llm] section from elle.toml with env var
overrides.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Try to import tomllib (Python 3.11+) or fall back to toml
try:
    import tomllib
except ImportError:
    tomllib = None  # type: ignore[assignment,unused-ignore]


class LLMProviderType(str, Enum):
    """Supported LLM provider types."""

    OLLAMA = "ollama"
    OPENAI = "openai"


class LLMProviderConfig(BaseModel):
    """Configuration for the primary LLM provider."""

    model_config = ConfigDict(frozen=True)

    type: LLMProviderType = LLMProviderType.OLLAMA
    host: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct-q8_0"
    api_key: str = Field(default="", repr=False)
    timeout: float = 120.0
    max_tokens: int = 4096
    temperature: float = 0.7


class LLMFallbackConfig(BaseModel):
    """Configuration for the fallback LLM provider."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    host: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct-q8_0"
    retry_interval: float = 60.0


class ElleLLMConfig(BaseModel):
    """Complete LLM configuration from elle.toml."""

    model_config = ConfigDict(frozen=True)

    provider: LLMProviderConfig = Field(default_factory=LLMProviderConfig)
    fallback: LLMFallbackConfig = Field(default_factory=LLMFallbackConfig)


# Default config paths
_SYSTEM_CONFIG = Path("/etc/elle/elle.toml")
_USER_CONFIG = Path.home() / ".config" / "elle" / "elle.toml"


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file if it exists."""
    if not path.exists():
        return {}
    try:
        if tomllib is not None:
            with open(path, "rb") as f:
                result: dict[str, Any] = tomllib.load(f)
        else:
            import toml

            result = toml.load(str(path))
        return result
    except Exception:
        return {}


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base dict, returning new dict."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def load_elle_llm_config(config_path: Path | None = None) -> ElleLLMConfig:
    """Load LLM configuration from elle.toml with env var overrides.

    Search order:
    1. /etc/elle/elle.toml (system-wide)
    2. ~/.config/elle/elle.toml (user override)
    3. Explicit config_path (if provided)
    4. Environment variables (ELLE_LLM_*)

    Returns:
        ElleLLMConfig with merged configuration.
    """
    # Merge TOML sources
    data: dict[str, Any] = {}

    system_data = _load_toml(_SYSTEM_CONFIG)
    if system_data:
        data = system_data

    user_data = _load_toml(_USER_CONFIG)
    if user_data:
        data = _merge_dict(data, user_data)

    if config_path:
        explicit_data = _load_toml(config_path)
        if explicit_data:
            data = _merge_dict(data, explicit_data)

    # Extract [llm] section
    llm_section = data.get("llm", {})

    # Build provider config
    provider_section = llm_section.get("provider", {})
    provider_kwargs: dict[str, Any] = {}

    if "type" in provider_section:
        provider_kwargs["type"] = provider_section["type"]
    if "host" in provider_section:
        provider_kwargs["host"] = provider_section["host"]
    if "model" in provider_section:
        provider_kwargs["model"] = provider_section["model"]
    if "api_key" in provider_section:
        provider_kwargs["api_key"] = provider_section["api_key"]
    if "timeout" in provider_section:
        provider_kwargs["timeout"] = provider_section["timeout"]
    if "max_tokens" in provider_section:
        provider_kwargs["max_tokens"] = provider_section["max_tokens"]
    if "temperature" in provider_section:
        provider_kwargs["temperature"] = provider_section["temperature"]

    # Build fallback config
    fallback_section = llm_section.get("fallback", {})
    fallback_kwargs: dict[str, Any] = {}

    if "enabled" in fallback_section:
        fallback_kwargs["enabled"] = fallback_section["enabled"]
    if "host" in fallback_section:
        fallback_kwargs["host"] = fallback_section["host"]
    if "model" in fallback_section:
        fallback_kwargs["model"] = fallback_section["model"]
    if "retry_interval" in fallback_section:
        fallback_kwargs["retry_interval"] = fallback_section["retry_interval"]

    # Apply environment variable overrides
    env_type = os.environ.get("ELLE_LLM_PROVIDER_TYPE")
    if env_type:
        provider_kwargs["type"] = env_type

    env_host = os.environ.get("ELLE_LLM_PROVIDER_HOST")
    if env_host:
        provider_kwargs["host"] = env_host

    env_model = os.environ.get("ELLE_LLM_PROVIDER_MODEL")
    if env_model:
        provider_kwargs["model"] = env_model

    env_api_key = os.environ.get("ELLE_LLM_API_KEY")
    if env_api_key:
        provider_kwargs["api_key"] = env_api_key

    env_fallback = os.environ.get("ELLE_LLM_FALLBACK_ENABLED")
    if env_fallback is not None:
        fallback_kwargs["enabled"] = env_fallback.lower() in ("true", "1", "yes")

    provider_config = LLMProviderConfig(**provider_kwargs)
    fallback_config = LLMFallbackConfig(**fallback_kwargs)

    return ElleLLMConfig(provider=provider_config, fallback=fallback_config)
