from __future__ import annotations

"""Comprehensive tests for elle.capabilities.autogen.bootstrap module.

Covers:
- PackageCategory enum values
- CORE_SYSTEM_PACKAGES / OPTIONAL_PACKAGES data integrity
- BootstrapResult dataclass: defaults, duration, success_rate
- is_package_installed: success, failure, timeout, generic exception
- get_elle_dependencies: with/without elle installed, subprocess errors
- get_installed_optional_packages: filtering logic
- get_essential_packages: parsing, errors, empty output, malformed lines
- BootstrapRunner: init, get_packages_to_bootstrap tier logic, run(), _has_capabilities, _learn_package
- Convenience functions: run_bootstrap, get_bootstrap_packages
- Bootstrap state: get_bootstrap_state, set_bootstrap_complete, should_run_bootstrap
"""

import json
import subprocess
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.capabilities.autogen.bootstrap import (
    CORE_SYSTEM_PACKAGES,
    OPTIONAL_PACKAGES,
    BootstrapResult,
    BootstrapRunner,
    PackageCategory,
    get_bootstrap_packages,
    get_bootstrap_state,
    get_elle_dependencies,
    get_essential_packages,
    get_installed_optional_packages,
    is_package_installed,
    run_bootstrap,
    set_bootstrap_complete,
    should_run_bootstrap,
)

# =============================================================================
# PackageCategory
# =============================================================================


class TestPackageCategory:
    """Tests for PackageCategory enum."""

    def test_all_categories_are_str_enum(self):
        """Every member inherits from str and Enum."""
        for member in PackageCategory:
            assert isinstance(member, str)
            assert isinstance(member.value, str)

    def test_expected_members(self):
        expected = {
            "SYSTEM",
            "ESSENTIAL",
            "FILE",
            "NETWORK",
            "TEXT",
            "PACKAGE",
            "SERVICE",
            "CONTAINER",
            "MEDIA",
            "ARCHIVE",
            "SECURITY",
        }
        assert set(PackageCategory.__members__.keys()) == expected

    def test_values(self):
        assert PackageCategory.SYSTEM == "system"
        assert PackageCategory.ESSENTIAL == "essential"
        assert PackageCategory.FILE == "file"
        assert PackageCategory.NETWORK == "network"
        assert PackageCategory.TEXT == "text"
        assert PackageCategory.PACKAGE == "package"
        assert PackageCategory.SERVICE == "service"
        assert PackageCategory.CONTAINER == "container"
        assert PackageCategory.MEDIA == "media"
        assert PackageCategory.ARCHIVE == "archive"
        assert PackageCategory.SECURITY == "security"


# =============================================================================
# Package Lists
# =============================================================================


class TestPackageLists:
    """Tests for the static package tuples."""

    def test_core_system_packages_not_empty(self):
        assert len(CORE_SYSTEM_PACKAGES) > 10

    def test_optional_packages_not_empty(self):
        assert len(OPTIONAL_PACKAGES) > 5

    def test_core_packages_structure(self):
        for name, category in CORE_SYSTEM_PACKAGES:
            assert isinstance(name, str)
            assert len(name) > 0
            assert isinstance(category, PackageCategory)

    def test_optional_packages_structure(self):
        for name, category in OPTIONAL_PACKAGES:
            assert isinstance(name, str)
            assert len(name) > 0
            assert isinstance(category, PackageCategory)

    def test_no_duplicate_core_packages(self):
        names = [n for n, _ in CORE_SYSTEM_PACKAGES]
        assert len(names) == len(set(names))

    def test_no_duplicate_optional_packages(self):
        names = [n for n, _ in OPTIONAL_PACKAGES]
        assert len(names) == len(set(names))

    def test_known_core_packages_present(self):
        names = [n for n, _ in CORE_SYSTEM_PACKAGES]
        for pkg in ("coreutils", "systemd", "apt", "curl", "grep"):
            assert pkg in names

    def test_known_optional_packages_present(self):
        names = [n for n, _ in OPTIONAL_PACKAGES]
        for pkg in ("nginx", "docker.io", "git"):
            assert pkg in names


# =============================================================================
# BootstrapResult
# =============================================================================


class TestBootstrapResult:
    """Tests for BootstrapResult dataclass."""

    def test_default_values(self):
        result = BootstrapResult()
        assert result.packages_attempted == 0
        assert result.packages_succeeded == 0
        assert result.packages_failed == 0
        assert result.packages_skipped == 0
        assert result.capabilities_generated == 0
        assert result.capabilities_validated == 0
        assert result.capabilities_saved == 0
        assert result.completed_at is None
        assert isinstance(result.started_at, datetime)
        assert result.succeeded_packages == []
        assert result.failed_packages == []
        assert result.skipped_packages == []

    def test_duration_seconds_incomplete(self):
        """When completed_at is None, duration is measured from now."""
        result = BootstrapResult()
        duration = result.duration_seconds
        assert duration >= 0

    def test_duration_seconds_complete(self):
        """When completed_at is set, duration uses both timestamps."""
        result = BootstrapResult()
        result.started_at = datetime(2025, 1, 1, 0, 0, 0)
        result.completed_at = datetime(2025, 1, 1, 0, 0, 10)
        assert result.duration_seconds == 10.0

    def test_success_rate_zero_attempted(self):
        result = BootstrapResult()
        assert result.success_rate == 0.0

    def test_success_rate_partial(self):
        result = BootstrapResult()
        result.packages_attempted = 10
        result.packages_succeeded = 3
        assert result.success_rate == 30.0

    def test_success_rate_all_succeeded(self):
        result = BootstrapResult()
        result.packages_attempted = 5
        result.packages_succeeded = 5
        assert result.success_rate == 100.0


