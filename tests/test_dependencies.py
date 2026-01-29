"""Tests for the dependency fallback system."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from elle.capabilities.dependencies.checker import (
    DependencyChecker,
    get_checker,
    reset_checker,
)
from elle.capabilities.dependencies.core import (
    ALLOWED_PACKAGES,
    AUGEAS_DEPENDENCY,
    CORE_DEPENDENCIES,
)
from elle.capabilities.dependencies.installer import (
    DependencyInstaller,
    DependencySecurityError,
)
from elle.capabilities.dependencies.models import (
    DependencyCheckResult,
    DependencyPreference,
    DependencySpec,
    InstallationRequest,
    InstallationResult,
)
from elle.capabilities.dependencies.registry import (
    DependencyRegistry,
    get_registry,
    reset_registry,
)
from elle.capabilities.dependencies.store import (
    DependencyPreferenceStore,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def registry():
    """Create a fresh registry for testing."""
    return DependencyRegistry()


@pytest.fixture
def test_dependency():
    """Create a test dependency."""
    return DependencySpec(
        name="test-dep",
        display_name="Test Dependency",
        description="A test dependency",
        apt_packages=("test-package",),
        check_command="test-cmd",
        required_by=("test.capability",),
    )


@pytest.fixture
def temp_db_path():
    """Create a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test_deps.db"


@pytest.fixture
def store(temp_db_path):
    """Create a store with temporary database."""
    s = DependencyPreferenceStore(db_path=temp_db_path)
    yield s
    s.close()


# =============================================================================
# Model Tests
# =============================================================================


class TestDependencyModels:
    """Tests for dependency data models."""

    def test_dependency_spec_creation(self):
        """Test creating a DependencySpec."""
        spec = DependencySpec(
            name="test",
            display_name="Test",
            description="Test dependency",
            apt_packages=("pkg1", "pkg2"),
            check_import="test.module",
        )

        assert spec.name == "test"
        assert spec.apt_packages == ("pkg1", "pkg2")
        assert spec.check_import == "test.module"
        assert spec.optional is False

    def test_dependency_spec_frozen(self):
        """Test that DependencySpec is frozen."""
        spec = DependencySpec(
            name="test",
            display_name="Test",
            description="Test dependency",
        )

        with pytest.raises(Exception):  # ValidationError for frozen model
            spec.name = "changed"

    def test_check_result_creation(self):
        """Test creating a DependencyCheckResult."""
        result = DependencyCheckResult(
            dependency="test",
            available=False,
            check_method="command",
            error_message="Command not found",
            install_command="sudo apt install test",
        )

        assert result.dependency == "test"
        assert result.available is False
        assert result.install_command is not None

    def test_preference_creation(self):
        """Test creating a DependencyPreference."""
        pref = DependencyPreference(
            dependency="test",
            preference="always_install",
        )

        assert pref.dependency == "test"
        assert pref.preference == "always_install"
        assert isinstance(pref.created_at, datetime)

    def test_installation_request_creation(self):
        """Test creating an InstallationRequest."""
        req = InstallationRequest(
            dependency="test",
            apt_packages=("pkg1", "pkg2"),
            reason="Testing",
        )

        assert req.dependency == "test"
        assert req.apt_packages == ("pkg1", "pkg2")
        assert req.reason == "Testing"

    def test_installation_result_creation(self):
        """Test creating an InstallationResult."""
        result = InstallationResult(
            success=True,
            dependency="test",
            installed_packages=("pkg1",),
            duration_sec=5.0,
        )

        assert result.success is True
        assert result.installed_packages == ("pkg1",)


# =============================================================================
# Registry Tests
# =============================================================================


class TestDependencyRegistry:
    """Tests for DependencyRegistry."""

    def test_register_dependency(self, registry, test_dependency):
        """Test registering a dependency."""
        registry.register(test_dependency)

        assert registry.has("test-dep")
        assert len(registry) == 1

    def test_register_duplicate_raises(self, registry, test_dependency):
        """Test that registering duplicate raises error."""
        registry.register(test_dependency)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(test_dependency)

    def test_get_dependency(self, registry, test_dependency):
        """Test getting a registered dependency."""
        registry.register(test_dependency)

        dep = registry.get("test-dep")
        assert dep is not None
        assert dep.name == "test-dep"

    def test_get_nonexistent_dependency(self, registry):
        """Test getting a non-existent dependency."""
        dep = registry.get("nonexistent")
        assert dep is None

    def test_get_for_capability(self, registry, test_dependency):
        """Test getting dependencies for a capability."""
        registry.register(test_dependency)

        deps = registry.get_for_capability("test.capability")
        assert len(deps) == 1
        assert deps[0].name == "test-dep"

    def test_get_for_unknown_capability(self, registry):
        """Test getting dependencies for unknown capability."""
        deps = registry.get_for_capability("unknown.capability")
        assert deps == []

    def test_list_all(self, registry, test_dependency):
        """Test listing all dependencies."""
        registry.register(test_dependency)

        all_deps = registry.list_all()
        assert len(all_deps) == 1

    def test_list_names(self, registry, test_dependency):
        """Test listing dependency names."""
        registry.register(test_dependency)

        names = registry.list_names()
        assert "test-dep" in names


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

    def test_registry_has_core_dependencies(self):
        """Test that registry loads core dependencies."""
        registry = get_registry()

        # Should have core dependencies registered
        assert registry.has("augeas")
        assert registry.has("docker")
        assert registry.has("wireguard")


