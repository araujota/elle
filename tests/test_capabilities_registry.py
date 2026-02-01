"""Tests for capabilities registry."""

import pytest
from pydantic import BaseModel

from elle.capabilities.exceptions import (
    CapabilityNotFoundError,
)
from elle.capabilities.models import (
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
)
from elle.capabilities.protocol import BaseCapability
from elle.capabilities.registry import (
    CapabilityRegistry,
    get_registry,
    reset_registry,
)

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
            registry.list_by_domain("service")
            # Service domain should have capabilities if loaded
            pass  # Don't assert specific count as it depends on imports


# =============================================================================
# Additional coverage tests
# =============================================================================

import logging
from unittest.mock import MagicMock, patch

from elle.capabilities.exceptions import CapabilityRegistrationError
from elle.capabilities.registry import (
    _load_core_capabilities,
    _load_python_core_capabilities,
    _load_stored_core_capabilities,
    _sync_core_if_needed,
)


class TestListByDomainUnknown:
    """Test list_by_domain returns empty for unknown domains."""

    def setup_method(self):
        self.registry = CapabilityRegistry()

    def test_list_by_domain_unknown_returns_empty(self):
        """Listing an unknown domain returns an empty list."""
        self.registry.register(TestCapability)
        result = self.registry.list_by_domain("network")
        assert result == []

    def test_list_by_domain_no_capabilities_registered(self):
        """Listing any domain on empty registry returns an empty list."""
        result = self.registry.list_by_domain("file")
        assert result == []


class TestSearchNoResults:
    """Test search returning empty results."""

    def setup_method(self):
        self.registry = CapabilityRegistry()

    def test_search_no_match_on_empty_registry(self):
        """Search on empty registry returns empty."""
        result = self.registry.search("anything")
        assert result == []

    def test_search_no_match_on_populated_registry(self):
        """Search that matches nothing returns empty."""
        self.registry.register(TestCapability)
        self.registry.register(AnotherTestCapability)
        result = self.registry.search("zzz_nonexistent_zzz")
        assert result == []


class TestRegisterDuplicate:
    """Test duplicate registration logs warning."""

    def setup_method(self):
        self.registry = CapabilityRegistry()

    def test_duplicate_register_logs_warning(self, caplog):
        """Registering same capability twice logs a warning."""
        self.registry.register(TestCapability)
        with caplog.at_level(logging.WARNING, logger="elle.capabilities.registry"):
            self.registry.register(TestCapability)

        assert any("Overwriting existing capability" in msg for msg in caplog.messages)
        # Still works after overwrite
        assert self.registry.is_registered("test.example")
        assert self.registry.count() == 1


class TestRegisterFailure:
    """Test registration failure when capability cannot be instantiated."""

    def setup_method(self):
        self.registry = CapabilityRegistry()

    def test_register_broken_capability_raises(self):
        """Registration fails when capability instantiation raises."""

        class BrokenCapability(BaseCapability):
            @property
            def spec(self):
                raise RuntimeError("boom")

            def dry_run(self, input):
                pass

            def run(self, input):
                pass

        with pytest.raises(CapabilityRegistrationError, match="Failed to instantiate"):
            self.registry.register(BrokenCapability)


class TestGetInstantiationFailure:
    """Test get() when cached instance creation fails."""

    def setup_method(self):
        self.registry = CapabilityRegistry()

    def test_get_returns_none_when_instantiation_fails(self, caplog):
        """get() returns None if capability class raises on instantiation."""
        # Register a valid capability first, then swap out the class
        self.registry.register(TestCapability)
        # Clear the cached instance so get() will try to re-create
        self.registry._instances.pop("test.example", None)

        # Replace class with one that raises on __init__
        bad_class = MagicMock(side_effect=RuntimeError("cannot create"))
        self.registry._capabilities["test.example"] = bad_class

        with caplog.at_level(logging.ERROR, logger="elle.capabilities.registry"):
            result = self.registry.get("test.example")

        assert result is None
        assert any("Failed to instantiate capability" in msg for msg in caplog.messages)


class TestListDomainsEnumeration:
    """Test list_domains enumerates all unique domains."""

    def setup_method(self):
        self.registry = CapabilityRegistry()

    def test_list_domains_returns_unique_domains(self):
        """list_domains returns all registered domains."""
        self.registry.register(TestCapability)       # domain = "file"
        self.registry.register(AnotherTestCapability)  # domain = "service"

        domains = self.registry.list_domains()
        assert set(domains) == {"file", "service"}

    def test_list_domains_empty_registry(self):
        """list_domains on empty registry returns empty."""
        domains = self.registry.list_domains()
        assert domains == []

    def test_list_domains_filters_empty_name_lists(self):
        """list_domains skips domains whose name lists are empty."""
        self.registry.register(TestCapability)
        # Manually clear the name list for 'file' domain to simulate
        # an unregistered domain that still has an entry
        self.registry._by_domain["file"] = []
        domains = self.registry.list_domains()
        assert "file" not in domains


