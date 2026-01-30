"""Tests for planner pre-execution validation.

Verifies plan validation against system trends, drift detection,
package and service checks, and the convenience helpers
(get_trend_warnings_for_plan, estimate_success_likelihood).
"""

from __future__ import annotations

from typing import Any

import pytest

from elle.cli.planner.models import (
    CommandPlan,
    PlanContext,
    PlanStep,
    PriorPlanContext,
    RollbackStep,
    TaskRequest,
    TrendContextRef,
    ValidationCheck,
)
from elle.cli.planner.validation import (
    _check_fingerprint_drift,
    _plan_involves_packages,
    _plan_involves_services,
    build_trend_context_ref,
    estimate_success_likelihood,
    get_trend_warnings_for_plan,
    validate_plan_against_trends,
)
from elle.daemon.telemetry.trends import (
    AnomalyResult,
    Forecast,
    PlanValidationResult,
    TrendContext,
    TrendWindow,
    ValidationWarning,
)


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------

def _step(command: str, risk: str = "low", can_fail: bool = False) -> PlanStep:
    """Create a simple PlanStep."""
    return PlanStep(
        command=command,
        explanation=f"Runs {command}",
        risk_level=risk,
        can_fail=can_fail,
    )


def _plan(
    steps: tuple[PlanStep, ...],
    *,
    title: str = "Test plan",
    rollback: tuple[RollbackStep, ...] = (),
    checks: tuple[ValidationCheck, ...] = (),
) -> CommandPlan:
    """Create a CommandPlan with given steps."""
    return CommandPlan(
        title=title,
        explanation="A test plan",
        steps=steps,
        rollback=rollback,
        checks=checks,
    )


def _trend_window(metric: str, current: float) -> TrendWindow:
    """Create a TrendWindow with only the metric name and current value."""
    return TrendWindow(metric=metric, current=current)


def _anomaly(metric: str, value: float, baseline_mean: float = 50.0) -> AnomalyResult:
    """Create an AnomalyResult."""
    return AnomalyResult(
        metric=metric,
        current_value=value,
        is_anomaly=True,
        anomaly_score=4.0,
        baseline_mean=baseline_mean,
        baseline_stddev=5.0,
        deviation_direction="above",
    )


def _forecast(
    metric: str,
    current: float,
    will_cross: bool = False,
    hours_to_threshold: float | None = None,
) -> Forecast:
    """Create a Forecast for testing."""
    return Forecast(
        metric=metric,
        current_value=current,
        predicted_value_24h=current + 5.0,
        predicted_value_7d=current + 20.0,
        will_cross_threshold=will_cross,
        time_to_threshold_hours=hours_to_threshold,
    )


def _trend_context(
    *,
    disk_trends: dict[str, TrendWindow] | None = None,
    memory_trend: TrendWindow | None = None,
    anomalies: tuple[AnomalyResult, ...] = (),
    forecasts: dict[str, Forecast] | None = None,
    has_warnings: bool = False,
    warning_messages: tuple[str, ...] = (),
) -> TrendContext:
    """Build a TrendContext with given parameters."""
    return TrendContext(
        disk_trends=disk_trends or {},
        memory_trend=memory_trend,
        anomalies=anomalies,
        forecasts=forecasts or {},
        has_warnings=has_warnings,
        warning_messages=warning_messages,
    )


def _plan_context(
    *,
    request: str = "test task",
    trend_context: TrendContextRef | None = None,
    prior_plans: tuple[PriorPlanContext, ...] = (),
) -> PlanContext:
    """Build a PlanContext."""
    return PlanContext(
        request=TaskRequest(request=request),
        trend_context=trend_context,
        prior_plans=prior_plans,
    )


# ---------------------------------------------------------------------------
# _plan_involves_packages
# ---------------------------------------------------------------------------


