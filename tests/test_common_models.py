"""Tests for shared utility Pydantic models."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from elle.common.models import (
    CommandPlan,
    ExecutionResult,
    TelemetryEvent,
)

# =============================================================================
# TelemetryEvent
# =============================================================================


class TestTelemetryEvent:
    """Tests for TelemetryEvent model."""

    @pytest.fixture()
    def sample_event(self) -> TelemetryEvent:
        return TelemetryEvent(
            ts=datetime(2025, 1, 15, 12, 0, 0),
            source="journal",
            severity="error",
            category="systemd",
            message="nginx.service failed",
            raw={"unit": "nginx.service", "code": "exited"},
        )

    def test_create(self, sample_event: TelemetryEvent) -> None:
        assert sample_event.ts == datetime(2025, 1, 15, 12, 0, 0)
        assert sample_event.source == "journal"
        assert sample_event.severity == "error"
        assert sample_event.category == "systemd"
        assert sample_event.message == "nginx.service failed"
        assert sample_event.raw == {"unit": "nginx.service", "code": "exited"}

    def test_all_sources(self) -> None:
        for source in ("journal", "kernel", "probe"):
            event = TelemetryEvent(
                ts=datetime.now(),
                source=source,
                severity="info",
                category="test",
                message="test",
                raw={},
            )
            assert event.source == source

    def test_all_severities(self) -> None:
        for sev in ("info", "warning", "error", "critical"):
            event = TelemetryEvent(
                ts=datetime.now(),
                source="journal",
                severity=sev,
                category="test",
                message="test",
                raw={},
            )
            assert event.severity == sev

    def test_invalid_source(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryEvent(
                ts=datetime.now(),
                source="syslog",
                severity="info",
                category="test",
                message="test",
                raw={},
            )

    def test_invalid_severity(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryEvent(
                ts=datetime.now(),
                source="journal",
                severity="debug",
                category="test",
                message="test",
                raw={},
            )

    def test_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryEvent(
                ts=datetime.now(),
                source="journal",
            )

    def test_raw_can_be_nested(self) -> None:
        event = TelemetryEvent(
            ts=datetime.now(),
            source="kernel",
            severity="critical",
            category="oom",
            message="Out of memory",
            raw={"process": {"name": "java", "pid": 1234}, "pages": [1, 2, 3]},
        )
        assert event.raw["process"]["name"] == "java"
        assert event.raw["pages"] == [1, 2, 3]

    def test_raw_can_be_empty(self) -> None:
        event = TelemetryEvent(
            ts=datetime.now(),
            source="probe",
            severity="warning",
            category="disk",
            message="Low disk space",
            raw={},
        )
        assert event.raw == {}

    def test_serialization_roundtrip(self, sample_event: TelemetryEvent) -> None:
        data = sample_event.model_dump()
        restored = TelemetryEvent.model_validate(data)
        assert restored.ts == sample_event.ts
        assert restored.source == sample_event.source
        assert restored.severity == sample_event.severity
        assert restored.category == sample_event.category
        assert restored.message == sample_event.message
        assert restored.raw == sample_event.raw

    def test_ts_accepts_string(self) -> None:
        event = TelemetryEvent(
            ts="2025-06-15T12:00:00",
            source="journal",
            severity="info",
            category="test",
            message="test",
            raw={},
        )
        assert isinstance(event.ts, datetime)


# =============================================================================
# CommandPlan
# =============================================================================


class TestCommandPlan:
    """Tests for CommandPlan model."""

    @pytest.fixture()
    def sample_plan(self) -> CommandPlan:
        return CommandPlan(
            explanation="Restart nginx to apply config changes",
            commands=["systemctl restart nginx"],
            checks=["systemctl is-active nginx"],
            rollback=["systemctl restart nginx"],
            risks=["Brief service interruption"],
            requires_privilege=True,
        )

    def test_create(self, sample_plan: CommandPlan) -> None:
        assert sample_plan.explanation == "Restart nginx to apply config changes"
        assert sample_plan.commands == ["systemctl restart nginx"]
        assert sample_plan.checks == ["systemctl is-active nginx"]
        assert sample_plan.rollback == ["systemctl restart nginx"]
        assert sample_plan.risks == ["Brief service interruption"]
        assert sample_plan.requires_privilege is True

    def test_multi_step_plan(self) -> None:
        plan = CommandPlan(
            explanation="Update and restart service",
            commands=[
                "apt update",
                "apt install -y nginx",
                "systemctl restart nginx",
            ],
            checks=[
                "dpkg -l nginx",
                "systemctl is-active nginx",
            ],
            rollback=["apt install -y nginx=1.18.0"],
            risks=["Package may fail to install", "Service downtime"],
            requires_privilege=True,
        )
        assert len(plan.commands) == 3
        assert len(plan.checks) == 2
        assert len(plan.risks) == 2

    def test_no_privilege_plan(self) -> None:
        plan = CommandPlan(
            explanation="Check disk space",
            commands=["df -h"],
            checks=[],
            rollback=[],
            risks=[],
            requires_privilege=False,
        )
        assert plan.requires_privilege is False
        assert plan.commands == ["df -h"]
        assert plan.checks == []
        assert plan.rollback == []
        assert plan.risks == []

    def test_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            CommandPlan(
                explanation="incomplete",
            )

    def test_serialization_roundtrip(self, sample_plan: CommandPlan) -> None:
        data = sample_plan.model_dump()
        restored = CommandPlan.model_validate(data)
        assert restored.explanation == sample_plan.explanation
        assert restored.commands == sample_plan.commands
        assert restored.checks == sample_plan.checks
        assert restored.rollback == sample_plan.rollback
        assert restored.risks == sample_plan.risks
        assert restored.requires_privilege == sample_plan.requires_privilege


# =============================================================================
# ExecutionResult
# =============================================================================


class TestExecutionResult:
    """Tests for ExecutionResult model."""

    def test_success_result(self) -> None:
        result = ExecutionResult(
            success=True,
            output="Service restarted successfully",
        )
        assert result.success is True
        assert result.output == "Service restarted successfully"
        assert result.stderr is None
        assert result.exit_code is None
        assert result.plan is None

    def test_failure_result(self) -> None:
        result = ExecutionResult(
            success=False,
            output="",
            stderr="Permission denied",
            exit_code=1,
        )
        assert result.success is False
        assert result.output == ""
        assert result.stderr == "Permission denied"
        assert result.exit_code == 1

    def test_with_plan(self) -> None:
        plan = CommandPlan(
            explanation="Restart service",
            commands=["systemctl restart nginx"],
            checks=["systemctl is-active nginx"],
            rollback=[],
            risks=["downtime"],
            requires_privilege=True,
        )
        result = ExecutionResult(
            success=True,
            output="done",
            plan=plan,
        )
        assert result.plan is not None
        assert result.plan.explanation == "Restart service"
        assert result.plan.requires_privilege is True

    def test_defaults(self) -> None:
        result = ExecutionResult(success=True, output="ok")
        assert result.stderr is None
        assert result.exit_code is None
        assert result.plan is None

    def test_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionResult(success=True)

    def test_serialization_roundtrip(self) -> None:
        plan = CommandPlan(
            explanation="test",
            commands=["echo hi"],
            checks=[],
            rollback=[],
            risks=[],
            requires_privilege=False,
        )
        result = ExecutionResult(
            success=True,
            output="hi",
            stderr=None,
            exit_code=0,
            plan=plan,
        )
        data = result.model_dump()
        restored = ExecutionResult.model_validate(data)
        assert restored.success == result.success
        assert restored.output == result.output
        assert restored.exit_code == result.exit_code
        assert restored.plan is not None
        assert restored.plan.explanation == "test"

    def test_empty_output(self) -> None:
        result = ExecutionResult(success=True, output="")
        assert result.output == ""

    def test_exit_code_zero(self) -> None:
        result = ExecutionResult(success=True, output="ok", exit_code=0)
        assert result.exit_code == 0

    def test_exit_code_nonzero(self) -> None:
        result = ExecutionResult(
            success=False,
            output="",
            stderr="error",
            exit_code=127,
        )
        assert result.exit_code == 127