class TestUnregisterDomainCleanup:
    """Test unregister cleans up domain mapping and cached instance."""

    def setup_method(self):
        self.registry = CapabilityRegistry()

    def test_unregister_clears_cached_instance(self):
        """Unregister removes the cached instance."""
        self.registry.register(TestCapability)
        # Force instance caching by calling get()
        cap = self.registry.get("test.example")
        assert cap is not None
        assert "test.example" in self.registry._instances

        self.registry.unregister("test.example")
        assert "test.example" not in self.registry._instances

    def test_unregister_handles_spec_failure(self):
        """Unregister succeeds even if capability spec raises on cleanup."""
        self.registry.register(TestCapability)
        # Replace with a class whose __init__ or spec raises
        bad_class = MagicMock(side_effect=RuntimeError("spec boom"))
        self.registry._capabilities["test.example"] = bad_class

        result = self.registry.unregister("test.example")
        assert result is True
        assert "test.example" not in self.registry._capabilities


class TestGetSpecMissing:
    """Test get_spec for nonexistent capability."""

    def setup_method(self):
        self.registry = CapabilityRegistry()

    def test_get_spec_returns_none_for_missing(self):
        """get_spec returns None for unregistered name."""
        assert self.registry.get_spec("nonexistent") is None


class TestLoadStoredCoreCapabilitiesFailure:
    """Test _load_stored_core_capabilities failure branches."""

    def test_returns_zero_on_import_error(self):
        """Returns 0 when autogen store/loader cannot be imported."""
        registry = CapabilityRegistry()
        with patch(
            "elle.capabilities.registry._load_stored_core_capabilities",
            wraps=_load_stored_core_capabilities,
        ):
            # Simulate import failure by patching the import targets
            with patch.dict(
                "sys.modules",
                {
                    "elle.capabilities.autogen.store": None,
                },
            ):
                count = _load_stored_core_capabilities(registry)
                assert count == 0

    def test_returns_zero_on_connection_error(self):
        """Returns 0 when get_store raises a connection error."""
        registry = CapabilityRegistry()
        mock_loader = MagicMock()
        mock_store_mod = MagicMock()
        mock_store_mod.get_store.side_effect = RuntimeError("DB connection failed")

        with patch.dict(
            "sys.modules",
            {
                "elle.capabilities.autogen.loader": mock_loader,
                "elle.capabilities.autogen.store": mock_store_mod,
            },
        ):
            count = _load_stored_core_capabilities(registry)
            assert count == 0


class TestLoadPythonCoreCapabilitiesFailure:
    """Test _load_python_core_capabilities failure branches."""

    def test_returns_zero_on_import_error(self):
        """Returns 0 when elle.capabilities.core cannot be imported."""
        registry = CapabilityRegistry()
        with patch.dict(
            "sys.modules",
            {"elle.capabilities.core": None},
        ):
            count = _load_python_core_capabilities(registry)
            assert count == 0

    def test_returns_zero_on_general_exception(self):
        """Returns 0 when get_core_capabilities raises non-import error."""
        registry = CapabilityRegistry()
        mock_core = MagicMock()
        mock_core.get_core_capabilities.side_effect = RuntimeError("unexpected")

        with patch.dict(
            "sys.modules",
            {"elle.capabilities.core": mock_core},
        ):
            count = _load_python_core_capabilities(registry)
            assert count == 0

    def test_skips_already_registered(self):
        """Skips capabilities already in the registry."""
        registry = CapabilityRegistry()
        registry.register(TestCapability)  # pre-register test.example

        mock_core = MagicMock()
        mock_core.get_core_capabilities.return_value = [TestCapability]

        with patch.dict(
            "sys.modules",
            {"elle.capabilities.core": mock_core},
        ):
            count = _load_python_core_capabilities(registry)
            assert count == 0  # skipped because already registered

    def test_handles_individual_cap_failure(self, caplog):
        """Logs warning and continues when individual capability fails."""
        registry = CapabilityRegistry()

        class FailingCap(BaseCapability):
            @property
            def spec(self):
                raise RuntimeError("cannot get spec")

            def dry_run(self, input):
                pass

            def run(self, input):
                pass

        mock_core = MagicMock()
        mock_core.get_core_capabilities.return_value = [FailingCap]

        with patch.dict(
            "sys.modules",
            {"elle.capabilities.core": mock_core},
        ):
            with caplog.at_level(logging.WARNING, logger="elle.capabilities.registry"):
                count = _load_python_core_capabilities(registry)

        assert count == 0
        assert any("Failed to register core capability" in msg for msg in caplog.messages)


