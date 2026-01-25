"""Server-Sent Events (SSE) streaming for OpenAI-compatible API.

Implements streaming responses following the OpenAI chat completions
streaming format. Responses are sent as SSE events with JSON data.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, AsyncIterator

from elle.daemon.api.openai_models import (
    ChatCompletionChunk,
    ChatMessage,
    ExecutionMode,
    StreamChoice,
)

if TYPE_CHECKING:
    from fastapi.responses import StreamingResponse

    from elle.daemon.api.auth import AuthContext
    from elle.daemon.api.engine_adapter import EngineAdapter


def _format_sse_chunk(data: str) -> str:
    """Format data as an SSE chunk.

    Args:
        data: JSON string to send.

    Returns:
        SSE-formatted string with 'data:' prefix.
    """
    return f"data: {data}\n\n"


def _create_chunk(
    completion_id: str,
    model: str,
    created: int,
    content: str | None = None,
    finish_reason: str | None = None,
) -> ChatCompletionChunk:
    """Create a streaming chunk.

    Args:
        completion_id: The completion ID (same across all chunks).
        model: The model name.
        created: Unix timestamp.
        content: Content delta (None for initial/final chunks).
        finish_reason: Finish reason (only in final chunk).

    Returns:
        ChatCompletionChunk for streaming.
    """
    delta = ChatMessage(
        role="assistant" if content is None and finish_reason is None else None,
        content=content,
    )

    choice = StreamChoice(
        index=0,
        delta=delta,
        finish_reason=finish_reason,
    )

    return ChatCompletionChunk(
        id=completion_id,
        created=created,
        model=model,
        choices=(choice,),
    )


async def stream_chat_completion(
    adapter: "EngineAdapter",
    messages: tuple[ChatMessage, ...],
    mode: ExecutionMode,
    auth: "AuthContext",
    model: str,
):
    """Create a streaming response for chat completions.

    Streams the response as Server-Sent Events following the OpenAI
    streaming format.

    Args:
        adapter: The engine adapter.
        messages: Conversation messages.
        mode: Execution mode.
        auth: Authentication context.
        model: Model name.

    Returns:
        StreamingResponse with SSE content.
    """
    from fastapi.responses import StreamingResponse

    async def generate_chunks() -> AsyncIterator[str]:
        """Generate SSE chunks."""
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())

        # Send initial chunk with role
        initial_chunk = _create_chunk(
            completion_id=completion_id,
            model=model,
            created=created,
        )
        yield _format_sse_chunk(initial_chunk.model_dump_json())

        # Stream content from the adapter
        try:
            async for content in adapter.process_chat_streaming(messages, mode, auth):
                if content:
                    content_chunk = _create_chunk(
                        completion_id=completion_id,
                        model=model,
                        created=created,
                        content=content,
                    )
                    yield _format_sse_chunk(content_chunk.model_dump_json())

            # Send final chunk with finish_reason
            final_chunk = _create_chunk(
                completion_id=completion_id,
                model=model,
                created=created,
                finish_reason="stop",
            )
            yield _format_sse_chunk(final_chunk.model_dump_json())

        except Exception as e:
            # Send error as content
            error_chunk = _create_chunk(
                completion_id=completion_id,
                model=model,
                created=created,
                content=f"\n\n[Error: {e!s}]",
            )
            yield _format_sse_chunk(error_chunk.model_dump_json())

            final_chunk = _create_chunk(
                completion_id=completion_id,
                model=model,
                created=created,
                finish_reason="stop",
            )
            yield _format_sse_chunk(final_chunk.model_dump_json())

        # Send done marker
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate_chunks(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


async def stream_error(
    error_message: str,
    model: str,
):
    """Create a streaming error response.

    Args:
        error_message: The error message to stream.
        model: Model name.

    Returns:
        StreamingResponse with error content.
    """
    from fastapi.responses import StreamingResponse

    async def generate_error() -> AsyncIterator[str]:
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())

        # Initial chunk
        initial_chunk = _create_chunk(
            completion_id=completion_id,
            model=model,
            created=created,
        )
        yield _format_sse_chunk(initial_chunk.model_dump_json())

        # Error content
        error_chunk = _create_chunk(
            completion_id=completion_id,
            model=model,
            created=created,
            content=f"Error: {error_message}",
        )
        yield _format_sse_chunk(error_chunk.model_dump_json())

        # Final chunk
        final_chunk = _create_chunk(
            completion_id=completion_id,
            model=model,
            created=created,
            finish_reason="stop",
        )
        yield _format_sse_chunk(final_chunk.model_dump_json())

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate_error(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
