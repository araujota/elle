"""Agentic handler for daemon-detected incidents.

Provides a bridge between the daemon's incident detection system and
the unified agentic execution loop. When critical incidents are detected,
this handler can automatically trigger analysis and remediation.

Architecture:
    IncidentCorrelator (detects incident)
           │
           ▼
    IncidentAgenticHandler.on_incident_created()
           │
           ├── Auto-remediation (if enabled and critical)
           │         │
           │         ▼
           │   AgenticLoop.run_for_incident()
           │
           └── Notification (ntfy, etc.)

Configuration:
    [daemon]
    auto_remediate_critical = false  # Auto-handle critical incidents
    agentic_handler_enabled = true   # Enable the handler

Usage:
    handler = IncidentAgenticHandler()
    await handler.on_incident_created(incident_id)

    # Or manual handling:
    result = await handler.handle_incident(incident_id)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

# Environment variables for configuration
AUTO_REMEDIATE_VAR = "ELLE_AUTO_REMEDIATE_CRITICAL"
AGENTIC_HANDLER_VAR = "ELLE_AGENTIC_HANDLER_ENABLED"


def is_auto_remediate_enabled() -> bool:
    """Check if auto-remediation is enabled."""
    return os.environ.get(AUTO_REMEDIATE_VAR, "").lower() in ("1", "true", "yes")


def is_handler_enabled() -> bool:
    """Check if the agentic handler is enabled."""
    return os.environ.get(AGENTIC_HANDLER_VAR, "").lower() in ("1", "true", "yes")


# =============================================================================
# Models
# =============================================================================


class IncidentHandlingResult(BaseModel):
    """Result from handling an incident through the agentic loop."""

    model_config = ConfigDict(frozen=True)

    incident_id: str = Field(description="ID of the incident that was handled")
    success: bool = Field(description="Whether handling was successful")
    auto_triggered: bool = Field(description="Whether this was auto-triggered by daemon")
    response: str = Field(description="Response from the agentic loop")
    execution_id: str | None = Field(
        default=None,
        description="Audit execution ID for tracing",
    )
    tool_calls_count: int = Field(default=0, description="Number of tool calls made")
    duration_ms: int = Field(default=0, description="Total handling time")
    error: str | None = Field(default=None, description="Error message if failed")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# Handler Implementation
# =============================================================================


class IncidentAgenticHandler:
    """Handles daemon-detected incidents through the agentic loop.

    This handler serves as the bridge between the daemon's incident
    detection system and the unified agentic execution loop.

    Modes:
    1. Manual: User explicitly requests incident handling
    2. Auto: Daemon automatically handles critical incidents

    Usage:
        handler = IncidentAgenticHandler()

        # Manual handling
        result = await handler.handle_incident(incident_id)

        # Called by correlator on new incident
        await handler.on_incident_created(incident_id)
    """

    def __init__(
        self,
        auto_remediate: bool | None = None,
        notify_on_handling: bool = True,
        max_concurrent: int = 2,
    ) -> None:
        """Initialize the handler.

        Args:
            auto_remediate: Whether to auto-handle critical incidents.
                           If None, uses environment variable.
            notify_on_handling: Whether to send notifications when handling.
            max_concurrent: Maximum concurrent incident handlings.
        """
        self.auto_remediate = auto_remediate if auto_remediate is not None else is_auto_remediate_enabled()
        self.notify_on_handling = notify_on_handling
        self.max_concurrent = max_concurrent

        # Track in-progress handlings
        self._in_progress: set[str] = set()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._handling_tasks: set[asyncio.Task[Any]] = set()

    async def handle_incident(
        self,
        incident_id: str,
        *,
        auto_triggered: bool = False,
        stream_callback: Any = None,
        confirm_callback: Any = None,
    ) -> IncidentHandlingResult:
        """Handle a detected incident through the agentic loop.

        Args:
            incident_id: ID of the incident to handle.
            auto_triggered: Whether this was auto-triggered by daemon.
            stream_callback: Optional callback for streaming output.
            confirm_callback: Optional callback for confirming actions.

        Returns:
            IncidentHandlingResult with outcome.
        """
        import time

        start_time = time.time()

        # Prevent duplicate handling
        if incident_id in self._in_progress:
            return IncidentHandlingResult(
                incident_id=incident_id,
                success=False,
                auto_triggered=auto_triggered,
                response="Incident is already being handled.",
                error="duplicate_handling",
            )

        try:
            async with self._semaphore:
                self._in_progress.add(incident_id)

                # Get incident details for logging
                incident_info = await self._get_incident_info(incident_id)
                if incident_info is None:
                    return IncidentHandlingResult(
                        incident_id=incident_id,
                        success=False,
                        auto_triggered=auto_triggered,
                        response=f"Incident not found: {incident_id}",
                        error="not_found",
                    )

                logger.info(
                    f"Handling incident {incident_id[:8]}: {incident_info.get('title', 'Unknown')} "
                    f"(auto={auto_triggered}, severity={incident_info.get('severity', 'unknown')})"
                )

                # Run agentic loop
                from elle.cli.agentic.loop import run_for_incident

                result = await run_for_incident(
                    incident_id,
                    stream_callback=stream_callback,
                    confirm_callback=confirm_callback,
                    auto_triggered=auto_triggered,
                )

                duration_ms = int((time.time() - start_time) * 1000)

                # Send notification if enabled
                if self.notify_on_handling and result.success:
                    await self._send_notification(
                        incident_id,
                        incident_info,
                        result.response,
                        auto_triggered,
                    )

                return IncidentHandlingResult(
                    incident_id=incident_id,
                    success=result.success,
                    auto_triggered=auto_triggered,
                    response=result.response,
                    execution_id=result.execution_id,
                    tool_calls_count=result.tool_call_count,
                    duration_ms=duration_ms,
                    error=result.error,
                )

        except Exception as e:
            logger.exception(f"Failed to handle incident {incident_id}")
            duration_ms = int((time.time() - start_time) * 1000)
            return IncidentHandlingResult(
                incident_id=incident_id,
                success=False,
                auto_triggered=auto_triggered,
                response=f"Error handling incident: {e}",
                duration_ms=duration_ms,
                error=str(e),
            )

        finally:
            self._in_progress.discard(incident_id)

    async def on_incident_created(self, incident_id: str) -> None:
        """Callback when a new incident is created by the correlator.

        This is the entry point from the daemon's incident detection.
        It decides whether to auto-handle based on severity and configuration.

        Args:
            incident_id: ID of the newly created incident.
        """
        if not is_handler_enabled():
            logger.debug(f"Agentic handler disabled, skipping incident {incident_id}")
            return

        try:
            incident_info = await self._get_incident_info(incident_id)
            if incident_info is None:
                logger.warning(f"Incident not found for handling: {incident_id}")
                return

            severity = incident_info.get("severity", "info")
            title = incident_info.get("title", "Unknown")

            logger.debug(f"New incident detected: {incident_id[:8]} - {title} (severity: {severity})")

            # Decide whether to auto-handle
            should_auto_handle = self.auto_remediate and severity in ("critical", "error")

            if should_auto_handle:
                # Bound pending tasks (C8)
                MAX_PENDING_TASKS = 50
                if len(self._handling_tasks) >= MAX_PENDING_TASKS:
                    logger.warning(
                        f"Too many pending handler tasks ({len(self._handling_tasks)}), "
                        f"rejecting incident {incident_id[:8]}"
                    )
                    return

                logger.info(f"Auto-handling critical incident: {incident_id[:8]} - {title}")
                # Run in background to not block the correlator
                task = asyncio.create_task(self.handle_incident(incident_id, auto_triggered=True))
                self._handling_tasks.add(task)

                def _on_task_done(t: asyncio.Task[Any]) -> None:
                    self._handling_tasks.discard(t)
                    if t.cancelled():
                        return
                    exc = t.exception()
                    if exc:
                        logger.error(f"Handler task failed: {exc}")

                task.add_done_callback(_on_task_done)
            else:
                # Just log and notify
                logger.debug(f"Incident logged (no auto-handle): {incident_id[:8]}")
                if self.notify_on_handling:
                    await self._send_incident_notification(incident_id, incident_info)

        except Exception as e:
            logger.warning(f"Error in on_incident_created: {e}")

    async def _get_incident_info(self, incident_id: str) -> dict[str, Any] | None:
        """Get incident details for handling decisions."""
        try:
            from elle.daemon.incidents.store import get_incident

            incident = get_incident(incident_id)
            if incident is None:
                return None

            return {
                "incident_id": incident.incident_id,
                "title": incident.title,
                "summary": incident.summary,
                "domain": incident.domain,
                "severity": incident.severity,
                "status": incident.status,
            }

        except Exception as e:
            logger.warning(f"Failed to get incident info: {e}")
            return None

    async def _send_notification(
        self,
        incident_id: str,
        incident_info: dict[str, Any],
        response: str,
        auto_triggered: bool,
    ) -> None:
        """Send notification about incident handling."""
        try:
            from elle.daemon.notifications import notify

            title = incident_info.get("title", "Unknown Incident")
            severity = incident_info.get("severity", "info")

            message = f"{'Auto-' if auto_triggered else ''}Handled: {title}\n\n{response[:500]}"

            notify(
                title=f"[{severity.upper()}] Incident Handled",
                body=message,
            )

        except Exception as e:
            logger.debug(f"Failed to send notification: {e}")

    async def _send_incident_notification(
        self,
        incident_id: str,
        incident_info: dict[str, Any],
    ) -> None:
        """Send notification about a new incident (without handling)."""
        try:
            from elle.daemon.notifications import notify

            title = incident_info.get("title", "Unknown Incident")
            severity = incident_info.get("severity", "info")
            summary = incident_info.get("summary", "")

            message = f"New incident detected: {title}\n\n{summary[:300]}"

            notify(
                title=f"[{severity.upper()}] {title}",
                body=message,
            )

        except Exception as e:
            logger.debug(f"Failed to send incident notification: {e}")


# =============================================================================
# Module-level handler instance
# =============================================================================

_handler: IncidentAgenticHandler | None = None


def get_agentic_handler() -> IncidentAgenticHandler:
    """Get the shared agentic handler instance.

    Returns:
        IncidentAgenticHandler instance.
    """
    global _handler
    if _handler is None:
        _handler = IncidentAgenticHandler()
    return _handler


def reset_agentic_handler() -> None:
    """Reset the shared handler (for testing)."""
    global _handler
    _handler = None


async def handle_incident(
    incident_id: str,
    *,
    auto_triggered: bool = False,
    stream_callback: Any = None,
    confirm_callback: Any = None,
) -> IncidentHandlingResult:
    """Convenience function to handle an incident.

    Args:
        incident_id: ID of the incident to handle.
        auto_triggered: Whether this was auto-triggered.
        stream_callback: Optional streaming callback.
        confirm_callback: Optional confirmation callback.

    Returns:
        IncidentHandlingResult with outcome.
    """
    handler = get_agentic_handler()
    return await handler.handle_incident(
        incident_id,
        auto_triggered=auto_triggered,
        stream_callback=stream_callback,
        confirm_callback=confirm_callback,
    )