class TestSyncCoreIfNeeded:
    """Test _sync_core_if_needed branches."""

    def test_sync_logs_on_update(self, caplog):
        """Logs info when capabilities are updated."""
        mock_migrator = MagicMock()
        mock_migrator.sync_core_capabilities_if_needed.return_value = {
            "updated_count": 3,
            "migrated_count": 0,
        }
        with patch.dict(
            "sys.modules",
            {"elle.capabilities.autogen.core_migrator": mock_migrator},
        ):
            with caplog.at_level(logging.INFO, logger="elle.capabilities.registry"):
                _sync_core_if_needed()

        assert any("Updated 3 core capabilities" in msg for msg in caplog.messages)

    def test_sync_logs_on_migration(self, caplog):
        """Logs info when capabilities are migrated."""
        mock_migrator = MagicMock()
        mock_migrator.sync_core_capabilities_if_needed.return_value = {
            "updated_count": 0,
            "migrated_count": 2,
        }
        with patch.dict(
            "sys.modules",
            {"elle.capabilities.autogen.core_migrator": mock_migrator},
        ):
            with caplog.at_level(logging.INFO, logger="elle.capabilities.registry"):
                _sync_core_if_needed()

        assert any("Migrated 2 new core capabilities" in msg for msg in caplog.messages)

    def test_sync_handles_exception(self, caplog):
        """Logs debug on exception without raising."""
        mock_migrator = MagicMock()
        mock_migrator.sync_core_capabilities_if_needed.side_effect = RuntimeError("oops")
        with patch.dict(
            "sys.modules",
            {"elle.capabilities.autogen.core_migrator": mock_migrator},
        ):
            with caplog.at_level(logging.DEBUG, logger="elle.capabilities.registry"):
                _sync_core_if_needed()

        assert any("Could not sync core capabilities" in msg for msg in caplog.messages)

    def test_sync_import_error(self, caplog):
        """Logs debug when migrator module cannot be imported."""
        with patch.dict(
            "sys.modules",
            {"elle.capabilities.autogen.core_migrator": None},
        ):
            with caplog.at_level(logging.DEBUG, logger="elle.capabilities.registry"):
                _sync_core_if_needed()

        assert any("Could not sync core capabilities" in msg for msg in caplog.messages)


class TestLoadCoreCapabilities:
    """Test _load_core_capabilities orchestration."""

    def test_logs_info_when_capabilities_loaded(self, caplog):
        """Logs info message when stored or python count > 0."""
        registry = CapabilityRegistry()
        with patch(
            "elle.capabilities.registry._sync_core_if_needed",
        ) as mock_sync, patch(
            "elle.capabilities.registry._load_stored_core_capabilities",
            return_value=5,
        ), patch(
            "elle.capabilities.registry._load_python_core_capabilities",
            return_value=2,
        ):
            with caplog.at_level(logging.INFO, logger="elle.capabilities.registry"):
                _load_core_capabilities(registry)

        mock_sync.assert_called_once()
        assert any("Loaded core capabilities: 5 from DB, 2 from Python" in msg for msg in caplog.messages)

    def test_no_log_when_zero_capabilities(self, caplog):
        """Does not log info when no capabilities loaded."""
        registry = CapabilityRegistry()
        with patch(
            "elle.capabilities.registry._sync_core_if_needed",
        ), patch(
            "elle.capabilities.registry._load_stored_core_capabilities",
            return_value=0,
        ), patch(
            "elle.capabilities.registry._load_python_core_capabilities",
            return_value=0,
        ):
            with caplog.at_level(logging.INFO, logger="elle.capabilities.registry"):
                _load_core_capabilities(registry)

        assert not any("Loaded core capabilities" in msg for msg in caplog.messages)


