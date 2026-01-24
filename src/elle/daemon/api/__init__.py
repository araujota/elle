"""FastAPI REST API for elled daemon.

Provides endpoints for:
- /v1/status - Daemon status and health
- /v1/events - Query telemetry events
- /v1/incidents - Query incidents
- /v1/incident/{id} - Get incident details
"""

from elle.daemon.api.app import create_app

__all__ = ["create_app"]
