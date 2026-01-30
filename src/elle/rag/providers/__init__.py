"""LLM provider abstraction layer.

Provides a pluggable backend system for LLM inference:
- OllamaProvider: Local inference via Ollama API
- OpenAIProvider: Remote inference via OpenAI-compatible API
- FallbackProvider: Tries primary, falls back to secondary on failure
"""

from __future__ import annotations

from elle.rag.providers.base import LLMProvider, ProviderResponse
from elle.rag.providers.fallback_provider import FallbackProvider
from elle.rag.providers.ollama_provider import OllamaProvider
from elle.rag.providers.openai_provider import OpenAIProvider

__all__ = [
    "FallbackProvider",
    "LLMProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "ProviderResponse",
]