# =============================================================================
# Checker Tests
# =============================================================================


class TestDependencyChecker:
    """Tests for DependencyChecker."""

    def setup_method(self):
        """Reset global resources before each test."""
        reset_registry()
        reset_checker()

    def teardown_method(self):
        """Reset global resources after each test."""
        reset_registry()
        reset_checker()

    def test_check_nonexistent_dependency(self):
        """Test checking a dependency that doesn't exist."""
        registry = get_registry()
        checker = DependencyChecker(registry)

        result = checker.check("nonexistent")
        assert result.available is False
        assert "Unknown dependency" in result.error_message

    def test_check_with_command(self):
        """Test checking a dependency via command."""
        # Test with a command that should exist (python3)
        registry = DependencyRegistry()
        dep = DependencySpec(
            name="python",
            display_name="Python",
            description="Python interpreter",
            apt_packages=("python3",),
            check_command="python3",
        )
        registry.register(dep)

        checker = DependencyChecker(registry)
        result = checker.check("python")

        # python3 should exist on most systems
        assert result.check_method in ("command", "cached")

    def test_check_with_file(self):
        """Test checking a dependency via file existence."""
        registry = DependencyRegistry()
        dep = DependencySpec(
            name="etc",
            display_name="Etc Dir",
            description="etc directory",
            check_file="/etc",
        )
        registry.register(dep)

        checker = DependencyChecker(registry)
        result = checker.check("etc")

        assert result.available is True

    def test_check_missing_file(self):
        """Test checking a dependency with missing file."""
        registry = DependencyRegistry()
        dep = DependencySpec(
            name="missing",
            display_name="Missing",
            description="Missing file",
            check_file="/nonexistent/path/that/should/not/exist",
        )
        registry.register(dep)

        checker = DependencyChecker(registry)
        result = checker.check("missing")

        assert result.available is False
        assert result.install_command is None  # No apt packages

    def test_cache_works(self):
        """Test that cache is used on subsequent checks."""
        registry = DependencyRegistry()
        dep = DependencySpec(
            name="cached",
            display_name="Cached",
            description="Cached dep",
            check_command="python3",
        )
        registry.register(dep)

        checker = DependencyChecker(registry)

        # First check
        checker.check("cached")
        # Second check should use cache
        result2 = checker.check("cached")

        assert result2.check_method == "cached"

    def test_invalidate_cache(self):
        """Test cache invalidation."""
        registry = DependencyRegistry()
        dep = DependencySpec(
            name="cached",
            display_name="Cached",
            description="Cached dep",
            check_command="python3",
        )
        registry.register(dep)

        checker = DependencyChecker(registry)
        checker.check("cached")

        checker.invalidate_cache("cached")
        result = checker.check("cached")

        assert result.check_method != "cached"

    def test_check_for_capability(self):
        """Test checking dependencies for a capability."""
        checker = get_checker()

        # Config capabilities require augeas
        missing = checker.check_for_capability("config.edit")

        # Result depends on whether augeas is installed
        # Just verify it returns a list
        assert isinstance(missing, list)


# =============================================================================
# Store Tests
# =============================================================================


