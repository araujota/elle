"""FastAPI application factory for elled REST API.

Creates and configures the FastAPI application with
all routes and middleware.
"""

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from elle.daemon.api.routes import router, set_daemon

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
        description="ELLE Daemon REST API for local system intelligence",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Configure CORS for local access
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:*", "http://127.0.0.1:*"],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    # Set daemon reference for routes
    set_daemon(daemon)

    # Include routes
    app.include_router(router)

    # Root redirect to docs
    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"message": "elled API - see /docs for documentation"}

    return app
