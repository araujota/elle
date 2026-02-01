"""Tests for capabilities policy integration."""

from __future__ import annotations

import builtins
from unittest.mock import MagicMock, patch

import pytest

from elle.capabilities.models import CapabilitySpec, SideEffect
from elle.capabilities.policy import (
    CapabilityPolicyResult,
    _create_allow_result,
    _create_confirm_result,
    _risk_to_policy_risk,
    capability_requires_confirmation,
    capability_requires_privilege_check,
    evaluate_capability,
)
from elle.policy.models import PolicyEffect, PolicyEvaluationResult
from elle.policy.models import RiskLevel as PolicyRiskLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    *,
    name: str = "test.cap",
    summary: str = "A test capability",
    domain: str = "service",
    risk: str = "low",
    requires_privilege: bool = False,
    side_effects: tuple[SideEffect, ...] = (),
) -> CapabilitySpec:
    """Build a CapabilitySpec with sensible defaults for testing."""
    return CapabilitySpec(
        name=name,
        summary=summary,
        domain=domain,
        risk=risk,
        requires_privilege=requires_privilege,
        side_effects=side_effects,
    )


def _make_side_effect(
    *,
    kind: str = "file_write",
    target: str = "/tmp/test",
    reversible: bool = True,
    description: str = "",
) -> SideEffect:
    """Build a SideEffect with sensible defaults for testing."""
    return SideEffect(
        kind=kind,
        target=target,
        reversible=reversible,
        description=description,
    )


# ===========================================================================
# _risk_to_policy_risk
# ===========================================================================


class TestRiskToPolicyRisk:
    """Tests for the capability-to-policy risk level mapping."""

    def test_none_maps_to_low(self):
        """Risk level 'none' should map to PolicyRiskLevel.LOW."""
        assert _risk_to_policy_risk("none") is PolicyRiskLevel.LOW

    def test_low_maps_to_low(self):
        """Risk level 'low' should map to PolicyRiskLevel.LOW."""
        assert _risk_to_policy_risk("low") is PolicyRiskLevel.LOW

    def test_medium_maps_to_medium(self):
        """Risk level 'medium' should map to PolicyRiskLevel.MEDIUM."""
        assert _risk_to_policy_risk("medium") is PolicyRiskLevel.MEDIUM

    def test_high_maps_to_high(self):
        """Risk level 'high' should map to PolicyRiskLevel.HIGH."""
        assert _risk_to_policy_risk("high") is PolicyRiskLevel.HIGH

    def test_critical_maps_to_high(self):
        """Risk level 'critical' should map to PolicyRiskLevel.HIGH."""
        assert _risk_to_policy_risk("critical") is PolicyRiskLevel.HIGH

    def test_unknown_defaults_to_medium(self):
        """An unrecognised risk string should fall back to MEDIUM."""
        assert _risk_to_policy_risk("extreme") is PolicyRiskLevel.MEDIUM


# ===========================================================================
# evaluate_capability
# ===========================================================================


class TestEvaluateCapability:
    """Tests for the evaluate_capability function."""

    def _make_mock_capability(self, **spec_kwargs) -> MagicMock:
        """Return a mock capability whose .spec is a real CapabilitySpec."""
        cap = MagicMock()
        cap.spec = _make_spec(**spec_kwargs)
        return cap

    @patch("elle.policy.evaluate")
    def test_success_returns_policy_result(self, mock_evaluate):
        """On normal evaluation the policy engine result is returned."""
        expected = PolicyEvaluationResult(
            allowed=True,
            effect=PolicyEffect.ALLOW,
            matched_rules=("rule-1",),
            requires_confirmation=False,
            requires_justification=False,
            requires_preview=False,
        )
        mock_evaluate.return_value = expected

        cap = self._make_mock_capability(
            name="service.restart",
            summary="Restart a service",
            domain="service",
            risk="medium",
        )
        result = evaluate_capability(cap, MagicMock())

        assert result is expected
        mock_evaluate.assert_called_once()
        call_args = mock_evaluate.call_args
        request = call_args[0][0]
        assert request.operation_type == "capability"
        assert request.command == "service.restart"
        assert request.domain == "capability.service"

    def test_import_error_returns_allow(self):
        """When the policy module cannot be imported, allow by default."""
        cap = self._make_mock_capability(name="s.r", domain="service", risk="low")
        mock_input = MagicMock()

        _real_import = builtins.__import__

        def _failing_import(name, *args, **kwargs):
            # Only block the deferred import inside evaluate_capability
            if name == "elle.policy":
                raise ImportError("simulated missing module")
            return _real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_failing_import):
            result = evaluate_capability(cap, mock_input)

        assert result.allowed is True
        assert result.requires_confirmation is False

    @patch("elle.policy.evaluate", side_effect=RuntimeError("boom"))
    def test_exception_returns_confirm_result(self, _mock_evaluate):
        """On an unexpected error the result requires confirmation."""
        cap = self._make_mock_capability(name="pkg.install", domain="package", risk="low")
        result = evaluate_capability(cap, MagicMock())

        assert result.allowed is True
        assert result.requires_confirmation is True
        assert "boom" in (getattr(result, "message", None) or "")


# ===========================================================================
# capability_requires_confirmation
# ===========================================================================


