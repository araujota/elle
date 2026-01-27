"""General-purpose LLM interface for ELLE.

Provides a high-level wrapper around Ollama for general LLM interactions:
- Text generation with configurable parameters
- JSON generation with automatic retry on parse errors
- Chat-style interactions with message history
- Model availability checking with fallbacks
- Keep-alive control for model warmth
- Context window management with compaction

Default model: Qwen2.5-7B-Instruct-Q8_0 (quantized for efficiency)

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
from collections.abc import Callable
from typing import Any, Literal, cast

import httpx
from pydantic import BaseModel, ConfigDict

from elle.rag.constants import (
    CHARS_PER_TOKEN,
    CONTEXT_COMPACT_TARGET,
    CONTEXT_COMPACT_THRESHOLD,
    CONTEXT_MIN_MESSAGES,
    JSON_MAX_RETRIES,
    JSON_RETRY_TEMPERATURE,
    LLM_FALLBACK_MODELS,
    LLM_KEEP_ALIVE,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_NUM_CTX,
    LLM_TEMPERATURE,
    LLM_TIMEOUT,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

# Default model for general LLM use (Q8 quantized)
DEFAULT_MODEL = LLM_MODEL

# Fallback models in order of preference
FALLBACK_MODELS = list(LLM_FALLBACK_MODELS)

# Default settings for general-purpose generation
DEFAULT_TIMEOUT = LLM_TIMEOUT
DEFAULT_MAX_TOKENS = LLM_MAX_TOKENS
DEFAULT_TEMPERATURE = LLM_TEMPERATURE
DEFAULT_KEEP_ALIVE = LLM_KEEP_ALIVE
DEFAULT_NUM_CTX = LLM_NUM_CTX


class LLMConfig(BaseModel):
    """Configuration for the LLM interface.

    Attributes:
        host: Ollama API host URL.
        model: Model name to use for generation.
        timeout: Request timeout in seconds.
        max_tokens: Maximum tokens to generate.
        temperature: Sampling temperature (0.0-1.0).
        keep_alive: Duration to keep model loaded after request.
        num_ctx: Context window size in tokens.
        json_retry_temperature: Temperature for JSON retry attempts.
        json_max_retries: Maximum retries for JSON parsing.
    """

    model_config = ConfigDict(frozen=True)

    host: str = "http://localhost:11434"
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = DEFAULT_TEMPERATURE
    keep_alive: str = DEFAULT_KEEP_ALIVE
    num_ctx: int = DEFAULT_NUM_CTX

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
# Context Window Management
# =============================================================================


class ContextWindowManager:
    """Manages context window for multi-turn LLM conversations.

    Tracks token usage and triggers compaction when the context approaches
    the configured threshold. Uses a sliding window strategy that preserves
    the system message and recent turns while summarizing earlier context.

    Usage:
        manager = ContextWindowManager(max_tokens=8192)
        manager.add_message("system", "You are a helpful assistant.")
        manager.add_message("user", "Hello!")
        manager.add_message("assistant", "Hi there!")

        # Get messages for next request
        messages = manager.get_messages()

        # Check if compaction is needed
        if manager.needs_compaction():
            await manager.compact(llm)
    """

    def __init__(
        self,
        max_tokens: int = LLM_NUM_CTX,
        compact_threshold: float = CONTEXT_COMPACT_THRESHOLD,
        compact_target: float = CONTEXT_COMPACT_TARGET,
        min_messages: int = CONTEXT_MIN_MESSAGES,
    ) -> None:
        """Initialize the context window manager.

        Args:
            max_tokens: Maximum context window size in tokens.
            compact_threshold: Trigger compaction at this fraction of max (0.75 = 75%).
            compact_target: Target fraction after compaction (0.50 = 50%).
            min_messages: Minimum messages to keep after compaction.
        """
        self.max_tokens = max_tokens
        self.compact_threshold = compact_threshold
        self.compact_target = compact_target
        self.min_messages = min_messages

        self._messages: list[dict[str, str]] = []
        self._token_count: int = 0
        self._compaction_count: int = 0

    def _estimate_tokens(self, content: str) -> int:
        """Estimate token count from string length.

        Uses a rough approximation of ~4 characters per token.
        This is imprecise but sufficient for threshold detection.

        Args:
            content: Text content.

        Returns:
            Estimated token count.
        """
        return max(1, len(content) // CHARS_PER_TOKEN)

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the conversation.

        Args:
            role: Message role ("system", "user", or "assistant").
            content: Message content.
        """
        tokens = self._estimate_tokens(content)
        self._messages.append({"role": role, "content": content})
        self._token_count += tokens

    def add_messages(self, messages: list[dict[str, str]]) -> None:
        """Add multiple messages to the conversation.

        Args:
            messages: List of message dicts with "role" and "content".
        """
        for msg in messages:
            self.add_message(msg["role"], msg["content"])

    def get_messages(self) -> list[dict[str, str]]:
        """Get all messages in the conversation.

        Returns:
            List of message dicts.
        """
        return list(self._messages)

    @property
    def token_count(self) -> int:
        """Get estimated token count."""
        return self._token_count

    @property
    def message_count(self) -> int:
        """Get message count."""
        return len(self._messages)

    @property
    def utilization(self) -> float:
        """Get context utilization as a fraction (0.0-1.0)."""
        return self._token_count / self.max_tokens if self.max_tokens > 0 else 0.0

    @property
    def compaction_count(self) -> int:
        """Get number of times context has been compacted."""
        return self._compaction_count

    def needs_compaction(self) -> bool:
        """Check if context needs compaction.

        Returns:
            True if token count exceeds compact_threshold * max_tokens.
        """
        threshold = int(self.max_tokens * self.compact_threshold)
        return self._token_count > threshold

    def clear(self) -> None:
        """Clear all messages and reset token count."""
        self._messages.clear()
        self._token_count = 0

    async def compact(self, llm: LLM) -> str:
        """Compact the context by summarizing earlier messages.

        Preserves:
        - System message (first message if role="system")
        - Last N conversation turns (user + assistant pairs)
        - Summary of earlier context

        Args:
            llm: LLM instance for generating summary.

        Returns:
            Summary of compacted content.
        """
        if len(self._messages) <= self.min_messages:
            return ""

        # Separate system message
        system_msg = None
        conversation = []
        for msg in self._messages:
            if msg["role"] == "system" and system_msg is None:
                system_msg = msg
            else:
                conversation.append(msg)

        # Calculate how many recent messages to keep
        target_tokens = int(self.max_tokens * self.compact_target)
        keep_count = min(self.min_messages - 1, len(conversation))  # -1 for system

        # Estimate tokens in kept messages
        kept_tokens = sum(self._estimate_tokens(msg["content"]) for msg in conversation[-keep_count:])
        if system_msg:
            kept_tokens += self._estimate_tokens(system_msg["content"])

        # Calculate space for summary
        summary_budget = max(200, target_tokens - kept_tokens - 100)

        # Messages to summarize
        to_summarize = conversation[:-keep_count] if keep_count > 0 else conversation

        if not to_summarize:
            return ""

        # Generate summary
        summary_prompt = (
            "Summarize the following conversation history in a concise paragraph. "
            "Focus on key information, decisions made, and context needed for continuing "
            f"the conversation. Keep it under {summary_budget // 4} words:\n\n"
        )
        for msg in to_summarize:
            summary_prompt += f"{msg['role'].upper()}: {msg['content'][:500]}\n"

        try:
            response = llm.generate(
                summary_prompt,
                max_tokens=summary_budget,
                temperature=0.3,
            )
            summary = response.content.strip()
        except Exception as e:
            logger.warning(f"Failed to generate summary: {e}")
            # Fallback: truncate
            summary = "Earlier conversation covered: " + ", ".join(msg["content"][:50] for msg in to_summarize[:3])

        # Rebuild messages
        new_messages: list[dict[str, str]] = []
        new_token_count = 0

        if system_msg:
            new_messages.append(system_msg)
            new_token_count += self._estimate_tokens(system_msg["content"])

        # Add summary as assistant context
        summary_msg = {
            "role": "assistant",
            "content": f"[Context from earlier conversation: {summary}]",
        }
        new_messages.append(summary_msg)
        new_token_count += self._estimate_tokens(summary_msg["content"])

        # Add recent messages
        for msg in conversation[-keep_count:]:
            new_messages.append(msg)
            new_token_count += self._estimate_tokens(msg["content"])

        # Update state
        self._messages = new_messages
        self._token_count = new_token_count
        self._compaction_count += 1

        logger.info(
            f"Context compacted: {len(to_summarize)} messages summarized, "
            f"{len(new_messages)} messages remaining, "
            f"~{new_token_count} tokens (was ~{self._token_count})"
        )

        return summary


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

    def __exit__(self, *args: Any) -> None:
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
        keep_alive: str | None = None,
        num_ctx: int | None = None,
    ) -> LLMResponse:
        """Generate a text completion.

        Args:
            prompt: The prompt to complete.
            system: Optional system prompt.
            model: Override default model.
            max_tokens: Override default max tokens.
            temperature: Override default temperature.
            timeout: Override default timeout.
            keep_alive: Override default keep_alive duration.
            num_ctx: Override default context window size.

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
            keep_alive=keep_alive or self.config.keep_alive,
            num_ctx=num_ctx or self.config.num_ctx,
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
        keep_alive: str,
        num_ctx: int,
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
            keep_alive: Keep-alive duration.
            num_ctx: Context window size.
            json_mode: Whether to request JSON output.

        Returns:
            LLMResponse with generated content.
        """
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
            raise LLMUnavailableError("Cannot connect to Ollama. Is it running? Start with: ollama serve") from e
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
        keep_alive: str | None = None,
        num_ctx: int | None = None,
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
            keep_alive: Override default keep_alive duration.
            num_ctx: Override default context window size.
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

        effective_keep_alive = keep_alive or self.config.keep_alive
        effective_num_ctx = num_ctx or self.config.num_ctx

        response = self._generate(
            prompt=full_prompt,
            system=json_system,
            model=model or self.model,
            max_tokens=max_tokens or self.config.max_tokens,
            temperature=temperature if temperature is not None else self.config.temperature,
            timeout=timeout or self.config.timeout,
            keep_alive=effective_keep_alive,
            num_ctx=effective_num_ctx,
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
                keep_alive=effective_keep_alive,
                num_ctx=effective_num_ctx,
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

        result: dict[str, Any] = json.loads(content)
        return result

    def _retry_json(
        self,
        original_prompt: str,
        failed_response: str,
        system: str,
        model: str,
        max_tokens: int,
        timeout: float,
        keep_alive: str,
        num_ctx: int,
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
            keep_alive: Keep-alive duration.
            num_ctx: Context window size.

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
            keep_alive=keep_alive,
            num_ctx=num_ctx,
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
        keep_alive: str | None = None,
        num_ctx: int | None = None,
    ) -> LLMResponse:
        """Chat-style generation with message history.

        Args:
            messages: List of messages (system, user, assistant roles).
            model: Override default model.
            max_tokens: Override default max tokens.
            temperature: Override default temperature.
            timeout: Override default timeout.
            keep_alive: Override default keep_alive duration.
            num_ctx: Override default context window size.

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
                normalized.append(
                    Message(
                        role=cast(Literal["system", "user", "assistant"], msg["role"]),
                        content=msg["content"],
                    )
                )
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
            "keep_alive": keep_alive or self.config.keep_alive,
            "options": {
                "num_predict": max_tokens or self.config.max_tokens,
                "temperature": temperature if temperature is not None else self.config.temperature,
                "num_ctx": num_ctx or self.config.num_ctx,
            },
        }

        if system_content:
            # Prepend system message
            payload["messages"] = [{"role": "system", "content": system_content}] + chat_messages

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
            raise LLMUnavailableError("Cannot connect to Ollama. Is it running? Start with: ollama serve") from e
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(f"Chat request timed out after {timeout or self.config.timeout}s") from e
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
        keep_alive: str | None = None,
        num_ctx: int | None = None,
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
            keep_alive: Override default keep_alive duration.
            num_ctx: Override default context window size.
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
            keep_alive=keep_alive,
            num_ctx=num_ctx,
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
            msg_list.append(
                {
                    "role": "user",
                    "content": (
                        "That was not valid JSON. Please respond with ONLY valid JSON. "
                        "No explanations, no markdown code blocks, just raw JSON."
                    ),
                }
            )

            retry_response = self.chat(
                msg_list,
                model=model,
                max_tokens=max_tokens,
                temperature=self.config.json_retry_temperature,
                timeout=timeout,
                keep_alive=keep_alive,
                num_ctx=num_ctx,
            )

            try:
                return self._parse_json(retry_response.content)
            except json.JSONDecodeError as e2:
                raise LLMJSONError(
                    f"Failed to parse JSON after retry: {e2}",
                    raw_content=retry_response.content,
                ) from e2


# =============================================================================
# Tool Call Models
# =============================================================================


class ToolCall(BaseModel):
    """A tool call requested by the LLM."""

    model_config = ConfigDict(frozen=True)

    id: str = ""
    name: str
    arguments: dict[str, Any]


class ToolCallResponse(BaseModel):
    """Response containing tool calls and/or content."""

    model_config = ConfigDict(frozen=True)

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    done: bool = True
    model: str = ""
    duration_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0


# =============================================================================
# LLM Session (Persistent KV Cache)
# =============================================================================


class LLMSession:
    """Persistent LLM session with KV cache.

    Maintains conversation history and KV cache across multiple
    chat calls within the same run. Ollama preserves KV cache
    when messages array is extended (not replaced).

    Usage:
        async with LLMSession(llm) as session:
            response = await session.chat("Hello!")
            # KV cache preserved
            response = await session.chat("Follow-up question")
    """

    def __init__(
        self,
        llm: LLM,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        keep_alive: str = "30m",
    ) -> None:
        """Initialize the session.

        Args:
            llm: LLM instance to use.
            system_prompt: Optional system prompt.
            tools: Optional list of tool definitions (Ollama format).
            keep_alive: How long to keep model loaded between calls.
        """
        self.llm = llm
        self.tools = tools
        self.keep_alive = keep_alive
        self._messages: list[dict[str, Any]] = []
        self._closed = False

        # Add system message if provided
        if system_prompt:
            self._messages.append({
                "role": "system",
                "content": system_prompt,
            })

    async def __aenter__(self) -> LLMSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the session."""
        self._closed = True

    @property
    def messages(self) -> list[dict[str, Any]]:
        """Get all messages in the session."""
        return list(self._messages)

    @property
    def message_count(self) -> int:
        """Get the number of messages."""
        return len(self._messages)

    def add_user_message(self, content: str) -> None:
        """Add a user message to the session.

        Args:
            content: Message content.
        """
        self._messages.append({
            "role": "user",
            "content": content,
        })

    def add_assistant_message(
        self,
        content: str = "",
        tool_calls: list[ToolCall] | None = None,
    ) -> None:
        """Add an assistant message to the session.

        Args:
            content: Message content.
            tool_calls: Optional tool calls made by the assistant.
        """
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": content,
        }
        if tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id or f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for i, tc in enumerate(tool_calls)
            ]
        self._messages.append(msg)

    def add_tool_result(
        self,
        tool_call_id: str,
        result: str,
    ) -> None:
        """Add a tool result to the session.

        Args:
            tool_call_id: ID of the tool call this is a result for.
            result: Result from the tool execution.
        """
        self._messages.append({
            "role": "tool",
            "content": result,
            "tool_call_id": tool_call_id,
        })

    async def chat(
        self,
        content: str | None = None,
        *,
        stream_callback: Callable[[str], None] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> ToolCallResponse:
        """Send a message and get a response.

        This preserves the KV cache from prior turns in the session.
        Each call extends the messages array rather than replacing it.

        Args:
            content: User message content. If None, continues from last state
                     (used after adding tool results).
            stream_callback: Optional callback for streaming tokens.
            temperature: Override temperature.
            max_tokens: Override max tokens.
            timeout: Override timeout.

        Returns:
            ToolCallResponse with content and/or tool calls.
        """
        if self._closed:
            raise RuntimeError("Session is closed")

        # Add user message if provided
        if content:
            self.add_user_message(content)

        # Build request payload
        payload: dict[str, Any] = {
            "model": self.llm.model,
            "messages": self._messages,
            "stream": stream_callback is not None,
            "keep_alive": self.keep_alive,
            "options": {
                "num_predict": max_tokens or self.llm.config.max_tokens,
                "temperature": temperature if temperature is not None else self.llm.config.temperature,
                "num_ctx": self.llm.config.num_ctx,
            },
        }

        if self.tools:
            payload["tools"] = self.tools

        try:
            if stream_callback:
                return await self._stream_chat(
                    payload, stream_callback, timeout or self.llm.config.timeout
                )
            else:
                return await self._sync_chat(payload, timeout or self.llm.config.timeout)

        except httpx.ConnectError as e:
            self.llm._available = False
            raise LLMUnavailableError(
                "Cannot connect to Ollama. Is it running? Start with: ollama serve"
            ) from e
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(
                f"Session chat timed out after {timeout or self.llm.config.timeout}s"
            ) from e
        except httpx.HTTPStatusError as e:
            raise LLMError(
                f"Ollama chat API error: {e.response.text}",
                status_code=e.response.status_code,
            ) from e

    async def _sync_chat(
        self,
        payload: dict[str, Any],
        timeout: float,
    ) -> ToolCallResponse:
        """Non-streaming chat request."""
        start_time = time.time()

        response = self.llm._client.post(
            "/api/chat",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()

        duration_ms = (time.time() - start_time) * 1000
        message = data.get("message", {})

        # Parse tool calls if present
        tool_calls: list[ToolCall] = []
        if "tool_calls" in message:
            for tc in message["tool_calls"]:
                func = tc.get("function", {})
                args_str = func.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except json.JSONDecodeError:
                    args = {}

                tool_calls.append(ToolCall(
                    id=tc.get("id", ""),
                    name=func.get("name", ""),
                    arguments=args,
                ))

        content = message.get("content", "")

        # Add assistant message to history
        self.add_assistant_message(content, tool_calls if tool_calls else None)

        return ToolCallResponse(
            content=content,
            tool_calls=tuple(tool_calls),
            done=data.get("done", True),
            model=data.get("model", self.llm.model),
            duration_ms=duration_ms,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
        )

    async def _stream_chat(
        self,
        payload: dict[str, Any],
        stream_callback: Callable[[str], None],
        timeout: float,
    ) -> ToolCallResponse:
        """Streaming chat request with completion verification."""
        start_time = time.time()
        collected_content = ""
        tool_calls: list[ToolCall] = []
        model = self.llm.model
        prompt_tokens = None
        completion_tokens = None
        stream_done = False
        last_chunk_time = time.time()
        chunk_timeout = 30.0  # Max time between chunks

        # Use httpx streaming
        with self.llm._client.stream(
            "POST",
            "/api/chat",
            json=payload,
            timeout=timeout,
        ) as response:
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue

                # Check for stalled stream
                current_time = time.time()
                if current_time - last_chunk_time > chunk_timeout:
                    logger.warning(
                        f"Stream stalled for {chunk_timeout}s, possible truncation"
                    )
                    break
                last_chunk_time = current_time

                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Extract content from message
                message = chunk.get("message", {})
                content = message.get("content", "")

                if content:
                    collected_content += content
                    stream_callback(content)

                # Check for tool calls
                if "tool_calls" in message:
                    for tc in message["tool_calls"]:
                        func = tc.get("function", {})
                        args_str = func.get("arguments", "{}")
                        try:
                            args = json.loads(args_str) if isinstance(args_str, str) else args_str
                        except json.JSONDecodeError:
                            args = {}

                        tool_calls.append(ToolCall(
                            id=tc.get("id", ""),
                            name=func.get("name", ""),
                            arguments=args,
                        ))

                # Get model and token counts from final chunk
                if chunk.get("done"):
                    stream_done = True
                    model = chunk.get("model", model)
                    prompt_tokens = chunk.get("prompt_eval_count")
                    completion_tokens = chunk.get("eval_count")

        duration_ms = (time.time() - start_time) * 1000

        # Verify stream completion
        if not stream_done:
            logger.warning(
                "Stream ended without completion signal - response may be truncated"
            )

        # Check for truncation indicators
        min_response_length = 10
        if len(collected_content.strip()) < min_response_length and not tool_calls:
            logger.warning(
                f"Response too short ({len(collected_content)} chars) - possible truncation"
            )

        # Add assistant message to history
        self.add_assistant_message(collected_content, tool_calls if tool_calls else None)

        return ToolCallResponse(
            content=collected_content,
            tool_calls=tuple(tool_calls),
            done=stream_done,
            model=model,
            duration_ms=duration_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    async def continue_after_tool(
        self,
        *,
        stream_callback: Callable[[str], None] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> ToolCallResponse:
        """Continue the conversation after adding tool results.

        Call this after adding tool results with add_tool_result()
        to get the LLM's next response.

        Args:
            stream_callback: Optional callback for streaming tokens.
            temperature: Override temperature.
            max_tokens: Override max tokens.
            timeout: Override timeout.

        Returns:
            ToolCallResponse with content and/or more tool calls.
        """
        return await self.chat(
            content=None,
            stream_callback=stream_callback,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )


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