class TestPlanInvolvesPackages:
    """Verify detection of package-related commands in plan steps."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "apt install nginx",
            "apt-get install -y curl",
            "dpkg -i some-package.deb",
            "snap install firefox",
            "pip install requests",
            "npm install express",
        ],
    )
    def test_detects_package_commands(self, cmd: str) -> None:
        plan = _plan(steps=(_step(cmd),))
        assert _plan_involves_packages(plan) is True

    def test_non_package_command(self) -> None:
        plan = _plan(steps=(_step("systemctl restart nginx"),))
        assert _plan_involves_packages(plan) is False

    def test_empty_plan(self) -> None:
        plan = _plan(steps=())
        assert _plan_involves_packages(plan) is False

    def test_step_with_none_command(self) -> None:
        step = PlanStep(
            command=None,
            explanation="capability step",
            risk_level="low",
            capability="package.install",
        )
        plan = _plan(steps=(step,))
        assert _plan_involves_packages(plan) is False

    def test_mixed_steps_one_is_package(self) -> None:
        plan = _plan(steps=(
            _step("echo hello"),
            _step("apt install vim"),
            _step("cat /etc/hosts"),
        ))
        assert _plan_involves_packages(plan) is True


# ---------------------------------------------------------------------------
# _plan_involves_services
# ---------------------------------------------------------------------------


class TestPlanInvolvesServices:
    """Verify detection of service-related commands in plan steps."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "systemctl start nginx",
            "systemctl restart postgresql",
            "service start apache2",
            "service restart mysql",
        ],
    )
    def test_detects_service_commands(self, cmd: str) -> None:
        plan = _plan(steps=(_step(cmd),))
        assert _plan_involves_services(plan) is True

    def test_systemctl_status_not_service_op(self) -> None:
        plan = _plan(steps=(_step("systemctl status nginx"),))
        assert _plan_involves_services(plan) is False

    def test_empty_plan(self) -> None:
        plan = _plan(steps=())
        assert _plan_involves_services(plan) is False


# ---------------------------------------------------------------------------
# validate_plan_against_trends: None trend_context
# ---------------------------------------------------------------------------


