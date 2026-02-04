"""FastAPI application factory for elled REST API.

Creates and configures the FastAPI application with
all routes and middleware, including the OpenAI-compatible
chat completions endpoint.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

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

logger = logging.getLogger(__name__)


# =============================================================================
# Rate Limiter (M1)
# =============================================================================


class _RateLimiter:
    """Sliding window rate limiter (per-IP, 60 requests/min)."""

    def __init__(self, max_requests: int = 60, window_sec: float = 60.0) -> None:
        self._max_requests = max_requests
        self._window_sec = window_sec
        self._lock = threading.Lock()
        self._requests: dict[str, list[float]] = {}

    def is_allowed(self, client_ip: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window_sec
        with self._lock:
            timestamps = self._requests.get(client_ip, [])
            timestamps = [t for t in timestamps if t > cutoff]
            if not timestamps:
                self._requests.pop(client_ip, None)
            if len(timestamps) >= self._max_requests:
                self._requests[client_ip] = timestamps
                return False
            timestamps.append(now)
            self._requests[client_ip] = timestamps
            # Selective eviction if map grows too large (H12)
            if len(self._requests) > 10_000:
                # First pass: evict IPs with no recent activity
                stale = [
                    ip for ip, ts in self._requests.items() if not ts or ts[-1] < now - self._window_sec
                ]
                for ip in stale:
                    del self._requests[ip]
                # Second pass: if still too large, remove oldest 25%
                if len(self._requests) > 10_000:
                    sorted_ips = sorted(self._requests, key=lambda ip: self._requests[ip][-1])
                    for ip in sorted_ips[: len(sorted_ips) // 4]:
                        del self._requests[ip]
            return True


_rate_limiter = _RateLimiter()


def create_app(daemon: ElledDaemon) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        daemon: The ElledDaemon instance to query.

    Returns:
        Configured FastAPI application.
    """
    # Conditionally disable API docs on non-localhost (M2)
    api_host = daemon.config.api.host if daemon.config else "127.0.0.1"
    is_localhost = api_host in ("127.0.0.1", "localhost", "::1")
    docs_url = "/docs" if is_localhost else None
    redoc_url = "/redoc" if is_localhost else None

    app = FastAPI(
        title="elled API",
        description=(
            "ELLE Daemon REST API for local system intelligence.\n\n"
            "Includes OpenAI-compatible `/v1/chat/completions` endpoint "
            "for programmatic access to ELLE's capabilities."
        ),
        version="0.1.0",
        docs_url=docs_url,
        redoc_url=redoc_url,
    )

    # Configure CORS for local access only — pin to configured port
    api_port = daemon.config.api.port if daemon.config else 8377
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://127.0.0.1:{api_port}",
            f"http://localhost:{api_port}",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Get session token from daemon's token manager
    session_token = None
    if hasattr(daemon, "_session_token_manager") and daemon._session_token_manager is not None:
        session_token = daemon._session_token_manager.token
        if session_token is None:
            logger.warning("Session token is None; API auth may be impaired")

    # Set up authentication middleware with session token
    auth_middleware = AuthMiddleware(
        daemon.config.api_auth,
        session_token=session_token,
    )

    @app.middleware("http")
    async def auth_middleware_handler(request: Request, call_next: Callable[[Request], Any]) -> Response:
        """Middleware to authenticate requests and attach auth context."""
        # Rate limit ALL endpoints including health (M1)
        client_ip = request.client.host if request.client else "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": "60"},
            )

        # Skip auth for health endpoint
        skip_paths = {"/v1/health", "/"}
        if is_localhost:
            skip_paths.update({"/docs", "/redoc", "/openapi.json"})
        if request.url.path in skip_paths:
            response: Response = await call_next(request)
            return response

        try:
            auth_context = await auth_middleware(request)
            request.state.auth_context = auth_context
        except Exception as e:
            from fastapi import HTTPException

            if isinstance(e, HTTPException):
                raise
            # Fail closed: do not let request proceed on auth infrastructure errors
            import logging

            logging.getLogger(__name__).error(f"Auth middleware error: {e}")
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=503,
                content={"detail": "Authentication service unavailable"},
            )

        response = await call_next(request)
        return response

    # Set daemon reference for routes
    set_daemon(daemon)

    # Set up state cache for state routes
    if hasattr(daemon, "_state_cache") and daemon._state_cache is not None:
        set_state_cache(daemon._state_cache)

    # Set up Man Vault service for vault routes
    if hasattr(daemon, "_manvault_service") and daemon._manvault_service is not None:
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
