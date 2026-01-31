"""Fallback LLM provider with circuit breaker.

Wraps a primary and secondary provider. On retriable failures
from the primary, falls back to the secondary. Uses a circuit
breaker to avoid hammering an unhealthy primary.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from elle.rag.providers.base import LLMProvider, ProviderResponse

logger = logging.getLogger(__name__)

# HTTP status codes that should NOT trigger fallback (auth/client errors)
_NO_FALLBACK_CODES = frozenset({400, 401, 403})

# HTTP status codes that SHOULD trigger fallback (server errors)
_RETRIABLE_CODES = frozenset({429, 500, 502, 503, 504})


def _is_retriable(exc: Exception) -> bool:
    """Determine if an exception should trigger fallback."""
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRIABLE_CODES
    return False


class FallbackProvider(LLMProvider):
    """Provider that tries primary, falls back to secondary on failure.

    Circuit breaker logic:
    - On retriable failure: mark primary unhealthy, record failure time
    - For next retry_interval seconds: skip straight to fallback
    - After interval: try primary again (probe attempt)
    - On success: mark primary healthy
    """

    def __init__(
        self,
        primary: LLMProvider,
        secondary: LLMProvider,
        retry_interval: float = 60.0,
    ) -> None:
        self._primary = primary
        self._secondary = secondary
        self._retry_interval = retry_interval

        # Circuit breaker state
        self._primary_healthy = True
        self._last_failure_time: float = 0.0
        self._fallback_logged = False

    @property
    def provider_type(self) -> str:
        return "fallback"

    @property
    def primary(self) -> LLMProvider:
        return self._primary

    @property
    def secondary(self) -> LLMProvider:
        return self._secondary

    @property
    def is_primary_healthy(self) -> bool:
        return self._primary_healthy

    def _should_try_primary(self) -> bool:
        """Decide whether to attempt the primary provider."""
        if self._primary_healthy:
            return True

        # Check if enough time has passed to probe primary again
        elapsed = time.time() - self._last_failure_time
        if elapsed >= self._retry_interval:
            logger.debug("Retry interval elapsed, probing primary provider")
            return True

        return False

    def _mark_primary_failed(self, error: Exception) -> None:
        """Record a primary provider failure."""
        was_healthy = self._primary_healthy
        self._primary_healthy = False
        self._last_failure_time = time.time()

        if was_healthy:
            logger.warning(f"Primary LLM provider failed, falling back to secondary: {error}")
            self._fallback_logged = True
        else:
            logger.debug(f"Primary LLM provider still unhealthy: {error}")

    def _mark_primary_recovered(self) -> None:
        """Record a primary provider recovery."""
        if not self._primary_healthy:
            logger.info("Primary LLM provider recovered")
            self._primary_healthy = True
            self._fallback_logged = False

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
        if self._should_try_primary():
            try:
                result = self._primary.generate(
                    prompt,
                    system=system,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                    json_mode=json_mode,
                    keep_alive=keep_alive,
                    num_ctx=num_ctx,
                )
                self._mark_primary_recovered()
                return result
            except Exception as e:
                if not _is_retriable(e):
                    raise
                self._mark_primary_failed(e)

        # Fall back to secondary with its own model
        return self._secondary.generate(
            prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            json_mode=json_mode,
            keep_alive=keep_alive,
            num_ctx=num_ctx,
        )

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
        if self._should_try_primary():
            try:
                result = self._primary.chat(
                    messages,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                    json_mode=json_mode,
                    tools=tools,
                    keep_alive=keep_alive,
                    num_ctx=num_ctx,
                )
                self._mark_primary_recovered()
                return result
            except Exception as e:
                if not _is_retriable(e):
                    raise
                self._mark_primary_failed(e)

        return self._secondary.chat(
            messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            json_mode=json_mode,
            tools=tools,
            keep_alive=keep_alive,
            num_ctx=num_ctx,
        )

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
        if self._should_try_primary():
            try:
                # Attempt to get the first chunk to verify connectivity
                stream = self._primary.stream_chat(
                    messages,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                    tools=tools,
                    keep_alive=keep_alive,
                    num_ctx=num_ctx,
                )
                self._mark_primary_recovered()
                yield from stream
                return
            except Exception as e:
                if not _is_retriable(e):
                    raise
                self._mark_primary_failed(e)

        yield from self._secondary.stream_chat(
            messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            tools=tools,
            keep_alive=keep_alive,
            num_ctx=num_ctx,
        )

    def is_available(self, timeout: float = 5.0) -> bool:
        if self._primary.is_available(timeout):
            return True
        return self._secondary.is_available(timeout)

    def list_models(self) -> list[str]:
        if self._should_try_primary():
            try:
                models = self._primary.list_models()
                if models:
                    return models
            except Exception:
                pass
        return self._secondary.list_models()

    def close(self) -> None:
        self._primary.close()
        self._secondary.close()
