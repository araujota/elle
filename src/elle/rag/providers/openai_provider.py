"""OpenAI-compatible LLM provider.

Implements the LLMProvider interface for remote OpenAI-compatible
endpoints (OpenAI, Azure, vLLM, LiteLLM, etc.) using the
/v1/chat/completions API.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from elle.rag.providers.base import LLMProvider, ProviderResponse

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """LLM provider for OpenAI-compatible APIs.

    Supports any endpoint implementing the /v1/chat/completions
    contract with Bearer token authentication and SSE streaming.
    """

    def __init__(
        self,
        host: str,
        api_key: str = "",
        timeout: float = 120.0,
        default_model: str = "gpt-4o",
    ) -> None:
        # Normalize host: strip trailing /v1 if present
        self._host = host.rstrip("/")
        if self._host.endswith("/v1"):
            self._host = self._host[:-3]

        self._api_key = api_key
        self._timeout = timeout
        self._default_model = default_model

        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            headers=headers,
        )

    @property
    def provider_type(self) -> str:
        return "openai"

    @property
    def host(self) -> str:
        return self._host

    def _build_url(self, path: str) -> str:
        return f"{self._host}/v1{path}"

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
        # Wrap prompt in messages for the chat completions API
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        return self.chat(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            json_mode=json_mode,
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
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        if tools:
            payload["tools"] = tools

        start_time = time.time()

        try:
            response = self._client.post(
                self._build_url("/chat/completions"),
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()

            duration_ms = (time.time() - start_time) * 1000
            choice = data.get("choices", [{}])[0] if data.get("choices") else {}
            message = choice.get("message", {})
            usage = data.get("usage", {})

            # Parse tool calls
            tool_calls: list[dict[str, Any]] = []
            for tc in message.get("tool_calls", []):
                func = tc.get("function", {})
                args_str = func.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except json.JSONDecodeError:
                    args = {}

                tool_calls.append(
                    {
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "arguments": args,
                    }
                )

            return ProviderResponse(
                content=message.get("content", "") or "",
                model=data.get("model", model),
                done=True,
                duration_ms=duration_ms,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                tool_calls=tool_calls,
            )

        except httpx.ConnectError:
            raise
        except httpx.TimeoutException:
            raise
        except httpx.HTTPStatusError:
            raise

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
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }

        if tools:
            payload["tools"] = tools

        start_time = time.time()

        with self._client.stream(
            "POST",
            self._build_url("/chat/completions"),
            json=payload,
            timeout=timeout,
        ) as response:
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue

                # SSE format: "data: {...}" or "data: [DONE]"
                if line.startswith("data: "):
                    data_str = line[6:]
                else:
                    data_str = line

                if data_str.strip() == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choice = chunk.get("choices", [{}])[0] if chunk.get("choices") else {}
                delta = choice.get("delta", {})
                content = delta.get("content", "") or ""
                finish_reason = choice.get("finish_reason")
                is_done = finish_reason is not None

                # Parse tool calls from delta
                tool_calls: list[dict[str, Any]] = []
                for tc in delta.get("tool_calls", []):
                    func = tc.get("function", {})
                    args_str = func.get("arguments", "")
                    tool_calls.append(
                        {
                            "id": tc.get("id", ""),
                            "name": func.get("name", ""),
                            "arguments_fragment": args_str,
                        }
                    )

                duration_ms = (time.time() - start_time) * 1000

                usage = chunk.get("usage", {})
                yield ProviderResponse(
                    content=content,
                    model=chunk.get("model", model),
                    done=is_done,
                    duration_ms=duration_ms if is_done else None,
                    prompt_tokens=usage.get("prompt_tokens") if is_done else None,
                    completion_tokens=usage.get("completion_tokens") if is_done else None,
                    tool_calls=tool_calls if tool_calls else [],
                )

    def is_available(self, timeout: float = 5.0) -> bool:
        try:
            response = self._client.get(
                self._build_url("/models"),
                timeout=timeout,
            )
            return response.status_code == 200
        except httpx.RequestError:
            return False

    def list_models(self) -> list[str]:
        try:
            response = self._client.get(self._build_url("/models"))
            response.raise_for_status()
            data = response.json()
            return [m.get("id", "") for m in data.get("data", [])]
        except httpx.RequestError:
            return []

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenAIProvider:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
