"""Ollama client for local LLM inference.

Provides a wrapper around Ollama's HTTP API with:
- Connection pooling and reuse
- Configurable timeout and max tokens
- Low temperature for deterministic outputs
- JSON mode support for structured outputs
- Retry logic for transient failures
- Keep-alive control for model warmth
- Context window size configuration
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

# Default Ollama settings
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT = 30.0  # seconds
DEFAULT_MAX_TOKENS = 150
DEFAULT_TEMPERATURE = 0.1
DEFAULT_KEEP_ALIVE = "5m"  # Duration models stay loaded (5 minutes)
DEFAULT_NUM_CTX = 4096  # Context window size


class OllamaConfig(BaseModel):
    """Configuration for Ollama client.

    Attributes:
        host: Ollama API host URL.
        timeout: Request timeout in seconds.
        max_tokens: Maximum tokens to generate.
        temperature: Sampling temperature (0.0-1.0).
        keep_alive: Duration to keep model loaded ("5m", "1h", "-1" for indefinite).
        num_ctx: Context window size in tokens.
    """

    model_config = ConfigDict(frozen=True)

    host: str = DEFAULT_HOST
    timeout: float = DEFAULT_TIMEOUT
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = DEFAULT_TEMPERATURE
    keep_alive: str = DEFAULT_KEEP_ALIVE
    num_ctx: int = DEFAULT_NUM_CTX


class OllamaResponse(BaseModel):
    """Response from Ollama API."""

    model_config = ConfigDict(frozen=True)

    content: str
    model: str
    done: bool
    total_duration_ns: int | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None

    @property
    def duration_ms(self) -> float | None:
        """Get duration in milliseconds."""
        if self.total_duration_ns is not None:
            return self.total_duration_ns / 1_000_000
        return None


class OllamaError(Exception):
    """Error from Ollama API."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class OllamaUnavailableError(OllamaError):
    """Ollama service is not available."""

    pass


