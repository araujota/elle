"""API route implementations for elled.

Implements:
- GET /v1/status - Daemon status
- GET /v1/events - Query telemetry events
- GET /v1/incidents - Query incidents
- GET /v1/incident/{id} - Get incident details
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from elle.daemon.api.auth import AuthContext, get_auth_context
from elle.daemon.api.models import (
    ActionResponse,
    ErrorResponse,
    EventResponse,
    EventsResponse,
    IncidentDetailResponse,
    IncidentsResponse,
    IncidentSummary,
    SnapshotResponse,
    StatusResponse,
)

if TYPE_CHECKING:
    from elle.daemon.main import ElledDaemon


# Router instance - will be configured with daemon reference
router = APIRouter(prefix="/v1", tags=["v1"])

# Daemon reference set during app creation
_daemon: ElledDaemon | None = None


def set_daemon(daemon: ElledDaemon) -> None:
    """Set the daemon reference for routes.

    Args:
        daemon: The ElledDaemon instance.
    """
    global _daemon
    _daemon = daemon


def get_daemon() -> ElledDaemon:
    """Get the daemon reference.

    Raises:
        HTTPException: If daemon not set.

    Returns:
        The ElledDaemon instance.
    """
    if _daemon is None:
        raise HTTPException(status_code=503, detail="Daemon not initialized")
    return _daemon


@router.get(
    "/status",
    response_model=StatusResponse,
    responses={
        401: {"description": "Authentication required"},
        503: {"model": ErrorResponse},
    },
)
async def get_status(
    auth: AuthContext = Depends(get_auth_context),
) -> StatusResponse:
    """Get daemon status and health information. Requires authentication."""
    _ = auth
    daemon = get_daemon()
    status = daemon.get_status()

    # Count events in last hour
    events_1h = 0
    incidents_open = 0
    try:
        from elle.daemon.telemetry.store import count_events

        since_1h = datetime.now(timezone.utc) - timedelta(hours=1)
        events_1h = count_events(since=since_1h)

        from elle.daemon.incidents.store import list_incidents

        open_incidents = list_incidents(status="open", limit=1000)
        incidents_open = len(open_incidents)
    except Exception:
        pass

    return StatusResponse(
        started_at=status.started_at,
        uptime_sec=status.uptime_sec,
        pid=status.pid,
        journal_active=status.journal_active,
        kernel_active=status.kernel_active,
        probes_active=status.probes_active,
        api_active=status.api_active,
        raw_queue_size=status.raw_queue.size,
        raw_queue_max=status.raw_queue.max_size,
        raw_queue_dropped=status.raw_queue.dropped,
        event_queue_size=status.event_queue.size,
        event_queue_max=status.event_queue.max_size,
        event_queue_dropped=status.event_queue.dropped,
        events_total=status.events_total,
        events_1h=events_1h,
        incidents_total=status.incidents_total,
        incidents_open=incidents_open,
        healthy=status.healthy,
        errors=list(status.errors),
    )


@router.get(
    "/events",
    response_model=EventsResponse,
    responses={
        401: {"description": "Authentication required"},
        503: {"model": ErrorResponse},
    },
)
async def get_events(
    since: datetime | None = Query(None, description="Events after this time"),
    until: datetime | None = Query(None, description="Events before this time"),
    category: str | None = Query(None, description="Filter by category"),
    severity: str | None = Query(None, description="Filter by severity"),
    entity: str | None = Query(None, description="Filter by entity"),
    source: str | None = Query(None, description="Filter by source"),
    search: str | None = Query(None, description="Full-text search"),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    auth: AuthContext = Depends(get_auth_context),
) -> EventsResponse:
    """Query telemetry events with filters. Requires authentication."""
    _ = auth
    get_daemon()  # Verify daemon is running

    try:
        from elle.daemon.telemetry.store import count_events, query_events, search_events

        # Use search or query
        if search:
            events = search_events(search, limit=limit)
            total = len(events)
        else:
            events = query_events(
                since=since,
                until=until,
                category=category,
                severity=severity,
                entity=entity,
                source=source,
                limit=limit,
                offset=offset,
            )
            total = count_events(
                since=since,
                category=category,
                severity=severity,
            )

        # Convert to response models
        event_responses = [
            EventResponse(
                event_id=e.event_id,
                ts=e.ts,
                source=e.source,
                severity=e.severity,
                category=e.category,
                message=e.message,
                entity=e.entity,
                fingerprint=e.fingerprint,
            )
            for e in events
        ]

        return EventsResponse(
            events=event_responses,
            total=total,
            limit=limit,
            offset=offset,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/incidents",
    response_model=IncidentsResponse,
    responses={
        401: {"description": "Authentication required"},
        503: {"model": ErrorResponse},
    },
)
async def get_incidents(
    since: datetime | None = Query(None, description="Incidents after this time"),
    domain: str | None = Query(None, description="Filter by domain"),
    status: str | None = Query(None, description="Filter by status"),
    severity: str | None = Query(None, description="Filter by severity"),
    search: str | None = Query(None, description="Full-text search"),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    auth: AuthContext = Depends(get_auth_context),
) -> IncidentsResponse:
    """Query incidents with filters. Requires authentication."""
    _ = auth
    get_daemon()  # Verify daemon is running

    try:
        from elle.daemon.incidents.store import list_incidents

        # Query incidents
        incidents = list_incidents(
            status=status,
            domain=domain,
            limit=limit,
            offset=offset,
        )

        # Filter by severity if specified
        if severity:
            incidents = [i for i in incidents if i.severity == severity]

        # Filter by since if specified
        if since:
            incidents = [i for i in incidents if i.created_at >= since]

        # Convert to summaries
        summaries = [
            IncidentSummary(
                incident_id=i.incident_id,
                created_at=i.created_at,
                updated_at=i.updated_at,
                domain=i.domain,
                severity=i.severity,
                status=i.status,
                title=i.title,
                outcome=i.outcome,
                event_count=len(i.event_ids),
            )
            for i in incidents
        ]

        return IncidentsResponse(
            incidents=summaries,
            total=len(summaries),
            limit=limit,
            offset=offset,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/incident/{incident_id}",
    response_model=IncidentDetailResponse,
    responses={
        401: {"description": "Authentication required"},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def get_incident(
    incident_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> IncidentDetailResponse:
    """Get full incident details by ID. Requires authentication."""
    _ = auth
    get_daemon()  # Verify daemon is running

    try:
        from elle.daemon.incidents.store import get_actions, get_incident, get_snapshots

        # Get incident
        incident = get_incident(incident_id)
        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")

        # Get actions
        actions = get_actions(incident_id)
        action_responses = [
            ActionResponse(
                step_index=a.step_index,
                kind=a.kind,
                command=a.command,
                exit_code=a.exit_code,
                success=a.success,
                created_at=a.created_at,
            )
            for a in actions
        ]

        # Get snapshots
        snapshots_dict: dict[str, SnapshotResponse] = {}
        snapshots = get_snapshots(incident_id)
        for which, snap in snapshots.items():
            ss = snap.snapshot

            # Calculate pressures
            mem_pressure = 0.0
            if ss.mem_total_mb > 0:
                mem_pressure = 1.0 - (ss.mem_available_mb / ss.mem_total_mb)

            disk_pressure = 0.0
            for disk in ss.disks:
                pct = disk.get("used_pct", 0) / 100
                if pct > disk_pressure:
                    disk_pressure = pct

            snapshots_dict[which] = SnapshotResponse(
                which=which,
                collected_at=ss.collected_at,
                mem_pressure=round(mem_pressure, 3),
                disk_pressure=round(disk_pressure, 3),
                cpu_load=ss.cpu_load[0] if ss.cpu_load else 0,
            )

        return IncidentDetailResponse(
            incident_id=incident.incident_id,
            created_at=incident.created_at,
            updated_at=incident.updated_at,
            domain=incident.domain,
            severity=incident.severity,
            status=incident.status,
            title=incident.title,
            summary=incident.summary,
            symptoms=list(incident.symptoms),
            suspected_causes=list(incident.suspected_causes),
            root_cause=incident.root_cause,
            event_ids=list(incident.event_ids),
            log_snippets=list(incident.log_snippets),
            decision=incident.decision,
            outcome=incident.outcome,
            verification_steps=list(incident.verification_steps),
            time_to_mitigate_sec=incident.time_to_mitigate_sec,
            time_to_resolve_sec=incident.time_to_resolve_sec,
            trigger_source=incident.trigger_source,
            trigger_command=incident.trigger_command,
            actions=action_responses,
            snapshots=snapshots_dict,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/incident/{incident_id}/execute-plan",
    response_model=dict,
    responses={
        401: {"description": "Authentication required"},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def execute_incident_plan(
    incident_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    """Execute a prepared remediation plan for a forecast incident.

    This endpoint triggers execution of a previously generated plan
    (stored in prepared_plan_json). Called when the user clicks
    "Execute Now" on a forecast warning notification.

    Requires authentication.
    """
    _ = auth
    get_daemon()  # Verify daemon is running

    try:
        from elle.daemon.incidents.forecast_handler import execute_prepared_plan

        result = await execute_prepared_plan(incident_id)

        if not result.success and result.error and "No prepared plan" in result.error:
            raise HTTPException(status_code=404, detail=result.error)

        return {
            "incident_id": result.incident_id,
            "executed": result.executed,
            "success": result.success,
            "error": result.error,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Simple health check endpoint."""
    daemon = get_daemon()
    status = daemon.get_status()
    return {
        "status": "healthy" if status.healthy else "unhealthy",
        "uptime_sec": status.uptime_sec,
    }


