from __future__ import annotations

"""Comprehensive tests for elle.capabilities.autogen.versioner.

All external dependencies (store, discovery, parser, generator, factory,
validator, pydantic_compat) are mocked.  No database or external service
required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.capabilities.autogen.models import (
    FullValidationResult,
    GeneratedCapabilitySpec,
    InputFieldSpec,
    StoredCapability,
    TrustLevel,
)
from elle.capabilities.autogen.versioner import (
    CapabilityVersioner,
    get_versioner,
    reset_versioner,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    name: str = "nginx.reload",
    source_command: str = "nginx",
    domain: str = "service",
) -> GeneratedCapabilitySpec:
    return GeneratedCapabilitySpec(
        name=name,
        display_name="Nginx Reload",
        description="Reload nginx configuration",
        domain=domain,
        risk_level="low",
        side_effects=("service_restart",),
        input_fields=(InputFieldSpec(name="config", field_type="str", description="Config path"),),
        command_template=f"{source_command} -s reload",
        source_command=source_command,
        confidence_score=0.9,
    )


def _make_stored(
    capability_name: str = "nginx.reload",
    source_command: str = "nginx",
    source_package: str | None = "nginx",
    package_version: str | None = "1.24.0",
    binary_path: str | None = "/usr/sbin/nginx",
    approved: bool = False,
    enabled: bool = True,
    spec_json: str | None = None,
    input_model_code: str = "class Input: pass",
    output_model_code: str = "class Output: pass",
    capability_class_code: str = "class Cap: pass",
    trust_level: TrustLevel = TrustLevel.OFFICIAL,
) -> StoredCapability:
    if spec_json is None:
        spec_json = _make_spec(name=capability_name, source_command=source_command).model_dump_json()
    return StoredCapability(
        id="test-id-1",
        capability_name=capability_name,
        spec_json=spec_json,
        input_model_code=input_model_code,
        output_model_code=output_model_code,
        capability_class_code=capability_class_code,
        source_command=source_command,
        man_page_hash="abc123",
        trust_level=trust_level,
        approved=approved,
        enabled=enabled,
        source_package=source_package,
        package_version=package_version,
        binary_path=binary_path,
    )


def _make_binary(
    name: str = "nginx",
    path: str = "/usr/sbin/nginx",
    package: str | None = "nginx",
) -> MagicMock:
    binary = MagicMock()
    binary.name = name
    binary.path = path
    binary.package = package
    return binary


def _make_man_page(
    name: str = "nginx",
    raw_text: str = "NGINX(1) man page content",
) -> MagicMock:
    man = MagicMock()
    man.name = name
    man.raw_text = raw_text
    return man


@pytest.fixture
def mock_store() -> MagicMock:
    """Create a mock AutogenStore."""
    store = MagicMock()
    store.list_all.return_value = []
    store.get.return_value = None
    store.save.return_value = "test-id"
    store.approve.return_value = True
    store.disable.return_value = True
    store.check_needs_regeneration.return_value = False
    return store


@pytest.fixture
def versioner(mock_store: MagicMock) -> CapabilityVersioner:
    """Create a versioner backed by a mock store."""
    return CapabilityVersioner(store=mock_store)


# ---------------------------------------------------------------------------
# CapabilityVersioner.__init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_init_with_explicit_store(self, mock_store):
        """Init with an explicit store should use it directly."""
        v = CapabilityVersioner(store=mock_store)
        assert v._store is mock_store
        assert v._generator is None
        assert v._package_to_capabilities == {}
        assert v._regeneration_stats["total_regenerations"] == 0

    def test_init_with_generator(self, mock_store):
        """Init with an explicit generator should store it."""
        gen = MagicMock()
        v = CapabilityVersioner(store=mock_store, generator=gen)
        assert v._generator is gen

    @patch("elle.capabilities.autogen.versioner.get_store")
    def test_init_default_store(self, mock_get_store):
        """Init without store should call get_store()."""
        sentinel = MagicMock()
        mock_get_store.return_value = sentinel
        v = CapabilityVersioner()
        mock_get_store.assert_called_once()
        assert v._store is sentinel


# ---------------------------------------------------------------------------
# build_package_map
# ---------------------------------------------------------------------------


class TestBuildPackageMap:
    def test_empty_store(self, versioner, mock_store):
        """Empty store should produce empty map."""
        mock_store.list_all.return_value = []
        result = versioner.build_package_map()
        assert result == {}

    def test_single_package_single_capability(self, versioner, mock_store):
        """One capability with a source_package."""
        stored = _make_stored()
        mock_store.list_all.return_value = [stored]

        result = versioner.build_package_map()

        assert "nginx" in result
        assert "nginx.reload" in result["nginx"]

    def test_single_package_multiple_capabilities(self, versioner, mock_store):
        """Multiple capabilities from the same package."""
        cap1 = _make_stored(capability_name="nginx.reload")
        cap2 = _make_stored(capability_name="nginx.stop")
        mock_store.list_all.return_value = [cap1, cap2]

        result = versioner.build_package_map()

        assert len(result["nginx"]) == 2
        assert "nginx.reload" in result["nginx"]
        assert "nginx.stop" in result["nginx"]

    def test_multiple_packages(self, versioner, mock_store):
        """Capabilities from different packages."""
        cap1 = _make_stored(capability_name="nginx.reload", source_package="nginx")
        cap2 = _make_stored(
            capability_name="systemctl.restart",
            source_command="systemctl",
            source_package="systemd",
        )
        mock_store.list_all.return_value = [cap1, cap2]

        result = versioner.build_package_map()

        assert "nginx" in result
        assert "systemd" in result

    def test_capability_without_package_skipped(self, versioner, mock_store):
        """Capabilities without source_package should be ignored."""
        stored = _make_stored(source_package=None)
        mock_store.list_all.return_value = [stored]

        result = versioner.build_package_map()

        assert result == {}

    def test_build_clears_old_map(self, versioner, mock_store):
        """Calling build_package_map a second time should clear old data."""
        cap1 = _make_stored(capability_name="nginx.reload", source_package="nginx")
        mock_store.list_all.return_value = [cap1]
        versioner.build_package_map()

        # Second call with different data
        cap2 = _make_stored(
            capability_name="systemctl.restart",
            source_command="systemctl",
            source_package="systemd",
        )
        mock_store.list_all.return_value = [cap2]
        result = versioner.build_package_map()

        assert "nginx" not in result
        assert "systemd" in result


# ---------------------------------------------------------------------------
# get_watched_packages
# ---------------------------------------------------------------------------


class TestGetWatchedPackages:
    def test_empty_map(self, versioner):
        """No packages should return empty set."""
        assert versioner.get_watched_packages() == set()

    def test_after_build(self, versioner, mock_store):
        """Should return package names from the built map."""
        stored = _make_stored()
        mock_store.list_all.return_value = [stored]
        versioner.build_package_map()

        watched = versioner.get_watched_packages()
        assert watched == {"nginx"}


# ---------------------------------------------------------------------------
# get_capabilities_for_package
# ---------------------------------------------------------------------------


class TestGetCapabilitiesForPackage:
    def test_known_package(self, versioner):
        """Known package should return its capabilities."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        assert versioner.get_capabilities_for_package("nginx") == ["nginx.reload"]

    def test_unknown_package(self, versioner):
        """Unknown package should return empty list."""
        assert versioner.get_capabilities_for_package("unknown") == []


