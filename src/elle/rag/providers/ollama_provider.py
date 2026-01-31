"""Ollama LLM provider.

Implements the LLMProvider interface for local Ollama inference,
using /api/generate and /api/chat endpoints.
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


class OllamaProvider(LLMProvider):
    """LLM provider for local Ollama inference.

    Connects to a running Ollama instance and uses its native
    /api/generate and /api/chat endpoints.
    """

    def __init__(
        self,
        host: str = "http://localhost:11434",
        timeout: float = 120.0,
    ) -> None:
        self._host = host
        self._timeout = timeout
        self._client = httpx.Client(
            base_url=host,
            timeout=httpx.Timeout(timeout),
        )

    @property
    def provider_type(self) -> str:
        return "ollama"

    @property
    def host(self) -> str:
        return self._host

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
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": keep_alive,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
                "num_ctx": num_ctx,
            },
        }

        if system:
            payload["system"] = system

        if json_mode:
            payload["format"] = "json"

        start_time = time.time()

        try:
            response = self._client.post(
                "/api/generate",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()

            duration_ms = (time.time() - start_time) * 1000

            return ProviderResponse(
                content=data.get("response", ""),
                model=data.get("model", model),
                done=data.get("done", True),
                duration_ms=duration_ms,
                prompt_tokens=data.get("prompt_eval_count"),
                completion_tokens=data.get("eval_count"),
            )

        except httpx.ConnectError:
            raise
        except httpx.TimeoutException:
            raise
        except httpx.HTTPStatusError:
            raise

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
            "stream": False,
            "keep_alive": keep_alive,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
                "num_ctx": num_ctx,
            },
        }

        if tools:
            payload["tools"] = tools

        start_time = time.time()

        try:
            response = self._client.post(
                "/api/chat",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()

            duration_ms = (time.time() - start_time) * 1000
            message = data.get("message", {})

            # Parse tool calls if present
            tool_calls: list[dict[str, Any]] = []
            if "tool_calls" in message:
                for tc in message["tool_calls"]:
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
                content=message.get("content", ""),
                model=data.get("model", model),
                done=data.get("done", True),
                duration_ms=duration_ms,
                prompt_tokens=data.get("prompt_eval_count"),
                completion_tokens=data.get("eval_count"),
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
            "stream": True,
            "keep_alive": keep_alive,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
                "num_ctx": num_ctx,
            },
        }

        if tools:
            payload["tools"] = tools

        start_time = time.time()

        with self._client.stream(
            "POST",
            "/api/chat",
            json=payload,
            timeout=timeout,
        ) as response:
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue

                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                message = chunk.get("message", {})
                content = message.get("content", "")
                is_done = chunk.get("done", False)

                # Parse tool calls from final chunk
                tool_calls: list[dict[str, Any]] = []
                if "tool_calls" in message:
                    for tc in message["tool_calls"]:
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

                duration_ms = (time.time() - start_time) * 1000

                yield ProviderResponse(
                    content=content,
                    model=chunk.get("model", model),
                    done=is_done,
                    duration_ms=duration_ms if is_done else None,
                    prompt_tokens=chunk.get("prompt_eval_count") if is_done else None,
                    completion_tokens=chunk.get("eval_count") if is_done else None,
                    tool_calls=tool_calls,
                )

    def is_available(self, timeout: float = 5.0) -> bool:
        try:
            response = self._client.get("/api/tags", timeout=timeout)
            return response.status_code == 200
        except httpx.RequestError:
            return False

    def list_models(self) -> list[str]:
        try:
            response = self._client.get("/api/tags")
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except httpx.RequestError:
            return []

    def close(self) -> None:
        self._client.close()
