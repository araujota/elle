"""Service management capabilities.

Provides typed, policy-governed operations for systemd service management:
- service.restart - Restart a service
- service.start - Start a service
- service.stop - Stop a service
- service.status - Check service status
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from elle.capabilities.models import (
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    RiskLevel,
    RollbackResult,
    SideEffect,
    VerificationResult,
)
from elle.capabilities.protocol import BaseCapability

logger = logging.getLogger(__name__)


# =============================================================================
# Input/Output Models
# =============================================================================


class ServiceInput(BaseModel):
    """Input for service operations."""

    model_config = ConfigDict(frozen=True)

    service: str = Field(
        description="Service name (e.g., 'nginx', 'ssh')",
        min_length=1,
    )
    timeout_sec: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Timeout for the operation in seconds",
    )


class ServiceOutput(BaseModel):
    """Output from service operations."""

    model_config = ConfigDict(frozen=True)

    service: str = Field(description="Service name")
    previous_state: str = Field(description="State before operation")
    new_state: str = Field(description="State after operation")
    pid: int | None = Field(default=None, description="Main process PID if running")


class ServiceStatusOutput(BaseModel):
    """Output from service status check."""

    model_config = ConfigDict(frozen=True)

    service: str = Field(description="Service name")
    active_state: str = Field(description="Active state (active, inactive, failed)")
    sub_state: str = Field(default="", description="Sub state (running, dead, etc.)")
    pid: int | None = Field(default=None, description="Main process PID")
    description: str = Field(default="", description="Service description")
    loaded: bool = Field(default=True, description="Whether unit file is loaded")


# =============================================================================
# Helper Functions
# =============================================================================


def _run_systemctl(
    action: str,
    service: str,
    timeout: int = 30,
) -> tuple[bool, str, str, int]:
    """Run a systemctl command.

    Args:
        action: The systemctl action (start, stop, restart, status).
        service: The service name.
        timeout: Command timeout in seconds.

    Returns:
        Tuple of (success, stdout, stderr, exit_code).
    """
    from elle.cli.subprocess_runner import RunMode, run_safe

    command = f"systemctl {action} {service}"
    result = run_safe(
        command,
        timeout=float(timeout),
        mode=RunMode.CAPTURE,
        check_denylist_flag=False,  # Policy already checked at capability level
    )

    return (result.success, result.stdout, result.stderr, result.exit_code)


def _get_service_state(service: str) -> tuple[str, str, int | None]:
    """Get current service state.

    Args:
        service: The service name.

    Returns:
        Tuple of (active_state, sub_state, pid).
    """
    from elle.cli.subprocess_runner import RunMode, run_safe

    # Get active state
    result = run_safe(
        f"systemctl is-active {service}",
        timeout=10.0,
        mode=RunMode.CAPTURE,
        check_denylist_flag=False,
    )
    active_state = result.stdout.strip() if result.stdout else "unknown"

    # Get detailed state
    result = run_safe(
        f"systemctl show {service} --property=SubState,MainPID",
        timeout=10.0,
        mode=RunMode.CAPTURE,
        check_denylist_flag=False,
    )

    sub_state = ""
    pid = None

    if result.success and result.stdout:
        for line in result.stdout.strip().split("\n"):
            if "=" in line:
                key, value = line.split("=", 1)
                if key == "SubState":
                    sub_state = value
                elif key == "MainPID":
                    try:
                        pid = int(value)
                        if pid == 0:
                            pid = None
                    except ValueError:
                        pass

    return (active_state, sub_state, pid)


# =============================================================================
# Service Restart Capability
# =============================================================================


class ServiceRestartCapability(BaseCapability):
    """Restart a systemd service."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="service.restart",
            summary="Restart a systemd service",
            domain="service",
            risk="medium",
            side_effects=(
                SideEffect(
                    kind="service_change",
                    target="{service}",
                    reversible=True,
                    description="Service will be restarted",
                ),
            ),
            requires_privilege=True,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="ServiceInput",
            output_schema_name="ServiceOutput",
        )

    def dry_run(self, input: ServiceInput) -> DryRunResult:
        """Preview service restart."""
        # Check current state
        active_state, sub_state, pid = _get_service_state(input.service)

        return DryRunResult(
            would_execute=(f"systemctl restart {input.service}",),
            would_modify=(f"{input.service}.service",),
            estimated_risk="medium",
            requires_confirmation=True,
            preview_text=(
                f"Would restart {input.service} "
                f"(currently: {active_state}/{sub_state}, PID: {pid or 'N/A'})"
            ),
            diff=None,
            is_valid=True,
        )

    def run(self, input: ServiceInput) -> CapabilityResult:
        """Restart the service."""
        start_time = time.time()

        # Get current state
        prev_state, prev_sub, _ = _get_service_state(input.service)

        # Run restart
        success, stdout, stderr, exit_code = _run_systemctl(
            "restart",
            input.service,
            input.timeout_sec,
        )

        # Get new state
        new_state, new_sub, new_pid = _get_service_state(input.service)

        execution_time = int((time.time() - start_time) * 1000)

        if success:
            return CapabilityResult(
                success=True,
                output=ServiceOutput(
                    service=input.service,
                    previous_state=f"{prev_state}/{prev_sub}",
                    new_state=f"{new_state}/{new_sub}",
                    pid=new_pid,
                ),
                error=None,
                warnings=(),
                side_effects_applied=self.spec.side_effects,
                execution_time_ms=execution_time,
                evidence=CapabilityEvidence(
                    commands_executed=(f"systemctl restart {input.service}",),
                    files_modified=(),
                    verification_passed=False,
                    verification_details="",
                    man_vault_citations=("systemctl(1)",),
                    rationale=f"Restarted {input.service} as requested",
                ),
            )
        else:
            return CapabilityResult(
                success=False,
                output=None,
                error=stderr or f"systemctl restart failed with exit code {exit_code}",
                warnings=(),
                side_effects_applied=(),
                execution_time_ms=execution_time,
                evidence=CapabilityEvidence(
                    commands_executed=(f"systemctl restart {input.service}",),
                    rationale=f"Failed to restart {input.service}",
                ),
            )

    def verify(self, input: ServiceInput) -> VerificationResult:
        """Verify service is running after restart."""
        active_state, sub_state, pid = _get_service_state(input.service)

        passed = active_state == "active"
        discrepancies = ()

        if not passed:
            discrepancies = (f"Service not active: {active_state}/{sub_state}",)

        return VerificationResult(
            passed=passed,
            checks_performed=(f"systemctl is-active {input.service}",),
            actual_state={
                "active_state": active_state,
                "sub_state": sub_state,
                "pid": pid,
            },
            expected_state={
                "active_state": "active",
            },
            discrepancies=discrepancies,
        )

    def rollback(
        self,
        input: ServiceInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback by restarting again (idempotent)."""
        # For restart, rollback is just another restart
        success, _, stderr, _ = _run_systemctl(
            "restart",
            input.service,
            input.timeout_sec,
        )

        return RollbackResult(
            success=success,
            error=stderr if not success else None,
            rolled_back=(f"{input.service}.service",) if success else (),
            failed_to_rollback=() if success else (f"{input.service}.service",),
            commands_executed=(f"systemctl restart {input.service}",),
        )


# =============================================================================
# Service Start Capability
# =============================================================================


class ServiceStartCapability(BaseCapability):
    """Start a systemd service."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="service.start",
            summary="Start a systemd service",
            domain="service",
            risk="low",
            side_effects=(
                SideEffect(
                    kind="service_change",
                    target="{service}",
                    reversible=True,
                    description="Service will be started",
                ),
            ),
            requires_privilege=True,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="ServiceInput",
            output_schema_name="ServiceOutput",
        )

    def dry_run(self, input: ServiceInput) -> DryRunResult:
        """Preview service start."""
        active_state, sub_state, pid = _get_service_state(input.service)

        if active_state == "active":
            return DryRunResult(
                would_execute=(),
                would_modify=(),
                estimated_risk="none",
                requires_confirmation=False,
                preview_text=f"Service {input.service} is already active",
                is_valid=True,
            )

        return DryRunResult(
            would_execute=(f"systemctl start {input.service}",),
            would_modify=(f"{input.service}.service",),
            estimated_risk="low",
            requires_confirmation=False,
            preview_text=f"Would start {input.service} (currently: {active_state})",
            is_valid=True,
        )

    def run(self, input: ServiceInput) -> CapabilityResult:
        """Start the service."""
        start_time = time.time()

        prev_state, _, _ = _get_service_state(input.service)

        success, stdout, stderr, exit_code = _run_systemctl(
            "start",
            input.service,
            input.timeout_sec,
        )

        new_state, new_sub, new_pid = _get_service_state(input.service)

        execution_time = int((time.time() - start_time) * 1000)

        if success:
            return CapabilityResult(
                success=True,
                output=ServiceOutput(
                    service=input.service,
                    previous_state=prev_state,
                    new_state=new_state,
                    pid=new_pid,
                ),
                side_effects_applied=self.spec.side_effects,
                execution_time_ms=execution_time,
                evidence=CapabilityEvidence(
                    commands_executed=(f"systemctl start {input.service}",),
                    man_vault_citations=("systemctl(1)",),
                    rationale=f"Started {input.service}",
                ),
            )
        else:
            return CapabilityResult(
                success=False,
                error=stderr or f"systemctl start failed with exit code {exit_code}",
                execution_time_ms=execution_time,
                evidence=CapabilityEvidence(
                    commands_executed=(f"systemctl start {input.service}",),
                    rationale=f"Failed to start {input.service}",
                ),
            )

    def verify(self, input: ServiceInput) -> VerificationResult:
        """Verify service is running."""
        active_state, sub_state, pid = _get_service_state(input.service)

        passed = active_state == "active"

        return VerificationResult(
            passed=passed,
            checks_performed=(f"systemctl is-active {input.service}",),
            actual_state={"active_state": active_state},
            expected_state={"active_state": "active"},
            discrepancies=()
            if passed
            else (f"Service not active: {active_state}",),
        )

    def rollback(
        self,
        input: ServiceInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback by stopping the service."""
        success, _, stderr, _ = _run_systemctl("stop", input.service, input.timeout_sec)

        return RollbackResult(
            success=success,
            error=stderr if not success else None,
            rolled_back=(f"{input.service}.service",) if success else (),
            failed_to_rollback=() if success else (f"{input.service}.service",),
            commands_executed=(f"systemctl stop {input.service}",),
        )


# =============================================================================
# Service Stop Capability
# =============================================================================


class ServiceStopCapability(BaseCapability):
    """Stop a systemd service."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="service.stop",
            summary="Stop a systemd service",
            domain="service",
            risk="medium",
            side_effects=(
                SideEffect(
                    kind="service_change",
                    target="{service}",
                    reversible=True,
                    description="Service will be stopped",
                ),
            ),
            requires_privilege=True,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="ServiceInput",
            output_schema_name="ServiceOutput",
        )

    def dry_run(self, input: ServiceInput) -> DryRunResult:
        """Preview service stop."""
        active_state, sub_state, pid = _get_service_state(input.service)

        if active_state != "active":
            return DryRunResult(
                would_execute=(),
                would_modify=(),
                estimated_risk="none",
                requires_confirmation=False,
                preview_text=f"Service {input.service} is not running ({active_state})",
                is_valid=True,
            )

        return DryRunResult(
            would_execute=(f"systemctl stop {input.service}",),
            would_modify=(f"{input.service}.service",),
            estimated_risk="medium",
            requires_confirmation=True,
            preview_text=f"Would stop {input.service} (PID: {pid or 'N/A'})",
            is_valid=True,
        )

    def run(self, input: ServiceInput) -> CapabilityResult:
        """Stop the service."""
        start_time = time.time()

        prev_state, _, _ = _get_service_state(input.service)

        success, stdout, stderr, exit_code = _run_systemctl(
            "stop",
            input.service,
            input.timeout_sec,
        )

        new_state, _, _ = _get_service_state(input.service)

        execution_time = int((time.time() - start_time) * 1000)

        if success:
            return CapabilityResult(
                success=True,
                output=ServiceOutput(
                    service=input.service,
                    previous_state=prev_state,
                    new_state=new_state,
                    pid=None,
                ),
                side_effects_applied=self.spec.side_effects,
                execution_time_ms=execution_time,
                evidence=CapabilityEvidence(
                    commands_executed=(f"systemctl stop {input.service}",),
                    man_vault_citations=("systemctl(1)",),
                    rationale=f"Stopped {input.service}",
                ),
            )
        else:
            return CapabilityResult(
                success=False,
                error=stderr or f"systemctl stop failed with exit code {exit_code}",
                execution_time_ms=execution_time,
                evidence=CapabilityEvidence(
                    commands_executed=(f"systemctl stop {input.service}",),
                    rationale=f"Failed to stop {input.service}",
                ),
            )

    def verify(self, input: ServiceInput) -> VerificationResult:
        """Verify service is stopped."""
        active_state, _, _ = _get_service_state(input.service)

        passed = active_state in ("inactive", "failed")

        return VerificationResult(
            passed=passed,
            checks_performed=(f"systemctl is-active {input.service}",),
            actual_state={"active_state": active_state},
            expected_state={"active_state": "inactive"},
            discrepancies=()
            if passed
            else (f"Service still active: {active_state}",),
        )

    def rollback(
        self,
        input: ServiceInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback by starting the service."""
        success, _, stderr, _ = _run_systemctl(
            "start", input.service, input.timeout_sec
        )

        return RollbackResult(
            success=success,
            error=stderr if not success else None,
            rolled_back=(f"{input.service}.service",) if success else (),
            failed_to_rollback=() if success else (f"{input.service}.service",),
            commands_executed=(f"systemctl start {input.service}",),
        )


# =============================================================================
# Service Status Capability
# =============================================================================


class ServiceStatusCapability(BaseCapability):
    """Check status of a systemd service."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="service.status",
            summary="Check status of a systemd service",
            domain="service",
            risk="none",
            side_effects=(),  # Read-only operation
            requires_privilege=False,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="ServiceInput",
            output_schema_name="ServiceStatusOutput",
        )

    def dry_run(self, input: ServiceInput) -> DryRunResult:
        """Preview status check."""
        return DryRunResult(
            would_execute=(f"systemctl status {input.service}",),
            would_modify=(),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text=f"Would check status of {input.service}",
            is_valid=True,
        )

    def run(self, input: ServiceInput) -> CapabilityResult:
        """Check service status."""
        from elle.cli.subprocess_runner import RunMode, run_safe

        start_time = time.time()

        # Get comprehensive status
        result = run_safe(
            f"systemctl show {input.service} "
            "--property=ActiveState,SubState,MainPID,Description,LoadState",
            timeout=10.0,
            mode=RunMode.CAPTURE,
            check_denylist_flag=False,
        )

        execution_time = int((time.time() - start_time) * 1000)

        if not result.success:
            return CapabilityResult(
                success=False,
                error=result.stderr or "Failed to get service status",
                execution_time_ms=execution_time,
                evidence=CapabilityEvidence(
                    commands_executed=(f"systemctl show {input.service}",),
                ),
            )

        # Parse output
        props = {}
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                if "=" in line:
                    key, value = line.split("=", 1)
                    props[key] = value

        pid = None
        if props.get("MainPID"):
            try:
                pid = int(props["MainPID"])
                if pid == 0:
                    pid = None
            except ValueError:
                pass

        return CapabilityResult(
            success=True,
            output=ServiceStatusOutput(
                service=input.service,
                active_state=props.get("ActiveState", "unknown"),
                sub_state=props.get("SubState", ""),
                pid=pid,
                description=props.get("Description", ""),
                loaded=props.get("LoadState") == "loaded",
            ),
            side_effects_applied=(),
            execution_time_ms=execution_time,
            evidence=CapabilityEvidence(
                commands_executed=(f"systemctl show {input.service}",),
                man_vault_citations=("systemctl(1)",),
                rationale=f"Checked status of {input.service}",
            ),
        )

    def verify(self, input: ServiceInput) -> VerificationResult:
        """Verification is not applicable for status check."""
        return VerificationResult(
            passed=True,
            checks_performed=(),
            actual_state={},
            expected_state={},
            discrepancies=(),
        )


# =============================================================================
# Exports
# =============================================================================

SERVICE_CAPABILITIES = [
    ServiceRestartCapability,
    ServiceStartCapability,
    ServiceStopCapability,
    ServiceStatusCapability,
]
