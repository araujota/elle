"""Abstract base class for LLM providers.

Defines the interface that all LLM providers must implement,
enabling pluggable backends (Ollama, OpenAI-compatible, etc.).
"""

from __future__ import annotations

import abc
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderResponse:
    """Unified response from any LLM provider."""

    content: str
    model: str
    done: bool = True
    duration_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is not None and self.completion_tokens is not None:
            return self.prompt_tokens + self.completion_tokens
        return None


class LLMProvider(abc.ABC):
    """Abstract LLM provider interface.

    All LLM backends (Ollama, OpenAI-compatible, etc.) implement
    this interface so the LLM class can delegate to any provider.
    """

    @abc.abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str,
        max_tokens: int,
        temperature: float,
        timeout: float,
        json_mode: bool = False,
        keep_alive: str = "-1",
        num_ctx: int = 32768,
    ) -> ProviderResponse:
        """Generate a text completion.

        Args:
            prompt: The prompt to complete.
            system: Optional system prompt.
            model: Model name.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            timeout: Request timeout in seconds.
            json_mode: Whether to request JSON output.
            keep_alive: Keep-alive duration (Ollama-specific, ignored by others).
            num_ctx: Context window size (Ollama-specific, ignored by others).

        Returns:
            ProviderResponse with generated content.
        """

    @abc.abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        timeout: float,
        json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
        keep_alive: str = "-1",
        num_ctx: int = 32768,
    ) -> ProviderResponse:
        """Chat-style generation with message history.

        Args:
            messages: List of message dicts with role/content.
            model: Model name.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            timeout: Request timeout in seconds.
            json_mode: Whether to request JSON output.
            tools: Optional tool definitions.
            keep_alive: Keep-alive duration (Ollama-specific).
            num_ctx: Context window size (Ollama-specific).

        Returns:
            ProviderResponse with the assistant's reply.
        """

    @abc.abstractmethod
    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        timeout: float,
        tools: list[dict[str, Any]] | None = None,
        keep_alive: str = "-1",
        num_ctx: int = 32768,
    ) -> Iterator[ProviderResponse]:
        """Streaming chat generation.

        Yields partial responses as they arrive.

        Args:
            messages: List of message dicts.
            model: Model name.
            max_tokens: Maximum tokens.
            temperature: Sampling temperature.
            timeout: Request timeout.
            tools: Optional tool definitions.
            keep_alive: Keep-alive duration (Ollama-specific).
            num_ctx: Context window size (Ollama-specific).

        Yields:
            ProviderResponse chunks.
        """

    @abc.abstractmethod
    def is_available(self, timeout: float = 5.0) -> bool:
        """Check if the provider is reachable.

        Args:
            timeout: Check timeout in seconds.

        Returns:
            True if the provider is available.
        """

    @abc.abstractmethod
    def list_models(self) -> list[str]:
        """List available models.

        Returns:
            List of model name strings.
        """

    @abc.abstractmethod
    def close(self) -> None:
        """Release provider resources."""

    @property
    @abc.abstractmethod
    def provider_type(self) -> str:
        """Return the provider type identifier (e.g. 'ollama', 'openai')."""
