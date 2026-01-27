"""FastAPI REST API for elled daemon.

Provides endpoints for:
- /v1/status - Daemon status and health
- /v1/events - Query telemetry events
- /v1/incidents - Query incidents
- /v1/incident/{id} - Get incident details
- /v1/chat/completions - OpenAI-compatible chat completions
- /v1/models - List available models
"""

from typing import Any

# Lazy imports to avoid requiring fastapi at import time
# when only models are needed


def create_app(*args: Any, **kwargs: Any) -> Any:
    """Create and configure the FastAPI application.

    Lazily imports the actual implementation to avoid
    requiring fastapi when only models are needed.
    """
    from elle.daemon.api.app import create_app as _create_app

    return _create_app(*args, **kwargs)


__all__ = ["create_app"]
