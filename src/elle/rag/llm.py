"""General-purpose LLM interface for ELLE.

Provides a high-level wrapper around Ollama for general LLM interactions:
- Text generation with configurable parameters
- JSON generation with automatic retry on parse errors
- Chat-style interactions with message history
- Model availability checking with fallbacks

Default model: Qwen2.5-7B-Instruct (qwen2.5:7b-instruct)

Usage:
    from elle.rag.llm import LLM, get_llm

    llm = get_llm()
    if llm.is_available():
        response = llm.generate("Explain how to check disk usage on Ubuntu")

        # Or with JSON output
        data = llm.generate_json(
            "List the top 3 commands for checking disk space",
            schema={"commands": ["str"]}
        )

        # Or chat-style
        response = llm.chat([
            {"role": "system", "content": "You are a Linux expert."},
            {"role": "user", "content": "How do I restart nginx?"}
        ])
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

# Default model for general LLM use
DEFAULT_MODEL = "qwen2.5:7b-instruct"

# Fallback models in order of preference
FALLBACK_MODELS = [
    "qwen2.5:7b",
    "llama3.1:8b-instruct-q4_0",
    "llama3.1:8b",
    "mistral:7b-instruct",
    "gemma2:9b",
]

# Default settings for general-purpose generation
DEFAULT_TIMEOUT = 120.0  # 2 minutes for complex generations
DEFAULT_MAX_TOKENS = 2048  # Reasonable limit for most responses
DEFAULT_TEMPERATURE = 0.7  # Balanced creativity/coherence

# JSON mode settings
JSON_RETRY_TEMPERATURE = 0.1  # Lower temperature for retry
JSON_MAX_RETRIES = 1  # Retry once on parse failure


class LLMConfig(BaseModel):
    """Configuration for the LLM interface."""

    model_config = ConfigDict(frozen=True)

    host: str = "http://localhost:11434"
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = DEFAULT_TEMPERATURE

    # JSON mode settings
    json_retry_temperature: float = JSON_RETRY_TEMPERATURE
    json_max_retries: int = JSON_MAX_RETRIES


# =============================================================================
# Response Models
# =============================================================================

class LLMResponse(BaseModel):
    """Response from an LLM generation."""

    model_config = ConfigDict(frozen=True)

    content: str
    model: str
    done: bool = True
    duration_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        """Get total token count."""
        if self.prompt_tokens is not None and self.completion_tokens is not None:
            return self.prompt_tokens + self.completion_tokens
        return None


class Message(BaseModel):
    """A chat message."""

    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str


# =============================================================================
# Exceptions
# =============================================================================

class LLMError(Exception):
    """Base exception for LLM errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class LLMUnavailableError(LLMError):
    """LLM service is not available."""
    pass


class LLMTimeoutError(LLMError):
    """LLM request timed out."""
    pass


class LLMJSONError(LLMError):
    """Failed to parse JSON from LLM response."""

    def __init__(self, message: str, raw_content: str | None = None) -> None:
        super().__init__(message)
        self.raw_content = raw_content


# =============================================================================
# LLM Interface
# =============================================================================