class OllamaClient:
    """Client for Ollama local LLM inference.

    Designed for low-latency classification tasks:
    - Reuses HTTP connections
    - Low temperature for consistent outputs
    - Supports JSON mode for structured responses
    - Configurable timeouts

    Usage:
        client = OllamaClient()
        if client.is_available():
            response = client.generate("phi3.5", "Classify this: hello")
    """

    def __init__(self, config: OllamaConfig | None = None) -> None:
        """Initialize Ollama client.

        Args:
            config: Client configuration. Uses defaults if not provided.
        """
        self.config = config or OllamaConfig()
        self._client = httpx.Client(
            base_url=self.config.host,
            timeout=httpx.Timeout(self.config.timeout),
        )
        self._available: bool | None = None

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> OllamaClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def is_available(self, force_check: bool = False) -> bool:
        """Check if Ollama is running and available.

        Args:
            force_check: Force a new check even if cached.

        Returns:
            True if Ollama is available.
        """
        if self._available is not None and not force_check:
            return self._available

        try:
            response = self._client.get("/api/tags", timeout=5.0)
            self._available = response.status_code == 200
        except httpx.RequestError:
            self._available = False

        return self._available

    def list_models(self) -> list[str]:
        """List available models.

        Returns:
            List of model names.

        Raises:
            OllamaUnavailableError: If Ollama is not available.
        """
        try:
            response = self._client.get("/api/tags")
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except httpx.RequestError as e:
            raise OllamaUnavailableError(f"Cannot connect to Ollama: {e}") from e

    def generate(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
        keep_alive: str | None = None,
        num_ctx: int | None = None,
    ) -> OllamaResponse:
        """Generate a completion.

        Args:
            model: Model name (e.g., "phi3.5", "gemma2:2b").
            prompt: The prompt to complete.
            system: Optional system prompt.
            json_mode: If True, request JSON output format.
            max_tokens: Override default max tokens.
            temperature: Override default temperature.
            keep_alive: Override default keep_alive duration.
            num_ctx: Override default context window size.

        Returns:
            OllamaResponse with the generated content.

        Raises:
            OllamaUnavailableError: If Ollama is not available.
            OllamaError: If the API returns an error.
        """
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": keep_alive or self.config.keep_alive,
            "options": {
                "num_predict": max_tokens or self.config.max_tokens,
                "temperature": temperature if temperature is not None else self.config.temperature,
                "num_ctx": num_ctx or self.config.num_ctx,
            },
        }

        if system:
            payload["system"] = system

        if json_mode:
            payload["format"] = "json"

        try:
            response = self._client.post("/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()

            return OllamaResponse(
                content=data.get("response", ""),
                model=data.get("model", model),
                done=data.get("done", True),
                total_duration_ns=data.get("total_duration"),
                prompt_eval_count=data.get("prompt_eval_count"),
                eval_count=data.get("eval_count"),
            )

        except httpx.ConnectError as e:
            self._available = False
            raise OllamaUnavailableError("Cannot connect to Ollama. Is it running? Start with: ollama serve") from e
        except httpx.TimeoutException as e:
            raise OllamaError(f"Request timed out after {self.config.timeout}s") from e
        except httpx.HTTPStatusError as e:
            raise OllamaError(
                f"Ollama API error: {e.response.text}",
                status_code=e.response.status_code,
            ) from e

    def generate_json(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        keep_alive: str | None = None,
        num_ctx: int | None = None,
        retry_on_parse_error: bool = True,
    ) -> dict[str, Any]:
        """Generate a JSON response with automatic parsing.

        Args:
            model: Model name.
            prompt: The prompt.
            system: Optional system prompt.
            max_tokens: Override max tokens.
            temperature: Override temperature.
            keep_alive: Override keep_alive duration.
            num_ctx: Override context window size.
            retry_on_parse_error: If True, retry once on JSON parse failure.

        Returns:
            Parsed JSON as a dictionary.

        Raises:
            OllamaError: If JSON parsing fails after retries.
        """
        response = self.generate(
            model,
            prompt,
            system=system,
            json_mode=True,
            max_tokens=max_tokens,
            temperature=temperature,
            keep_alive=keep_alive,
            num_ctx=num_ctx,
        )

        try:
            result: dict[str, Any] = json.loads(response.content)
            return result
        except json.JSONDecodeError as e:
            if not retry_on_parse_error:
                raise OllamaError(f"Failed to parse JSON response: {e}") from e

            # Retry with explicit instruction
            logger.warning("JSON parse failed, retrying with explicit instruction")
            retry_prompt = (
                f"{prompt}\n\n"
                "IMPORTANT: Your previous output was invalid JSON. "
                "Output ONLY valid JSON with no extra text."
            )

            response = self.generate(
                model,
                retry_prompt,
                system=system,
                json_mode=True,
                max_tokens=max_tokens,
                temperature=0.0,  # Even lower for retry
                keep_alive=keep_alive,
                num_ctx=num_ctx,
            )

            try:
                retry_result: dict[str, Any] = json.loads(response.content)
                return retry_result
            except json.JSONDecodeError as e2:
                raise OllamaError(f"Failed to parse JSON after retry: {response.content[:200]}") from e2

    def generate_embedding(
        self,
        model: str,
        text: str,
    ) -> list[float]:
        """Generate an embedding vector for text.

        Args:
            model: Embedding model name (e.g., "nomic-embed-text").
            text: Text to embed.

        Returns:
            Embedding vector as a list of floats.

        Raises:
            OllamaUnavailableError: If Ollama is not available.
            OllamaError: If the API returns an error.
        """
        try:
            response = self._client.post(
                "/api/embeddings",
                json={"model": model, "prompt": text},
            )
            response.raise_for_status()
            data = response.json()
            embedding: list[float] = data.get("embedding", [])
            return embedding

        except httpx.ConnectError as e:
            self._available = False
            raise OllamaUnavailableError("Cannot connect to Ollama. Is it running? Start with: ollama serve") from e
        except httpx.TimeoutException as e:
            raise OllamaError(f"Embedding request timed out after {self.config.timeout}s") from e
        except httpx.HTTPStatusError as e:
            raise OllamaError(
                f"Ollama embedding API error: {e.response.text}",
                status_code=e.response.status_code,
            ) from e

    def generate_embeddings_batch(
        self,
        model: str,
        texts: list[str],
    ) -> list[list[float]]:
        """Generate embeddings for multiple texts.

        Note: Ollama's API doesn't support true batch embedding,
        so this makes sequential requests. Consider using
        async for better performance with many texts.

        Args:
            model: Embedding model name.
            texts: List of texts to embed.

        Returns:
            List of embedding vectors.

        Raises:
            OllamaUnavailableError: If Ollama is not available.
            OllamaError: If the API returns an error.
        """
        embeddings = []
        for text in texts:
            embedding = self.generate_embedding(model, text)
            embeddings.append(embedding)
        return embeddings


# Module-level client instance
_client: OllamaClient | None = None


def get_client() -> OllamaClient:
    """Get the shared Ollama client instance.

    Returns:
        The OllamaClient singleton.
    """
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client
