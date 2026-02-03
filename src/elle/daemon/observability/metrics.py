"""Observability metrics collection for external dashboards.

Collects and formats system observability metrics for:
- JSON API endpoints
- Prometheus format export

Metrics include:
- Event counts and rates
- Incident statistics and MTTR
- Capability success rates
- Forecast health status
- System health indicators
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Metrics Models
# =============================================================================


class ObservabilityMetrics(BaseModel):
    """System observability metrics for external dashboards.

    Aggregates metrics from multiple ELLE subsystems into a single
    snapshot that can be exposed via API or Prometheus format.
    """

    model_config = ConfigDict(frozen=True)

    # Event counts
    total_events_1h: int = Field(
        default=0,
        description="Events received in the last hour",
    )
    total_events_24h: int = Field(
        default=0,
        description="Events received in the last 24 hours",
    )
    events_by_severity: dict[str, int] = Field(
        default_factory=dict,
        description="Event counts by severity level",
    )
    events_by_source: dict[str, int] = Field(
        default_factory=dict,
        description="Event counts by source type",
    )

    # Incident counts
    total_incidents: int = Field(
        default=0,
        description="Total incidents ever created",
    )
    open_incidents: int = Field(
        default=0,
        description="Currently open incidents",
    )
    incidents_by_domain: dict[str, int] = Field(
        default_factory=dict,
        description="Incident counts by domain",
    )
    incidents_by_outcome: dict[str, int] = Field(
        default_factory=dict,
        description="Incident counts by outcome",
    )

    # MTTR (Mean Time To Resolve)
    mttr_overall_sec: float | None = Field(
        default=None,
        description="Overall mean time to resolve in seconds",
    )
    mttr_by_domain: dict[str, float] = Field(
        default_factory=dict,
        description="MTTR by domain in seconds",
    )

    # Capability success rates (from efficacy tables)
    capability_success_rates: dict[str, float] = Field(
        default_factory=dict,
        description="Success rate by capability (0-1)",
    )
    capability_executions_1h: int = Field(
        default=0,
        description="Capability executions in the last hour",
    )

    # Forecast health
    active_warnings: int = Field(
        default=0,
        description="Active 'prepare' urgency forecasts",
    )
    active_criticals: int = Field(
        default=0,
        description="Active 'act_now' urgency forecasts",
    )
    forecasts_by_metric: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Latest forecast state by metric",
    )

    # System health
    telemetryd_healthy: bool = Field(
        default=True,
        description="Whether telemetryd connection is healthy",
    )
    daemon_uptime_sec: float = Field(
        default=0.0,
        description="Daemon uptime in seconds",
    )
    last_event_age_sec: float = Field(
        default=0.0,
        description="Seconds since last event received",
    )

    # Reactive functions
    reactive_functions_enabled: int = Field(
        default=0,
        description="Number of enabled reactive functions",
    )
    reactive_executions_1h: int = Field(
        default=0,
        description="Reactive function executions in the last hour",
    )

    # Cloud submission queue
    cloud_queue_pending: int = Field(
        default=0,
        description="Pending cloud submissions awaiting retry",
    )
    cloud_retries_total: int = Field(
        default=0,
        description="Total retry attempts for cloud submissions",
    )
    cloud_success_total: int = Field(
        default=0,
        description="Successful cloud submission retries",
    )
    cloud_failed_permanent: int = Field(
        default=0,
        description="Permanently failed cloud submissions",
    )
    cloud_healthy: bool = Field(
        default=True,
        description="Whether the cloud endpoint is reachable",
    )

    # Collection metadata
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When these metrics were collected",
    )


# =============================================================================
# Metrics Collection
# =============================================================================


async def collect_observability_metrics() -> ObservabilityMetrics:
    """Collect all observability metrics from ELLE subsystems.

    Aggregates data from:
    - Incident Vault (incidents, MTTR, outcomes)
    - Event database (event counts)
    - Efficacy tables (capability success rates)
    - Trend system (forecast state)
    - Telemetryd watcher (connection health)
    - Reactive store (function counts)

    Returns:
        ObservabilityMetrics snapshot.
    """
    now = datetime.now(timezone.utc)

    # Initialize with defaults
    metrics_data: dict[str, Any] = {
        "collected_at": now,
    }

    # Collect incident statistics
    try:
        incident_stats = await _get_incident_statistics()
        metrics_data.update(incident_stats)
    except Exception as e:
        logger.warning(f"Failed to collect incident stats: {e}")

    # Collect event statistics
    try:
        event_stats = await _get_event_statistics()
        metrics_data.update(event_stats)
    except Exception as e:
        logger.warning(f"Failed to collect event stats: {e}")

    # Collect efficacy statistics
    try:
        efficacy_stats = await _get_efficacy_statistics()
        metrics_data.update(efficacy_stats)
    except Exception as e:
        logger.warning(f"Failed to collect efficacy stats: {e}")

    # Collect forecast statistics
    try:
        forecast_stats = await _get_forecast_statistics()
        metrics_data.update(forecast_stats)
    except Exception as e:
        logger.warning(f"Failed to collect forecast stats: {e}")

    # Collect reactive function statistics
    try:
        reactive_stats = await _get_reactive_statistics()
        metrics_data.update(reactive_stats)
    except Exception as e:
        logger.warning(f"Failed to collect reactive stats: {e}")

    # Collect system health
    try:
        health_stats = await _get_system_health()
        metrics_data.update(health_stats)
    except Exception as e:
        logger.warning(f"Failed to collect health stats: {e}")

    # Collect cloud queue stats
    try:
        cloud_stats = _get_cloud_queue_stats()
        metrics_data.update(cloud_stats)
    except Exception as e:
        logger.warning(f"Failed to collect cloud queue stats: {e}")

    return ObservabilityMetrics(**metrics_data)


async def _get_incident_statistics() -> dict[str, Any]:
    """Get incident-related statistics."""

    def _query() -> dict[str, Any]:
        stats: dict[str, Any] = {
            "total_incidents": 0,
            "open_incidents": 0,
            "incidents_by_domain": {},
            "incidents_by_outcome": {},
            "mttr_overall_sec": None,
            "mttr_by_domain": {},
        }
        from elle.storage.engine import get_conn

        with get_conn(schema="incidents") as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt FROM incidents")
            total_row = cursor.fetchone()
            stats["total_incidents"] = total_row["cnt"] if total_row else 0

            cursor.execute("SELECT COUNT(*) AS cnt FROM incidents WHERE status = 'open'")
            open_row = cursor.fetchone()
            stats["open_incidents"] = open_row["cnt"] if open_row else 0

            cursor.execute("SELECT domain, COUNT(*) AS cnt FROM incidents GROUP BY domain")
            stats["incidents_by_domain"] = {row["domain"]: row["cnt"] for row in cursor.fetchall()}

            cursor.execute("SELECT outcome, COUNT(*) AS cnt FROM incidents GROUP BY outcome")
            stats["incidents_by_outcome"] = {row["outcome"]: row["cnt"] for row in cursor.fetchall()}

            cursor.execute(
                "SELECT AVG(time_to_resolve_sec) AS avg_ttr FROM incidents "
                "WHERE status = 'resolved' AND time_to_resolve_sec IS NOT NULL"
            )
            row = cursor.fetchone()
            if row and row["avg_ttr"]:
                stats["mttr_overall_sec"] = float(row["avg_ttr"])

            cursor.execute(
                "SELECT domain, AVG(time_to_resolve_sec) AS avg_ttr FROM incidents "
                "WHERE status = 'resolved' AND time_to_resolve_sec IS NOT NULL "
                "GROUP BY domain"
            )
            stats["mttr_by_domain"] = {
                row["domain"]: float(row["avg_ttr"]) for row in cursor.fetchall() if row["avg_ttr"]
            }
        return stats

    try:
        return await asyncio.to_thread(_query)
    except Exception as e:
        logger.debug(f"Incident stats error: {e}")
        return {
            "total_incidents": 0,
            "open_incidents": 0,
            "incidents_by_domain": {},
            "incidents_by_outcome": {},
            "mttr_overall_sec": None,
            "mttr_by_domain": {},
        }


async def _get_event_statistics() -> dict[str, Any]:
    """Get event-related statistics."""

    def _query() -> dict[str, Any]:
        stats: dict[str, Any] = {
            "total_events_1h": 0,
            "total_events_24h": 0,
            "events_by_severity": {},
            "events_by_source": {},
        }
        from elle.storage.engine import get_conn

        with get_conn(schema="telemetry") as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc)
            hour_ago = (now - timedelta(hours=1)).isoformat()
            day_ago = (now - timedelta(hours=24)).isoformat()

            cursor.execute("SELECT COUNT(*) AS cnt FROM events WHERE ts >= %s", (hour_ago,))
            row = cursor.fetchone()
            stats["total_events_1h"] = row["cnt"] if row else 0

            cursor.execute("SELECT COUNT(*) AS cnt FROM events WHERE ts >= %s", (day_ago,))
            row = cursor.fetchone()
            stats["total_events_24h"] = row["cnt"] if row else 0

            cursor.execute(
                "SELECT severity, COUNT(*) AS cnt FROM events WHERE ts >= %s GROUP BY severity",
                (day_ago,),
            )
            stats["events_by_severity"] = {row["severity"]: row["cnt"] for row in cursor.fetchall()}

            cursor.execute(
                "SELECT source, COUNT(*) AS cnt FROM events WHERE ts >= %s GROUP BY source",
                (day_ago,),
            )
            stats["events_by_source"] = {row["source"]: row["cnt"] for row in cursor.fetchall()}
        return stats

    try:
        return await asyncio.to_thread(_query)
    except Exception as e:
        logger.debug(f"Event stats error: {e}")
        return {
            "total_events_1h": 0,
            "total_events_24h": 0,
            "events_by_severity": {},
            "events_by_source": {},
        }


async def _get_efficacy_statistics() -> dict[str, Any]:
    """Get capability efficacy statistics."""

    def _query() -> dict[str, Any]:
        stats: dict[str, Any] = {
            "capability_success_rates": {},
            "capability_executions_1h": 0,
        }
        from elle.storage.engine import get_conn

        with get_conn(schema="incidents") as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT approach_signature, success_rate FROM solution_approach_efficacy WHERE total_uses > 0"
            )
            stats["capability_success_rates"] = {
                row["approach_signature"]: float(row["success_rate"]) for row in cursor.fetchall()
            }

            hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM incident_actions WHERE created_at >= %s AND kind = 'capability'",
                (hour_ago,),
            )
            row = cursor.fetchone()
            stats["capability_executions_1h"] = row["cnt"] if row else 0
        return stats

    try:
        return await asyncio.to_thread(_query)
    except Exception as e:
        logger.debug(f"Efficacy stats error: {e}")
        return {"capability_success_rates": {}, "capability_executions_1h": 0}


async def _get_forecast_statistics() -> dict[str, Any]:
    """Get forecast-related statistics."""

    def _query() -> dict[str, Any]:
        stats: dict[str, Any] = {
            "active_warnings": 0,
            "active_criticals": 0,
            "forecasts_by_metric": {},
        }
        from elle.storage.engine import get_conn

        with get_conn(schema="incidents") as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM incidents WHERE forecast_urgency = 'prepare' AND status = 'open'"
            )
            row = cursor.fetchone()
            stats["active_warnings"] = row["cnt"] if row else 0

            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM incidents WHERE forecast_urgency = 'act_now' AND status = 'open'"
            )
            row = cursor.fetchone()
            stats["active_criticals"] = row["cnt"] if row else 0
        return stats

    try:
        return await asyncio.to_thread(_query)
    except Exception as e:
        logger.debug(f"Forecast stats error: {e}")
        return {"active_warnings": 0, "active_criticals": 0, "forecasts_by_metric": {}}


async def _get_reactive_statistics() -> dict[str, Any]:
    """Get reactive function statistics."""
    stats: dict[str, Any] = {
        "reactive_functions_enabled": 0,
        "reactive_executions_1h": 0,
    }

    try:
        from elle.reactive.store import get_execution_count, get_function_count

        stats["reactive_functions_enabled"] = get_function_count(enabled_only=True)

        hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        stats["reactive_executions_1h"] = get_execution_count(since=hour_ago)

    except Exception as e:
        logger.debug(f"Reactive stats error: {e}")

    return stats


def _get_cloud_queue_stats() -> dict[str, Any]:
    """Get cloud submission queue statistics."""
    stats: dict[str, Any] = {
        "cloud_queue_pending": 0,
        "cloud_retries_total": 0,
        "cloud_success_total": 0,
        "cloud_failed_permanent": 0,
        "cloud_healthy": True,
    }

    try:
        from elle.daemon.incidents.cloud_queue import get_cloud_queue

        queue = get_cloud_queue()
        metrics = queue.get_metrics()
        stats["cloud_queue_pending"] = metrics.pending_count
        stats["cloud_retries_total"] = metrics.total_retries
        stats["cloud_success_total"] = metrics.succeeded_count
        stats["cloud_failed_permanent"] = metrics.failed_permanent_count

    except RuntimeError:
        # Queue not configured
        pass
    except Exception as e:
        logger.debug(f"Cloud queue stats error: {e}")

    try:
        from elle.daemon.incidents.cloud_retry_worker import get_cloud_retry_worker

        worker = get_cloud_retry_worker()
        stats["cloud_healthy"] = worker.stats.cloud_healthy

    except Exception as e:
        logger.debug(f"Cloud worker stats error: {e}")

    return stats


async def _get_system_health() -> dict[str, Any]:
    """Get system health indicators from the running daemon."""
    stats: dict[str, Any] = {
        "telemetryd_healthy": False,
        "daemon_uptime_sec": 0.0,
        "last_event_age_sec": -1.0,
    }

    try:
        from elle.daemon.api.routes import get_daemon

        daemon = get_daemon()
        status = daemon.get_status()
        stats["telemetryd_healthy"] = status.healthy
        stats["daemon_uptime_sec"] = float(status.uptime_sec)
    except Exception as e:
        logger.debug(f"Could not query daemon for health: {e}")

    return stats


# =============================================================================
# Prometheus Format
# =============================================================================


def format_prometheus(metrics: ObservabilityMetrics) -> str:
    """Format metrics in Prometheus exposition format.

    Args:
        metrics: ObservabilityMetrics to format.

    Returns:
        Prometheus format string.
    """
    lines = []

    # Helper to add metric
    def add_metric(
        name: str,
        value: float | int,
        help_text: str,
        metric_type: str = "gauge",
        labels: dict[str, str] | None = None,
    ) -> None:
        lines.append(f"# HELP elle_{name} {help_text}")
        lines.append(f"# TYPE elle_{name} {metric_type}")
        if labels:
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"elle_{name}{{{label_str}}} {value}")
        else:
            lines.append(f"elle_{name} {value}")

    # Event metrics
    add_metric(
        "events_total_1h",
        metrics.total_events_1h,
        "Events received in the last hour",
        "gauge",
    )
    add_metric(
        "events_total_24h",
        metrics.total_events_24h,
        "Events received in the last 24 hours",
        "gauge",
    )

    for severity, count in metrics.events_by_severity.items():
        add_metric(
            "events_by_severity",
            count,
            "Events by severity",
            "gauge",
            {"severity": severity},
        )

    # Incident metrics
    add_metric(
        "incidents_total",
        metrics.total_incidents,
        "Total incidents created",
        "counter",
    )
    add_metric(
        "incidents_open",
        metrics.open_incidents,
        "Currently open incidents",
        "gauge",
    )

    for domain, count in metrics.incidents_by_domain.items():
        add_metric(
            "incidents_by_domain",
            count,
            "Incidents by domain",
            "gauge",
            {"domain": domain},
        )

    for outcome, count in metrics.incidents_by_outcome.items():
        add_metric(
            "incidents_by_outcome",
            count,
            "Incidents by outcome",
            "gauge",
            {"outcome": outcome},
        )

    # MTTR
    if metrics.mttr_overall_sec is not None:
        add_metric(
            "mttr_seconds",
            metrics.mttr_overall_sec,
            "Mean time to resolve in seconds",
            "gauge",
        )

    for domain, mttr in metrics.mttr_by_domain.items():
        add_metric(
            "mttr_by_domain_seconds",
            mttr,
            "MTTR by domain in seconds",
            "gauge",
            {"domain": domain},
        )

    # Capability metrics
    add_metric(
        "capability_executions_1h",
        metrics.capability_executions_1h,
        "Capability executions in the last hour",
        "gauge",
    )

    for capability, rate in metrics.capability_success_rates.items():
        add_metric(
            "capability_success_rate",
            rate,
            "Capability success rate",
            "gauge",
            {"capability": capability},
        )

    # Forecast metrics
    add_metric(
        "forecasts_active_warnings",
        metrics.active_warnings,
        "Active warning-level forecasts",
        "gauge",
    )
    add_metric(
        "forecasts_active_criticals",
        metrics.active_criticals,
        "Active critical-level forecasts",
        "gauge",
    )

    # System health
    add_metric(
        "telemetryd_healthy",
        1 if metrics.telemetryd_healthy else 0,
        "Whether telemetryd connection is healthy",
        "gauge",
    )
    add_metric(
        "daemon_uptime_seconds",
        metrics.daemon_uptime_sec,
        "Daemon uptime in seconds",
        "gauge",
    )
    add_metric(
        "last_event_age_seconds",
        metrics.last_event_age_sec,
        "Seconds since last event received",
        "gauge",
    )

    # Reactive function metrics
    add_metric(
        "reactive_functions_enabled",
        metrics.reactive_functions_enabled,
        "Number of enabled reactive functions",
        "gauge",
    )
    add_metric(
        "reactive_executions_1h",
        metrics.reactive_executions_1h,
        "Reactive function executions in the last hour",
        "gauge",
    )

    # Cloud submission queue metrics
    add_metric(
        "cloud_queue_pending",
        metrics.cloud_queue_pending,
        "Pending cloud submissions awaiting retry",
        "gauge",
    )
    add_metric(
        "cloud_retries_total",
        metrics.cloud_retries_total,
        "Total retry attempts for cloud submissions",
        "counter",
    )
    add_metric(
        "cloud_success_total",
        metrics.cloud_success_total,
        "Successful cloud submission retries",
        "counter",
    )
    add_metric(
        "cloud_failed_permanent",
        metrics.cloud_failed_permanent,
        "Permanently failed cloud submissions",
        "counter",
    )
    add_metric(
        "cloud_healthy",
        1 if metrics.cloud_healthy else 0,
        "Whether the cloud endpoint is reachable",
        "gauge",
    )

    return "\n".join(lines) + "\n"
