"""Pydantic models for REST API requests and responses.

Defines the API contract for elled endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StatusResponse(BaseModel):
    """Response for GET /v1/status."""

    model_config = ConfigDict(frozen=True)

    # Runtime info
    started_at: datetime = Field(description="Daemon start time")
    uptime_sec: int = Field(ge=0, description="Uptime in seconds")
    pid: int = Field(description="Process ID")

    # Component status
    journal_active: bool = Field(default=False)
    kernel_active: bool = Field(default=False)
    probes_active: bool = Field(default=False)
    api_active: bool = Field(default=True)

    # Queue stats
    raw_queue_size: int = Field(ge=0, default=0)
    raw_queue_max: int = Field(ge=0, default=0)
    raw_queue_dropped: int = Field(ge=0, default=0)
    event_queue_size: int = Field(ge=0, default=0)
    event_queue_max: int = Field(ge=0, default=0)
    event_queue_dropped: int = Field(ge=0, default=0)

    # Counters
    events_total: int = Field(ge=0, default=0)
    events_1h: int = Field(ge=0, default=0)
    incidents_total: int = Field(ge=0, default=0)
    incidents_open: int = Field(ge=0, default=0)

    # Health
    healthy: bool = Field(default=True)
    errors: list[str] = Field(default_factory=list)


class EventResponse(BaseModel):
    """Single event in API response."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(description="Event UUID")
    ts: datetime = Field(description="Event timestamp")
    source: str = Field(description="Event source (journal/kernel/probe)")
    severity: str = Field(description="Severity level")
    category: str = Field(description="Event category")
    message: str = Field(description="Event message")
    entity: str | None = Field(default=None, description="Affected entity")
    fingerprint: str | None = Field(default=None, description="Dedup fingerprint")


class EventsResponse(BaseModel):
    """Response for GET /v1/events."""

    model_config = ConfigDict(frozen=True)

    events: list[EventResponse] = Field(description="List of events")
    total: int = Field(ge=0, description="Total matching events")
    limit: int = Field(ge=1, description="Requested limit")
    offset: int = Field(ge=0, description="Requested offset")


class EventsQuery(BaseModel):
    """Query parameters for GET /v1/events."""

    model_config = ConfigDict(frozen=True)

    since: datetime | None = Field(default=None, description="Events after this time")
    until: datetime | None = Field(default=None, description="Events before this time")
    category: str | None = Field(default=None, description="Filter by category")
    severity: str | None = Field(default=None, description="Filter by severity")
    entity: str | None = Field(default=None, description="Filter by entity")
    source: str | None = Field(default=None, description="Filter by source")
    search: str | None = Field(default=None, description="Full-text search query")
    limit: int = Field(default=100, ge=1, le=1000, description="Max results")
    offset: int = Field(default=0, ge=0, description="Pagination offset")


class IncidentSummary(BaseModel):
    """Summary of an incident for list view."""

    model_config = ConfigDict(frozen=True)

    incident_id: str = Field(description="Incident UUID")
    created_at: datetime = Field(description="Creation time")
    updated_at: datetime = Field(description="Last update time")
    domain: str = Field(description="Incident domain")
    severity: str = Field(description="Severity level")
    status: str = Field(description="Current status")
    title: str = Field(description="Incident title")
    outcome: str = Field(default="unknown")
    event_count: int = Field(ge=0, default=0)


class IncidentsResponse(BaseModel):
    """Response for GET /v1/incidents."""

    model_config = ConfigDict(frozen=True)

    incidents: list[IncidentSummary] = Field(description="List of incidents")
    total: int = Field(ge=0, description="Total matching incidents")
    limit: int = Field(ge=1, description="Requested limit")
    offset: int = Field(ge=0, description="Requested offset")


class IncidentsQuery(BaseModel):
    """Query parameters for GET /v1/incidents."""

    model_config = ConfigDict(frozen=True)

    since: datetime | None = Field(default=None, description="Incidents after this time")
    domain: str | None = Field(default=None, description="Filter by domain")
    status: str | None = Field(default=None, description="Filter by status")
    severity: str | None = Field(default=None, description="Filter by severity")
    search: str | None = Field(default=None, description="Full-text search query")
    limit: int = Field(default=100, ge=1, le=1000, description="Max results")
    offset: int = Field(default=0, ge=0, description="Pagination offset")


class ActionResponse(BaseModel):
    """Action taken during incident."""

    model_config = ConfigDict(frozen=True)

    step_index: int = Field(ge=0)
    kind: str = Field(description="Action type")
    command: str | None = Field(default=None)
    exit_code: int | None = Field(default=None)
    success: bool = Field(default=False)
    created_at: datetime


class SnapshotResponse(BaseModel):
    """System snapshot attached to incident."""

    model_config = ConfigDict(frozen=True)

    which: str = Field(description="pre or post")
    collected_at: datetime
    mem_pressure: float = Field(ge=0, le=1, default=0)
    disk_pressure: float = Field(ge=0, le=1, default=0)
    cpu_load: float = Field(ge=0, default=0)


class IncidentDetailResponse(BaseModel):
    """Full incident detail for GET /v1/incident/{id}."""

    model_config = ConfigDict(frozen=True)

    # Identity
    incident_id: str
    created_at: datetime
    updated_at: datetime

    # Classification
    domain: str
    severity: str
    status: str

    # Content
    title: str
    summary: str = ""
    symptoms: list[str] = Field(default_factory=list)
    suspected_causes: list[str] = Field(default_factory=list)
    root_cause: str | None = None

    # Evidence
    event_ids: list[str] = Field(default_factory=list)
    log_snippets: list[str] = Field(default_factory=list)

    # Decision
    decision: dict[str, Any] = Field(default_factory=dict)

    # Outcome
    outcome: str = "unknown"
    verification_steps: list[str] = Field(default_factory=list)
    time_to_mitigate_sec: int | None = None
    time_to_resolve_sec: int | None = None

    # Trigger
    trigger_source: str = "manual"
    trigger_command: str | None = None

    # Related data
    actions: list[ActionResponse] = Field(default_factory=list)
    snapshots: dict[str, SnapshotResponse] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Error response."""

    model_config = ConfigDict(frozen=True)

    error: str = Field(description="Error message")
    detail: str | None = Field(default=None, description="Detailed error info")
