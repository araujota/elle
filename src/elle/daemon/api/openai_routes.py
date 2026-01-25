"""OpenAI-compatible API routes for ELLE.

Implements:
- POST /v1/chat/completions - Chat completions endpoint
- GET /v1/models - List available models

All requests go through intent classification before execution.
This is NOT direct LLM access - every request is processed through
ELLE's intent → policy → execution pipeline.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from elle.daemon.api.auth import AuthContext, get_auth_context
from elle.daemon.api.engine_adapter import (
    EngineAdapter,
    format_completion_response,
    get_adapter,
)
from elle.daemon.api.openai_models import (
    ELLE_MODELS,
    MODEL_TO_MODE,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ModelsListResponse,
)
from elle.daemon.api.streaming import stream_chat_completion, stream_error

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Create router with OpenAI-compatible prefix
openai_router = APIRouter(prefix="/v1", tags=["openai-compat"])


# Module-level adapter reference (set during app setup)
_adapter: EngineAdapter | None = None


def set_adapter(adapter: EngineAdapter) -> None:
    """Set the engine adapter for routes.

    Args:
        adapter: The EngineAdapter instance.
    """
    global _adapter
    _adapter = adapter


def get_engine_adapter() -> EngineAdapter:
    """Get the engine adapter.

    Returns:
        The configured EngineAdapter.
    """
    if _adapter is not None:
        return _adapter
    return get_adapter()


@openai_router.post(
    "/chat/completions",
    response_model=ChatCompletionResponse,
    response_model_exclude_none=True,
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Execution mode not allowed"},
        422: {"description": "Validation error"},
    },
)
async def chat_completions(
    request: ChatCompletionRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> ChatCompletionResponse | StreamingResponse:
    """Create a chat completion.

    This endpoint processes requests through ELLE's intent classification
    and policy pipeline. It is NOT direct LLM access - every request is
    classified, policy-checked, and then routed to the appropriate handler.

    **Execution Modes:**
    - `elle`: Full execution with policy gates
    - `elle.readonly`: Read-only mode (no mutations)
    - `elle.capabilities_only`: Returns tool_calls without execution

    **Important:** Settings like temperature, system prompt override, and
    custom tools are intentionally NOT exposed. ELLE manages these internally.
    """
    # Map model name to execution mode
    mode = MODEL_TO_MODE.get(request.model)
    if mode is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown model: {request.model}",
        )

    # Verify auth allows this mode
    if not auth.can_use_mode(mode):
        raise HTTPException(
            status_code=403,
            detail=f"Execution mode '{mode.value}' not allowed for this authentication",
        )

    # Get the adapter
    adapter = get_engine_adapter()

    # Handle streaming
    if request.stream:
        return await stream_chat_completion(
            adapter=adapter,
            messages=request.messages,
            mode=mode,
            auth=auth,
            model=request.model,
        )

    # Non-streaming response
    try:
        response = await adapter.process_chat(
            messages=request.messages,
            mode=mode,
            auth=auth,
        )

        return format_completion_response(response, request.model)

    except Exception as e:
        logger.exception("Error processing chat completion")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing request: {e!s}",
        ) from e


@openai_router.get(
    "/models",
    response_model=ModelsListResponse,
)
async def list_models() -> ModelsListResponse:
    """List available models.

    Returns the three ELLE execution modes as models:
    - `elle`: Full execution mode
    - `elle.readonly`: Read-only mode
    - `elle.capabilities_only`: Returns capabilities without execution
    """
    return ModelsListResponse(data=ELLE_MODELS)


@openai_router.get("/models/{model_id}")
async def get_model(model_id: str):
    """Get information about a specific model.

    Args:
        model_id: The model ID to retrieve.

    Returns:
        Model information if found.

    Raises:
        HTTPException: If model not found.
    """
    for model in ELLE_MODELS:
        if model.id == model_id:
            return model

    raise HTTPException(
        status_code=404,
        detail=f"Model not found: {model_id}",
    )