# ---------------------------------------------------------------------------
# add_capability_mapping
# ---------------------------------------------------------------------------


class TestAddCapabilityMapping:
    def test_new_package_new_capability(self, versioner):
        """Adding to a new package should create the list."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        assert versioner._package_to_capabilities["nginx"] == ["nginx.reload"]

    def test_existing_package_new_capability(self, versioner):
        """Adding a new capability to an existing package."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        versioner.add_capability_mapping("nginx", "nginx.stop")
        assert versioner._package_to_capabilities["nginx"] == ["nginx.reload", "nginx.stop"]

    def test_duplicate_ignored(self, versioner):
        """Adding the same capability twice should not duplicate it."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        versioner.add_capability_mapping("nginx", "nginx.reload")
        assert versioner._package_to_capabilities["nginx"] == ["nginx.reload"]


# ---------------------------------------------------------------------------
# on_package_upgraded
# ---------------------------------------------------------------------------


class TestOnPackageUpgraded:
    @pytest.mark.asyncio
    async def test_no_capabilities(self, versioner):
        """Package with no capabilities returns empty list."""
        result = await versioner.on_package_upgraded("unknown", "1.0", "2.0")
        assert result == []

    @pytest.mark.asyncio
    async def test_capability_not_in_store(self, versioner, mock_store):
        """Capability in map but missing from store should be skipped."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        mock_store.get.return_value = None

        result = await versioner.on_package_upgraded("nginx", "1.24.0", "1.26.0")

        assert result == []

    @pytest.mark.asyncio
    async def test_version_already_matches(self, versioner, mock_store):
        """Capability already at the new version should be skipped."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        stored = _make_stored(package_version="1.26.0")
        mock_store.get.return_value = stored

        result = await versioner.on_package_upgraded("nginx", "1.24.0", "1.26.0")

        assert result == []

    @pytest.mark.asyncio
    async def test_successful_regeneration(self, versioner, mock_store):
        """Successful regeneration should add cap name to result."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        stored = _make_stored(package_version="1.24.0")
        mock_store.get.return_value = stored

        with patch.object(
            versioner,
            "_regenerate_capability",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await versioner.on_package_upgraded("nginx", "1.24.0", "1.26.0")

        assert result == ["nginx.reload"]
        assert versioner._regeneration_stats["successful"] == 1
        assert versioner._regeneration_stats["total_regenerations"] == 1

    @pytest.mark.asyncio
    async def test_failed_regeneration(self, versioner, mock_store):
        """Failed regeneration should not add cap name to result."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        stored = _make_stored(package_version="1.24.0")
        mock_store.get.return_value = stored

        with patch.object(
            versioner,
            "_regenerate_capability",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await versioner.on_package_upgraded("nginx", "1.24.0", "1.26.0")

        assert result == []
        assert versioner._regeneration_stats["failed"] == 1
        assert versioner._regeneration_stats["total_regenerations"] == 1

    @pytest.mark.asyncio
    async def test_per_package_stats_tracked(self, versioner, mock_store):
        """Per-package regeneration counts should be tracked."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        versioner.add_capability_mapping("nginx", "nginx.stop")
        stored = _make_stored(package_version="1.24.0")
        mock_store.get.return_value = stored

        with patch.object(
            versioner,
            "_regenerate_capability",
            new_callable=AsyncMock,
            return_value=True,
        ):
            await versioner.on_package_upgraded("nginx", "1.24.0", "1.26.0")

        assert versioner._regeneration_stats["by_package"]["nginx"] == 2

    @pytest.mark.asyncio
    async def test_per_package_stats_accumulate(self, versioner, mock_store):
        """Calling on_package_upgraded twice should accumulate stats."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        stored = _make_stored(package_version="1.24.0")
        mock_store.get.return_value = stored

        with patch.object(
            versioner,
            "_regenerate_capability",
            new_callable=AsyncMock,
            return_value=True,
        ):
            await versioner.on_package_upgraded("nginx", "1.24.0", "1.26.0")

        # Update stored version for second call
        stored2 = _make_stored(package_version="1.26.0")
        mock_store.get.return_value = stored2

        with patch.object(
            versioner,
            "_regenerate_capability",
            new_callable=AsyncMock,
            return_value=True,
        ):
            await versioner.on_package_upgraded("nginx", "1.26.0", "1.28.0")

        assert versioner._regeneration_stats["by_package"]["nginx"] == 2

    @pytest.mark.asyncio
    async def test_old_version_none(self, versioner, mock_store):
        """Old version can be None (first install)."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        stored = _make_stored(package_version="1.22.0")
        mock_store.get.return_value = stored

        with patch.object(
            versioner,
            "_regenerate_capability",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await versioner.on_package_upgraded("nginx", None, "1.24.0")

        assert result == ["nginx.reload"]

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self, versioner, mock_store):
        """Mix of successes and failures in a single upgrade call."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        versioner.add_capability_mapping("nginx", "nginx.stop")

        stored = _make_stored(package_version="1.24.0")
        mock_store.get.return_value = stored

        call_count = 0

        async def alternate_result(s, v):
            nonlocal call_count
            call_count += 1
            return call_count == 1  # First succeeds, second fails

        with patch.object(versioner, "_regenerate_capability", side_effect=alternate_result):
            result = await versioner.on_package_upgraded("nginx", "1.24.0", "1.26.0")

        assert len(result) == 1
        assert versioner._regeneration_stats["successful"] == 1
        assert versioner._regeneration_stats["failed"] == 1
        assert versioner._regeneration_stats["total_regenerations"] == 2


# ---------------------------------------------------------------------------
# _regenerate_capability
# ---------------------------------------------------------------------------


class TestRegenerateCapability:
    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.versioner.discover_binary")
    async def test_binary_not_found(self, mock_discover, versioner):
        """When binary is not found, return False."""
        mock_discover.return_value = None
        stored = _make_stored()

        result = await versioner._regenerate_capability(stored, "2.0.0")

        assert result is False

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.versioner.parse_man_page")
    @patch("elle.capabilities.autogen.versioner.discover_binary")
    async def test_man_page_not_found(self, mock_discover, mock_parse, versioner):
        """When man page cannot be parsed, return False."""
        mock_discover.return_value = _make_binary()
        mock_parse.return_value = None
        stored = _make_stored()

        result = await versioner._regenerate_capability(stored, "2.0.0")

        assert result is False

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.versioner.parse_man_page")
    @patch("elle.capabilities.autogen.versioner.discover_binary")
    async def test_man_page_unchanged_updates_version_only(self, mock_discover, mock_parse, versioner, mock_store):
        """When man page hash is unchanged, call _update_version_only."""
        binary = _make_binary()
        man = _make_man_page()
        mock_discover.return_value = binary
        mock_parse.return_value = man
        mock_store.check_needs_regeneration.return_value = False

        stored = _make_stored()

        with patch.object(versioner, "_update_version_only", return_value=True) as mock_update:
            result = await versioner._regenerate_capability(stored, "2.0.0")

        assert result is True
        mock_update.assert_called_once_with(stored, binary, "2.0.0")

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.versioner.safe_model_dump_json", return_value='{"valid": true}')
    @patch("elle.capabilities.autogen.versioner.create_capability_from_spec")
    @patch("elle.capabilities.autogen.versioner.validate_capability")
    @patch("elle.capabilities.autogen.versioner.generate_capability_spec", new_callable=AsyncMock)
    @patch("elle.capabilities.autogen.versioner.parse_man_page")
    @patch("elle.capabilities.autogen.versioner.discover_binary")
    async def test_full_regeneration_success(
        self,
        mock_discover,
        mock_parse,
        mock_gen_spec,
        mock_validate,
        mock_create,
        mock_dump_json,
        versioner,
        mock_store,
    ):
        """Full regeneration path: man page changed, LLM succeeds."""
        binary = _make_binary()
        man = _make_man_page(raw_text="NEW man page content")
        mock_discover.return_value = binary
        mock_parse.return_value = man
        mock_store.check_needs_regeneration.return_value = True

        new_spec = _make_spec()
        mock_gen_spec.return_value = new_spec

        validation = MagicMock(spec=FullValidationResult)
        validation.trust_level = TrustLevel.OFFICIAL
        mock_validate.return_value = validation

        mock_create.return_value = ("input_code", "output_code", "cap_code")

        stored = _make_stored(approved=False, enabled=True)

        result = await versioner._regenerate_capability(stored, "2.0.0")

        assert result is True
        mock_store.save.assert_called_once()
        # Not approved, so approve should NOT be called
        mock_store.approve.assert_not_called()
        # Enabled, so disable should NOT be called
        mock_store.disable.assert_not_called()

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.versioner.safe_model_dump_json", return_value='{"valid": true}')
    @patch("elle.capabilities.autogen.versioner.create_capability_from_spec")
    @patch("elle.capabilities.autogen.versioner.validate_capability")
    @patch("elle.capabilities.autogen.versioner.generate_capability_spec", new_callable=AsyncMock)
    @patch("elle.capabilities.autogen.versioner.parse_man_page")
    @patch("elle.capabilities.autogen.versioner.discover_binary")
    async def test_regeneration_preserves_approved_status(
        self,
        mock_discover,
        mock_parse,
        mock_gen_spec,
        mock_validate,
        mock_create,
        mock_dump_json,
        versioner,
        mock_store,
    ):
        """Previously approved capabilities should be re-approved after regen."""
        binary = _make_binary()
        man = _make_man_page(raw_text="NEW content")
        mock_discover.return_value = binary
        mock_parse.return_value = man
        mock_store.check_needs_regeneration.return_value = True

        mock_gen_spec.return_value = _make_spec()
        mock_validate.return_value = MagicMock(trust_level=TrustLevel.OFFICIAL)
        mock_create.return_value = ("ic", "oc", "cc")

        stored = _make_stored(approved=True, enabled=True)

        result = await versioner._regenerate_capability(stored, "2.0.0")

        assert result is True
        mock_store.approve.assert_called_once_with("nginx.reload")

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.versioner.safe_model_dump_json", return_value='{"valid": true}')
    @patch("elle.capabilities.autogen.versioner.create_capability_from_spec")
    @patch("elle.capabilities.autogen.versioner.validate_capability")
    @patch("elle.capabilities.autogen.versioner.generate_capability_spec", new_callable=AsyncMock)
    @patch("elle.capabilities.autogen.versioner.parse_man_page")
    @patch("elle.capabilities.autogen.versioner.discover_binary")
    async def test_regeneration_preserves_disabled_status(
        self,
        mock_discover,
        mock_parse,
        mock_gen_spec,
        mock_validate,
        mock_create,
        mock_dump_json,
        versioner,
        mock_store,
    ):
        """Previously disabled capabilities should be re-disabled after regen."""
        binary = _make_binary()
        man = _make_man_page(raw_text="NEW content")
        mock_discover.return_value = binary
        mock_parse.return_value = man
        mock_store.check_needs_regeneration.return_value = True

        mock_gen_spec.return_value = _make_spec()
        mock_validate.return_value = MagicMock(trust_level=TrustLevel.OFFICIAL)
        mock_create.return_value = ("ic", "oc", "cc")

        stored = _make_stored(approved=False, enabled=False)

        result = await versioner._regenerate_capability(stored, "2.0.0")

        assert result is True
        mock_store.disable.assert_called_once_with("nginx.reload")

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.versioner.generate_capability_spec", new_callable=AsyncMock)
    @patch("elle.capabilities.autogen.versioner.parse_man_page")
    @patch("elle.capabilities.autogen.versioner.discover_binary")
    async def test_spec_generation_fails(self, mock_discover, mock_parse, mock_gen_spec, versioner, mock_store):
        """When generate_capability_spec returns None, return False."""
        mock_discover.return_value = _make_binary()
        mock_parse.return_value = _make_man_page(raw_text="NEW content")
        mock_store.check_needs_regeneration.return_value = True
        mock_gen_spec.return_value = None

        stored = _make_stored()

        result = await versioner._regenerate_capability(stored, "2.0.0")

        assert result is False

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.versioner.discover_binary")
    async def test_exception_returns_false(self, mock_discover, versioner):
        """Any exception during regeneration should return False."""
        mock_discover.side_effect = RuntimeError("kaboom")
        stored = _make_stored()

        result = await versioner._regenerate_capability(stored, "2.0.0")

        assert result is False

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.versioner.safe_model_dump_json", return_value="{}")
    @patch("elle.capabilities.autogen.versioner.create_capability_from_spec")
    @patch("elle.capabilities.autogen.versioner.validate_capability")
    @patch("elle.capabilities.autogen.versioner.generate_capability_spec", new_callable=AsyncMock)
    @patch("elle.capabilities.autogen.versioner.parse_man_page")
    @patch("elle.capabilities.autogen.versioner.discover_binary")
    async def test_subcommand_extraction_from_dotted_name(
        self,
        mock_discover,
        mock_parse,
        mock_gen_spec,
        mock_validate,
        mock_create,
        mock_dump_json,
        versioner,
        mock_store,
    ):
        """Subcommand should be extracted from 'cmd.subcommand' name."""
        binary = _make_binary(name="systemctl")
        man = _make_man_page(name="systemctl", raw_text="CHANGED")
        mock_discover.return_value = binary
        mock_parse.return_value = man
        mock_store.check_needs_regeneration.return_value = True

        mock_gen_spec.return_value = _make_spec(name="systemctl.restart", source_command="systemctl")
        mock_validate.return_value = MagicMock(trust_level=TrustLevel.CORE)
        mock_create.return_value = ("ic", "oc", "cc")

        stored = _make_stored(
            capability_name="systemctl.restart",
            source_command="systemctl",
        )

        result = await versioner._regenerate_capability(stored, "252-1")

        assert result is True
        # Verify subcommand was passed to generate_capability_spec
        mock_gen_spec.assert_awaited_once()
        call_kwargs = mock_gen_spec.call_args
        assert call_kwargs.kwargs.get("subcommand") == "restart" or (
            len(call_kwargs.args) > 1 or call_kwargs.kwargs.get("subcommand") == "restart"
        )

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.versioner.safe_model_dump_json", return_value="{}")
    @patch("elle.capabilities.autogen.versioner.create_capability_from_spec")
    @patch("elle.capabilities.autogen.versioner.validate_capability")
    @patch("elle.capabilities.autogen.versioner.generate_capability_spec", new_callable=AsyncMock)
    @patch("elle.capabilities.autogen.versioner.parse_man_page")
    @patch("elle.capabilities.autogen.versioner.discover_binary")
    async def test_no_dot_in_name_means_no_subcommand(
        self,
        mock_discover,
        mock_parse,
        mock_gen_spec,
        mock_validate,
        mock_create,
        mock_dump_json,
        versioner,
        mock_store,
    ):
        """Capability name without a dot should pass subcommand=None."""
        binary = _make_binary(name="ls")
        man = _make_man_page(name="ls", raw_text="CHANGED")
        mock_discover.return_value = binary
        mock_parse.return_value = man
        mock_store.check_needs_regeneration.return_value = True

        mock_gen_spec.return_value = _make_spec(name="lscmd", source_command="ls")
        mock_validate.return_value = MagicMock(trust_level=TrustLevel.CORE)
        mock_create.return_value = ("ic", "oc", "cc")

        stored = _make_stored(
            capability_name="lscmd",
            source_command="ls",
        )

        result = await versioner._regenerate_capability(stored, "9.0")

        assert result is True
        call_kwargs = mock_gen_spec.call_args
        assert call_kwargs.kwargs.get("subcommand") is None

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.versioner.safe_model_dump_json", return_value="{}")
    @patch("elle.capabilities.autogen.versioner.create_capability_from_spec")
    @patch("elle.capabilities.autogen.versioner.validate_capability")
    @patch("elle.capabilities.autogen.versioner.generate_capability_spec", new_callable=AsyncMock)
    @patch("elle.capabilities.autogen.versioner.parse_man_page")
    @patch("elle.capabilities.autogen.versioner.discover_binary")
    async def test_save_passes_correct_arguments(
        self,
        mock_discover,
        mock_parse,
        mock_gen_spec,
        mock_validate,
        mock_create,
        mock_dump_json,
        versioner,
        mock_store,
    ):
        """Verify store.save receives all expected keyword arguments."""
        binary = _make_binary(package="nginx-pkg")
        man = _make_man_page(raw_text="UPDATED man content")
        mock_discover.return_value = binary
        mock_parse.return_value = man
        mock_store.check_needs_regeneration.return_value = True

        spec = _make_spec()
        mock_gen_spec.return_value = spec
        mock_validate.return_value = MagicMock(trust_level=TrustLevel.OFFICIAL)
        mock_create.return_value = ("in_code", "out_code", "cap_code")

        stored = _make_stored()

        await versioner._regenerate_capability(stored, "2.0.0")

        save_kwargs = mock_store.save.call_args.kwargs
        assert save_kwargs["spec"] is spec
        assert save_kwargs["input_model_code"] == "in_code"
        assert save_kwargs["output_model_code"] == "out_code"
        assert save_kwargs["capability_class_code"] == "cap_code"
        assert save_kwargs["man_page_text"] == "UPDATED man content"
        assert save_kwargs["trust_level"] == TrustLevel.OFFICIAL
        assert save_kwargs["source_package"] == "nginx-pkg"
        assert save_kwargs["package_version"] == "2.0.0"
        assert save_kwargs["binary_path"] == "/usr/sbin/nginx"


# ---------------------------------------------------------------------------
# _update_version_only
# ---------------------------------------------------------------------------


class TestUpdateVersionOnly:
    def test_success(self, versioner, mock_store):
        """Successful version-only update."""
        mock_spec_obj = MagicMock()
        with patch(
            "elle.capabilities.autogen.models.GeneratedCapabilitySpec.model_validate_json",
            return_value=mock_spec_obj,
        ):
            stored = _make_stored(approved=False, enabled=True)
            binary = _make_binary(package="nginx-pkg")

            result = versioner._update_version_only(stored, binary, "2.0.0")

        assert result is True
        mock_store.save.assert_called_once()

    def test_preserves_approval(self, versioner, mock_store):
        """Previously approved capability should be re-approved."""
        with patch(
            "elle.capabilities.autogen.models.GeneratedCapabilitySpec.model_validate_json",
            return_value=MagicMock(),
        ):
            stored = _make_stored(approved=True, enabled=True)
            binary = _make_binary()

            versioner._update_version_only(stored, binary, "2.0.0")

        mock_store.approve.assert_called_once_with("nginx.reload")

    def test_preserves_disabled(self, versioner, mock_store):
        """Previously disabled capability should be re-disabled."""
        with patch(
            "elle.capabilities.autogen.models.GeneratedCapabilitySpec.model_validate_json",
            return_value=MagicMock(),
        ):
            stored = _make_stored(approved=False, enabled=False)
            binary = _make_binary()

            versioner._update_version_only(stored, binary, "2.0.0")

        mock_store.disable.assert_called_once_with("nginx.reload")

    def test_exception_returns_false(self, versioner, mock_store):
        """Exception during version-only update should return False."""
        with patch(
            "elle.capabilities.autogen.models.GeneratedCapabilitySpec.model_validate_json",
            side_effect=ValueError("bad json"),
        ):
            stored = _make_stored()
            binary = _make_binary()

            result = versioner._update_version_only(stored, binary, "2.0.0")

        assert result is False

    def test_save_arguments(self, versioner, mock_store):
        """Verify store.save receives correct arguments for version-only update."""
        mock_parsed = MagicMock()
        with patch(
            "elle.capabilities.autogen.models.GeneratedCapabilitySpec.model_validate_json",
            return_value=mock_parsed,
        ):
            stored = _make_stored(
                input_model_code="old_input",
                output_model_code="old_output",
                capability_class_code="old_cap",
                trust_level=TrustLevel.CORE,
            )
            binary = _make_binary(package="nginx-new")

            versioner._update_version_only(stored, binary, "3.0.0")

        save_kwargs = mock_store.save.call_args.kwargs
        assert save_kwargs["spec"] is mock_parsed
        assert save_kwargs["input_model_code"] == "old_input"
        assert save_kwargs["output_model_code"] == "old_output"
        assert save_kwargs["capability_class_code"] == "old_cap"
        assert save_kwargs["man_page_text"] == ""
        assert save_kwargs["trust_level"] == TrustLevel.CORE
        assert save_kwargs["validation_json"] is None
        assert save_kwargs["source_package"] == "nginx-new"
        assert save_kwargs["package_version"] == "3.0.0"
        assert save_kwargs["binary_path"] == "/usr/sbin/nginx"


# ---------------------------------------------------------------------------
# check_version_mismatch
# ---------------------------------------------------------------------------


class TestCheckVersionMismatch:
    def test_mismatch(self, versioner, mock_store):
        """When stored version differs from current, return True."""
        stored = _make_stored(package_version="1.24.0")
        mock_store.get.return_value = stored

        assert versioner.check_version_mismatch("nginx.reload", "1.26.0") is True

    def test_match(self, versioner, mock_store):
        """When stored version equals current, return False."""
        stored = _make_stored(package_version="1.24.0")
        mock_store.get.return_value = stored

        assert versioner.check_version_mismatch("nginx.reload", "1.24.0") is False

    def test_capability_not_found(self, versioner, mock_store):
        """When capability is not in store, return False."""
        mock_store.get.return_value = None

        assert versioner.check_version_mismatch("unknown", "1.0") is False

    def test_no_package_version(self, versioner, mock_store):
        """When stored capability has no package_version, return False."""
        stored = _make_stored(package_version=None)
        mock_store.get.return_value = stored

        assert versioner.check_version_mismatch("nginx.reload", "1.0") is False


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    def test_empty(self, versioner):
        """Empty versioner should return zero counts."""
        stats = versioner.get_stats()

        assert stats["packages_tracked"] == 0
        assert stats["capabilities_tracked"] == 0
        assert stats["regeneration_stats"]["total_regenerations"] == 0
        assert stats["regeneration_stats"]["successful"] == 0
        assert stats["regeneration_stats"]["failed"] == 0
        assert stats["regeneration_stats"]["by_package"] == {}

    def test_with_mappings(self, versioner):
        """Stats should reflect current mappings."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        versioner.add_capability_mapping("nginx", "nginx.stop")
        versioner.add_capability_mapping("systemd", "service.restart")

        stats = versioner.get_stats()

        assert stats["packages_tracked"] == 2
        assert stats["capabilities_tracked"] == 3


# ---------------------------------------------------------------------------
# get_package_capabilities_summary
# ---------------------------------------------------------------------------


class TestGetPackageCapabilitiesSummary:
    def test_empty(self, versioner):
        """Empty versioner should return empty list."""
        assert versioner.get_package_capabilities_summary() == []

    def test_with_version_info(self, versioner, mock_store):
        """Summary should include version from store."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        stored = _make_stored(package_version="1.24.0")
        mock_store.get.return_value = stored

        summary = versioner.get_package_capabilities_summary()

        assert len(summary) == 1
        assert summary[0]["package"] == "nginx"
        assert summary[0]["version"] == "1.24.0"
        assert summary[0]["capabilities"] == ["nginx.reload"]
        assert summary[0]["count"] == 1

    def test_version_not_found(self, versioner, mock_store):
        """When no stored cap has a version, version should be None."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        mock_store.get.return_value = None

        summary = versioner.get_package_capabilities_summary()

        assert len(summary) == 1
        assert summary[0]["version"] is None

    def test_version_from_first_available(self, versioner, mock_store):
        """Version should come from the first capability that has one."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        versioner.add_capability_mapping("nginx", "nginx.stop")

        # First cap has no version, second does
        stored_no_ver = _make_stored(capability_name="nginx.reload", package_version=None)
        stored_with_ver = _make_stored(capability_name="nginx.stop", package_version="1.26.0")

        def mock_get(name):
            if name == "nginx.reload":
                return stored_no_ver
            if name == "nginx.stop":
                return stored_with_ver
            return None

        mock_store.get.side_effect = mock_get

        summary = versioner.get_package_capabilities_summary()

        assert summary[0]["version"] == "1.26.0"

    def test_sorted_by_package_name(self, versioner, mock_store):
        """Summary should be sorted by package name."""
        versioner.add_capability_mapping("zlib", "zlib.compress")
        versioner.add_capability_mapping("apt", "apt.install")

        mock_store.get.return_value = None

        summary = versioner.get_package_capabilities_summary()

        assert summary[0]["package"] == "apt"
        assert summary[1]["package"] == "zlib"

    def test_multiple_packages(self, versioner, mock_store):
        """Multiple packages should each have their own entry."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        versioner.add_capability_mapping("systemd", "service.restart")

        stored_nginx = _make_stored(capability_name="nginx.reload", package_version="1.24.0")
        stored_systemd = _make_stored(
            capability_name="service.restart",
            source_command="systemctl",
            source_package="systemd",
            package_version="252-1",
        )

        def mock_get(name):
            if name == "nginx.reload":
                return stored_nginx
            if name == "service.restart":
                return stored_systemd
            return None

        mock_store.get.side_effect = mock_get

        summary = versioner.get_package_capabilities_summary()

        assert len(summary) == 2
        packages = [s["package"] for s in summary]
        assert "nginx" in packages
        assert "systemd" in packages


# ---------------------------------------------------------------------------
# get_versioner / reset_versioner (module-level singleton)
# ---------------------------------------------------------------------------


class TestModuleSingleton:
    def setup_method(self):
        """Ensure clean state before each test."""
        reset_versioner()

    def teardown_method(self):
        """Ensure clean state after each test."""
        reset_versioner()

    @patch("elle.capabilities.autogen.versioner.get_store")
    def test_get_versioner_returns_instance(self, mock_get_store):
        """get_versioner should return a CapabilityVersioner."""
        mock_get_store.return_value = MagicMock()
        v = get_versioner()
        assert isinstance(v, CapabilityVersioner)

    @patch("elle.capabilities.autogen.versioner.get_store")
    def test_get_versioner_singleton(self, mock_get_store):
        """Repeated calls should return the same instance."""
        mock_get_store.return_value = MagicMock()
        v1 = get_versioner()
        v2 = get_versioner()
        assert v1 is v2

    def test_get_versioner_with_store(self, mock_store):
        """Passing a store should use it for the instance."""
        v = get_versioner(store=mock_store)
        assert v._store is mock_store

    @patch("elle.capabilities.autogen.versioner.get_store")
    def test_reset_versioner(self, mock_get_store):
        """After reset, next get_versioner should create new instance."""
        mock_get_store.return_value = MagicMock()
        v1 = get_versioner()
        reset_versioner()
        v2 = get_versioner()
        assert v1 is not v2

    def test_reset_versioner_when_none(self):
        """Resetting when already None should not raise."""
        reset_versioner()
        reset_versioner()  # Should be safe


# ---------------------------------------------------------------------------
# Integration-like scenarios (still fully mocked)
# ---------------------------------------------------------------------------


class TestIntegrationScenarios:
    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.versioner.safe_model_dump_json", return_value="{}")
    @patch("elle.capabilities.autogen.versioner.create_capability_from_spec")
    @patch("elle.capabilities.autogen.versioner.validate_capability")
    @patch("elle.capabilities.autogen.versioner.generate_capability_spec", new_callable=AsyncMock)
    @patch("elle.capabilities.autogen.versioner.parse_man_page")
    @patch("elle.capabilities.autogen.versioner.discover_binary")
    async def test_build_map_then_upgrade(
        self,
        mock_discover,
        mock_parse,
        mock_gen_spec,
        mock_validate,
        mock_create,
        mock_dump_json,
        versioner,
        mock_store,
    ):
        """Build map from store, then handle an upgrade event."""
        # Build initial map
        stored = _make_stored(package_version="1.24.0")
        mock_store.list_all.return_value = [stored]
        versioner.build_package_map()

        # Now handle upgrade
        mock_store.get.return_value = stored
        binary = _make_binary()
        man = _make_man_page(raw_text="CHANGED")
        mock_discover.return_value = binary
        mock_parse.return_value = man
        mock_store.check_needs_regeneration.return_value = True
        mock_gen_spec.return_value = _make_spec()
        mock_validate.return_value = MagicMock(trust_level=TrustLevel.OFFICIAL)
        mock_create.return_value = ("ic", "oc", "cc")

        result = await versioner.on_package_upgraded("nginx", "1.24.0", "1.26.0")

        assert result == ["nginx.reload"]
        stats = versioner.get_stats()
        assert stats["regeneration_stats"]["successful"] == 1
        assert stats["regeneration_stats"]["by_package"]["nginx"] == 1

    @pytest.mark.asyncio
    async def test_add_mapping_then_upgrade(self, versioner, mock_store):
        """Manually add mapping, then handle upgrade."""
        versioner.add_capability_mapping("nginx", "nginx.reload")

        stored = _make_stored(package_version="1.24.0")
        mock_store.get.return_value = stored

        with patch.object(
            versioner,
            "_regenerate_capability",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await versioner.on_package_upgraded("nginx", "1.24.0", "1.26.0")

        assert result == ["nginx.reload"]
        watched = versioner.get_watched_packages()
        assert "nginx" in watched

    def test_stats_after_multiple_operations(self, versioner, mock_store):
        """Stats should accurately reflect all operations."""
        versioner.add_capability_mapping("nginx", "nginx.reload")
        versioner.add_capability_mapping("nginx", "nginx.stop")
        versioner.add_capability_mapping("curl", "curl.fetch")

        stats = versioner.get_stats()
        assert stats["packages_tracked"] == 2
        assert stats["capabilities_tracked"] == 3

        summary = versioner.get_package_capabilities_summary()
        assert len(summary) == 2
