"""Tests for capabilities/dependencies/ coverage.

Covers:
- dependencies/schema.py: DDL constants, _migrate_to_v1, ensure_schema
- dependencies/checker.py: Uncovered branches in check(), _run_checks(), _check_import(), etc.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from elle.capabilities.dependencies.models import (
    DependencyCheckResult,
    DependencyPreference,
    DependencySpec,
    InstallationRequest,
    InstallationResult,
)

# ===========================================================================
# Model instantiation tests (boost coverage for models.py)
# ===========================================================================


class TestDependencyModels:
    """Test that all dependency models can be instantiated."""

    def test_dependency_spec(self):
        spec = DependencySpec(
            name="test-dep",
            display_name="Test Dep",
            description="A test dependency",
            apt_packages=("test-pkg",),
            check_command="test-cmd",
        )
        assert spec.name == "test-dep"
        assert spec.apt_packages == ("test-pkg",)
        assert spec.optional is False

    def test_dependency_check_result(self):
        result = DependencyCheckResult(
            dependency="test-dep",
            available=True,
            check_method="command",
        )
        assert result.available is True
        assert result.install_command is None

    def test_dependency_check_result_unavailable(self):
        result = DependencyCheckResult(
            dependency="test-dep",
            available=False,
            check_method="import",
            error_message="Module not found",
            install_command="sudo apt install test-pkg",
        )
        assert result.available is False
        assert "apt" in result.install_command

    def test_dependency_preference(self):
        pref = DependencyPreference(
            dependency="test-dep",
            preference="always_install",
        )
        assert pref.preference == "always_install"
        assert pref.created_at is not None

    def test_installation_request(self):
        req = InstallationRequest(
            dependency="test-dep",
            apt_packages=("test-pkg",),
            reason="needed for config editing",
        )
        assert req.dependency == "test-dep"

    def test_installation_result_success(self):
        result = InstallationResult(
            success=True,
            dependency="test-dep",
            installed_packages=("test-pkg",),
            duration_sec=3.5,
        )
        assert result.success is True
        assert result.error_message is None

    def test_installation_result_failure(self):
        result = InstallationResult(
            success=False,
            dependency="test-dep",
            error_message="E: Unable to locate package",
        )
        assert result.success is False


# ===========================================================================
# DependencyChecker tests
# ===========================================================================


from elle.capabilities.dependencies.checker import DependencyChecker
from elle.capabilities.dependencies.registry import DependencyRegistry


def _make_checker_and_registry():
    """Create a DependencyChecker with a fresh registry."""
    reg = DependencyRegistry()
    return DependencyChecker(reg, cache_ttl=300.0), reg


def _make_dep(**kwargs) -> DependencySpec:
    """Shorthand to build a DependencySpec with sensible defaults."""
    defaults = {
        "name": "test-dep",
        "display_name": "Test",
        "description": "test",
    }
    defaults.update(kwargs)
    return DependencySpec(**defaults)


class TestDependencyCheckerUnknownDep:
    def test_check_unknown_returns_unavailable(self):
        checker, reg = _make_checker_and_registry()
        result = checker.check("nonexistent")
        assert result.available is False
        assert result.check_method == "none"
        assert "Unknown" in result.error_message


class TestDependencyCheckerImportCheck:
    def test_check_import_success_simple_module(self):
        """Single-part module import succeeds."""
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(name="json-dep", check_import="json")
        reg.register(dep)

        result = checker.check("json-dep")
        assert result.available is True
        assert result.check_method == "import"

    def test_check_import_nested_module(self):
        """Multi-part module import succeeds (e.g., os.path)."""
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(name="ospath", check_import="os.path")
        reg.register(dep)

        result = checker.check("ospath")
        assert result.available is True

    def test_check_import_failure(self):
        """Import that fails falls through to other checks."""
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(name="missing", check_import="nonexistent_module_xyz")
        reg.register(dep)

        result = checker.check("missing")
        assert result.available is False
        assert "import" in result.error_message

    def test_check_import_nested_attr_fallback(self):
        """Nested import where submodule does not exist as attribute tries submodule import."""
        checker, reg = _make_checker_and_registry()
        # collections.abc is an actual submodule, not just an attribute
        dep = _make_dep(name="col-abc", check_import="collections.abc")
        reg.register(dep)

        result = checker.check("col-abc")
        assert result.available is True


class TestDependencyCheckerCommandCheck:
    def test_check_command_success(self):
        """Command that exists in PATH is found."""
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(name="python-cmd", check_command="python3")
        reg.register(dep)

        result = checker.check("python-cmd")
        assert result.available is True
        assert result.check_method == "command"

    def test_check_command_missing(self):
        """Command that does not exist in PATH."""
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(name="fakecmd", check_command="definitely_not_a_command_xyz")
        reg.register(dep)

        result = checker.check("fakecmd")
        assert result.available is False
        assert "not found" in result.error_message


class TestDependencyCheckerFileCheck:
    def test_check_file_success(self, tmp_path):
        """File that exists."""
        test_file = tmp_path / "dep_marker"
        test_file.write_text("exists")

        checker, reg = _make_checker_and_registry()
        dep = _make_dep(name="filedep", check_file=str(test_file))
        reg.register(dep)

        result = checker.check("filedep")
        assert result.available is True
        assert result.check_method == "file"

    def test_check_file_missing(self):
        """File that does not exist."""
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(name="filedep", check_file="/nonexistent/path/xyz")
        reg.register(dep)

        result = checker.check("filedep")
        assert result.available is False
        assert "not found" in result.error_message


class TestDependencyCheckerNoChecks:
    def test_dep_with_no_checks(self):
        """Dep with no check_import, check_command, check_file."""
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(name="nochecks")
        reg.register(dep)

        result = checker.check("nochecks")
        assert result.available is False
        assert "No checks defined" in result.error_message


class TestDependencyCheckerCache:
    def test_cache_hit(self):
        """Second check uses cache."""
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(name="json-cache", check_import="json")
        reg.register(dep)

        r1 = checker.check("json-cache")
        assert r1.available is True

        r2 = checker.check("json-cache")
        assert r2.available is True
        assert r2.check_method == "cached"

    def test_cache_bypass(self):
        """use_cache=False bypasses cache."""
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(name="json-nocache", check_import="json")
        reg.register(dep)

        checker.check("json-nocache")  # populate cache
        r2 = checker.check("json-nocache", use_cache=False)
        assert r2.check_method == "import"  # Not "cached"

    def test_invalidate_specific(self):
        """Invalidate a specific cache entry."""
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(name="json-inv", check_import="json")
        reg.register(dep)

        checker.check("json-inv")
        checker.invalidate_cache("json-inv")

        r2 = checker.check("json-inv")
        assert r2.check_method == "import"

    def test_invalidate_all(self):
        """Invalidate all cache entries."""
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(name="json-inv-all", check_import="json")
        reg.register(dep)

        checker.check("json-inv-all")
        checker.invalidate_cache()

        r2 = checker.check("json-inv-all")
        assert r2.check_method == "import"


class TestDependencyCheckerCheckAll:
    def test_check_all_returns_list(self):
        checker, reg = _make_checker_and_registry()
        dep1 = _make_dep(name="json-all", check_import="json")
        dep2 = _make_dep(name="missing-all", check_import="nonexistent_xyz")
        reg.register(dep1)
        reg.register(dep2)

        results = checker.check_all(["json-all", "missing-all"])
        assert len(results) == 2
        assert results[0].available is True
        assert results[1].available is False


class TestDependencyCheckerCheckForCapability:
    def test_check_for_capability_returns_missing(self):
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(
            name="miss-cap",
            check_import="nonexistent_xyz",
            required_by=("config.edit",),
        )
        reg.register(dep)

        results = checker.check_for_capability("config.edit")
        assert len(results) == 1
        assert results[0].dependency == "miss-cap"

    def test_check_for_capability_returns_empty_when_all_available(self):
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(
            name="json-cap",
            check_import="json",
            required_by=("json.read",),
        )
        reg.register(dep)

        results = checker.check_for_capability("json.read")
        assert results == []


class TestDependencyCheckerBuildResult:
    def test_build_result_with_apt_packages(self):
        """When unavailable and dep has apt_packages, install_command is set."""
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(name="apt-dep", apt_packages=("pkg-a", "pkg-b"))
        reg.register(dep)

        result = checker._build_result("apt-dep", False)
        assert result.install_command == "sudo apt install pkg-a pkg-b"

    def test_build_result_without_apt_packages(self):
        """When unavailable and no apt_packages, install_command is None."""
        checker, reg = _make_checker_and_registry()
        dep = _make_dep(name="noapt-dep")
        reg.register(dep)

        result = checker._build_result("noapt-dep", False)
        assert result.install_command is None


# ===========================================================================
# Module-level convenience functions
# ===========================================================================


class TestCheckerConvenienceFunctions:
    def test_get_checker_singleton(self):
        from elle.capabilities.dependencies.checker import get_checker, reset_checker

        reset_checker()
        c1 = get_checker()
        c2 = get_checker()
        assert c1 is c2
        reset_checker()

    def test_check_dependency(self):
        from elle.capabilities.dependencies.checker import check_dependency, reset_checker

        reset_checker()
        # "json" should be unknown to the default registry, but function should not raise
        result = check_dependency("json")
        # It may or may not be available depending on core deps, just check it runs
        assert isinstance(result, DependencyCheckResult)
        reset_checker()

    def test_check_dependencies_for_capability(self):
        from elle.capabilities.dependencies.checker import (
            check_dependencies_for_capability,
            reset_checker,
        )

        reset_checker()
        results = check_dependencies_for_capability("nonexistent.capability")
        assert isinstance(results, list)
        reset_checker()


# ===========================================================================
# Schema tests (dependencies/schema.py)
# ===========================================================================


class TestDependencySchema:
    def test_schema_constants_exist(self):
        from elle.capabilities.dependencies.schema import (
            INDEXES,
            INSTALLATION_HISTORY_TABLE,
            PG_SCHEMA,
            PREFERENCES_TABLE,
            SCHEMA_VERSION,
        )

        assert SCHEMA_VERSION == 1
        assert PG_SCHEMA == "deps"
        assert "dependency_preferences" in PREFERENCES_TABLE
        assert "installation_history" in INSTALLATION_HISTORY_TABLE
        assert len(INDEXES) == 2

    def test_migrate_to_v1(self):
        """_migrate_to_v1 calls conn.execute for each DDL statement."""
        from elle.capabilities.dependencies.schema import _migrate_to_v1

        mock_conn = MagicMock()
        _migrate_to_v1(mock_conn)

        # 2 CREATE TABLE + 2 INDEX = 4 calls
        assert mock_conn.execute.call_count == 4

    def test_ensure_schema(self):
        """ensure_schema calls run_migrations."""
        from elle.capabilities.dependencies.schema import ensure_schema

        mock_conn = MagicMock()
        with patch("elle.storage.migrate.run_migrations") as mock_run:
            ensure_schema(mock_conn)
            mock_run.assert_called_once_with(mock_conn, "deps")
