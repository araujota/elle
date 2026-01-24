"""Tests for capabilities models."""

import pytest
from datetime import datetime

from elle.capabilities.models import (
    CapabilityDomain,
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    RiskLevel,
    RollbackResult,
    SideEffect,
    SideEffectKind,
    TrustLevel,
    VerificationResult,
)


class TestSideEffect:
    """Tests for SideEffect model."""

    def test_create_side_effect(self):
        """Test creating a basic side effect."""
        effect = SideEffect(
            kind="file_write",
            target="/etc/config",
            reversible=True,
            description="Config will be modified",
        )
        assert effect.kind == "file_write"
        assert effect.target == "/etc/config"
        assert effect.reversible is True
        assert effect.description == "Config will be modified"

    def test_side_effect_defaults(self):
        """Test side effect default values."""
        effect = SideEffect(
            kind="service_change",
            target="nginx.service",
        )
        assert effect.reversible is True
        assert effect.description == ""

    def test_side_effect_frozen(self):
        """Test that side effects are immutable."""
        effect = SideEffect(kind="file_write", target="/tmp/test")
        with pytest.raises(Exception):  # ValidationError or AttributeError
            effect.target = "/other/path"


class TestCapabilitySpec:
    """Tests for CapabilitySpec model."""

    def test_create_spec(self):
        """Test creating a capability spec."""
        spec = CapabilitySpec(
            name="service.restart",
            summary="Restart a systemd service",
            domain="service",
            risk="medium",
            side_effects=(
                SideEffect(
                    kind="service_change",
                    target="{service}",
                    description="Service will restart",
                ),
            ),
            requires_privilege=True,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
        )
        assert spec.name == "service.restart"
        assert spec.domain == "service"
        assert spec.risk == "medium"
        assert spec.requires_privilege is True
        assert len(spec.side_effects) == 1

    def test_spec_defaults(self):
        """Test spec default values."""
        spec = CapabilitySpec(
            name="test.cap",
            summary="Test capability",
            domain="file",
            risk="low",
        )
        assert spec.side_effects == ()
        assert spec.requires_privilege is False
        assert spec.idempotent is False
        assert spec.trust_level == "core"
        assert spec.version == "1.0.0"
        assert spec.input_schema_name is None
        assert spec.output_schema_name is None


class TestCapabilityEvidence:
    """Tests for CapabilityEvidence model."""

    def test_create_evidence(self):
        """Test creating capability evidence."""
        evidence = CapabilityEvidence(
            commands_executed=("systemctl restart nginx",),
            files_modified=(),
            verification_passed=True,
            verification_details="Service is active",
            man_vault_citations=("systemctl(1)",),
            rationale="Restarted nginx as requested",
        )
        assert "systemctl restart nginx" in evidence.commands_executed
        assert evidence.verification_passed is True
        assert "systemctl(1)" in evidence.man_vault_citations

    def test_evidence_defaults(self):
        """Test evidence default values."""
        evidence = CapabilityEvidence()
        assert evidence.commands_executed == ()
        assert evidence.files_modified == ()
        assert evidence.verification_passed is False
        assert evidence.verification_details == ""
        assert evidence.man_vault_citations == ()
        assert evidence.rationale == ""


class TestCapabilityResult:
    """Tests for CapabilityResult model."""

    def test_successful_result(self):
        """Test creating a successful result."""
        result = CapabilityResult(
            success=True,
            output={"status": "active"},
            error=None,
            warnings=(),
            side_effects_applied=(
                SideEffect(kind="service_change", target="nginx"),
            ),
            execution_time_ms=150,
            evidence=CapabilityEvidence(
                verification_passed=True,
            ),
        )
        assert result.success is True
        assert result.output == {"status": "active"}
        assert result.error is None
        assert len(result.side_effects_applied) == 1

    def test_failed_result(self):
        """Test creating a failed result."""
        result = CapabilityResult(
            success=False,
            output=None,
            error="Service not found",
            warnings=("Service may be masked",),
        )
        assert result.success is False
        assert result.error == "Service not found"
        assert "Service may be masked" in result.warnings


class TestDryRunResult:
    """Tests for DryRunResult model."""

    def test_valid_dry_run(self):
        """Test creating a valid dry run result."""
        result = DryRunResult(
            would_execute=("systemctl restart nginx",),
            would_modify=("nginx.service",),
            estimated_risk="medium",
            requires_confirmation=True,
            preview_text="Would restart nginx",
            diff=None,
            is_valid=True,
        )
        assert result.is_valid is True
        assert result.requires_confirmation is True
        assert "systemctl restart nginx" in result.would_execute

    def test_invalid_dry_run(self):
        """Test creating an invalid dry run result."""
        result = DryRunResult(
            is_valid=False,
            validation_errors=("Service does not exist",),
            preview_text="Cannot restart: service not found",
        )
        assert result.is_valid is False
        assert "Service does not exist" in result.validation_errors

    def test_dry_run_with_diff(self):
        """Test dry run with diff content."""
        result = DryRunResult(
            would_execute=(),
            would_modify=("/etc/config",),
            estimated_risk="low",
            preview_text="Would modify config",
            diff="--- a/config\n+++ b/config\n-old\n+new",
            is_valid=True,
        )
        assert result.diff is not None
        assert "--- a/config" in result.diff


class TestVerificationResult:
    """Tests for VerificationResult model."""

    def test_passed_verification(self):
        """Test creating a passed verification result."""
        result = VerificationResult(
            passed=True,
            checks_performed=("systemctl is-active nginx",),
            actual_state={"active_state": "active"},
            expected_state={"active_state": "active"},
            discrepancies=(),
        )
        assert result.passed is True
        assert len(result.discrepancies) == 0

    def test_failed_verification(self):
        """Test creating a failed verification result."""
        result = VerificationResult(
            passed=False,
            checks_performed=("systemctl is-active nginx",),
            actual_state={"active_state": "failed"},
            expected_state={"active_state": "active"},
            discrepancies=("Service not active: failed",),
        )
        assert result.passed is False
        assert len(result.discrepancies) == 1


class TestRollbackResult:
    """Tests for RollbackResult model."""

    def test_successful_rollback(self):
        """Test creating a successful rollback result."""
        result = RollbackResult(
            success=True,
            error=None,
            rolled_back=("nginx.service",),
            failed_to_rollback=(),
            commands_executed=("systemctl restart nginx",),
        )
        assert result.success is True
        assert "nginx.service" in result.rolled_back

    def test_failed_rollback(self):
        """Test creating a failed rollback result."""
        result = RollbackResult(
            success=False,
            error="Rollback not supported",
            rolled_back=(),
            failed_to_rollback=("nginx.service",),
        )
        assert result.success is False
        assert result.error == "Rollback not supported"
