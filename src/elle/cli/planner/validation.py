"""Pre-execution validation for command plans.

Validates plans against current system trends and state
to predict likelihood of success and generate warnings.
"""

from typing import Any

from elle.cli.planner.models import (
    CommandPlan,
    PlanContext,
    TrendContextRef,
)
from elle.daemon.telemetry.trends import (
    PlanValidationResult,
    TrendContext,
    ValidationWarning,
)


def build_trend_context_ref(trend_context: TrendContext | None) -> TrendContextRef | None:
    """Convert full TrendContext to lightweight reference.

    Args:
        trend_context: Full trend context from aggregator.

    Returns:
        Lightweight reference for plan context.
    """
    if trend_context is None:
        return None

    disk_usage = {}
    for mount, trend in trend_context.disk_trends.items():
        disk_usage[mount] = trend.current

    return TrendContextRef(
        has_warnings=trend_context.has_warnings,
        warning_messages=trend_context.warning_messages,
        disk_usage_pct=disk_usage,
        memory_usage_pct=trend_context.memory_trend.current if trend_context.memory_trend else None,
        active_anomalies=len(trend_context.anomalies),
    )


def validate_plan_against_trends(
    plan: CommandPlan,
    trend_context: TrendContext | None,
    prior_fingerprint: dict[str, Any] | None = None,
) -> PlanValidationResult:
    """Validate a plan against current system trends.

    Checks if the plan is likely to succeed given current system state
    and generates warnings for potential issues.

    Args:
        plan: The command plan to validate.
        trend_context: Current system trends.
        prior_fingerprint: Fingerprint from a successful prior execution.

    Returns:
        PlanValidationResult with warnings and success probability.
    """
    if trend_context is None:
        return PlanValidationResult(
            likely_to_succeed=True,
            success_probability=0.7,
            checks_performed=("no_trend_data",),
        )

    warnings: list[ValidationWarning] = []
    checks: list[str] = []
    success_probability = 0.9

    # Check disk space for package installs
    if _plan_involves_packages(plan):
        checks.append("disk_space_for_packages")
        for mount, trend in trend_context.disk_trends.items():
            if trend.current >= 90:
                warnings.append(ValidationWarning(
                    severity="critical" if trend.current >= 95 else "warning",
                    message=f"Disk {mount} is at {trend.current:.1f}% - package install may fail",
                    metric=f"disk.{mount}.used_pct",
                    suggestion="Free up disk space before installing packages",
                ))
                success_probability -= 0.3

    # Check memory for service starts
    if _plan_involves_services(plan):
        checks.append("memory_for_services")
        if trend_context.memory_trend and trend_context.memory_trend.current >= 90:
            warnings.append(ValidationWarning(
                severity="warning",
                message=f"Memory usage is {trend_context.memory_trend.current:.1f}% - service start may fail",
                metric="mem.used_pct",
                suggestion="Consider stopping unused services first",
            ))
            success_probability -= 0.2

    # Check for active anomalies
    if trend_context.anomalies:
        checks.append("active_anomalies")
        for anomaly in trend_context.anomalies:
            warnings.append(ValidationWarning(
                severity="info",
                message=f"Anomaly detected in {anomaly.metric}: {anomaly.current_value:.1f} "
                        f"(expected ~{anomaly.baseline_mean:.1f})",
                metric=anomaly.metric,
            ))
            success_probability -= 0.05

    # Check for forecasted threshold crossings during execution
    for metric, forecast in trend_context.forecasts.items():
        if forecast.will_cross_threshold:
            checks.append(f"forecast_{metric}")
            hours = forecast.time_to_threshold_hours or 0
            if hours < 24:
                warnings.append(ValidationWarning(
                    severity="warning",
                    message=f"{metric} will reach critical threshold in ~{hours:.0f} hours",
                    metric=metric,
                    suggestion="Consider addressing disk space before proceeding",
                ))
                success_probability -= 0.1

    # Check drift from prior successful execution
    drift_detected = False
    drift_details: dict[str, Any] = {}
    if prior_fingerprint:
        checks.append("drift_detection")
        drift_result = _check_fingerprint_drift(trend_context, prior_fingerprint)
        if drift_result:
            drift_detected = True
            drift_details = drift_result
            for warning in drift_result.get("warnings", []):
                warnings.append(ValidationWarning(
                    severity="info",
                    message=warning,
                ))
                success_probability -= 0.05

    # Clamp probability
    success_probability = max(0.1, min(0.99, success_probability))

    has_critical = any(w.severity == "critical" for w in warnings)

    return PlanValidationResult(
        likely_to_succeed=success_probability >= 0.6 and not has_critical,
        success_probability=success_probability,
        warnings=tuple(warnings),
        has_warnings=len(warnings) > 0,
        has_critical_warnings=has_critical,
        checks_performed=tuple(checks),
        drift_detected=drift_detected,
        drift_details=drift_details,
    )


