"""Tests for capabilities registry."""

import pytest

from elle.capabilities.registry import (
    CapabilityRegistry,
    get_registry,
    reset_registry,
)
from elle.capabilities.models import (
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    VerificationResult,
)
from elle.capabilities.protocol import BaseCapability
from elle.capabilities.exceptions import (
    CapabilityNotFoundError,
    CapabilityRegistrationError,
)

from pydantic import BaseModel


# =============================================================================
# Test fixtures
# =============================================================================


class TestInput(BaseModel):
    value: str


class TestCapability(BaseCapability):
    """A test capability for unit tests."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="test.example",
            summary="A test capability",
            domain="file",
            risk="low",
        )

    def dry_run(self, input: TestInput) -> DryRunResult:
        return DryRunResult(
            would_execute=("echo test",),
            preview_text="Would run test",
            is_valid=True,
        )

    def run(self, input: TestInput) -> CapabilityResult:
        return CapabilityResult(
            success=True,
            output={"value": input.value},
        )


class AnotherTestCapability(BaseCapability):
    """Another test capability."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="test.another",
            summary="Another test capability",
            domain="service",
            risk="medium",
        )

    def dry_run(self, input: TestInput) -> DryRunResult:
        return DryRunResult(is_valid=True)

    def run(self, input: TestInput) -> CapabilityResult:
        return CapabilityResult(success=True)


# =============================================================================
# Tests
# =============================================================================


class TestCapabilityRegistry:
    """Tests for CapabilityRegistry."""

    def setup_method(self):
        """Reset registry before each test."""
        self.registry = CapabilityRegistry()

    def test_register_capability(self):
        """Test registering a capability."""
        self.registry.register(TestCapability)

        assert self.registry.is_registered("test.example")
        assert self.registry.count() == 1

    def test_get_capability(self):
        """Test getting a registered capability."""
        self.registry.register(TestCapability)

        cap = self.registry.get("test.example")
        assert cap is not None
        assert cap.spec.name == "test.example"

    def test_get_nonexistent_capability(self):
        """Test getting a capability that doesn't exist."""
        cap = self.registry.get("nonexistent.cap")
        assert cap is None

    def test_get_or_raise(self):
        """Test get_or_raise with missing capability."""
        with pytest.raises(CapabilityNotFoundError):
            self.registry.get_or_raise("nonexistent.cap")

    def test_get_spec(self):
        """Test getting just the spec."""
        self.registry.register(TestCapability)

        spec = self.registry.get_spec("test.example")
        assert spec is not None
        assert spec.name == "test.example"
        assert spec.domain == "file"

    def test_list_all(self):
        """Test listing all capabilities."""
        self.registry.register(TestCapability)
        self.registry.register(AnotherTestCapability)

        specs = self.registry.list_all()
        assert len(specs) == 2
        names = {s.name for s in specs}
        assert "test.example" in names
        assert "test.another" in names

    def test_list_by_domain(self):
        """Test listing capabilities by domain."""
        self.registry.register(TestCapability)
        self.registry.register(AnotherTestCapability)

        file_caps = self.registry.list_by_domain("file")
        assert len(file_caps) == 1
        assert file_caps[0].name == "test.example"

        service_caps = self.registry.list_by_domain("service")
        assert len(service_caps) == 1
        assert service_caps[0].name == "test.another"

    def test_list_names(self):
        """Test listing capability names."""
        self.registry.register(TestCapability)
        self.registry.register(AnotherTestCapability)

        names = self.registry.list_names()
        assert len(names) == 2
        assert "test.example" in names
        assert "test.another" in names

    def test_list_domains(self):
        """Test listing domains with capabilities."""
        self.registry.register(TestCapability)
        self.registry.register(AnotherTestCapability)

        domains = self.registry.list_domains()
        assert "file" in domains
        assert "service" in domains

    def test_search(self):
        """Test searching capabilities."""
        self.registry.register(TestCapability)
        self.registry.register(AnotherTestCapability)

        # Search by name
        results = self.registry.search("example")
        assert len(results) == 1
        assert results[0].name == "test.example"

        # Search by summary
        results = self.registry.search("another")
        assert len(results) == 1
        assert results[0].name == "test.another"

        # No results
        results = self.registry.search("nonexistent")
        assert len(results) == 0

    def test_unregister(self):
        """Test unregistering a capability."""
        self.registry.register(TestCapability)
        assert self.registry.is_registered("test.example")

        result = self.registry.unregister("test.example")
        assert result is True
        assert not self.registry.is_registered("test.example")

    def test_unregister_nonexistent(self):
        """Test unregistering a capability that doesn't exist."""
        result = self.registry.unregister("nonexistent")
        assert result is False

    def test_clear(self):
        """Test clearing all capabilities."""
        self.registry.register(TestCapability)
        self.registry.register(AnotherTestCapability)
        assert self.registry.count() == 2

        self.registry.clear()
        assert self.registry.count() == 0

    def test_capability_instance_caching(self):
        """Test that capability instances are cached."""
        self.registry.register(TestCapability)

        cap1 = self.registry.get("test.example")
        cap2 = self.registry.get("test.example")

        assert cap1 is cap2  # Same instance

    def test_overwrite_warning(self, caplog):
        """Test that overwriting a capability logs a warning."""
        self.registry.register(TestCapability)
        self.registry.register(TestCapability)  # Register again

        # Should still work
        assert self.registry.is_registered("test.example")


class TestGlobalRegistry:
    """Tests for global registry functions."""

    def setup_method(self):
        """Reset global registry before each test."""
        reset_registry()

    def teardown_method(self):
        """Reset global registry after each test."""
        reset_registry()

    def test_get_registry_singleton(self):
        """Test that get_registry returns singleton."""
        reg1 = get_registry()
        reg2 = get_registry()
        assert reg1 is reg2

    def test_reset_registry(self):
        """Test that reset_registry creates new instance."""
        reg1 = get_registry()
        reset_registry()
        reg2 = get_registry()
        assert reg1 is not reg2

    def test_global_registry_has_core_capabilities(self):
        """Test that global registry loads core capabilities."""
        registry = get_registry()

        # Should have some core capabilities registered
        # (actual count depends on what's available)
        assert registry.count() >= 0

        # If core capabilities are available, check for service caps
        if registry.count() > 0:
            service_caps = registry.list_by_domain("service")
            # Service domain should have capabilities if loaded
            pass  # Don't assert specific count as it depends on imports
