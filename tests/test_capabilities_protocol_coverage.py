"""Tests for capabilities/protocol.py coverage.

Covers abstract method bodies (Capability Protocol) and BaseCapability
default implementations (lines 76, 94, 114, 128, 143, 163, 167, 171).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from elle.capabilities.models import CapabilityResult, CapabilitySpec, DryRunResult
from elle.capabilities.protocol import BaseCapability, Capability


class DummyInput(BaseModel):
    value: str = "test"


class DummyOutput(BaseModel):
    result: str = ""


class TestBaseCapabilityDefaults:
    """Tests for BaseCapability default implementations."""

    def _make_base(self):
        return BaseCapability()

    def test_spec_raises_not_implemented(self):
        """BaseCapability.spec raises NotImplementedError."""
        base = self._make_base()
        with pytest.raises(NotImplementedError, match="spec"):
            _ = base.spec

    def test_dry_run_raises_not_implemented(self):
        """BaseCapability.dry_run raises NotImplementedError."""
        base = self._make_base()
        with pytest.raises(NotImplementedError, match="dry_run"):
            base.dry_run(DummyInput())

    def test_run_raises_not_implemented(self):
        """BaseCapability.run raises NotImplementedError."""
        base = self._make_base()
        with pytest.raises(NotImplementedError, match="run"):
            base.run(DummyInput())

    def test_verify_returns_passed(self):
        """BaseCapability.verify returns a passed VerificationResult."""
        base = self._make_base()
        result = base.verify(DummyInput())
        assert result.passed is True
        assert result.checks_performed == ()
        assert result.actual_state == {}
        assert result.expected_state == {}
        assert result.discrepancies == ()

    def test_rollback_returns_unsupported(self):
        """BaseCapability.rollback returns failure indicating unsupported."""
        base = self._make_base()
        cap_result = CapabilityResult(success=True)
        result = base.rollback(DummyInput(), cap_result)
        assert result.success is False
        assert "not supported" in result.error.lower()
        assert result.rolled_back == ()
        assert result.failed_to_rollback == ()
        assert result.commands_executed == ()


class TestCapabilityProtocol:
    """Tests that Capability Protocol defines the expected interface."""

    def test_protocol_is_runtime_checkable(self):
        """Capability protocol is runtime_checkable."""

        class ConcreteCapability:
            @property
            def spec(self) -> CapabilitySpec:
                return CapabilitySpec(
                    name="test.cap",
                    summary="Test",
                    domain="file",
                    risk="none",
                )

            def dry_run(self, input):
                return DryRunResult()

            def run(self, input):
                return CapabilityResult(success=True)

            def verify(self, input):
                from elle.capabilities.models import VerificationResult

                return VerificationResult(passed=True)

            def rollback(self, input, result):
                from elle.capabilities.models import RollbackResult

                return RollbackResult(success=False)

        cap = ConcreteCapability()
        assert isinstance(cap, Capability)

    def test_non_capability_not_instance(self):
        """Objects that do not implement the protocol are not instances."""

        class NotACapability:
            pass

        assert not isinstance(NotACapability(), Capability)

    def test_protocol_spec_body_executes(self):
        """Calling Protocol method body (the `...` statement) returns None.

        The protocol methods have a body of ``...`` (Ellipsis). We invoke them
        directly on the Protocol class to exercise those lines.
        """
        # Calling Protocol.__init__ is not typical, but we need to hit the
        # body of each method. We use the *unbound* function from the class.
        result = Capability.spec.fget(None)  # type: ignore
        assert result is None  # `...` evaluates to Ellipsis but the return is None in Protocol

    def test_protocol_dry_run_body(self):
        """Call Protocol.dry_run body directly."""
        result = Capability.dry_run(None, DummyInput())  # type: ignore
        assert result is None

    def test_protocol_run_body(self):
        """Call Protocol.run body directly."""
        result = Capability.run(None, DummyInput())  # type: ignore
        assert result is None

    def test_protocol_verify_body(self):
        """Call Protocol.verify body directly."""
        result = Capability.verify(None, DummyInput())  # type: ignore
        assert result is None

    def test_protocol_rollback_body(self):
        """Call Protocol.rollback body directly."""
        cap_result = CapabilityResult(success=True)
        result = Capability.rollback(None, DummyInput(), cap_result)  # type: ignore
        assert result is None