def _plan_involves_packages(plan: CommandPlan) -> bool:
    """Check if plan involves package operations."""
    package_keywords = ["apt", "apt-get", "dpkg", "snap", "pip install", "npm install"]
    for step in plan.steps:
        if step.command:
            for kw in package_keywords:
                if kw in step.command:
                    return True
    return False


def _plan_involves_services(plan: CommandPlan) -> bool:
    """Check if plan involves service operations."""
    service_keywords = ["systemctl start", "systemctl restart", "service start", "service restart"]
    for step in plan.steps:
        if step.command:
            for kw in service_keywords:
                if kw in step.command:
                    return True
    return False


def _check_fingerprint_drift(
    trend_context: TrendContext,
    prior_fingerprint: dict[str, Any],
) -> dict[str, Any] | None:
    """Check for drift from a prior successful execution.

    Compares current system state to the fingerprint captured
    during a successful prior execution.

    Args:
        trend_context: Current trends.
        prior_fingerprint: Fingerprint from prior success.

    Returns:
        Drift details if drift detected, None otherwise.
    """
    warnings: list[str] = []
    details: dict[str, Any] = {}

    # Compare disk pressure
    prior_disk = prior_fingerprint.get("disk_pressure", 0.0)
    current_disk = max(
        (t.current / 100 for t in trend_context.disk_trends.values()),
        default=0.0,
    )
    disk_delta = current_disk - prior_disk
    if disk_delta > 0.2:
        warnings.append(
            f"Disk pressure increased {disk_delta*100:.0f}% since similar task succeeded"
        )
        details["disk_pressure_delta"] = disk_delta

    # Compare memory pressure
    prior_mem = prior_fingerprint.get("mem_pressure", 0.0)
    current_mem = (
        trend_context.memory_trend.current / 100
        if trend_context.memory_trend else 0.0
    )
    mem_delta = current_mem - prior_mem
    if mem_delta > 0.2:
        warnings.append(
            f"Memory pressure increased {mem_delta*100:.0f}% since similar task succeeded"
        )
        details["mem_pressure_delta"] = mem_delta

    if warnings:
        details["warnings"] = warnings
        return details

    return None


def get_trend_warnings_for_plan(
    plan: CommandPlan,
    context: PlanContext,
) -> list[str]:
    """Get human-readable warnings for a plan based on trends.

    Convenience function for display in the UI.

    Args:
        plan: The command plan.
        context: Planning context including trends.

    Returns:
        List of warning messages.
    """
    if not context.trend_context or not context.trend_context.has_warnings:
        return []

    warnings = []

    # Include trend warnings relevant to the plan
    trend_ref = context.trend_context
    if trend_ref.warning_messages:
        for msg in trend_ref.warning_messages:
            # Check if warning is relevant to plan
            if _plan_involves_packages(plan) and "disk" in msg.lower() or _plan_involves_services(plan) and "mem" in msg.lower():
                warnings.append(msg)

    return warnings


def estimate_success_likelihood(
    plan: CommandPlan,
    context: PlanContext,
) -> float:
    """Estimate the likelihood of plan success.

    Quick heuristic for UI display without full validation.

    Args:
        plan: The command plan.
        context: Planning context.

    Returns:
        Estimated success probability (0.0 - 1.0).
    """
    base = 0.8

    # Reduce for high-risk steps
    high_risk = sum(1 for s in plan.steps if s.risk_level == "high")
    base -= high_risk * 0.1

    # Reduce if no rollback for risky operations
    if not plan.has_rollback and high_risk > 0:
        base -= 0.1

    # Reduce for trend warnings
    if context.trend_context:
        if context.trend_context.has_warnings:
            base -= 0.1 * len(context.trend_context.warning_messages)
        if context.trend_context.active_anomalies > 0:
            base -= 0.05 * context.trend_context.active_anomalies

    # Boost for prior art
    if context.prior_plans:
        successful = sum(
            1 for p in context.prior_plans
            if p.outcome in ("improved", "partial", "success")
        )
        if successful:
            base += 0.1

    return max(0.1, min(0.99, base))