@router.get(
    "/metrics",
    responses={
        401: {"description": "Authentication required"},
        503: {"model": ErrorResponse},
    },
)
async def get_metrics(
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    """Get observability metrics in JSON format.

    Returns comprehensive metrics including:
    - Event counts and rates
    - Incident statistics and MTTR
    - Capability success rates
    - Forecast health status
    - System health indicators

    Requires authentication.
    """
    _ = auth
    get_daemon()  # Verify daemon is running

    try:
        from elle.daemon.observability.metrics import collect_observability_metrics

        metrics = await collect_observability_metrics()
        return metrics.model_dump(mode="json")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/metrics/prometheus",
    response_class=PlainTextResponse,
    responses={
        401: {"description": "Authentication required"},
        503: {"model": ErrorResponse},
    },
)
async def get_prometheus_metrics(
    auth: AuthContext = Depends(get_auth_context),
) -> Any:
    """Get metrics in Prometheus exposition format.

    Returns metrics formatted for Prometheus scraping:
    - All metrics prefixed with 'elle_'
    - Proper HELP and TYPE annotations
    - Labels for dimensional data

    Requires authentication.
    """
    _ = auth
    get_daemon()  # Verify daemon is running

    try:
        from elle.daemon.observability.metrics import (
            collect_observability_metrics,
            format_prometheus,
        )

        metrics = await collect_observability_metrics()
        prometheus_output = format_prometheus(metrics)

        return PlainTextResponse(
            content=prometheus_output,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