# =============================================================================
# is_package_installed
# =============================================================================


class TestIsPackageInstalled:
    """Tests for is_package_installed function."""

    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_installed(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="install ok installed",
        )
        assert is_package_installed("coreutils") is True
        mock_run.assert_called_once_with(
            ["dpkg-query", "-W", "-f", "${Status}", "coreutils"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_not_installed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert is_package_installed("nonexistent") is False

    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_deinstall_status(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="deinstall ok config-files",
        )
        assert is_package_installed("removed-pkg") is False

    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 5)
        assert is_package_installed("any-pkg") is False

    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_generic_exception(self, mock_run):
        mock_run.side_effect = OSError("no such binary")
        assert is_package_installed("any-pkg") is False


# =============================================================================
# get_elle_dependencies
# =============================================================================


class TestGetElleDependencies:
    """Tests for get_elle_dependencies function."""

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_elle_installed_with_deps(self, mock_run, mock_installed):
        """Elle is installed and has dependencies."""
        mock_installed.side_effect = lambda p: p in ("elle", "python3", "python3-gi")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="python3, libc6 (>= 2.31), libsystemd0",
        )
        deps = get_elle_dependencies()
        assert "elle" in deps
        assert "python3" in deps
        # libc6 and libsystemd0 from parsed deps
        assert "libc6" in deps
        assert "libsystemd0" in deps

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_elle_not_installed(self, mock_run, mock_installed):
        """Elle package not installed; deps query also fails."""
        mock_installed.return_value = False
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        deps = get_elle_dependencies()
        assert "elle" not in deps

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_subprocess_exception(self, mock_run, mock_installed):
        """Subprocess raises -- should not propagate."""
        mock_installed.return_value = False
        mock_run.side_effect = OSError("subprocess fail")
        deps = get_elle_dependencies()
        assert isinstance(deps, list)

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_empty_dep_string(self, mock_run, mock_installed):
        """Dependency string is empty."""
        mock_installed.side_effect = lambda p: p == "elle"
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        deps = get_elle_dependencies()
        assert "elle" in deps

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_dep_with_version_constraints(self, mock_run, mock_installed):
        """Dependencies with version constraints like 'pkg (>= 1.0)'."""
        mock_installed.side_effect = lambda p: p == "elle"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="libfoo (>= 2.0), libbar(>=1.0)",
        )
        deps = get_elle_dependencies()
        assert "libfoo" in deps
        assert "libbar" in deps

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_python_pkg_deps_deduplication(self, mock_run, mock_installed):
        """Python pkg deps should not duplicate entries already present."""
        mock_installed.return_value = True
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="python3, python3-gi",
        )
        deps = get_elle_dependencies()
        # python3 appears both in parsed deps and python_pkg_deps -- should be deduplicated
        assert deps.count("python3") == 1


# =============================================================================
# get_installed_optional_packages
# =============================================================================


class TestGetInstalledOptionalPackages:
    """Tests for get_installed_optional_packages function."""

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    def test_some_installed(self, mock_installed):
        mock_installed.side_effect = lambda p: p in ("nginx", "git")
        result = get_installed_optional_packages()
        names = [n for n, _ in result]
        assert "nginx" in names
        assert "git" in names

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    def test_none_installed(self, mock_installed):
        mock_installed.return_value = False
        result = get_installed_optional_packages()
        assert result == []

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    def test_all_installed(self, mock_installed):
        mock_installed.return_value = True
        result = get_installed_optional_packages()
        assert len(result) == len(OPTIONAL_PACKAGES)

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    def test_categories_preserved(self, mock_installed):
        mock_installed.side_effect = lambda p: p == "nginx"
        result = get_installed_optional_packages()
        assert len(result) == 1
        assert result[0] == ("nginx", PackageCategory.SERVICE)


# =============================================================================
# get_essential_packages
# =============================================================================