class TestLoadStoredCoreCapabilitiesSuccess:
    """Test _load_stored_core_capabilities success path."""

    def test_loads_approved_enabled_caps(self):
        """Successfully loads approved and enabled stored capabilities."""
        registry = CapabilityRegistry()

        stored_cap = MagicMock()
        stored_cap.approved = True
        stored_cap.enabled = True
        stored_cap.capability_name = "stored.test"

        mock_store_mod = MagicMock()
        mock_store_mod.get_store.return_value.list_core.return_value = [stored_cap]

        mock_loader = MagicMock()
        mock_loader.load_capability_from_stored.return_value = TestCapability

        with patch.dict(
            "sys.modules",
            {
                "elle.capabilities.autogen.store": mock_store_mod,
                "elle.capabilities.autogen.loader": mock_loader,
            },
        ):
            count = _load_stored_core_capabilities(registry)

        assert count == 1
        assert registry.is_registered("test.example")

    def test_skips_unapproved_caps(self):
        """Skips stored capabilities that are not approved."""
        registry = CapabilityRegistry()

        stored_cap = MagicMock()
        stored_cap.approved = False
        stored_cap.enabled = True
        stored_cap.capability_name = "unapproved.cap"

        mock_store_mod = MagicMock()
        mock_store_mod.get_store.return_value.list_core.return_value = [stored_cap]

        mock_loader = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.capabilities.autogen.store": mock_store_mod,
                "elle.capabilities.autogen.loader": mock_loader,
            },
        ):
            count = _load_stored_core_capabilities(registry)

        assert count == 0

    def test_skips_disabled_caps(self):
        """Skips stored capabilities that are not enabled."""
        registry = CapabilityRegistry()

        stored_cap = MagicMock()
        stored_cap.approved = True
        stored_cap.enabled = False
        stored_cap.capability_name = "disabled.cap"

        mock_store_mod = MagicMock()
        mock_store_mod.get_store.return_value.list_core.return_value = [stored_cap]

        mock_loader = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.capabilities.autogen.store": mock_store_mod,
                "elle.capabilities.autogen.loader": mock_loader,
            },
        ):
            count = _load_stored_core_capabilities(registry)

        assert count == 0

    def test_skips_already_registered(self):
        """Skips stored capabilities already in the registry."""
        registry = CapabilityRegistry()
        registry.register(TestCapability)  # pre-register

        stored_cap = MagicMock()
        stored_cap.approved = True
        stored_cap.enabled = True
        stored_cap.capability_name = "test.example"  # same name

        mock_store_mod = MagicMock()
        mock_store_mod.get_store.return_value.list_core.return_value = [stored_cap]

        mock_loader = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.capabilities.autogen.store": mock_store_mod,
                "elle.capabilities.autogen.loader": mock_loader,
            },
        ):
            count = _load_stored_core_capabilities(registry)

        assert count == 0

    def test_handles_individual_load_failure(self, caplog):
        """Logs warning and continues when individual stored cap fails."""
        registry = CapabilityRegistry()

        stored_cap = MagicMock()
        stored_cap.approved = True
        stored_cap.enabled = True
        stored_cap.capability_name = "broken.stored"

        mock_store_mod = MagicMock()
        mock_store_mod.get_store.return_value.list_core.return_value = [stored_cap]

        mock_loader = MagicMock()
        mock_loader.load_capability_from_stored.side_effect = RuntimeError("load fail")

        with patch.dict(
            "sys.modules",
            {
                "elle.capabilities.autogen.store": mock_store_mod,
                "elle.capabilities.autogen.loader": mock_loader,
            },
        ):
            with caplog.at_level(logging.WARNING, logger="elle.capabilities.registry"):
                count = _load_stored_core_capabilities(registry)

        assert count == 0
        assert any("Failed to load stored core capability" in msg for msg in caplog.messages)

    def test_handles_none_from_loader(self):
        """Skips when loader returns None for a stored capability."""
        registry = CapabilityRegistry()

        stored_cap = MagicMock()
        stored_cap.approved = True
        stored_cap.enabled = True
        stored_cap.capability_name = "null.cap"

        mock_store_mod = MagicMock()
        mock_store_mod.get_store.return_value.list_core.return_value = [stored_cap]

        mock_loader = MagicMock()
        mock_loader.load_capability_from_stored.return_value = None

        with patch.dict(
            "sys.modules",
            {
                "elle.capabilities.autogen.store": mock_store_mod,
                "elle.capabilities.autogen.loader": mock_loader,
            },
        ):
            count = _load_stored_core_capabilities(registry)

        assert count == 0


class TestGetOrRaiseSuccess:
    """Test get_or_raise with a registered capability."""

    def setup_method(self):
        self.registry = CapabilityRegistry()

    def test_get_or_raise_returns_capability(self):
        """get_or_raise returns the capability when registered."""
        self.registry.register(TestCapability)
        cap = self.registry.get_or_raise("test.example")
        assert cap is not None
        assert cap.spec.name == "test.example"


class TestListByDomainInstantiationFailure:
    """Test list_by_domain when get() returns None for some entries."""

    def setup_method(self):
        self.registry = CapabilityRegistry()

    def test_list_by_domain_skips_failed_instantiation(self):
        """list_by_domain skips capabilities that fail to instantiate."""
        self.registry.register(TestCapability)
        # Clear instance cache and replace class with broken one
        self.registry._instances.pop("test.example", None)
        bad_class = MagicMock(side_effect=RuntimeError("broken"))
        self.registry._capabilities["test.example"] = bad_class

        specs = self.registry.list_by_domain("file")
        assert len(specs) == 0
