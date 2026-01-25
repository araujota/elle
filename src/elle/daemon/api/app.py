"""FastAPI application factory for elled REST API.

Creates and configures the FastAPI application with
all routes and middleware, including the OpenAI-compatible
chat completions endpoint.
"""

from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from elle.daemon.api.auth import AuthMiddleware
from elle.daemon.api.engine_adapter import get_adapter
from elle.daemon.api.openai_routes import openai_router, set_adapter
from elle.daemon.api.routes import router, set_daemon
from elle.daemon.api.state_routes import router as state_router
from elle.daemon.api.state_routes import set_state_cache
from elle.daemon.api.vault_routes import router as vault_router
from elle.daemon.api.vault_routes import set_manvault_service

if TYPE_CHECKING:
    from elle.daemon.main import ElledDaemon


def create_app(daemon: "ElledDaemon") -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        daemon: The ElledDaemon instance to query.

    Returns:
        Configured FastAPI application.
    """
    app = FastAPI(
        title="elled API",
        description=(
            "ELLE Daemon REST API for local system intelligence.\n\n"
            "Includes OpenAI-compatible `/v1/chat/completions` endpoint "
            "for programmatic access to ELLE's capabilities."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Configure CORS for local access
    # Allows both GET (status/events) and POST (chat completions)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:*", "http://127.0.0.1:*"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*", "Authorization"],
    )

    # Get session token from daemon's token manager
    session_token = None
    if hasattr(daemon, '_session_token_manager') and daemon._session_token_manager is not None:
        session_token = daemon._session_token_manager.token

    # Set up authentication middleware with session token
    auth_middleware = AuthMiddleware(
        daemon.config.api_auth,
        session_token=session_token,
    )

    @app.middleware("http")
    async def auth_middleware_handler(request: Request, call_next):
        """Middleware to authenticate requests and attach auth context."""
        # Skip auth for docs (local development only) and health endpoints
        # Note: /docs and /redoc are only exposed on localhost
        if request.url.path in ("/docs", "/redoc", "/openapi.json", "/v1/health", "/"):
            return await call_next(request)

        try:
            auth_context = await auth_middleware(request)
            request.state.auth_context = auth_context
        except Exception as e:
            # Re-raise auth errors to reject unauthenticated requests
            from fastapi import HTTPException
            if isinstance(e, HTTPException):
                raise
            # Other errors - let request proceed but log
            import logging
            logging.getLogger(__name__).debug(f"Auth middleware error: {e}")

        return await call_next(request)

    # Set daemon reference for routes
    set_daemon(daemon)

    # Set up state cache for state routes
    if hasattr(daemon, '_state_cache') and daemon._state_cache is not None:
        set_state_cache(daemon._state_cache)

    # Set up Man Vault service for vault routes
    if hasattr(daemon, '_manvault_service') and daemon._manvault_service is not None:
        set_manvault_service(daemon._manvault_service)

    # Set up engine adapter for OpenAI routes
    adapter = get_adapter()
    set_adapter(adapter)

    # Include existing routes
    app.include_router(router)

    # Include state routes
    app.include_router(state_router)

    # Include vault routes
    app.include_router(vault_router)

    # Include OpenAI-compatible routes
    app.include_router(openai_router)

    # Root redirect to docs
    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "message": "elled API - see /docs for documentation",
            "openai_endpoint": "/v1/chat/completions",
            "models_endpoint": "/v1/models",
        }

    return app