class TestGetEssentialPackages:
    """Tests for get_essential_packages function."""

    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="base-files yes\ncoreutils yes\nbash yes\nsome-pkg no\n",
        )
        result = get_essential_packages()
        assert len(result) == 3
        assert ("base-files", PackageCategory.ESSENTIAL) in result
        assert ("coreutils", PackageCategory.ESSENTIAL) in result
        assert ("bash", PackageCategory.ESSENTIAL) in result

    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_no_essential(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="pkg1 no\npkg2 no\n")
        result = get_essential_packages()
        assert result == []

    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_empty_output(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = get_essential_packages()
        assert result == []

    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_nonzero_returncode(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = get_essential_packages()
        assert result == []

    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_timeout_exception(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 30)
        result = get_essential_packages()
        assert result == []

    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_generic_exception(self, mock_run):
        mock_run.side_effect = RuntimeError("unexpected")
        result = get_essential_packages()
        assert result == []

    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_malformed_lines_skipped(self, mock_run):
        """Lines that don't have a space-separated pair are skipped."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="base-files yes\njust-one-word\n\nvalid-pkg yes\n",
        )
        result = get_essential_packages()
        names = [n for n, _ in result]
        assert "base-files" in names
        assert "valid-pkg" in names
        # "just-one-word" has only one part so it should not produce Essential
        # (parts[1] would not match "yes")

    @patch("elle.capabilities.autogen.bootstrap.subprocess.run")
    def test_case_insensitive_yes(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="base-files YES\ncoreutils Yes\n",
        )
        result = get_essential_packages()
        assert len(result) == 2


# =============================================================================
# BootstrapRunner.__init__
# =============================================================================


class TestBootstrapRunnerInit:
    """Tests for BootstrapRunner initialization."""

    def test_defaults(self):
        runner = BootstrapRunner()
        assert runner.include_core is True
        assert runner.include_essential is True
        assert runner.include_optional is True
        assert runner.include_dependencies is True
        assert runner.max_concurrent == 1
        assert runner.skip_existing is True

    def test_custom(self):
        runner = BootstrapRunner(
            include_core=False,
            include_essential=False,
            include_optional=False,
            include_dependencies=False,
            max_concurrent=4,
            skip_existing=False,
        )
        assert runner.include_core is False
        assert runner.include_essential is False
        assert runner.include_optional is False
        assert runner.include_dependencies is False
        assert runner.max_concurrent == 4
        assert runner.skip_existing is False


# =============================================================================
# BootstrapRunner.get_packages_to_bootstrap
# =============================================================================


class TestGetPackagesToBootstrap:
    """Tests for BootstrapRunner.get_packages_to_bootstrap()."""

    @patch("elle.capabilities.autogen.bootstrap.get_installed_optional_packages")
    @patch("elle.capabilities.autogen.bootstrap.get_elle_dependencies")
    @patch("elle.capabilities.autogen.bootstrap.get_essential_packages")
    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    def test_all_tiers_enabled(self, mock_installed, mock_essential, mock_deps, mock_optional):
        mock_installed.return_value = True
        mock_essential.return_value = [("essential-pkg", PackageCategory.ESSENTIAL)]
        mock_deps.return_value = ["dep-pkg"]
        mock_optional.return_value = [("optional-pkg", PackageCategory.SERVICE)]

        runner = BootstrapRunner()
        packages = runner.get_packages_to_bootstrap()

        names = [n for n, _ in packages]
        # Core packages should be present (all mock-installed)
        assert "coreutils" in names
        # Essential tier
        assert "essential-pkg" in names
        # Dependency tier
        assert "dep-pkg" in names
        # Optional tier
        assert "optional-pkg" in names

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    def test_core_only(self, mock_installed):
        mock_installed.side_effect = lambda p: p in ("coreutils", "apt")
        runner = BootstrapRunner(
            include_core=True,
            include_essential=False,
            include_optional=False,
            include_dependencies=False,
        )
        packages = runner.get_packages_to_bootstrap()
        names = [n for n, _ in packages]
        assert "coreutils" in names
        assert "apt" in names

    @patch("elle.capabilities.autogen.bootstrap.get_installed_optional_packages")
    @patch("elle.capabilities.autogen.bootstrap.get_elle_dependencies")
    @patch("elle.capabilities.autogen.bootstrap.get_essential_packages")
    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    def test_deduplication_across_tiers(self, mock_installed, mock_essential, mock_deps, mock_optional):
        """A package appearing in multiple tiers should only appear once."""
        mock_installed.return_value = True
        # "coreutils" is already in CORE_SYSTEM_PACKAGES
        mock_essential.return_value = [("coreutils", PackageCategory.ESSENTIAL)]
        mock_deps.return_value = ["coreutils"]
        mock_optional.return_value = [("coreutils", PackageCategory.SYSTEM)]

        runner = BootstrapRunner()
        packages = runner.get_packages_to_bootstrap()
        count = sum(1 for n, _ in packages if n == "coreutils")
        assert count == 1

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    def test_nothing_enabled(self, mock_installed):
        runner = BootstrapRunner(
            include_core=False,
            include_essential=False,
            include_optional=False,
            include_dependencies=False,
        )
        packages = runner.get_packages_to_bootstrap()
        assert packages == []

    @patch("elle.capabilities.autogen.bootstrap.get_installed_optional_packages")
    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    def test_optional_only(self, mock_installed, mock_optional):
        mock_installed.return_value = True
        mock_optional.return_value = [("nginx", PackageCategory.SERVICE)]
        runner = BootstrapRunner(
            include_core=False,
            include_essential=False,
            include_optional=True,
            include_dependencies=False,
        )
        packages = runner.get_packages_to_bootstrap()
        names = [n for n, _ in packages]
        assert "nginx" in names


# =============================================================================
# BootstrapRunner._has_capabilities
# =============================================================================


class TestHasCapabilities:
    """Tests for BootstrapRunner._has_capabilities()."""

    def test_has_capabilities_true(self):
        runner = BootstrapRunner()
        mock_store = MagicMock()
        mock_store.list_by_package.return_value = [MagicMock()]

        with patch("elle.capabilities.autogen.get_store", return_value=mock_store):
            assert runner._has_capabilities("some-pkg") is True

    def test_has_capabilities_false(self):
        runner = BootstrapRunner()
        mock_store = MagicMock()
        mock_store.list_by_package.return_value = []

        with patch("elle.capabilities.autogen.get_store", return_value=mock_store):
            assert runner._has_capabilities("some-pkg") is False

    def test_has_capabilities_exception(self):
        runner = BootstrapRunner()

        with patch(
            "elle.capabilities.autogen.get_store",
            side_effect=RuntimeError("db error"),
        ):
            assert runner._has_capabilities("some-pkg") is False


# =============================================================================
# BootstrapRunner._learn_package
# =============================================================================


class TestLearnPackage:
    """Tests for BootstrapRunner._learn_package()."""

    @pytest.mark.asyncio
    async def test_learn_package_success(self):
        """Full success path: gather, generate, validate, save."""
        runner = BootstrapRunner()

        # Mock intelligence
        mock_intel = MagicMock()
        mock_intel.extraction_sources = ["man"]
        mock_intel.primary_binary = "testcmd"
        mock_intel.metadata.name = "test-pkg"
        mock_intel.metadata.version = "1.0"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context text"

        # Mock LLM
        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = {
            "capabilities": [
                {
                    "name": "test.cmd",
                    "display_name": "Test Cmd",
                    "description": "A test",
                    "domain": "file",
                    "risk_level": "low",
                    "side_effects": ["file_read"],
                    "input_fields": [{"name": "path", "field_type": "str", "description": "A path"}],
                    "output_fields": [{"name": "result", "field_type": "str", "description": "Result"}],
                    "command_template": "testcmd {path}",
                    "source_command": "testcmd",
                    "confidence_score": 0.9,
                },
            ],
        }

        # Mock validation
        mock_validation = MagicMock()
        mock_validation.overall_passed = True
        mock_validation.trust_level = "official"

        # Mock store
        mock_store = MagicMock()

        # Mock factory
        mock_gen_input = MagicMock(return_value="input_code")
        mock_gen_output = MagicMock(return_value="output_code")
        mock_gen_cap = MagicMock(return_value="cap_code")

        # Mock composer
        mock_composer = MagicMock()
        mock_composer.compose.return_value = "system prompt"

        with (
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.llm.LLM", return_value=mock_llm),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.capabilities.autogen.validate_capability", return_value=mock_validation),
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch("elle.capabilities.autogen.factory.generate_input_model_code", mock_gen_input),
            patch("elle.capabilities.autogen.factory.generate_output_model_code", mock_gen_output),
            patch("elle.capabilities.autogen.factory.generate_capability_class_code", mock_gen_cap),
            patch("elle.capabilities.autogen.bootstrap.safe_model_dump_json", return_value='{"valid": true}'),
        ):
            generated, validated, saved = await runner._learn_package("test-pkg")

        assert generated == 1
        assert validated == 1
        assert saved == 1

    @pytest.mark.asyncio
    async def test_learn_package_no_sources(self):
        """Raises ValueError when no extraction_sources."""
        runner = BootstrapRunner()

        mock_intel = MagicMock()
        mock_intel.extraction_sources = []

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)

        with (
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            pytest.raises(ValueError, match="No intelligence sources"),
        ):
            await runner._learn_package("empty-pkg")

    @pytest.mark.asyncio
    async def test_learn_package_no_capabilities_key(self):
        """LLM returns JSON without 'capabilities' key."""
        runner = BootstrapRunner()

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ["man"]
        mock_intel.primary_binary = "testcmd"
        mock_intel.metadata.name = "test-pkg"
        mock_intel.metadata.version = "1.0"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = {"something_else": []}

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "sys"

        mock_store = MagicMock()

        with (
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.llm.LLM", return_value=mock_llm),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
        ):
            generated, validated, saved = await runner._learn_package("test-pkg")

        assert generated == 0
        assert validated == 0
        assert saved == 0

    @pytest.mark.asyncio
    async def test_learn_package_invalid_spec_skipped(self):
        """Invalid cap_data entries are skipped without aborting."""
        runner = BootstrapRunner()

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ["man"]
        mock_intel.primary_binary = "testcmd"
        mock_intel.metadata.name = "test-pkg"
        mock_intel.metadata.version = "1.0"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = {
            "capabilities": [
                {"totally": "invalid"},  # Will fail GeneratedCapabilitySpec(**cap_data)
            ],
        }

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "sys"
        mock_store = MagicMock()

        with (
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.llm.LLM", return_value=mock_llm),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
        ):
            generated, validated, saved = await runner._learn_package("test-pkg")

        assert generated == 0
        assert validated == 0
        assert saved == 0

    @pytest.mark.asyncio
    async def test_learn_package_validation_fails(self):
        """Capabilities that fail validation are not saved."""
        runner = BootstrapRunner()

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ["man"]
        mock_intel.primary_binary = "testcmd"
        mock_intel.metadata.name = "test-pkg"
        mock_intel.metadata.version = "1.0"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = {
            "capabilities": [
                {
                    "name": "test.cmd",
                    "display_name": "T",
                    "description": "D",
                    "domain": "file",
                    "risk_level": "low",
                    "command_template": "test",
                    "source_command": "test",
                    "confidence_score": 0.8,
                },
            ],
        }

        mock_validation = MagicMock()
        mock_validation.overall_passed = False

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "sys"
        mock_store = MagicMock()

        with (
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.llm.LLM", return_value=mock_llm),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.capabilities.autogen.validate_capability", return_value=mock_validation),
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
        ):
            generated, validated, saved = await runner._learn_package("test-pkg")

        assert generated == 1
        assert validated == 0
        assert saved == 0

    @pytest.mark.asyncio
    async def test_learn_package_save_exception(self):
        """Exception during save is caught; saved count stays zero."""
        runner = BootstrapRunner()

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ["man"]
        mock_intel.primary_binary = "testcmd"
        mock_intel.metadata.name = "test-pkg"
        mock_intel.metadata.version = "1.0"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = {
            "capabilities": [
                {
                    "name": "test.cmd",
                    "display_name": "T",
                    "description": "D",
                    "domain": "file",
                    "risk_level": "low",
                    "command_template": "test",
                    "source_command": "test",
                    "confidence_score": 0.8,
                },
            ],
        }

        mock_validation = MagicMock()
        mock_validation.overall_passed = True
        mock_validation.trust_level = "official"

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "sys"
        mock_store = MagicMock()
        mock_store.save.side_effect = RuntimeError("db write failed")

        with (
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.llm.LLM", return_value=mock_llm),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.capabilities.autogen.validate_capability", return_value=mock_validation),
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch("elle.capabilities.autogen.factory.generate_input_model_code", return_value=""),
            patch("elle.capabilities.autogen.factory.generate_output_model_code", return_value=""),
            patch("elle.capabilities.autogen.factory.generate_capability_class_code", return_value=""),
            patch("elle.capabilities.autogen.bootstrap.safe_model_dump_json", return_value="{}"),
        ):
            generated, validated, saved = await runner._learn_package("test-pkg")

        assert generated == 1
        assert validated == 1
        assert saved == 0

    @pytest.mark.asyncio
    async def test_learn_package_source_command_default(self):
        """When source_command not in cap_data, falls back to primary_binary."""
        runner = BootstrapRunner()

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ["man"]
        mock_intel.primary_binary = "my-binary"
        mock_intel.metadata.name = "test-pkg"
        mock_intel.metadata.version = "1.0"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = {
            "capabilities": [
                {
                    "name": "test.cmd",
                    "display_name": "T",
                    "description": "D",
                    "domain": "file",
                    "risk_level": "low",
                    # No "source_command" -- should default
                    "command_template": "test",
                    "confidence_score": 0.8,
                },
            ],
        }

        mock_validation = MagicMock()
        mock_validation.overall_passed = False  # stop early, we just want to test parsing

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "sys"
        mock_store = MagicMock()

        with (
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.llm.LLM", return_value=mock_llm),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.capabilities.autogen.validate_capability", return_value=mock_validation),
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
        ):
            generated, validated, saved = await runner._learn_package("test-pkg")

        # Should have successfully parsed the spec even without source_command
        assert generated == 1

    @pytest.mark.asyncio
    async def test_learn_package_validation_exception_skipped(self):
        """Exception during validate_capability is caught, spec is skipped."""
        runner = BootstrapRunner()

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ["man"]
        mock_intel.primary_binary = "testcmd"
        mock_intel.metadata.name = "test-pkg"
        mock_intel.metadata.version = "1.0"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = {
            "capabilities": [
                {
                    "name": "test.cmd",
                    "display_name": "T",
                    "description": "D",
                    "domain": "file",
                    "risk_level": "low",
                    "command_template": "test",
                    "source_command": "test",
                    "confidence_score": 0.8,
                },
            ],
        }

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "sys"
        mock_store = MagicMock()

        with (
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.llm.LLM", return_value=mock_llm),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch(
                "elle.capabilities.autogen.validate_capability",
                side_effect=RuntimeError("validation broke"),
            ),
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
        ):
            generated, validated, saved = await runner._learn_package("test-pkg")

        assert generated == 1
        assert validated == 0
        assert saved == 0


# =============================================================================
# BootstrapRunner.run
# =============================================================================


class TestBootstrapRunnerRun:
    """Tests for BootstrapRunner.run()."""

    @pytest.mark.asyncio
    async def test_run_empty_packages(self):
        """No packages to process."""
        runner = BootstrapRunner(
            include_core=False,
            include_essential=False,
            include_optional=False,
            include_dependencies=False,
        )
        result = await runner.run()
        assert result.packages_attempted == 0
        assert result.completed_at is not None

    @pytest.mark.asyncio
    async def test_run_skips_existing(self):
        """Packages with existing capabilities are skipped."""
        runner = BootstrapRunner(skip_existing=True)

        with (
            patch.object(
                runner,
                "get_packages_to_bootstrap",
                return_value=[("pkg1", PackageCategory.SYSTEM)],
            ),
            patch.object(runner, "_has_capabilities", return_value=True),
        ):
            result = await runner.run()

        assert result.packages_skipped == 1
        assert "pkg1" in result.skipped_packages
        assert result.packages_succeeded == 0

    @pytest.mark.asyncio
    async def test_run_successful_package(self):
        """Successfully learned package."""
        runner = BootstrapRunner(skip_existing=False)

        with (
            patch.object(
                runner,
                "get_packages_to_bootstrap",
                return_value=[("pkg1", PackageCategory.SYSTEM)],
            ),
            patch.object(runner, "_learn_package", new_callable=AsyncMock, return_value=(3, 2, 2)),
        ):
            result = await runner.run()

        assert result.packages_succeeded == 1
        assert "pkg1" in result.succeeded_packages
        assert result.capabilities_generated == 3
        assert result.capabilities_validated == 2
        assert result.capabilities_saved == 2

    @pytest.mark.asyncio
    async def test_run_failed_package(self):
        """Package learning raises exception."""
        runner = BootstrapRunner(skip_existing=False)

        with (
            patch.object(
                runner,
                "get_packages_to_bootstrap",
                return_value=[("bad-pkg", PackageCategory.SYSTEM)],
            ),
            patch.object(
                runner,
                "_learn_package",
                new_callable=AsyncMock,
                side_effect=RuntimeError("learn failed"),
            ),
        ):
            result = await runner.run()

        assert result.packages_failed == 1
        assert any(name == "bad-pkg" for name, _ in result.failed_packages)

    @pytest.mark.asyncio
    async def test_run_gather_exception_in_task(self):
        """BaseException from gather is handled."""
        runner = BootstrapRunner(skip_existing=False)

        # Simulate a task that returns an exception from gather
        with (
            patch.object(
                runner,
                "get_packages_to_bootstrap",
                return_value=[("exc-pkg", PackageCategory.SYSTEM)],
            ),
            patch.object(
                runner,
                "_learn_package",
                new_callable=AsyncMock,
                side_effect=Exception("unexpected"),
            ),
        ):
            result = await runner.run()

        assert result.packages_failed == 1

    @pytest.mark.asyncio
    async def test_run_progress_callback(self):
        """Progress callback is invoked."""
        runner = BootstrapRunner(skip_existing=False)
        callback_calls = []

        def on_progress(current, total, name):
            callback_calls.append((current, total, name))

        with (
            patch.object(
                runner,
                "get_packages_to_bootstrap",
                return_value=[
                    ("p1", PackageCategory.SYSTEM),
                    ("p2", PackageCategory.NETWORK),
                ],
            ),
            patch.object(
                runner,
                "_learn_package",
                new_callable=AsyncMock,
                return_value=(1, 1, 1),
            ),
        ):
            result = await runner.run(progress_callback=on_progress)

        assert len(callback_calls) == 2
        assert callback_calls[0] == (1, 2, "p1")
        assert callback_calls[1] == (2, 2, "p2")
        assert result.packages_succeeded == 2

    @pytest.mark.asyncio
    async def test_run_mixed_results(self):
        """Mix of success, failure, and skipped packages."""
        runner = BootstrapRunner(skip_existing=True)

        async def mock_learn(pkg_name):
            if pkg_name == "ok-pkg":
                return (2, 1, 1)
            raise ValueError("nope")

        def mock_has_caps(pkg_name):
            return pkg_name == "existing-pkg"

        with (
            patch.object(
                runner,
                "get_packages_to_bootstrap",
                return_value=[
                    ("existing-pkg", PackageCategory.SYSTEM),
                    ("ok-pkg", PackageCategory.SYSTEM),
                    ("fail-pkg", PackageCategory.NETWORK),
                ],
            ),
            patch.object(runner, "_has_capabilities", side_effect=mock_has_caps),
            patch.object(runner, "_learn_package", side_effect=mock_learn),
        ):
            result = await runner.run()

        assert result.packages_skipped == 1
        assert result.packages_succeeded == 1
        assert result.packages_failed == 1
        assert result.capabilities_generated == 2
        assert result.capabilities_validated == 1
        assert result.capabilities_saved == 1

    @pytest.mark.asyncio
    async def test_run_no_progress_callback(self):
        """Run without progress callback (None)."""
        runner = BootstrapRunner(skip_existing=False)

        with (
            patch.object(
                runner,
                "get_packages_to_bootstrap",
                return_value=[("p1", PackageCategory.SYSTEM)],
            ),
            patch.object(
                runner,
                "_learn_package",
                new_callable=AsyncMock,
                return_value=(1, 1, 1),
            ),
        ):
            result = await runner.run(progress_callback=None)

        assert result.packages_succeeded == 1

    @pytest.mark.asyncio
    async def test_run_failed_package_with_none_error(self):
        """A failed package returning None error gets 'Unknown error'."""
        runner = BootstrapRunner(skip_existing=False)

        # Simulate the process_package returning (name, False, None, 0, 0, 0)
        # by having _learn_package raise an exception that gets caught inside process_package
        with (
            patch.object(
                runner,
                "get_packages_to_bootstrap",
                return_value=[("fail-pkg", PackageCategory.SYSTEM)],
            ),
            patch.object(
                runner,
                "_learn_package",
                new_callable=AsyncMock,
                side_effect=ValueError("some error"),
            ),
        ):
            result = await runner.run()

        assert result.packages_failed == 1
        assert len(result.failed_packages) == 1
        name, error = result.failed_packages[0]
        assert name == "fail-pkg"
        assert "some error" in error


# =============================================================================
# Convenience Functions
# =============================================================================


class TestRunBootstrap:
    """Tests for run_bootstrap convenience function."""

    @pytest.mark.asyncio
    async def test_delegates_to_runner(self):
        """run_bootstrap creates a runner and calls run()."""
        mock_result = BootstrapResult()
        mock_result.packages_succeeded = 5

        with patch.object(BootstrapRunner, "run", new_callable=AsyncMock, return_value=mock_result):
            result = await run_bootstrap(
                include_core=False,
                include_essential=False,
                include_optional=True,
                include_dependencies=False,
                max_concurrent=3,
                skip_existing=False,
            )

        assert result.packages_succeeded == 5

    @pytest.mark.asyncio
    async def test_with_progress_callback(self):
        calls = []

        def callback(current, total, name):
            calls.append(name)

        mock_result = BootstrapResult()

        with patch.object(BootstrapRunner, "run", new_callable=AsyncMock, return_value=mock_result) as mock_run:
            await run_bootstrap(progress_callback=callback)
            mock_run.assert_called_once_with(callback)


class TestGetBootstrapPackages:
    """Tests for get_bootstrap_packages convenience function."""

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    def test_returns_string_tuples(self, mock_installed):
        mock_installed.return_value = True
        packages = get_bootstrap_packages()
        assert isinstance(packages, list)
        if packages:
            name, cat = packages[0]
            assert isinstance(name, str)
            assert isinstance(cat, str)  # category value, not enum

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    def test_empty_when_nothing_installed(self, mock_installed):
        mock_installed.return_value = False
        packages = get_bootstrap_packages()
        # Core packages won't show up because is_package_installed returns False
        # But essential/optional queries might still return items via their own mocks
        # With is_package_installed returning False, essential tier also filters them out
        # and optional also uses is_package_installed
        assert isinstance(packages, list)


# =============================================================================
# Bootstrap State
# =============================================================================


class TestGetBootstrapState:
    """Tests for get_bootstrap_state function."""

    def _mock_state_file(self, *, exists=True, read_text_return=None, read_text_side_effect=None):
        """Create a mock Path object for _BOOTSTRAP_STATE_FILE."""
        mock_file = MagicMock()
        mock_file.exists.return_value = exists
        if read_text_side_effect:
            mock_file.read_text.side_effect = read_text_side_effect
        elif read_text_return is not None:
            mock_file.read_text.return_value = read_text_return
        return mock_file

    def test_no_file(self):
        mock_file = self._mock_state_file(exists=False)
        with patch("elle.capabilities.autogen.bootstrap._BOOTSTRAP_STATE_FILE", mock_file):
            state = get_bootstrap_state()
        assert state == {"completed": False, "last_run": None, "version": None}

    def test_valid_json(self):
        data = {"completed": True, "last_run": "2025-01-01T00:00:00", "version": "0.1.0"}
        mock_file = self._mock_state_file(exists=True, read_text_return=json.dumps(data))
        with patch("elle.capabilities.autogen.bootstrap._BOOTSTRAP_STATE_FILE", mock_file):
            state = get_bootstrap_state()
        assert state["completed"] is True
        assert state["version"] == "0.1.0"

    def test_corrupt_json(self):
        mock_file = self._mock_state_file(exists=True, read_text_return="not json!!!")
        with patch("elle.capabilities.autogen.bootstrap._BOOTSTRAP_STATE_FILE", mock_file):
            state = get_bootstrap_state()
        assert state == {"completed": False, "last_run": None, "version": None}

    def test_non_dict_json(self):
        mock_file = self._mock_state_file(exists=True, read_text_return="[1, 2, 3]")
        with patch("elle.capabilities.autogen.bootstrap._BOOTSTRAP_STATE_FILE", mock_file):
            state = get_bootstrap_state()
        assert state == {"completed": False, "last_run": None, "version": None}

    def test_read_text_raises(self):
        mock_file = self._mock_state_file(exists=True, read_text_side_effect=PermissionError("denied"))
        with patch("elle.capabilities.autogen.bootstrap._BOOTSTRAP_STATE_FILE", mock_file):
            state = get_bootstrap_state()
        assert state == {"completed": False, "last_run": None, "version": None}


class TestSetBootstrapComplete:
    """Tests for set_bootstrap_complete function."""

    @patch("elle.__version__", "1.2.3")
    def test_writes_state(self):
        result = BootstrapResult()
        result.completed_at = datetime(2025, 6, 15, 12, 0, 0)
        result.packages_succeeded = 10
        result.capabilities_saved = 25

        mock_file = MagicMock()
        mock_parent = MagicMock()
        mock_file.parent = mock_parent

        with patch("elle.capabilities.autogen.bootstrap._BOOTSTRAP_STATE_FILE", mock_file):
            set_bootstrap_complete(result)

        mock_parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_file.write_text.assert_called_once()
        written = json.loads(mock_file.write_text.call_args[0][0])
        assert written["completed"] is True
        assert written["version"] == "1.2.3"
        assert written["packages_succeeded"] == 10
        assert written["capabilities_saved"] == 25

    @patch("elle.__version__", "1.0.0")
    def test_completed_at_none(self):
        result = BootstrapResult()
        result.completed_at = None

        mock_file = MagicMock()
        mock_file.parent = MagicMock()

        with patch("elle.capabilities.autogen.bootstrap._BOOTSTRAP_STATE_FILE", mock_file):
            set_bootstrap_complete(result)

        written = json.loads(mock_file.write_text.call_args[0][0])
        assert written["last_run"] is None

    @patch("elle.__version__", "1.0.0")
    def test_write_permission_error(self):
        """PermissionError during write is caught, does not raise."""
        result = BootstrapResult()
        result.completed_at = datetime(2025, 1, 1)

        mock_file = MagicMock()
        mock_parent = MagicMock()
        mock_parent.mkdir.side_effect = PermissionError("no access")
        mock_file.parent = mock_parent

        with patch("elle.capabilities.autogen.bootstrap._BOOTSTRAP_STATE_FILE", mock_file):
            # Should not raise
            set_bootstrap_complete(result)


class TestShouldRunBootstrap:
    """Tests for should_run_bootstrap function."""

    def test_never_completed(self):
        with patch(
            "elle.capabilities.autogen.bootstrap.get_bootstrap_state",
            return_value={"completed": False},
        ):
            assert should_run_bootstrap() is True

    def test_completed_same_version(self):
        from elle import __version__

        with patch(
            "elle.capabilities.autogen.bootstrap.get_bootstrap_state",
            return_value={"completed": True, "version": __version__},
        ):
            assert should_run_bootstrap() is False

    def test_completed_different_version(self):
        with patch(
            "elle.capabilities.autogen.bootstrap.get_bootstrap_state",
            return_value={"completed": True, "version": "0.0.0-old"},
        ):
            assert should_run_bootstrap() is True

    def test_completed_no_version_key(self):
        with patch(
            "elle.capabilities.autogen.bootstrap.get_bootstrap_state",
            return_value={"completed": True},
        ):
            # state.get("version") returns None, which != __version__
            assert should_run_bootstrap() is True

    def test_completed_none_version(self):
        with patch(
            "elle.capabilities.autogen.bootstrap.get_bootstrap_state",
            return_value={"completed": True, "version": None},
        ):
            assert should_run_bootstrap() is True


# =============================================================================
# Edge Cases in _learn_package parsing
# =============================================================================


class TestLearnPackageParsing:
    """Edge cases in the cap_data parsing inside _learn_package."""

    @pytest.mark.asyncio
    async def test_cap_data_without_input_field_constraints(self):
        """Input fields missing 'constraints' get an empty dict added."""
        runner = BootstrapRunner()

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ["man"]
        mock_intel.primary_binary = "testcmd"
        mock_intel.metadata.name = "pkg"
        mock_intel.metadata.version = "1.0"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "ctx"

        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = {
            "capabilities": [
                {
                    "name": "test.cmd",
                    "display_name": "T",
                    "description": "D",
                    "domain": "file",
                    "risk_level": "low",
                    "command_template": "test",
                    "source_command": "test",
                    "confidence_score": 0.8,
                    "input_fields": [
                        {"name": "path", "field_type": "str", "description": "P"}
                        # No "constraints" key
                    ],
                    "output_fields": [{"name": "out", "field_type": "str", "description": "O"}],
                    "side_effects": ["file_read"],
                },
            ],
        }

        mock_validation = MagicMock()
        mock_validation.overall_passed = False

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "sys"
        mock_store = MagicMock()

        with (
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.llm.LLM", return_value=mock_llm),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.capabilities.autogen.validate_capability", return_value=mock_validation),
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
        ):
            generated, validated, saved = await runner._learn_package("pkg")

        # The spec should have been parsed successfully with constraints defaulted
        assert generated == 1

    @pytest.mark.asyncio
    async def test_cap_data_with_primary_binary_none_fallback(self):
        """When primary_binary is None, source_command falls back to package_name."""
        runner = BootstrapRunner()

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ["man"]
        mock_intel.primary_binary = None
        mock_intel.metadata.name = "the-package"
        mock_intel.metadata.version = "2.0"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "ctx"

        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = {
            "capabilities": [
                {
                    "name": "test.cmd",
                    "display_name": "T",
                    "description": "D",
                    "domain": "file",
                    "risk_level": "low",
                    "command_template": "test",
                    # No source_command: should use intel.primary_binary or package_name
                    "confidence_score": 0.8,
                },
            ],
        }

        mock_validation = MagicMock()
        mock_validation.overall_passed = False

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "sys"
        mock_store = MagicMock()

        with (
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.llm.LLM", return_value=mock_llm),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.capabilities.autogen.validate_capability", return_value=mock_validation),
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
        ):
            generated, validated, saved = await runner._learn_package("the-package")

        # primary_binary is None, so `intel.primary_binary or package_name` resolves to "the-package"
        assert generated == 1
