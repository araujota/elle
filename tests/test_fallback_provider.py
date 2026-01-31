"""Tests for FallbackProvider with circuit breaker logic."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from elle.rag.providers.base import LLMProvider, ProviderResponse
from elle.rag.providers.fallback_provider import (
    FallbackProvider,
    _is_retriable,
    _NO_FALLBACK_CODES,
    _RETRIABLE_CODES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(content: str = "ok") -> ProviderResponse:
    """Build a simple ProviderResponse."""
    return ProviderResponse(content=content, model="test-model", done=True)


class StubProvider(LLMProvider):
    """Minimal concrete provider for testing."""

    def __init__(
        self,
        name: str = "stub",
        *,
        available: bool = True,
        models: list[str] | None = None,
    ) -> None:
        self.name = name
        self._available = available
        self._models = models if models is not None else ["model-a"]
        self.generate_fn: Any = None
        self.chat_fn: Any = None
        self.stream_chat_fn: Any = None
        self.closed = False

    @property
    def provider_type(self) -> str:
        return self.name

    def generate(self, prompt, **kwargs) -> ProviderResponse:
        if self.generate_fn:
            return self.generate_fn(prompt, **kwargs)
        return _make_response(f"{self.name}:generate")

    def chat(self, messages, **kwargs) -> ProviderResponse:
        if self.chat_fn:
            return self.chat_fn(messages, **kwargs)
        return _make_response(f"{self.name}:chat")

    def stream_chat(self, messages, **kwargs) -> Iterator[ProviderResponse]:
        if self.stream_chat_fn:
            return self.stream_chat_fn(messages, **kwargs)
        yield _make_response(f"{self.name}:stream")

    def is_available(self, timeout: float = 5.0) -> bool:
        return self._available

    def list_models(self) -> list[str]:
        return self._models

    def close(self) -> None:
        self.closed = True


def _make_http_status_error(status_code: int) -> httpx.HTTPStatusError:
    """Build an httpx.HTTPStatusError with the given status code."""
    request = httpx.Request("POST", "http://test")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=request,
        response=response,
    )


# ============================================================================
# _is_retriable
# ============================================================================


class TestIsRetriable:
    """Tests for the _is_retriable helper."""

    def test_connect_error_is_retriable(self) -> None:
        exc = httpx.ConnectError("connection refused")
        assert _is_retriable(exc) is True

    def test_timeout_exception_is_retriable(self) -> None:
        exc = httpx.ReadTimeout("read timed out")
        assert _is_retriable(exc) is True

    def test_connect_timeout_is_retriable(self) -> None:
        exc = httpx.ConnectTimeout("connect timed out")
        assert _is_retriable(exc) is True

    @pytest.mark.parametrize("code", sorted(_RETRIABLE_CODES))
    def test_retriable_status_codes(self, code: int) -> None:
        exc = _make_http_status_error(code)
        assert _is_retriable(exc) is True

    @pytest.mark.parametrize("code", sorted(_NO_FALLBACK_CODES))
    def test_non_retriable_status_codes(self, code: int) -> None:
        exc = _make_http_status_error(code)
        assert _is_retriable(exc) is False

    def test_generic_exception_not_retriable(self) -> None:
        assert _is_retriable(RuntimeError("boom")) is False

    def test_value_error_not_retriable(self) -> None:
        assert _is_retriable(ValueError("bad")) is False


# ============================================================================
# FallbackProvider properties
# ============================================================================


class TestFallbackProviderProperties:
    """Tests for FallbackProvider attributes and properties."""

    def test_provider_type(self) -> None:
        fp = FallbackProvider(StubProvider("p"), StubProvider("s"))
        assert fp.provider_type == "fallback"

    def test_primary_property(self) -> None:
        primary = StubProvider("p")
        fp = FallbackProvider(primary, StubProvider("s"))
        assert fp.primary is primary

    def test_secondary_property(self) -> None:
        secondary = StubProvider("s")
        fp = FallbackProvider(StubProvider("p"), secondary)
        assert fp.secondary is secondary

    def test_is_primary_healthy_initially_true(self) -> None:
        fp = FallbackProvider(StubProvider(), StubProvider())
        assert fp.is_primary_healthy is True

    def test_custom_retry_interval(self) -> None:
        fp = FallbackProvider(StubProvider(), StubProvider(), retry_interval=120.0)
        assert fp._retry_interval == 120.0


# ============================================================================
# generate()
# ============================================================================


class TestFallbackProviderGenerate:
    """Tests for FallbackProvider.generate()."""

    _gen_kwargs = dict(
        model="m", max_tokens=100, temperature=0.5, timeout=30.0,
    )

    def test_primary_success(self) -> None:
        primary = StubProvider("p")
        secondary = StubProvider("s")
        fp = FallbackProvider(primary, secondary)

        result = fp.generate("hello", **self._gen_kwargs)
        assert result.content == "p:generate"
        assert fp.is_primary_healthy is True

    def test_fallback_on_connect_error(self) -> None:
        primary = StubProvider("p")
        primary.generate_fn = MagicMock(side_effect=httpx.ConnectError("refused"))
        secondary = StubProvider("s")
        fp = FallbackProvider(primary, secondary)

        result = fp.generate("hello", **self._gen_kwargs)
        assert result.content == "s:generate"
        assert fp.is_primary_healthy is False

    def test_fallback_on_timeout(self) -> None:
        primary = StubProvider("p")
        primary.generate_fn = MagicMock(side_effect=httpx.ReadTimeout("timeout"))
        fp = FallbackProvider(primary, StubProvider("s"))

        result = fp.generate("hello", **self._gen_kwargs)
        assert result.content == "s:generate"
        assert fp.is_primary_healthy is False

    def test_fallback_on_retriable_status(self) -> None:
        primary = StubProvider("p")
        primary.generate_fn = MagicMock(side_effect=_make_http_status_error(503))
        fp = FallbackProvider(primary, StubProvider("s"))

        result = fp.generate("hello", **self._gen_kwargs)
        assert result.content == "s:generate"

    def test_no_fallback_on_client_error(self) -> None:
        primary = StubProvider("p")
        primary.generate_fn = MagicMock(side_effect=_make_http_status_error(400))
        fp = FallbackProvider(primary, StubProvider("s"))

        with pytest.raises(httpx.HTTPStatusError):
            fp.generate("hello", **self._gen_kwargs)

    def test_no_fallback_on_non_retriable(self) -> None:
        primary = StubProvider("p")
        primary.generate_fn = MagicMock(side_effect=ValueError("bad"))
        fp = FallbackProvider(primary, StubProvider("s"))

        with pytest.raises(ValueError, match="bad"):
            fp.generate("hello", **self._gen_kwargs)

    def test_circuit_breaker_skips_primary(self) -> None:
        """After failure the primary is skipped within retry_interval."""
        primary = StubProvider("p")
        primary.generate_fn = MagicMock(side_effect=httpx.ConnectError("down"))
        secondary = StubProvider("s")
        fp = FallbackProvider(primary, secondary, retry_interval=60.0)

        # First call triggers fallback
        fp.generate("hello", **self._gen_kwargs)
        primary.generate_fn.reset_mock()

        # Second call should skip primary entirely
        result = fp.generate("hello", **self._gen_kwargs)
        assert result.content == "s:generate"
        primary.generate_fn.assert_not_called()

    def test_circuit_breaker_retries_after_interval(self) -> None:
        """After retry_interval the primary is probed again."""
        primary = StubProvider("p")
        primary.generate_fn = MagicMock(side_effect=httpx.ConnectError("down"))
        fp = FallbackProvider(primary, StubProvider("s"), retry_interval=0.0)

        # First call fails
        fp.generate("hello", **self._gen_kwargs)

        # With interval=0, next call retries primary
        primary.generate_fn = MagicMock(return_value=_make_response("recovered"))
        result = fp.generate("hello", **self._gen_kwargs)
        assert result.content == "recovered"
        assert fp.is_primary_healthy is True

    def test_recovery_resets_state(self) -> None:
        """Primary recovery resets healthy flag and fallback_logged."""
        primary = StubProvider("p")
        primary.generate_fn = MagicMock(side_effect=httpx.ConnectError("down"))
        fp = FallbackProvider(primary, StubProvider("s"), retry_interval=0.0)

        fp.generate("hello", **self._gen_kwargs)
        assert fp.is_primary_healthy is False
        assert fp._fallback_logged is True

        primary.generate_fn = MagicMock(return_value=_make_response("back"))
        fp.generate("hello", **self._gen_kwargs)
        assert fp.is_primary_healthy is True
        assert fp._fallback_logged is False


# ============================================================================
# chat()
# ============================================================================


class TestFallbackProviderChat:
    """Tests for FallbackProvider.chat()."""

    _chat_kwargs = dict(
        model="m", max_tokens=100, temperature=0.5, timeout=30.0,
    )
    _msgs: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]

    def test_primary_success(self) -> None:
        fp = FallbackProvider(StubProvider("p"), StubProvider("s"))
        result = fp.chat(self._msgs, **self._chat_kwargs)
        assert result.content == "p:chat"

    def test_fallback_on_retriable(self) -> None:
        primary = StubProvider("p")
        primary.chat_fn = MagicMock(side_effect=httpx.ConnectError("refused"))
        fp = FallbackProvider(primary, StubProvider("s"))

        result = fp.chat(self._msgs, **self._chat_kwargs)
        assert result.content == "s:chat"

    def test_no_fallback_on_non_retriable(self) -> None:
        primary = StubProvider("p")
        primary.chat_fn = MagicMock(side_effect=_make_http_status_error(401))
        fp = FallbackProvider(primary, StubProvider("s"))

        with pytest.raises(httpx.HTTPStatusError):
            fp.chat(self._msgs, **self._chat_kwargs)

    def test_circuit_breaker_skips_primary_chat(self) -> None:
        primary = StubProvider("p")
        primary.chat_fn = MagicMock(side_effect=httpx.ConnectError("down"))
        secondary = StubProvider("s")
        fp = FallbackProvider(primary, secondary, retry_interval=60.0)

        fp.chat(self._msgs, **self._chat_kwargs)
        primary.chat_fn.reset_mock()

        result = fp.chat(self._msgs, **self._chat_kwargs)
        assert result.content == "s:chat"
        primary.chat_fn.assert_not_called()


# ============================================================================
# stream_chat()
# ============================================================================


class TestFallbackProviderStreamChat:
    """Tests for FallbackProvider.stream_chat()."""

    _stream_kwargs = dict(
        model="m", max_tokens=100, temperature=0.5, timeout=30.0,
    )
    _msgs: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]

    def test_primary_stream_success(self) -> None:
        fp = FallbackProvider(StubProvider("p"), StubProvider("s"))
        chunks = list(fp.stream_chat(self._msgs, **self._stream_kwargs))
        assert len(chunks) >= 1
        assert chunks[0].content == "p:stream"

    def test_fallback_stream_on_retriable(self) -> None:
        primary = StubProvider("p")
        primary.stream_chat_fn = MagicMock(side_effect=httpx.ConnectError("down"))
        fp = FallbackProvider(primary, StubProvider("s"))

        chunks = list(fp.stream_chat(self._msgs, **self._stream_kwargs))
        assert chunks[0].content == "s:stream"

    def test_no_fallback_stream_on_non_retriable(self) -> None:
        primary = StubProvider("p")
        primary.stream_chat_fn = MagicMock(side_effect=_make_http_status_error(403))
        fp = FallbackProvider(primary, StubProvider("s"))

        with pytest.raises(httpx.HTTPStatusError):
            list(fp.stream_chat(self._msgs, **self._stream_kwargs))

    def test_circuit_breaker_skips_primary_stream(self) -> None:
        primary = StubProvider("p")
        primary.stream_chat_fn = MagicMock(side_effect=httpx.ConnectError("down"))
        secondary = StubProvider("s")
        fp = FallbackProvider(primary, secondary, retry_interval=60.0)

        list(fp.stream_chat(self._msgs, **self._stream_kwargs))
        primary.stream_chat_fn.reset_mock()

        chunks = list(fp.stream_chat(self._msgs, **self._stream_kwargs))
        assert chunks[0].content == "s:stream"
        primary.stream_chat_fn.assert_not_called()


# ============================================================================
# is_available()
# ============================================================================


class TestFallbackProviderIsAvailable:
    """Tests for FallbackProvider.is_available()."""

    def test_primary_available(self) -> None:
        fp = FallbackProvider(
            StubProvider(available=True),
            StubProvider(available=False),
        )
        assert fp.is_available() is True

    def test_secondary_available(self) -> None:
        fp = FallbackProvider(
            StubProvider(available=False),
            StubProvider(available=True),
        )
        assert fp.is_available() is True

    def test_neither_available(self) -> None:
        fp = FallbackProvider(
            StubProvider(available=False),
            StubProvider(available=False),
        )
        assert fp.is_available() is False


# ============================================================================
# list_models()
# ============================================================================


class TestFallbackProviderListModels:
    """Tests for FallbackProvider.list_models()."""

    def test_primary_returns_models(self) -> None:
        fp = FallbackProvider(
            StubProvider(models=["a", "b"]),
            StubProvider(models=["c"]),
        )
        assert fp.list_models() == ["a", "b"]

    def test_primary_empty_falls_to_secondary(self) -> None:
        fp = FallbackProvider(
            StubProvider(models=[]),
            StubProvider(models=["c"]),
        )
        assert fp.list_models() == ["c"]

    def test_primary_exception_falls_to_secondary(self) -> None:
        primary = StubProvider(models=[])
        primary.list_models = MagicMock(side_effect=RuntimeError("boom"))
        fp = FallbackProvider(primary, StubProvider(models=["d"]))
        assert fp.list_models() == ["d"]

    def test_circuit_breaker_skips_primary_list_models(self) -> None:
        primary = StubProvider("p")
        primary.generate_fn = MagicMock(side_effect=httpx.ConnectError("down"))
        secondary = StubProvider("s", models=["fallback-model"])
        fp = FallbackProvider(primary, secondary, retry_interval=60.0)

        # Trip the circuit breaker via generate
        fp.generate("x", model="m", max_tokens=10, temperature=0.5, timeout=5.0)

        # list_models should skip primary
        result = fp.list_models()
        assert result == ["fallback-model"]


# ============================================================================
# close()
# ============================================================================


class TestFallbackProviderClose:
    """Tests for FallbackProvider.close()."""

    def test_close_both_providers(self) -> None:
        primary = StubProvider("p")
        secondary = StubProvider("s")
        fp = FallbackProvider(primary, secondary)

        fp.close()
        assert primary.closed is True
        assert secondary.closed is True


# ============================================================================
# _mark_primary_failed / _mark_primary_recovered
# ============================================================================


class TestCircuitBreakerInternals:
    """Tests for internal circuit breaker state transitions."""

    def test_mark_primary_failed_from_healthy(self) -> None:
        fp = FallbackProvider(StubProvider(), StubProvider())
        assert fp._primary_healthy is True

        fp._mark_primary_failed(RuntimeError("fail"))
        assert fp._primary_healthy is False
        assert fp._fallback_logged is True
        assert fp._last_failure_time > 0

    def test_mark_primary_failed_from_unhealthy(self) -> None:
        fp = FallbackProvider(StubProvider(), StubProvider())
        fp._primary_healthy = False
        fp._fallback_logged = False

        fp._mark_primary_failed(RuntimeError("still bad"))
        assert fp._primary_healthy is False
        # fallback_logged remains unchanged when already unhealthy
        assert fp._fallback_logged is False

    def test_mark_primary_recovered_from_unhealthy(self) -> None:
        fp = FallbackProvider(StubProvider(), StubProvider())
        fp._primary_healthy = False
        fp._fallback_logged = True

        fp._mark_primary_recovered()
        assert fp._primary_healthy is True
        assert fp._fallback_logged is False

    def test_mark_primary_recovered_when_already_healthy(self) -> None:
        fp = FallbackProvider(StubProvider(), StubProvider())
        fp._primary_healthy = True

        # Should be a no-op
        fp._mark_primary_recovered()
        assert fp._primary_healthy is True

    def test_should_try_primary_when_healthy(self) -> None:
        fp = FallbackProvider(StubProvider(), StubProvider())
        assert fp._should_try_primary() is True

    def test_should_try_primary_before_interval(self) -> None:
        fp = FallbackProvider(StubProvider(), StubProvider(), retry_interval=60.0)
        fp._primary_healthy = False
        fp._last_failure_time = time.time()
        assert fp._should_try_primary() is False

    def test_should_try_primary_after_interval(self) -> None:
        fp = FallbackProvider(StubProvider(), StubProvider(), retry_interval=0.0)
        fp._primary_healthy = False
        fp._last_failure_time = time.time() - 1.0
        assert fp._should_try_primary() is True
