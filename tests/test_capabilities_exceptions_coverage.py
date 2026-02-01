"""Tests for capabilities/exceptions.py coverage.

Covers constructor parameter assignments for:
- CapabilityInputError with field (lines 27-28)
- CapabilityExecutionError with capability_name, exit_code, stderr (lines 62-65)
- CapabilityRegistrationError with capability_name (lines 72-73)
"""

from __future__ import annotations

from elle.capabilities.exceptions import (
    CapabilityCancelledError,
    CapabilityDeniedError,
    CapabilityError,
    CapabilityExecutionError,
    CapabilityInputError,
    CapabilityNotFoundError,
    CapabilityRegistrationError,
)


class TestCapabilityInputError:
    """Tests for CapabilityInputError."""

    def test_with_field(self):
        """CapabilityInputError stores the field parameter."""
        err = CapabilityInputError("invalid value", field="port")
        assert err.field == "port"
        assert str(err) == "invalid value"

    def test_without_field(self):
        """CapabilityInputError defaults field to None."""
        err = CapabilityInputError("bad input")
        assert err.field is None
        assert str(err) == "bad input"

    def test_is_capability_error(self):
        """CapabilityInputError inherits from CapabilityError."""
        err = CapabilityInputError("msg", field="x")
        assert isinstance(err, CapabilityError)


class TestCapabilityExecutionError:
    """Tests for CapabilityExecutionError."""

    def test_with_all_params(self):
        """CapabilityExecutionError stores capability_name, exit_code, stderr."""
        err = CapabilityExecutionError(
            "execution failed",
            capability_name="service.restart",
            exit_code=1,
            stderr="unit not found",
        )
        assert err.capability_name == "service.restart"
        assert err.exit_code == 1
        assert err.stderr == "unit not found"
        assert str(err) == "execution failed"

    def test_with_defaults(self):
        """CapabilityExecutionError defaults optional params to None."""
        err = CapabilityExecutionError("failed")
        assert err.capability_name is None
        assert err.exit_code is None
        assert err.stderr is None

    def test_is_capability_error(self):
        """CapabilityExecutionError inherits from CapabilityError."""
        err = CapabilityExecutionError("msg")
        assert isinstance(err, CapabilityError)


class TestCapabilityRegistrationError:
    """Tests for CapabilityRegistrationError."""

    def test_with_capability_name(self):
        """CapabilityRegistrationError stores the capability_name parameter."""
        err = CapabilityRegistrationError("already registered", capability_name="file.read")
        assert err.capability_name == "file.read"
        assert str(err) == "already registered"

    def test_without_capability_name(self):
        """CapabilityRegistrationError defaults capability_name to None."""
        err = CapabilityRegistrationError("registration error")
        assert err.capability_name is None

    def test_is_capability_error(self):
        """CapabilityRegistrationError inherits from CapabilityError."""
        err = CapabilityRegistrationError("msg", capability_name="x.y")
        assert isinstance(err, CapabilityError)


class TestCapabilityNotFoundError:
    """Tests for CapabilityNotFoundError."""

    def test_stores_capability_name(self):
        """CapabilityNotFoundError stores capability_name."""
        err = CapabilityNotFoundError("service.unknown")
        assert err.capability_name == "service.unknown"
        assert "service.unknown" in str(err)


class TestCapabilityDeniedError:
    """Tests for CapabilityDeniedError."""

    def test_stores_all_fields(self):
        """CapabilityDeniedError stores capability_name and rule_id."""
        err = CapabilityDeniedError("denied", capability_name="file.delete", rule_id="rule-42")
        assert err.capability_name == "file.delete"
        assert err.rule_id == "rule-42"


class TestCapabilityCancelledError:
    """Tests for CapabilityCancelledError."""

    def test_default_message(self):
        """CapabilityCancelledError has default message."""
        err = CapabilityCancelledError()
        assert "cancelled" in str(err).lower()