class TestDependencyPreferenceStore:
    """Tests for DependencyPreferenceStore."""

    def test_get_default_preference(self, store):
        """Test that default preference is 'ask'."""
        pref = store.get_preference("unknown")
        assert pref == "ask"

    def test_set_and_get_preference(self, store):
        """Test setting and getting a preference."""
        store.set_preference("test", "always_install")
        pref = store.get_preference("test")

        assert pref == "always_install"

    def test_update_preference(self, store):
        """Test updating an existing preference."""
        store.set_preference("test", "always_install")
        store.set_preference("test", "always_skip")

        pref = store.get_preference("test")
        assert pref == "always_skip"

    def test_get_preference_full(self, store):
        """Test getting full preference record."""
        store.set_preference("test", "always_install")

        pref = store.get_preference_full("test")
        assert pref is not None
        assert pref.dependency == "test"
        assert pref.preference == "always_install"
        assert isinstance(pref.created_at, datetime)

    def test_delete_preference(self, store):
        """Test deleting a preference."""
        store.set_preference("test", "always_install")
        deleted = store.delete_preference("test")

        assert deleted is True
        assert store.get_preference("test") == "ask"

    def test_delete_nonexistent_preference(self, store):
        """Test deleting non-existent preference."""
        deleted = store.delete_preference("nonexistent")
        assert deleted is False

    def test_list_preferences(self, store):
        """Test listing all preferences."""
        store.set_preference("a", "always_install")
        store.set_preference("b", "always_skip")

        prefs = store.list_preferences()
        assert len(prefs) == 2
        names = {p.dependency for p in prefs}
        assert "a" in names
        assert "b" in names

    def test_record_installation(self, store):
        """Test recording installation history."""
        result = InstallationResult(
            success=True,
            dependency="test",
            installed_packages=("pkg1", "pkg2"),
            duration_sec=3.5,
        )

        record_id = store.record_installation(result)
        assert record_id is not None

    def test_get_installation_history(self, store):
        """Test getting installation history."""
        result1 = InstallationResult(
            success=True,
            dependency="test",
            installed_packages=("pkg1",),
        )
        result2 = InstallationResult(
            success=False,
            dependency="test",
            error_message="Failed",
        )

        store.record_installation(result1)
        store.record_installation(result2)

        history = store.get_installation_history("test")
        assert len(history) == 2

    def test_was_recently_installed(self, store):
        """Test checking recent installation."""
        result = InstallationResult(
            success=True,
            dependency="test",
        )
        store.record_installation(result)

        assert store.was_recently_installed("test") is True
        assert store.was_recently_installed("other") is False


# =============================================================================
# Installer Tests
# =============================================================================


class TestDependencyInstaller:
    """Tests for DependencyInstaller."""

    def test_validate_allowed_packages(self, store):
        """Test that allowed packages pass validation."""
        registry = get_registry()
        checker = DependencyChecker(registry)
        installer = DependencyInstaller(registry, checker, store)

        # These should not raise
        assert installer.can_install(("augeas-tools",))
        assert installer.can_install(("docker.io",))
        assert installer.can_install(("wireguard",))

    def test_validate_disallowed_packages(self, store):
        """Test that disallowed packages are rejected."""
        registry = get_registry()
        checker = DependencyChecker(registry)
        installer = DependencyInstaller(registry, checker, store)

        # Arbitrary packages should not be allowed
        assert not installer.can_install(("some-random-package",))
        assert not installer.can_install(("malicious-package",))

    def test_get_disallowed_packages(self, store):
        """Test getting list of disallowed packages."""
        registry = get_registry()
        checker = DependencyChecker(registry)
        installer = DependencyInstaller(registry, checker, store)

        disallowed = installer.get_disallowed_packages(("augeas-tools", "malicious", "docker.io", "evil"))

        assert "malicious" in disallowed
        assert "evil" in disallowed
        assert "augeas-tools" not in disallowed
        assert "docker.io" not in disallowed

    @pytest.mark.asyncio
    async def test_install_disallowed_package_raises(self, store):
        """Test that installing disallowed package raises error."""
        registry = get_registry()
        checker = DependencyChecker(registry)
        installer = DependencyInstaller(registry, checker, store)

        request = InstallationRequest(
            dependency="malicious",
            apt_packages=("malicious-package",),
            reason="Testing",
        )

        with pytest.raises(DependencySecurityError):
            await installer.install(request)


# =============================================================================
# Core Dependencies Tests
# =============================================================================


class TestCoreDependencies:
    """Tests for core dependency definitions."""

    def test_augeas_dependency(self):
        """Test Augeas dependency definition."""
        assert AUGEAS_DEPENDENCY.name == "augeas"
        assert "augeas-tools" in AUGEAS_DEPENDENCY.apt_packages
        assert AUGEAS_DEPENDENCY.check_command == "augtool"

    def test_core_dependencies_count(self):
        """Test that core dependencies are defined."""
        assert len(CORE_DEPENDENCIES) >= 3

    def test_allowed_packages_includes_core(self):
        """Test that ALLOWED_PACKAGES includes packages from core deps."""
        for dep in CORE_DEPENDENCIES:
            for pkg in dep.apt_packages:
                assert pkg in ALLOWED_PACKAGES, f"Package {pkg} not in ALLOWED_PACKAGES"

    def test_allowed_packages_is_frozenset(self):
        """Test that ALLOWED_PACKAGES cannot be modified."""
        assert isinstance(ALLOWED_PACKAGES, frozenset)