class TestCapabilityRequiresConfirmation:
    """Tests for the capability_requires_confirmation helper."""

    def test_high_risk_requires_confirmation(self):
        """High risk capabilities always require confirmation."""
        spec = _make_spec(risk="high")
        assert capability_requires_confirmation(spec) is True

    def test_critical_risk_requires_confirmation(self):
        """Critical risk capabilities always require confirmation."""
        spec = _make_spec(risk="critical")
        assert capability_requires_confirmation(spec) is True

    def test_privileged_requires_confirmation(self):
        """Privileged capabilities require confirmation regardless of risk."""
        spec = _make_spec(risk="low", requires_privilege=True)
        assert capability_requires_confirmation(spec) is True

    def test_irreversible_side_effect_requires_confirmation(self):
        """An irreversible side effect triggers confirmation."""
        spec = _make_spec(
            risk="low",
            side_effects=(_make_side_effect(reversible=False),),
        )
        assert capability_requires_confirmation(spec) is True

    def test_safe_capability_no_confirmation(self):
        """A low-risk, unprivileged, all-reversible capability needs no confirmation."""
        spec = _make_spec(
            risk="low",
            requires_privilege=False,
            side_effects=(_make_side_effect(reversible=True),),
        )
        assert capability_requires_confirmation(spec) is False


# ===========================================================================
# capability_requires_privilege_check
# ===========================================================================


class TestCapabilityRequiresPrivilegeCheck:
    """Tests for the capability_requires_privilege_check helper."""

    def test_privileged_returns_true(self):
        """A spec with requires_privilege=True should return True."""
        spec = _make_spec(requires_privilege=True)
        assert capability_requires_privilege_check(spec) is True

    def test_unprivileged_returns_false(self):
        """A spec with requires_privilege=False should return False."""
        spec = _make_spec(requires_privilege=False)
        assert capability_requires_privilege_check(spec) is False


# ===========================================================================
# _create_allow_result
# ===========================================================================


class TestCreateAllowResult:
    """Tests for the _create_allow_result fallback factory."""

    def test_normal_path_returns_policy_result(self):
        """When the policy module is importable, return a proper PolicyEvaluationResult."""
        result = _create_allow_result()

        assert isinstance(result, PolicyEvaluationResult)
        assert result.allowed is True
        assert result.effect == PolicyEffect.ALLOW
        assert result.matched_rules == ()
        assert result.requires_confirmation is False
        assert result.requires_justification is False
        assert result.requires_preview is False

    def test_import_error_returns_minimal_result(self):
        """When the policy module is missing, fall back to a minimal duck-typed object."""
        _real_import = builtins.__import__

        def _failing_import(name, *args, **kwargs):
            if name == "elle.policy":
                raise ImportError("simulated")
            return _real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_failing_import):
            result = _create_allow_result()

        assert result.allowed is True
        assert result.effect == "allow"
        assert result.matched_rules == ()
        assert result.requires_confirmation is False
        assert result.requires_justification is False
        assert result.requires_preview is False
        assert result.is_blocked is False
        assert result.should_proceed is True
        assert result.message is None


# ===========================================================================
# _create_confirm_result
# ===========================================================================


class TestCreateConfirmResult:
    """Tests for the _create_confirm_result fallback factory."""

    def test_normal_path_returns_policy_result(self):
        """When the policy module is importable, return a proper PolicyEvaluationResult."""
        result = _create_confirm_result("something went wrong")

        assert isinstance(result, PolicyEvaluationResult)
        assert result.allowed is True
        assert result.effect == PolicyEffect.REQUIRE_CONFIRMATION
        assert result.requires_confirmation is True
        assert result.message == "something went wrong"
        assert result.matched_rules == ()
        assert result.requires_justification is False
        assert result.requires_preview is False

    def test_import_error_fallback_has_scoping_bug(self):
        """The ImportError fallback raises NameError due to a class-scope issue.

        Python class bodies cannot see enclosing function locals, so
        ``message = message`` inside ``_MinimalResult`` fails with a
        NameError because the ``message`` parameter is not visible in the
        class body scope.  This test documents the existing defect.
        """
        _real_import = builtins.__import__

        def _failing_import(name, *args, **kwargs):
            if name == "elle.policy":
                raise ImportError("simulated")
            return _real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_failing_import):
            with pytest.raises(NameError, match="message"):
                _create_confirm_result("oops")


# ===========================================================================
# CapabilityPolicyResult
# ===========================================================================


class TestCapabilityPolicyResult:
    """Tests for the CapabilityPolicyResult Pydantic model."""

    def test_from_full_policy_result(self):
        """Conversion from a complete PolicyEvaluationResult."""
        source = PolicyEvaluationResult(
            allowed=False,
            effect=PolicyEffect.DENY,
            matched_rules=("rule-block-all",),
            requires_confirmation=False,
            requires_justification=False,
            requires_preview=True,
            message="Operation denied by policy",
        )

        cpr = CapabilityPolicyResult.from_policy_result(source)

        assert cpr.allowed is False
        assert cpr.requires_confirmation is False
        assert cpr.requires_preview is True
        assert cpr.message == "Operation denied by policy"
        assert cpr.matched_rules == ("rule-block-all",)
        assert cpr.is_blocked is True

    def test_from_minimal_object(self):
        """Conversion from an object missing some attributes uses safe defaults."""

        class _Bare:
            allowed = True

        cpr = CapabilityPolicyResult.from_policy_result(_Bare())

        assert cpr.allowed is True
        assert cpr.requires_confirmation is False
        assert cpr.requires_preview is False
        assert cpr.message is None
        assert cpr.matched_rules == ()
        assert cpr.is_blocked is False
