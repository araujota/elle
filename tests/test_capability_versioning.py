"""Tests for the capability versioning system."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from elle.capabilities.autogen.models import (
    GeneratedCapabilitySpec,
    InputFieldSpec,
)
from elle.capabilities.autogen.store import AutogenStore
from elle.capabilities.autogen.versioner import (
    CapabilityVersioner,
    get_versioner,
    reset_versioner,
)


@pytest.fixture
def temp_db():
    """Create a temporary database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        yield Path(f.name)
    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def store(temp_db):
    """Create a store with temporary database."""
    return AutogenStore(temp_db)


@pytest.fixture
def versioner(store):
    """Create a versioner with the store."""
    return CapabilityVersioner(store=store)


@pytest.fixture
def sample_spec():
    """Create a sample capability spec."""
    return GeneratedCapabilitySpec(
        name="nginx.reload",
        display_name="Nginx Reload",
        description="Reload nginx configuration",
        domain="service",
        risk_level="low",
        side_effects=("service_restart",),
        input_fields=(InputFieldSpec(name="config", field_type="str", description="Config path"),),
        command_template="nginx -s reload",
        source_command="nginx",
        confidence_score=0.9,
    )


class TestCapabilityVersioner:
    """Tests for CapabilityVersioner class."""

    def test_init(self, versioner, store):
        """Test versioner initialization."""
        assert versioner._store is store
        assert len(versioner._package_to_capabilities) == 0

    def test_build_package_map_empty(self, versioner):
        """Test building package map with no capabilities."""
        package_map = versioner.build_package_map()

        assert len(package_map) == 0

    def test_build_package_map(self, versioner, store, sample_spec):
        """Test building package map with capabilities."""
        # Save a capability with package info
        store.save(
            spec=sample_spec,
            input_model_code="class Input: pass",
            output_model_code="class Output: pass",
            capability_class_code="class Cap: pass",
            man_page_text="test content",
            source_package="nginx",
            package_version="1.24.0-1ubuntu1",
            binary_path="/usr/sbin/nginx",
        )

        package_map = versioner.build_package_map()

        assert "nginx" in package_map
        assert "nginx.reload" in package_map["nginx"]

    def test_get_watched_packages(self, versioner, store, sample_spec):
        """Test getting watched packages."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
            source_package="nginx",
            package_version="1.24.0",
        )

        versioner.build_package_map()
        watched = versioner.get_watched_packages()

        assert "nginx" in watched

    def test_get_capabilities_for_package(self, versioner, store, sample_spec):
        """Test getting capabilities for a package."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
            source_package="nginx",
        )

        versioner.build_package_map()
        caps = versioner.get_capabilities_for_package("nginx")

        assert "nginx.reload" in caps
        assert versioner.get_capabilities_for_package("unknown") == []

    def test_add_capability_mapping(self, versioner):
        """Test adding capability mapping."""
        versioner.add_capability_mapping("nginx", "nginx.start")
        versioner.add_capability_mapping("nginx", "nginx.stop")
        versioner.add_capability_mapping("systemd", "service.restart")

        assert "nginx.start" in versioner._package_to_capabilities["nginx"]
        assert "nginx.stop" in versioner._package_to_capabilities["nginx"]
        assert "service.restart" in versioner._package_to_capabilities["systemd"]

    def test_add_capability_mapping_no_duplicates(self, versioner):
        """Test adding same capability doesn't create duplicates."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        versioner.add_capability_mapping("nginx", "nginx.reload")

        assert versioner._package_to_capabilities["nginx"].count("nginx.reload") == 1

    @pytest.mark.asyncio
    async def test_on_package_upgraded_no_capabilities(self, versioner):
        """Test handling upgrade for package with no capabilities."""
        result = await versioner.on_package_upgraded("unknown", "1.0", "2.0")

        assert result == []

    @pytest.mark.asyncio
    async def test_on_package_upgraded_same_version(self, versioner, store, sample_spec):
        """Test handling upgrade when version hasn't changed."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
            source_package="nginx",
            package_version="1.24.0",
        )

        versioner.build_package_map()

        result = await versioner.on_package_upgraded("nginx", "1.22.0", "1.24.0")

        assert result == []  # Already at that version

    def test_check_version_mismatch(self, versioner, store, sample_spec):
        """Test checking version mismatch."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
            source_package="nginx",
            package_version="1.24.0",
        )

        # Mismatch
        assert versioner.check_version_mismatch("nginx.reload", "1.26.0") is True

        # Match
        assert versioner.check_version_mismatch("nginx.reload", "1.24.0") is False

        # Unknown capability
        assert versioner.check_version_mismatch("unknown", "1.0.0") is False

    def test_get_stats(self, versioner, store, sample_spec):
        """Test getting versioner statistics."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
            source_package="nginx",
        )

        versioner.build_package_map()
        stats = versioner.get_stats()

        assert stats["packages_tracked"] == 1
        assert stats["capabilities_tracked"] == 1

    def test_get_package_capabilities_summary(self, versioner, store, sample_spec):
        """Test getting package capabilities summary."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
            source_package="nginx",
            package_version="1.24.0",
        )

        versioner.build_package_map()
        summary = versioner.get_package_capabilities_summary()

        assert len(summary) == 1
        assert summary[0]["package"] == "nginx"
        assert summary[0]["version"] == "1.24.0"
        assert "nginx.reload" in summary[0]["capabilities"]


class TestGetVersioner:
    """Tests for get_versioner function."""

    def test_get_versioner_singleton(self, temp_db):
        """Test versioner singleton behavior."""
        reset_versioner()

        store = AutogenStore(temp_db)
        v1 = get_versioner(store)
        v2 = get_versioner(store)

        assert v1 is v2

        reset_versioner()

    def test_reset_versioner(self, temp_db):
        """Test versioner reset."""
        reset_versioner()

        store = AutogenStore(temp_db)
        v1 = get_versioner(store)
        reset_versioner()
        v2 = get_versioner(store)

        assert v1 is not v2

        reset_versioner()


class TestAutogenStorePackageVersioning:
    """Tests for package versioning in AutogenStore."""

    def test_save_with_package_info(self, store, sample_spec):
        """Test saving capability with package info."""
        store.save(
            spec=sample_spec,
            input_model_code="class Input: pass",
            output_model_code="class Output: pass",
            capability_class_code="class Cap: pass",
            man_page_text="test content",
            source_package="nginx",
            package_version="1.24.0-1ubuntu1",
            binary_path="/usr/sbin/nginx",
        )

        stored = store.get("nginx.reload")
        assert stored is not None
        assert stored.source_package == "nginx"
        assert stored.package_version == "1.24.0-1ubuntu1"
        assert stored.binary_path == "/usr/sbin/nginx"

    def test_save_without_package_info(self, store, sample_spec):
        """Test saving capability without package info."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
        )

        stored = store.get("nginx.reload")
        assert stored is not None
        assert stored.source_package is None
        assert stored.package_version is None
        assert stored.binary_path is None

    def test_list_by_package(self, store, sample_spec):
        """Test listing capabilities by package."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
            source_package="nginx",
        )

        # Create another spec
        spec2 = GeneratedCapabilitySpec(
            name="nginx.stop",
            display_name="Nginx Stop",
            description="Stop nginx",
            domain="service",
            risk_level="medium",
            command_template="nginx -s stop",
            source_command="nginx",
        )

        store.save(
            spec=spec2,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
            source_package="nginx",
        )

        caps = store.list_by_package("nginx")
        assert len(caps) == 2
        names = [c.capability_name for c in caps]
        assert "nginx.reload" in names
        assert "nginx.stop" in names

        # Unknown package
        assert store.list_by_package("unknown") == []

    def test_list_packages_with_capabilities(self, store, sample_spec):
        """Test listing packages with capabilities."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
            source_package="nginx",
            package_version="1.24.0",
        )

        packages = store.list_packages_with_capabilities()
        assert len(packages) == 1
        assert packages[0] == ("nginx", "1.24.0")

    def test_update_preserves_package_info(self, store, sample_spec):
        """Test that updating a capability preserves package info."""
        # Initial save with package info
        store.save(
            spec=sample_spec,
            input_model_code="v1",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
            source_package="nginx",
            package_version="1.24.0",
            binary_path="/usr/sbin/nginx",
        )

        # Update with new package version
        store.save(
            spec=sample_spec,
            input_model_code="v2",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
            source_package="nginx",
            package_version="1.26.0",
            binary_path="/usr/sbin/nginx",
        )

        stored = store.get("nginx.reload")
        assert stored.input_model_code == "v2"
        assert stored.package_version == "1.26.0"