class LLM:
    """General-purpose LLM interface for ELLE.

    Provides text and JSON generation with:
    - Configurable timeouts, max tokens, and temperature
    - Automatic model detection with fallbacks
    - JSON mode with retry on parse errors
    - Chat-style interactions with message history

    Usage:
        llm = LLM()
        if llm.is_available():
            response = llm.generate("Explain systemctl")
            print(response.content)
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        """Initialize the LLM interface.

        Args:
            config: LLM configuration. Uses defaults if not provided.
        """
        self.config = config or LLMConfig()
        self._client = httpx.Client(
            base_url=self.config.host,
            timeout=httpx.Timeout(self.config.timeout),
        )
        self._available: bool | None = None
        self._detected_model: str | None = None

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> LLM:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    # -------------------------------------------------------------------------
    # Availability and Model Detection
    # -------------------------------------------------------------------------

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
            LLMUnavailableError: If Ollama is not available.
        """
        try:
            response = self._client.get("/api/tags")
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except httpx.RequestError as e:
            raise LLMUnavailableError(f"Cannot connect to Ollama: {e}") from e

    @property
    def model(self) -> str:
        """Get the model to use (auto-detect if needed).

        Returns:
            Model name string.
        """
        if self._detected_model:
            return self._detected_model

        # Try configured model first
        if self.is_available():
            available = self.list_models()

            # Check configured model
            if any(self.config.model in m for m in available):
                self._detected_model = self.config.model
                logger.info(f"Using configured model: {self.config.model}")
                return self._detected_model

            # Try fallbacks
            for candidate in FALLBACK_MODELS:
                if any(candidate in m for m in available):
                    self._detected_model = candidate
                    logger.info(f"Using fallback model: {candidate}")
                    return self._detected_model

        # Return configured model (will fail if not available)
        self._detected_model = self.config.model
        return self._detected_model

    def has_model(self, model: str) -> bool:
        """Check if a specific model is available.

        Args:
            model: Model name to check.

        Returns:
            True if the model is available.
        """
        if not self.is_available():
            return False
        available = self.list_models()
        return any(model in m for m in available)

    # -------------------------------------------------------------------------
    # Text Generation
    # -------------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> LLMResponse:
        """Generate a text completion.

        Args:
            prompt: The prompt to complete.
            system: Optional system prompt.
            model: Override default model.
            max_tokens: Override default max tokens.
            temperature: Override default temperature.
            timeout: Override default timeout.

        Returns:
            LLMResponse with the generated content.

        Raises:
            LLMUnavailableError: If Ollama is not available.
            LLMTimeoutError: If the request times out.
            LLMError: If the API returns an error.
        """
        return self._generate(
            prompt=prompt,
            system=system,
            model=model or self.model,
            max_tokens=max_tokens or self.config.max_tokens,
            temperature=temperature if temperature is not None else self.config.temperature,
            timeout=timeout or self.config.timeout,
            json_mode=False,
        )

    def _generate(
        self,
        prompt: str,
        *,
        system: str | None,
        model: str,
        max_tokens: int,
        temperature: float,
        timeout: float,
        json_mode: bool,
    ) -> LLMResponse:
        """Internal generation method.

        Args:
            prompt: The prompt.
            system: System prompt.
            model: Model name.
            max_tokens: Max tokens.
            temperature: Temperature.
            timeout: Timeout.
            json_mode: Whether to request JSON output.

        Returns:
            LLMResponse with generated content.
        """
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
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

            return LLMResponse(
                content=data.get("response", ""),
                model=data.get("model", model),
                done=data.get("done", True),
                duration_ms=duration_ms,
                prompt_tokens=data.get("prompt_eval_count"),
                completion_tokens=data.get("eval_count"),
            )

        except httpx.ConnectError as e:
            self._available = False
            raise LLMUnavailableError(
                "Cannot connect to Ollama. Is it running? Start with: ollama serve"
            ) from e
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(
                f"Request timed out after {timeout}s. Try increasing timeout or reducing max_tokens."
            ) from e
        except httpx.HTTPStatusError as e:
            raise LLMError(
                f"Ollama API error: {e.response.text}",
                status_code=e.response.status_code,
            ) from e

    # -------------------------------------------------------------------------
    # JSON Generation
    # -------------------------------------------------------------------------

    def generate_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
        retry_on_error: bool = True,
    ) -> dict[str, Any]:
        """Generate a JSON response with automatic parsing and retry.

        If the response is not valid JSON, retries once with a lower
        temperature and explicit instructions.

        Args:
            prompt: The prompt.
            system: Optional system prompt.
            schema: Optional JSON schema hint to include in prompt.
            model: Override default model.
            max_tokens: Override default max tokens.
            temperature: Override default temperature.
            timeout: Override default timeout.
            retry_on_error: Whether to retry on JSON parse error.

        Returns:
            Parsed JSON as a dictionary.

        Raises:
            LLMJSONError: If JSON parsing fails after retries.
            LLMUnavailableError: If Ollama is not available.
            LLMTimeoutError: If the request times out.
        """
        # Build prompt with schema hint if provided
        full_prompt = prompt
        if schema:
            schema_str = json.dumps(schema, indent=2)
            full_prompt = f"{prompt}\n\nReturn JSON matching this schema:\n{schema_str}"

        # Add JSON instruction to system prompt
        json_system = system or ""
        if json_system:
            json_system += "\n\n"
        json_system += "You must respond with valid JSON only. No explanations, no markdown, just JSON."

        response = self._generate(
            prompt=full_prompt,
            system=json_system,
            model=model or self.model,
            max_tokens=max_tokens or self.config.max_tokens,
            temperature=temperature if temperature is not None else self.config.temperature,
            timeout=timeout or self.config.timeout,
            json_mode=True,
        )

        # Try to parse JSON
        try:
            return self._parse_json(response.content)
        except json.JSONDecodeError as e:
            if not retry_on_error:
                raise LLMJSONError(
                    f"Failed to parse JSON: {e}",
                    raw_content=response.content,
                ) from e

            logger.warning(f"JSON parse failed, retrying: {e}")
            return self._retry_json(
                original_prompt=full_prompt,
                failed_response=response.content,
                system=json_system,
                model=model or self.model,
                max_tokens=max_tokens or self.config.max_tokens,
                timeout=timeout or self.config.timeout,
            )

    def _parse_json(self, content: str) -> dict[str, Any]:
        """Parse JSON from response content.

        Handles common issues like markdown code blocks.

        Args:
            content: Raw response content.

        Returns:
            Parsed JSON dictionary.

        Raises:
            json.JSONDecodeError: If parsing fails.
        """
        content = content.strip()

        # Remove markdown code blocks if present
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        return json.loads(content)

    def _retry_json(
        self,
        original_prompt: str,
        failed_response: str,
        system: str,
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> dict[str, Any]:
        """Retry JSON generation after a parse failure.

        Uses lower temperature and explicit error correction.

        Args:
            original_prompt: The original prompt.
            failed_response: The response that failed to parse.
            system: System prompt.
            model: Model name.
            max_tokens: Max tokens.
            timeout: Timeout.

        Returns:
            Parsed JSON dictionary.

        Raises:
            LLMJSONError: If retry also fails.
        """
        retry_prompt = (
            f"{original_prompt}\n\n"
            f"IMPORTANT: Your previous response was not valid JSON:\n"
            f"```\n{failed_response[:500]}\n```\n\n"
            f"Please respond with ONLY valid JSON. "
            f"No explanations, no markdown code blocks, just raw JSON."
        )

        response = self._generate(
            prompt=retry_prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=self.config.json_retry_temperature,
            timeout=timeout,
            json_mode=True,
        )

        try:
            return self._parse_json(response.content)
        except json.JSONDecodeError as e:
            raise LLMJSONError(
                f"Failed to parse JSON after retry: {e}",
                raw_content=response.content,
            ) from e

    # -------------------------------------------------------------------------
    # Chat Interface
    # -------------------------------------------------------------------------

    def chat(
        self,
        messages: list[Message] | list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> LLMResponse:
        """Chat-style generation with message history.

        Args:
            messages: List of messages (system, user, assistant roles).
            model: Override default model.
            max_tokens: Override default max tokens.
            temperature: Override default temperature.
            timeout: Override default timeout.

        Returns:
            LLMResponse with the assistant's reply.

        Raises:
            LLMUnavailableError: If Ollama is not available.
            LLMTimeoutError: If the request times out.
            LLMError: If the API returns an error.
        """
        # Convert dict messages to Message objects
        normalized: list[Message] = []
        for msg in messages:
            if isinstance(msg, dict):
                normalized.append(Message(role=msg["role"], content=msg["content"]))
            else:
                normalized.append(msg)

        # Extract system message if present
        system_content = None
        chat_messages = []
        for msg in normalized:
            if msg.role == "system":
                system_content = msg.content
            else:
                chat_messages.append({"role": msg.role, "content": msg.content})

        # Use Ollama's chat endpoint
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": chat_messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens or self.config.max_tokens,
                "temperature": temperature if temperature is not None else self.config.temperature,
            },
        }

        if system_content:
            # Prepend system message
            payload["messages"] = [
                {"role": "system", "content": system_content}
            ] + chat_messages

        start_time = time.time()

        try:
            response = self._client.post(
                "/api/chat",
                json=payload,
                timeout=timeout or self.config.timeout,
            )
            response.raise_for_status()
            data = response.json()

            duration_ms = (time.time() - start_time) * 1000
            message = data.get("message", {})

            return LLMResponse(
                content=message.get("content", ""),
                model=data.get("model", model or self.model),
                done=data.get("done", True),
                duration_ms=duration_ms,
                prompt_tokens=data.get("prompt_eval_count"),
                completion_tokens=data.get("eval_count"),
            )

        except httpx.ConnectError as e:
            self._available = False
            raise LLMUnavailableError(
                "Cannot connect to Ollama. Is it running? Start with: ollama serve"
            ) from e
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(
                f"Chat request timed out after {timeout or self.config.timeout}s"
            ) from e
        except httpx.HTTPStatusError as e:
            raise LLMError(
                f"Ollama chat API error: {e.response.text}",
                status_code=e.response.status_code,
            ) from e

    def chat_json(
        self,
        messages: list[Message] | list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
        retry_on_error: bool = True,
    ) -> dict[str, Any]:
        """Chat-style JSON generation with message history.

        Args:
            messages: List of messages.
            schema: Optional JSON schema hint.
            model: Override default model.
            max_tokens: Override default max tokens.
            temperature: Override default temperature.
            timeout: Override default timeout.
            retry_on_error: Whether to retry on JSON parse error.

        Returns:
            Parsed JSON dictionary.

        Raises:
            LLMJSONError: If JSON parsing fails after retries.
        """
        # Convert to list of dicts for manipulation
        msg_list = []
        for msg in messages:
            if isinstance(msg, Message):
                msg_list.append({"role": msg.role, "content": msg.content})
            else:
                msg_list.append(msg)

        # Add JSON instruction to system message
        has_system = any(m["role"] == "system" for m in msg_list)
        json_instruction = "You must respond with valid JSON only. No explanations, no markdown, just JSON."

        if schema:
            schema_str = json.dumps(schema, indent=2)
            json_instruction += f"\n\nReturn JSON matching this schema:\n{schema_str}"

        if has_system:
            for msg in msg_list:
                if msg["role"] == "system":
                    msg["content"] += f"\n\n{json_instruction}"
                    break
        else:
            msg_list.insert(0, {"role": "system", "content": json_instruction})

        response = self.chat(
            msg_list,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )

        try:
            return self._parse_json(response.content)
        except json.JSONDecodeError as e:
            if not retry_on_error:
                raise LLMJSONError(
                    f"Failed to parse JSON: {e}",
                    raw_content=response.content,
                ) from e

            logger.warning(f"Chat JSON parse failed, retrying: {e}")

            # Add correction message and retry
            msg_list.append({"role": "assistant", "content": response.content})
            msg_list.append({
                "role": "user",
                "content": (
                    "That was not valid JSON. Please respond with ONLY valid JSON. "
                    "No explanations, no markdown code blocks, just raw JSON."
                ),
            })

            retry_response = self.chat(
                msg_list,
                model=model,
                max_tokens=max_tokens,
                temperature=self.config.json_retry_temperature,
                timeout=timeout,
            )

            try:
                return self._parse_json(retry_response.content)
            except json.JSONDecodeError as e2:
                raise LLMJSONError(
                    f"Failed to parse JSON after retry: {e2}",
                    raw_content=retry_response.content,
                ) from e2


# =============================================================================
# Module-level Singleton
# =============================================================================

_llm: LLM | None = None


def get_llm(config: LLMConfig | None = None) -> LLM:
    """Get the shared LLM instance.

    Args:
        config: Optional config (only used on first call).

    Returns:
        The LLM singleton.
    """
    global _llm
    if _llm is None:
        _llm = LLM(config)
    return _llm


def reset_llm() -> None:
    """Reset the shared LLM instance.

    Useful for testing or reconfiguration.
    """
    global _llm
    if _llm is not None:
        _llm.close()
        _llm = None