class TestValidateNoTrends:
    """When no trend data is available, a sensible fallback is returned."""

    def test_no_trends_returns_likely_success(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        result = validate_plan_against_trends(plan, None)
        assert result.likely_to_succeed is True
        assert result.success_probability == pytest.approx(0.7)
        assert "no_trend_data" in result.checks_performed


# ---------------------------------------------------------------------------
# validate_plan_against_trends: disk checks
# ---------------------------------------------------------------------------


class TestValidateDiskChecks:
    """Plan involving packages is checked against disk usage trends."""

    def test_high_disk_adds_warning(self) -> None:
        plan = _plan(steps=(_step("apt install nginx"),))
        tc = _trend_context(
            disk_trends={"/": _trend_window("disk./.used_pct", 92.0)},
        )
        result = validate_plan_against_trends(plan, tc)
        assert result.has_warnings is True
        assert any("disk" in w.message.lower() for w in result.warnings)
        assert "disk_space_for_packages" in result.checks_performed

    def test_critical_disk_adds_critical_warning(self) -> None:
        plan = _plan(steps=(_step("apt-get install -y gcc"),))
        tc = _trend_context(
            disk_trends={"/": _trend_window("disk./.used_pct", 96.0)},
        )
        result = validate_plan_against_trends(plan, tc)
        assert result.has_critical_warnings is True
        assert any(w.severity == "critical" for w in result.warnings)
        assert result.likely_to_succeed is False

    def test_healthy_disk_no_warning(self) -> None:
        plan = _plan(steps=(_step("apt install vim"),))
        tc = _trend_context(
            disk_trends={"/": _trend_window("disk./.used_pct", 50.0)},
        )
        result = validate_plan_against_trends(plan, tc)
        assert not any("disk" in w.message.lower() for w in result.warnings)

    def test_non_package_plan_skips_disk_check(self) -> None:
        plan = _plan(steps=(_step("echo hello"),))
        tc = _trend_context(
            disk_trends={"/": _trend_window("disk./.used_pct", 99.0)},
        )
        result = validate_plan_against_trends(plan, tc)
        assert "disk_space_for_packages" not in result.checks_performed


# ---------------------------------------------------------------------------
# validate_plan_against_trends: memory checks
# ---------------------------------------------------------------------------


class TestValidateMemoryChecks:
    """Plan involving services is checked against memory trend."""

    def test_high_memory_adds_warning(self) -> None:
        plan = _plan(steps=(_step("systemctl restart nginx"),))
        tc = _trend_context(
            memory_trend=_trend_window("mem.used_pct", 93.0),
        )
        result = validate_plan_against_trends(plan, tc)
        assert result.has_warnings is True
        assert any("memory" in w.message.lower() for w in result.warnings)
        assert "memory_for_services" in result.checks_performed

    def test_ok_memory_no_warning(self) -> None:
        plan = _plan(steps=(_step("systemctl start nginx"),))
        tc = _trend_context(
            memory_trend=_trend_window("mem.used_pct", 60.0),
        )
        result = validate_plan_against_trends(plan, tc)
        assert not any("memory" in w.message.lower() for w in result.warnings)

    def test_no_memory_trend_skips_check(self) -> None:
        plan = _plan(steps=(_step("systemctl restart nginx"),))
        tc = _trend_context(memory_trend=None)
        result = validate_plan_against_trends(plan, tc)
        # Memory check should still be listed because the plan involves services
        assert "memory_for_services" in result.checks_performed


# ---------------------------------------------------------------------------
# validate_plan_against_trends: anomaly checks
# ---------------------------------------------------------------------------


class TestValidateAnomalyChecks:
    """Active anomalies generate info-level warnings."""

    def test_anomalies_produce_warnings(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        tc = _trend_context(
            anomalies=(
                _anomaly("cpu.load_1m", 8.5),
                _anomaly("mem.used_pct", 95.0),
            ),
        )
        result = validate_plan_against_trends(plan, tc)
        assert len(result.warnings) == 2
        assert all(w.severity == "info" for w in result.warnings)
        assert "active_anomalies" in result.checks_performed

    def test_no_anomalies_no_warning(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        tc = _trend_context()
        result = validate_plan_against_trends(plan, tc)
        assert "active_anomalies" not in result.checks_performed


# ---------------------------------------------------------------------------
# validate_plan_against_trends: forecast checks
# ---------------------------------------------------------------------------


class TestValidateForecastChecks:
    """Forecast threshold crossings within 24h generate warnings."""

    def test_imminent_threshold_crossing_warns(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        tc = _trend_context(
            forecasts={
                "disk./.used_pct": _forecast(
                    "disk./.used_pct", 80.0, will_cross=True, hours_to_threshold=12.0
                ),
            },
        )
        result = validate_plan_against_trends(plan, tc)
        assert any("threshold" in w.message.lower() for w in result.warnings)

    def test_far_off_crossing_no_warning(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        tc = _trend_context(
            forecasts={
                "disk./.used_pct": _forecast(
                    "disk./.used_pct", 60.0, will_cross=True, hours_to_threshold=72.0
                ),
            },
        )
        result = validate_plan_against_trends(plan, tc)
        # 72h > 24h, so no warning about threshold
        assert not any("threshold" in w.message.lower() for w in result.warnings)

    def test_no_crossing_no_warning(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        tc = _trend_context(
            forecasts={
                "disk./.used_pct": _forecast("disk./.used_pct", 40.0, will_cross=False),
            },
        )
        result = validate_plan_against_trends(plan, tc)
        assert not any("threshold" in w.message.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# validate_plan_against_trends: success probability
# ---------------------------------------------------------------------------


class TestSuccessProbability:
    """The success probability is adjusted based on warnings and clamped."""

    def test_base_probability_with_no_issues(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        tc = _trend_context()
        result = validate_plan_against_trends(plan, tc)
        assert result.success_probability == pytest.approx(0.9)

    def test_probability_reduced_for_disk_warning(self) -> None:
        plan = _plan(steps=(_step("apt install nginx"),))
        tc = _trend_context(
            disk_trends={"/": _trend_window("disk./.used_pct", 91.0)},
        )
        result = validate_plan_against_trends(plan, tc)
        # Base 0.9 - 0.3 for disk warning = 0.6
        assert result.success_probability == pytest.approx(0.6)

    def test_probability_reduced_for_anomalies(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        tc = _trend_context(anomalies=(_anomaly("cpu.load_1m", 10.0),))
        result = validate_plan_against_trends(plan, tc)
        # Base 0.9 - 0.05 per anomaly = 0.85
        assert result.success_probability == pytest.approx(0.85)

    def test_probability_never_below_minimum(self) -> None:
        plan = _plan(steps=(_step("apt install nginx"),))
        # Create many disk warnings to push probability very low
        tc = _trend_context(
            disk_trends={
                "/": _trend_window("disk./.used_pct", 96.0),
                "/home": _trend_window("disk./home.used_pct", 96.0),
                "/var": _trend_window("disk./var.used_pct", 96.0),
            },
            anomalies=tuple(_anomaly(f"metric_{i}", 99.0) for i in range(10)),
        )
        result = validate_plan_against_trends(plan, tc)
        assert result.success_probability >= 0.1

    def test_probability_never_above_maximum(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        tc = _trend_context()
        result = validate_plan_against_trends(plan, tc)
        assert result.success_probability <= 0.99

    def test_critical_warning_blocks_success(self) -> None:
        plan = _plan(steps=(_step("apt install nginx"),))
        tc = _trend_context(
            disk_trends={"/": _trend_window("disk./.used_pct", 97.0)},
        )
        result = validate_plan_against_trends(plan, tc)
        assert result.has_critical_warnings is True
        assert result.likely_to_succeed is False


# ---------------------------------------------------------------------------
# validate_plan_against_trends: drift detection
# ---------------------------------------------------------------------------


class TestDriftDetection:
    """Test drift detection when prior_fingerprint is supplied."""

    def test_no_prior_fingerprint_no_drift(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        tc = _trend_context()
        result = validate_plan_against_trends(plan, tc, prior_fingerprint=None)
        assert result.drift_detected is False
        assert "drift_detection" not in result.checks_performed

    def test_drift_detected_for_disk_pressure_increase(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        tc = _trend_context(
            disk_trends={"/": _trend_window("disk./.used_pct", 85.0)},
        )
        prior_fp = {"disk_pressure": 0.3}  # Was 30%, now 85%
        result = validate_plan_against_trends(plan, tc, prior_fingerprint=prior_fp)
        assert result.drift_detected is True
        assert "drift_detection" in result.checks_performed
        assert result.drift_details.get("disk_pressure_delta") is not None

    def test_drift_detected_for_memory_pressure_increase(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        tc = _trend_context(
            memory_trend=_trend_window("mem.used_pct", 90.0),
        )
        prior_fp = {"mem_pressure": 0.4}  # Was 40%, now 90%
        result = validate_plan_against_trends(plan, tc, prior_fingerprint=prior_fp)
        assert result.drift_detected is True
        assert "mem_pressure_delta" in result.drift_details

    def test_no_drift_when_values_similar(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        tc = _trend_context(
            disk_trends={"/": _trend_window("disk./.used_pct", 50.0)},
            memory_trend=_trend_window("mem.used_pct", 50.0),
        )
        prior_fp = {"disk_pressure": 0.48, "mem_pressure": 0.48}
        result = validate_plan_against_trends(plan, tc, prior_fingerprint=prior_fp)
        assert result.drift_detected is False


# ---------------------------------------------------------------------------
# _check_fingerprint_drift internal
# ---------------------------------------------------------------------------


class TestCheckFingerprintDrift:
    """Direct tests for the drift detection helper."""

    def test_returns_none_when_no_drift(self) -> None:
        tc = _trend_context(
            disk_trends={"/": _trend_window("disk./.used_pct", 50.0)},
            memory_trend=_trend_window("mem.used_pct", 50.0),
        )
        result = _check_fingerprint_drift(tc, {"disk_pressure": 0.50, "mem_pressure": 0.50})
        assert result is None

    def test_returns_details_when_disk_drift(self) -> None:
        tc = _trend_context(
            disk_trends={"/": _trend_window("disk./.used_pct", 80.0)},
        )
        result = _check_fingerprint_drift(tc, {"disk_pressure": 0.2})
        assert result is not None
        assert "disk_pressure_delta" in result
        assert "warnings" in result
        assert len(result["warnings"]) > 0

    def test_returns_details_when_memory_drift(self) -> None:
        tc = _trend_context(
            memory_trend=_trend_window("mem.used_pct", 85.0),
        )
        result = _check_fingerprint_drift(tc, {"mem_pressure": 0.3})
        assert result is not None
        assert "mem_pressure_delta" in result

    def test_no_memory_trend_no_memory_drift(self) -> None:
        tc = _trend_context(memory_trend=None)
        result = _check_fingerprint_drift(tc, {"mem_pressure": 0.1})
        # No memory trend data, so no memory drift
        assert result is None


# ---------------------------------------------------------------------------
# build_trend_context_ref
# ---------------------------------------------------------------------------


class TestBuildTrendContextRef:
    """Tests for converting TrendContext to lightweight TrendContextRef."""

    def test_none_input_returns_none(self) -> None:
        assert build_trend_context_ref(None) is None

    def test_basic_conversion(self) -> None:
        tc = _trend_context(
            disk_trends={
                "/": _trend_window("disk./.used_pct", 42.5),
                "/home": _trend_window("disk./home.used_pct", 60.0),
            },
            memory_trend=_trend_window("mem.used_pct", 71.0),
            has_warnings=True,
            warning_messages=("Disk approaching threshold",),
            anomalies=(_anomaly("cpu.load_1m", 5.0),),
        )
        ref = build_trend_context_ref(tc)
        assert ref is not None
        assert ref.has_warnings is True
        assert ref.warning_messages == ("Disk approaching threshold",)
        assert ref.disk_usage_pct["/"] == pytest.approx(42.5)
        assert ref.disk_usage_pct["/home"] == pytest.approx(60.0)
        assert ref.memory_usage_pct == pytest.approx(71.0)
        assert ref.active_anomalies == 1

    def test_no_memory_trend(self) -> None:
        tc = _trend_context(memory_trend=None)
        ref = build_trend_context_ref(tc)
        assert ref is not None
        assert ref.memory_usage_pct is None

    def test_empty_disk_trends(self) -> None:
        tc = _trend_context()
        ref = build_trend_context_ref(tc)
        assert ref is not None
        assert ref.disk_usage_pct == {}


# ---------------------------------------------------------------------------
# get_trend_warnings_for_plan
# ---------------------------------------------------------------------------


class TestGetTrendWarningsForPlan:
    """Tests for the convenience warning extraction function."""

    def test_no_trend_context_returns_empty(self) -> None:
        plan = _plan(steps=(_step("apt install nginx"),))
        ctx = _plan_context()
        assert get_trend_warnings_for_plan(plan, ctx) == []

    def test_no_warnings_returns_empty(self) -> None:
        plan = _plan(steps=(_step("apt install nginx"),))
        ctx = _plan_context(
            trend_context=TrendContextRef(has_warnings=False),
        )
        assert get_trend_warnings_for_plan(plan, ctx) == []

    def test_disk_warning_relevant_to_package_plan(self) -> None:
        plan = _plan(steps=(_step("apt install nginx"),))
        ctx = _plan_context(
            trend_context=TrendContextRef(
                has_warnings=True,
                warning_messages=("Disk / at 92% usage",),
            ),
        )
        warnings = get_trend_warnings_for_plan(plan, ctx)
        assert len(warnings) == 1
        assert "Disk" in warnings[0]

    def test_memory_warning_relevant_to_service_plan(self) -> None:
        plan = _plan(steps=(_step("systemctl restart nginx"),))
        ctx = _plan_context(
            trend_context=TrendContextRef(
                has_warnings=True,
                warning_messages=("mem usage at 95%",),
            ),
        )
        warnings = get_trend_warnings_for_plan(plan, ctx)
        assert len(warnings) == 1
        assert "mem" in warnings[0]

    def test_irrelevant_warning_filtered_out(self) -> None:
        plan = _plan(steps=(_step("echo hello"),))
        ctx = _plan_context(
            trend_context=TrendContextRef(
                has_warnings=True,
                warning_messages=("Disk / at 92%",),
            ),
        )
        # Plan doesn't involve packages or services, so disk warning
        # shouldn't match
        warnings = get_trend_warnings_for_plan(plan, ctx)
        assert len(warnings) == 0


# ---------------------------------------------------------------------------
# estimate_success_likelihood
# ---------------------------------------------------------------------------


class TestEstimateSuccessLikelihood:
    """Tests for the quick heuristic success estimator."""

    def test_baseline_no_issues(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        ctx = _plan_context()
        likelihood = estimate_success_likelihood(plan, ctx)
        assert likelihood == pytest.approx(0.8)

    def test_high_risk_steps_reduce_likelihood(self) -> None:
        plan = _plan(steps=(
            _step("dd if=/dev/zero of=/dev/sda", risk="high"),
        ))
        ctx = _plan_context()
        likelihood = estimate_success_likelihood(plan, ctx)
        # Base 0.8 - 0.1 for high risk - 0.1 for no rollback = 0.6
        assert likelihood == pytest.approx(0.6)

    def test_high_risk_with_rollback_less_penalty(self) -> None:
        plan = _plan(
            steps=(_step("netplan apply", risk="high"),),
            rollback=(
                RollbackStep(
                    command="netplan rollback",
                    explanation="Undo network change",
                ),
            ),
        )
        ctx = _plan_context()
        likelihood = estimate_success_likelihood(plan, ctx)
        # Base 0.8 - 0.1 for high risk = 0.7 (no extra penalty because rollback exists)
        assert likelihood == pytest.approx(0.7)

    def test_multiple_high_risk_steps(self) -> None:
        plan = _plan(steps=(
            _step("mkfs.ext4 /dev/sdb1", risk="high"),
            _step("dd if=/dev/zero of=/dev/sdc", risk="high"),
        ))
        ctx = _plan_context()
        likelihood = estimate_success_likelihood(plan, ctx)
        # Base 0.8 - 0.2 for two high risk - 0.1 no rollback = 0.5
        assert likelihood == pytest.approx(0.5)

    def test_trend_warnings_reduce_likelihood(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        ctx = _plan_context(
            trend_context=TrendContextRef(
                has_warnings=True,
                warning_messages=("Disk at 95%", "Memory at 90%"),
            ),
        )
        likelihood = estimate_success_likelihood(plan, ctx)
        # Base 0.8 - 0.1 * 2 warnings = 0.6
        assert likelihood == pytest.approx(0.6)

    def test_anomalies_reduce_likelihood(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        ctx = _plan_context(
            trend_context=TrendContextRef(
                has_warnings=False,
                active_anomalies=3,
            ),
        )
        likelihood = estimate_success_likelihood(plan, ctx)
        # Base 0.8 - 0.05 * 3 anomalies = 0.65
        assert likelihood == pytest.approx(0.65)

    def test_prior_art_boosts_likelihood(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        ctx = _plan_context(
            prior_plans=(
                PriorPlanContext(
                    incident_id="prior-1",
                    title="Similar task",
                    outcome="improved",
                    score=0.8,
                ),
            ),
        )
        likelihood = estimate_success_likelihood(plan, ctx)
        # Base 0.8 + 0.1 for successful prior = 0.9
        assert likelihood == pytest.approx(0.9)

    def test_prior_art_failure_no_boost(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        ctx = _plan_context(
            prior_plans=(
                PriorPlanContext(
                    incident_id="prior-1",
                    title="Failed task",
                    outcome="worse",
                    score=0.8,
                ),
            ),
        )
        likelihood = estimate_success_likelihood(plan, ctx)
        # Base 0.8, no boost because prior was "worse"
        assert likelihood == pytest.approx(0.8)

    def test_likelihood_clamped_at_minimum(self) -> None:
        plan = _plan(steps=(
            _step("cmd", risk="high"),
            _step("cmd", risk="high"),
            _step("cmd", risk="high"),
            _step("cmd", risk="high"),
            _step("cmd", risk="high"),
            _step("cmd", risk="high"),
            _step("cmd", risk="high"),
            _step("cmd", risk="high"),
        ))
        ctx = _plan_context(
            trend_context=TrendContextRef(
                has_warnings=True,
                warning_messages=("w1", "w2", "w3", "w4", "w5"),
                active_anomalies=10,
            ),
        )
        likelihood = estimate_success_likelihood(plan, ctx)
        assert likelihood >= 0.1

    def test_likelihood_clamped_at_maximum(self) -> None:
        plan = _plan(steps=(_step("echo ok"),))
        ctx = _plan_context(
            prior_plans=(
                PriorPlanContext(incident_id="p1", title="t", outcome="improved", score=0.9),
                PriorPlanContext(incident_id="p2", title="t", outcome="success", score=0.9),
            ),
        )
        likelihood = estimate_success_likelihood(plan, ctx)
        assert likelihood <= 0.99


# ---------------------------------------------------------------------------
# Combined scenario tests
# ---------------------------------------------------------------------------


class TestCombinedScenarios:
    """Integration-style tests combining multiple checks."""

    def test_package_plan_with_full_disk_and_anomalies(self) -> None:
        """Package install with critical disk + anomalies should fail validation."""
        plan = _plan(steps=(_step("apt install postgresql"),))
        tc = _trend_context(
            disk_trends={"/": _trend_window("disk./.used_pct", 97.0)},
            anomalies=(_anomaly("io.util_pct", 99.0),),
        )
        result = validate_plan_against_trends(plan, tc)
        assert result.likely_to_succeed is False
        assert result.has_critical_warnings is True
        assert "disk_space_for_packages" in result.checks_performed
        assert "active_anomalies" in result.checks_performed
        assert result.success_probability < 0.6

    def test_service_plan_with_high_memory_and_forecast(self) -> None:
        """Service restart with high memory and an imminent threshold crossing."""
        plan = _plan(steps=(_step("systemctl restart postgres"),))
        tc = _trend_context(
            memory_trend=_trend_window("mem.used_pct", 92.0),
            forecasts={
                "mem.used_pct": _forecast(
                    "mem.used_pct", 92.0, will_cross=True, hours_to_threshold=6.0
                ),
            },
        )
        result = validate_plan_against_trends(plan, tc)
        assert result.has_warnings is True
        assert "memory_for_services" in result.checks_performed

    def test_clean_system_passes_validation(self) -> None:
        """A healthy system should pass with high probability."""
        plan = _plan(steps=(
            _step("apt install nginx"),
            _step("systemctl start nginx"),
        ))
        tc = _trend_context(
            disk_trends={"/": _trend_window("disk./.used_pct", 40.0)},
            memory_trend=_trend_window("mem.used_pct", 55.0),
        )
        result = validate_plan_against_trends(plan, tc)
        assert result.likely_to_succeed is True
        assert result.success_probability >= 0.8
        assert result.has_warnings is False
        assert result.has_critical_warnings is False

    def test_drift_combined_with_disk_warning(self) -> None:
        """Drift detection alongside disk pressure warnings."""
        plan = _plan(steps=(_step("apt install redis"),))
        tc = _trend_context(
            disk_trends={"/": _trend_window("disk./.used_pct", 91.0)},
        )
        prior_fp = {"disk_pressure": 0.3}
        result = validate_plan_against_trends(plan, tc, prior_fingerprint=prior_fp)
        assert result.drift_detected is True
        assert result.has_warnings is True
        assert "drift_detection" in result.checks_performed
        assert "disk_space_for_packages" in result.checks_performed
