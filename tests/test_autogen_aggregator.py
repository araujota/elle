from __future__ import annotations

"""Tests for the intelligence aggregator module.

Covers:
- estimate_tokens function
- IntelligenceAggregator.__init__ (default and custom token_budget)
- IntelligenceAggregator.gather (happy path, extractor failures, partial success)
- IntelligenceAggregator._aggregate (all branches: metadata/manifest defaults,
  primary_binary selection, completions preference, subcommands, systemd_units)
- IntelligenceAggregator._deduplicate_flags (unique, higher confidence wins,
  same confidence with/without description, sorting)
- IntelligenceAggregator.to_llm_context (all formatting branches: metadata fields,
  primary binary, binaries overflow, subcommands overflow, flags with values,
  long descriptions, flags overflow, systemd units with exec_start,
  config files overflow, extraction sources)
- get_aggregator singleton (first call, cached return)
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from elle.capabilities.autogen.intelligence.models import (
    ExtractedFlag,
    ExtractedSubcommand,
    ExtractionResult,
    FileManifest,
    PackageIntelligence,
    PackageMetadata,
    ShellCompletions,
    SystemdUnitInfo,
)

# ---------------------------------------------------------------------------
# Helpers to build model fixtures
# ---------------------------------------------------------------------------


def _make_flag(
    flag: str = "--verbose",
    description: str = "",
    confidence: float = 0.8,
    source: str = "man_page",
    takes_value: bool = False,
    value_type: str | None = None,
) -> ExtractedFlag:
    return ExtractedFlag(
        flag=flag,
        description=description,
        takes_value=takes_value,
        value_type=value_type,
        source=source,
        confidence=confidence,
    )


def _make_subcommand(
    name: str = "run",
    description: str = "",
    source: str = "bash_completion",
) -> ExtractedSubcommand:
    return ExtractedSubcommand(name=name, description=description, source=source)


def _make_metadata(
    name: str = "ffmpeg",
    version: str = "5.1.3",
    description: str = "Multimedia framework",
    section: str | None = "utils",
    homepage: str | None = "https://ffmpeg.org",
) -> PackageMetadata:
    return PackageMetadata(
        name=name,
        version=version,
        description=description,
        section=section,
        homepage=homepage,
    )


def _make_manifest(
    binaries: tuple[str, ...] = ("/usr/bin/ffmpeg",),
    man_pages: tuple[str, ...] = (),
    completions: tuple[str, ...] = (),
    systemd_units: tuple[str, ...] = (),
    config_files: tuple[str, ...] = (),
) -> FileManifest:
    return FileManifest(
        binaries=binaries,
        man_pages=man_pages,
        completions=completions,
        systemd_units=systemd_units,
        config_files=config_files,
    )


def _make_result(
    source_name: str = "dpkg",
    success: bool = True,
    flags: tuple[ExtractedFlag, ...] = (),
    subcommands: tuple[ExtractedSubcommand, ...] = (),
    completions: ShellCompletions | None = None,
    systemd_units: tuple[SystemdUnitInfo, ...] = (),
    metadata: PackageMetadata | None = None,
    manifest: FileManifest | None = None,
    error: str | None = None,
) -> ExtractionResult:
    return ExtractionResult(
        source_name=source_name,
        success=success,
        flags=flags,
        subcommands=subcommands,
        completions=completions,
        systemd_units=systemd_units,
        metadata=metadata,
        manifest=manifest,
        error=error,
    )


def _make_systemd_unit(
    unit_name: str = "nginx.service",
    unit_type: str = "service",
    description: str | None = "A high performance web server",
    exec_start: str | None = "/usr/sbin/nginx -g 'daemon on;'",
) -> SystemdUnitInfo:
    return SystemdUnitInfo(
        unit_name=unit_name,
        unit_type=unit_type,
        description=description,
        exec_start=exec_start,
    )


def _make_completions(
    shell_type: str = "bash",
    flags: tuple[ExtractedFlag, ...] = (),
    subcommands: tuple[ExtractedSubcommand, ...] = (),
) -> ShellCompletions:
    return ShellCompletions(
        flags=flags,
        subcommands=subcommands,
        shell_type=shell_type,
    )


# ===================================================================
# Tests for estimate_tokens
# ===================================================================


class TestEstimateTokens:
    """Tests for the module-level estimate_tokens function."""

    def test_empty_string(self):
        from elle.capabilities.autogen.intelligence.aggregator import estimate_tokens

        assert estimate_tokens("") == 0

    def test_short_string(self):
        from elle.capabilities.autogen.intelligence.aggregator import estimate_tokens

        # 12 chars -> 3 tokens
        assert estimate_tokens("hello world!") == 3

    def test_longer_string(self):
        from elle.capabilities.autogen.intelligence.aggregator import estimate_tokens

        text = "a" * 100
        assert estimate_tokens(text) == 25

    def test_four_chars_per_token(self):
        from elle.capabilities.autogen.intelligence.aggregator import estimate_tokens

        # 8 chars -> exactly 2 tokens
        assert estimate_tokens("12345678") == 2

    def test_not_evenly_divisible(self):
        from elle.capabilities.autogen.intelligence.aggregator import estimate_tokens

        # 5 chars -> 1 token (integer division)
        assert estimate_tokens("abcde") == 1


# ===================================================================
# Tests for IntelligenceAggregator.__init__
# ===================================================================


class TestAggregatorInit:
    """Tests for IntelligenceAggregator constructor."""

    def test_default_budget(self):
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        agg = IntelligenceAggregator()
        assert agg.token_budget == IntelligenceAggregator.TOKEN_BUDGET_MAX

    def test_custom_budget(self):
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        agg = IntelligenceAggregator(token_budget=2000)
        assert agg.token_budget == 2000

    def test_none_budget_uses_default(self):
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        agg = IntelligenceAggregator(token_budget=None)
        assert agg.token_budget == IntelligenceAggregator.TOKEN_BUDGET_MAX

    def test_zero_budget_uses_default(self):
        """token_budget=0 is falsy so falls back to TOKEN_BUDGET_MAX."""
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        agg = IntelligenceAggregator(token_budget=0)
        assert agg.token_budget == IntelligenceAggregator.TOKEN_BUDGET_MAX


# ===================================================================
# Tests for IntelligenceAggregator.gather
# ===================================================================


class TestAggregatorGather:
    """Tests for the async gather method."""

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.intelligence.aggregator.get_extractors")
    async def test_gather_happy_path(self, mock_get_extractors):
        """All extractors succeed."""
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        meta = _make_metadata()
        manifest = _make_manifest()
        flag = _make_flag("--help", confidence=0.9)

        ext1 = MagicMock()
        ext1.name = "dpkg"
        ext1.extract.return_value = _make_result(source_name="dpkg", success=True, metadata=meta)

        ext2 = MagicMock()
        ext2.name = "manifest"
        ext2.extract.return_value = _make_result(source_name="manifest", success=True, manifest=manifest)

        ext3 = MagicMock()
        ext3.name = "help_output"
        ext3.extract.return_value = _make_result(source_name="help_output", success=True, flags=(flag,))

        mock_get_extractors.return_value = [ext1, ext2, ext3]

        agg = IntelligenceAggregator()
        intel = await agg.gather("ffmpeg")

        assert intel.package_name == "ffmpeg"
        assert intel.metadata.name == "ffmpeg"
        assert "dpkg" in intel.extraction_sources
        assert "manifest" in intel.extraction_sources
        assert "help_output" in intel.extraction_sources

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.intelligence.aggregator.get_extractors")
    async def test_gather_extractor_raises_exception(self, mock_get_extractors):
        """An extractor raises; gather continues and records the failure."""
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        good_ext = MagicMock()
        good_ext.name = "dpkg"
        good_ext.extract.return_value = _make_result(source_name="dpkg", success=True, metadata=_make_metadata())

        bad_ext = MagicMock()
        bad_ext.name = "broken"
        bad_ext.extract.side_effect = RuntimeError("boom")

        mock_get_extractors.return_value = [good_ext, bad_ext]

        agg = IntelligenceAggregator()
        intel = await agg.gather("ffmpeg")

        # The broken extractor should not be in successful sources
        assert "broken" not in intel.extraction_sources
        # The good extractor should still be present
        assert "dpkg" in intel.extraction_sources

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.intelligence.aggregator.get_extractors")
    async def test_gather_all_extractors_fail(self, mock_get_extractors):
        """All extractors fail; gather still returns a valid PackageIntelligence."""
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        ext = MagicMock()
        ext.name = "broken"
        ext.extract.side_effect = RuntimeError("nope")

        mock_get_extractors.return_value = [ext]

        agg = IntelligenceAggregator()
        intel = await agg.gather("nonexistent")

        assert intel.package_name == "nonexistent"
        assert intel.extraction_sources == ()
        assert intel.metadata.version == "unknown"

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.intelligence.aggregator.get_extractors")
    async def test_gather_no_extractors(self, mock_get_extractors):
        """No extractors at all still returns valid output."""
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        mock_get_extractors.return_value = []

        agg = IntelligenceAggregator()
        intel = await agg.gather("empty")

        assert intel.package_name == "empty"
        assert intel.extraction_sources == ()

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.intelligence.aggregator.get_extractors")
    async def test_gather_manifest_passed_to_subsequent_extractors(self, mock_get_extractors):
        """Manifest from earlier extractor is passed to later ones."""
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        manifest = _make_manifest(binaries=("/usr/bin/curl",))

        ext1 = MagicMock()
        ext1.name = "manifest_provider"
        ext1.extract.return_value = _make_result(source_name="manifest_provider", success=True, manifest=manifest)

        ext2 = MagicMock()
        ext2.name = "consumer"
        ext2.extract.return_value = _make_result(source_name="consumer", success=True)

        mock_get_extractors.return_value = [ext1, ext2]

        agg = IntelligenceAggregator()
        await agg.gather("curl")

        # ext2 should have been called with the manifest from ext1
        _, call_kwargs = ext2.extract.call_args
        assert call_kwargs.get("manifest") is not None or ext2.extract.call_args[0][1] is manifest

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.intelligence.aggregator.get_extractors")
    async def test_gather_unsuccessful_result_does_not_update_manifest(self, mock_get_extractors):
        """An unsuccessful result's manifest/metadata should not be used."""
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        ext = MagicMock()
        ext.name = "fail_ext"
        ext.extract.return_value = _make_result(
            source_name="fail_ext",
            success=False,
            manifest=_make_manifest(binaries=("/usr/bin/bad",)),
            metadata=_make_metadata(name="bad"),
        )

        mock_get_extractors.return_value = [ext]

        agg = IntelligenceAggregator()
        intel = await agg.gather("pkg")

        # Since the only extractor failed, defaults should be used
        assert intel.metadata.version == "unknown"
        assert intel.manifest.binaries == ()


# ===================================================================
# Tests for IntelligenceAggregator._aggregate
# ===================================================================


class TestAggregate:
    """Tests for the _aggregate private method."""

    def _agg(self) -> object:
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        return IntelligenceAggregator()

    def test_sources_from_successful_results_only(self):
        agg = self._agg()
        results = [
            _make_result(source_name="a", success=True),
            _make_result(source_name="b", success=False),
            _make_result(source_name="c", success=True),
        ]
        intel = agg._aggregate("pkg", results, None, None)
        assert set(intel.extraction_sources) == {"a", "c"}

    def test_default_metadata_when_none(self):
        agg = self._agg()
        intel = agg._aggregate("pkg", [], None, None)
        assert intel.metadata.name == "pkg"
        assert intel.metadata.version == "unknown"
        assert intel.metadata.description == ""

    def test_default_manifest_when_none(self):
        agg = self._agg()
        intel = agg._aggregate("pkg", [], None, None)
        assert intel.manifest.binaries == ()

    def test_provided_metadata_used(self):
        agg = self._agg()
        meta = _make_metadata(name="curl", version="7.81")
        intel = agg._aggregate("curl", [], None, meta)
        assert intel.metadata.version == "7.81"

    def test_provided_manifest_used(self):
        agg = self._agg()
        manifest = _make_manifest(binaries=("/usr/bin/curl",))
        intel = agg._aggregate("curl", [], manifest, None)
        assert "/usr/bin/curl" in intel.manifest.binaries

    def test_primary_binary_matches_package_name(self):
        agg = self._agg()
        manifest = _make_manifest(binaries=("/usr/bin/ffprobe", "/usr/bin/ffmpeg", "/usr/bin/ffplay"))
        intel = agg._aggregate("ffmpeg", [], manifest, None)
        assert intel.primary_binary == "ffmpeg"

    def test_primary_binary_falls_back_to_first(self):
        agg = self._agg()
        manifest = _make_manifest(binaries=("/usr/bin/ffprobe", "/usr/bin/ffplay"))
        intel = agg._aggregate("ffmpeg", [], manifest, None)
        assert intel.primary_binary == "ffprobe"

    def test_primary_binary_none_when_no_binaries(self):
        agg = self._agg()
        intel = agg._aggregate("pkg", [], None, None)
        assert intel.primary_binary is None

    def test_subcommands_collected_from_successful_results(self):
        agg = self._agg()
        sub1 = _make_subcommand(name="start")
        sub2 = _make_subcommand(name="stop")
        results = [
            _make_result(source_name="a", success=True, subcommands=(sub1,)),
            _make_result(source_name="b", success=False, subcommands=(sub2,)),
            _make_result(source_name="c", success=True, subcommands=(sub2,)),
        ]
        intel = agg._aggregate("pkg", results, None, None)
        names = [s.name for s in intel.subcommands]
        assert "start" in names
        assert "stop" in names
        # sub2 from the failed result should not be included -- only 2 total
        assert len(intel.subcommands) == 2

    def test_completions_prefers_bash(self):
        agg = self._agg()
        zsh_comp = _make_completions(shell_type="zsh")
        bash_comp = _make_completions(shell_type="bash")
        results = [
            _make_result(source_name="zsh", success=True, completions=zsh_comp),
            _make_result(source_name="bash", success=True, completions=bash_comp),
        ]
        intel = agg._aggregate("pkg", results, None, None)
        assert intel.completions is not None
        assert intel.completions.shell_type == "bash"

    def test_completions_falls_back_to_non_bash(self):
        agg = self._agg()
        zsh_comp = _make_completions(shell_type="zsh")
        results = [
            _make_result(source_name="zsh", success=True, completions=zsh_comp),
        ]
        intel = agg._aggregate("pkg", results, None, None)
        assert intel.completions is not None
        assert intel.completions.shell_type == "zsh"

    def test_completions_none_when_no_results_have_them(self):
        agg = self._agg()
        results = [_make_result(source_name="a", success=True)]
        intel = agg._aggregate("pkg", results, None, None)
        assert intel.completions is None

    def test_completions_skips_failed_results(self):
        agg = self._agg()
        bash_comp = _make_completions(shell_type="bash")
        results = [
            _make_result(source_name="bash", success=False, completions=bash_comp),
        ]
        intel = agg._aggregate("pkg", results, None, None)
        assert intel.completions is None

    def test_systemd_units_collected(self):
        agg = self._agg()
        unit = _make_systemd_unit()
        results = [
            _make_result(source_name="systemd", success=True, systemd_units=(unit,)),
        ]
        intel = agg._aggregate("nginx", results, None, None)
        assert len(intel.systemd_units) == 1
        assert intel.systemd_units[0].unit_name == "nginx.service"

    def test_systemd_units_skips_failed(self):
        agg = self._agg()
        unit = _make_systemd_unit()
        results = [
            _make_result(source_name="systemd", success=False, systemd_units=(unit,)),
        ]
        intel = agg._aggregate("nginx", results, None, None)
        assert len(intel.systemd_units) == 0

    def test_token_estimate_populated(self):
        agg = self._agg()
        intel = agg._aggregate("pkg", [], None, None)
        # token_estimate should be a positive integer for non-empty context
        assert intel.token_estimate > 0


# ===================================================================
# Tests for IntelligenceAggregator._deduplicate_flags
# ===================================================================


class TestDeduplicateFlags:
    """Tests for the _deduplicate_flags method."""

    def _agg(self) -> object:
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        return IntelligenceAggregator()

    def test_empty_results(self):
        agg = self._agg()
        assert agg._deduplicate_flags([]) == []

    def test_skips_unsuccessful_results(self):
        agg = self._agg()
        flag = _make_flag("--verbose", confidence=0.9)
        results = [_make_result(success=False, flags=(flag,))]
        assert agg._deduplicate_flags(results) == []

    def test_skips_results_without_flags(self):
        agg = self._agg()
        results = [_make_result(success=True, flags=())]
        assert agg._deduplicate_flags(results) == []

    def test_unique_flags_preserved(self):
        agg = self._agg()
        f1 = _make_flag("--alpha", confidence=0.8)
        f2 = _make_flag("--beta", confidence=0.7)
        results = [_make_result(success=True, flags=(f1, f2))]
        deduped = agg._deduplicate_flags(results)
        assert len(deduped) == 2
        flag_names = [f.flag for f in deduped]
        assert "--alpha" in flag_names
        assert "--beta" in flag_names

    def test_higher_confidence_wins(self):
        agg = self._agg()
        low = _make_flag("--verbose", confidence=0.5, source="help")
        high = _make_flag("--verbose", confidence=0.9, source="man")
        results = [
            _make_result(source_name="help", success=True, flags=(low,)),
            _make_result(source_name="man", success=True, flags=(high,)),
        ]
        deduped = agg._deduplicate_flags(results)
        assert len(deduped) == 1
        assert deduped[0].confidence == 0.9
        assert deduped[0].source == "man"

    def test_same_confidence_prefers_description(self):
        agg = self._agg()
        no_desc = _make_flag("--verbose", confidence=0.8, description="")
        with_desc = _make_flag("--verbose", confidence=0.8, description="Be verbose")
        results = [
            _make_result(source_name="a", success=True, flags=(no_desc,)),
            _make_result(source_name="b", success=True, flags=(with_desc,)),
        ]
        deduped = agg._deduplicate_flags(results)
        assert len(deduped) == 1
        assert deduped[0].description == "Be verbose"

    def test_same_confidence_both_have_desc_keeps_first(self):
        agg = self._agg()
        first = _make_flag("--verbose", confidence=0.8, description="First desc")
        second = _make_flag("--verbose", confidence=0.8, description="Second desc")
        results = [
            _make_result(source_name="a", success=True, flags=(first,)),
            _make_result(source_name="b", success=True, flags=(second,)),
        ]
        deduped = agg._deduplicate_flags(results)
        assert len(deduped) == 1
        # Both have descriptions, so the first one encountered is retained
        assert deduped[0].description == "First desc"

    def test_case_insensitive_dedup(self):
        agg = self._agg()
        upper = _make_flag("--Verbose", confidence=0.9)
        lower = _make_flag("--verbose", confidence=0.5)
        results = [
            _make_result(source_name="a", success=True, flags=(upper,)),
            _make_result(source_name="b", success=True, flags=(lower,)),
        ]
        deduped = agg._deduplicate_flags(results)
        assert len(deduped) == 1

    def test_sorted_by_flag_name(self):
        agg = self._agg()
        f_z = _make_flag("--zebra", confidence=0.8)
        f_a = _make_flag("--alpha", confidence=0.8)
        f_m = _make_flag("--middle", confidence=0.8)
        results = [_make_result(success=True, flags=(f_z, f_a, f_m))]
        deduped = agg._deduplicate_flags(results)
        flags = [f.flag for f in deduped]
        assert flags == ["--alpha", "--middle", "--zebra"]


# ===================================================================
# Tests for IntelligenceAggregator.to_llm_context
# ===================================================================


class TestToLlmContext:
    """Tests for the to_llm_context formatting method."""

    def _agg(self) -> object:
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        return IntelligenceAggregator()

    def _minimal_intel(self, **overrides) -> PackageIntelligence:
        defaults = {
            "package_name": "testpkg",
            "metadata": _make_metadata(
                name="testpkg",
                version="1.0",
                description="",
                section=None,
                homepage=None,
            ),
            "manifest": FileManifest(),
            "all_flags": (),
            "subcommands": (),
            "completions": None,
            "systemd_units": (),
            "extraction_sources": ("dpkg",),
            "extraction_timestamp": datetime(2024, 1, 1),
            "token_estimate": 0,
            "primary_binary": None,
        }
        defaults.update(overrides)
        return PackageIntelligence(**defaults)

    def test_header_present(self):
        agg = self._agg()
        intel = self._minimal_intel()
        ctx = agg.to_llm_context(intel)
        assert "# Package: testpkg" in ctx

    def test_metadata_name_and_version(self):
        agg = self._agg()
        intel = self._minimal_intel()
        ctx = agg.to_llm_context(intel)
        assert "- Name: testpkg" in ctx
        assert "- Version: 1.0" in ctx

    def test_metadata_description_when_present(self):
        agg = self._agg()
        intel = self._minimal_intel(metadata=_make_metadata(description="A great tool"))
        ctx = agg.to_llm_context(intel)
        assert "- Description: A great tool" in ctx

    def test_metadata_description_absent_when_empty(self):
        agg = self._agg()
        intel = self._minimal_intel(metadata=_make_metadata(description=""))
        ctx = agg.to_llm_context(intel)
        assert "- Description:" not in ctx

    def test_metadata_section_when_present(self):
        agg = self._agg()
        intel = self._minimal_intel(metadata=_make_metadata(section="utils"))
        ctx = agg.to_llm_context(intel)
        assert "- Section: utils" in ctx

    def test_metadata_section_absent_when_none(self):
        agg = self._agg()
        intel = self._minimal_intel(metadata=_make_metadata(section=None))
        ctx = agg.to_llm_context(intel)
        assert "- Section:" not in ctx

    def test_metadata_homepage_when_present(self):
        agg = self._agg()
        intel = self._minimal_intel(metadata=_make_metadata(homepage="https://example.com"))
        ctx = agg.to_llm_context(intel)
        assert "- Homepage: https://example.com" in ctx

    def test_metadata_homepage_absent_when_none(self):
        agg = self._agg()
        intel = self._minimal_intel(metadata=_make_metadata(homepage=None))
        ctx = agg.to_llm_context(intel)
        assert "- Homepage:" not in ctx

    def test_primary_binary_section(self):
        agg = self._agg()
        intel = self._minimal_intel(primary_binary="ffmpeg")
        ctx = agg.to_llm_context(intel)
        assert "## Primary Binary: ffmpeg" in ctx

    def test_primary_binary_absent_when_none(self):
        agg = self._agg()
        intel = self._minimal_intel(primary_binary=None)
        ctx = agg.to_llm_context(intel)
        assert "## Primary Binary:" not in ctx

    def test_binaries_section(self):
        agg = self._agg()
        intel = self._minimal_intel(manifest=_make_manifest(binaries=("/usr/bin/ffmpeg", "/usr/bin/ffprobe")))
        ctx = agg.to_llm_context(intel)
        assert "## Binaries" in ctx
        assert "- ffmpeg" in ctx
        assert "- ffprobe" in ctx

    def test_binaries_truncated_after_10(self):
        agg = self._agg()
        binaries = tuple(f"/usr/bin/tool{i}" for i in range(15))
        intel = self._minimal_intel(manifest=_make_manifest(binaries=binaries))
        ctx = agg.to_llm_context(intel)
        assert "... and 5 more" in ctx

    def test_binaries_not_truncated_at_10(self):
        agg = self._agg()
        binaries = tuple(f"/usr/bin/tool{i}" for i in range(10))
        intel = self._minimal_intel(manifest=_make_manifest(binaries=binaries))
        ctx = agg.to_llm_context(intel)
        assert "... and" not in ctx

    def test_binaries_absent_when_empty(self):
        agg = self._agg()
        intel = self._minimal_intel()
        ctx = agg.to_llm_context(intel)
        assert "## Binaries" not in ctx

    def test_subcommands_section(self):
        agg = self._agg()
        subs = (
            _make_subcommand(name="start", description="Start a service"),
            _make_subcommand(name="stop", description=""),
        )
        intel = self._minimal_intel(subcommands=subs)
        ctx = agg.to_llm_context(intel)
        assert "## Subcommands" in ctx
        assert "- start: Start a service" in ctx
        assert "- stop" in ctx

    def test_subcommands_truncated_after_20(self):
        agg = self._agg()
        subs = tuple(_make_subcommand(name=f"cmd{i}") for i in range(25))
        intel = self._minimal_intel(subcommands=subs)
        ctx = agg.to_llm_context(intel)
        assert "... and 5 more" in ctx

    def test_subcommands_not_truncated_at_20(self):
        agg = self._agg()
        subs = tuple(_make_subcommand(name=f"cmd{i}") for i in range(20))
        intel = self._minimal_intel(subcommands=subs)
        ctx = agg.to_llm_context(intel)
        assert "... and" not in ctx

    def test_subcommands_absent_when_empty(self):
        agg = self._agg()
        intel = self._minimal_intel()
        ctx = agg.to_llm_context(intel)
        assert "## Subcommands" not in ctx

    def test_flags_section_basic(self):
        agg = self._agg()
        flags = (_make_flag("--verbose", confidence=0.8, source="man"),)
        intel = self._minimal_intel(all_flags=flags)
        ctx = agg.to_llm_context(intel)
        assert "## Flags/Options" in ctx
        assert "`--verbose`" in ctx
        assert "(man, 80%)" in ctx

    def test_flags_with_value_type(self):
        agg = self._agg()
        flags = (
            _make_flag(
                "--output",
                takes_value=True,
                value_type="file",
                confidence=0.9,
                source="man",
            ),
        )
        intel = self._minimal_intel(all_flags=flags)
        ctx = agg.to_llm_context(intel)
        assert "<file>" in ctx

    def test_flags_long_description_truncated(self):
        agg = self._agg()
        long_desc = "x" * 150
        flags = (_make_flag("--long", description=long_desc, confidence=0.8, source="man"),)
        intel = self._minimal_intel(all_flags=flags)
        ctx = agg.to_llm_context(intel)
        assert "..." in ctx
        # The truncated description should be 100 chars + "..."
        assert ("x" * 100 + "...") in ctx

    def test_flags_short_description_not_truncated(self):
        agg = self._agg()
        short_desc = "Short desc"
        flags = (_make_flag("--short", description=short_desc, confidence=0.8, source="man"),)
        intel = self._minimal_intel(all_flags=flags)
        ctx = agg.to_llm_context(intel)
        assert ": Short desc" in ctx
        # No ellipsis appended to short descriptions
        assert "Short desc..." not in ctx

    def test_flags_sorted_by_confidence_desc(self):
        agg = self._agg()
        flags = (
            _make_flag("--low", confidence=0.5, source="help"),
            _make_flag("--high", confidence=0.95, source="man"),
            _make_flag("--mid", confidence=0.7, source="bash"),
        )
        intel = self._minimal_intel(all_flags=flags)
        ctx = agg.to_llm_context(intel)
        # High should appear before mid and low
        high_pos = ctx.index("--high")
        mid_pos = ctx.index("--mid")
        low_pos = ctx.index("--low")
        assert high_pos < mid_pos < low_pos

    def test_flags_truncated_by_max_flags(self):
        agg = self._agg()
        flags = tuple(_make_flag(f"--flag{i:03d}", confidence=0.8, source="man") for i in range(60))
        intel = self._minimal_intel(all_flags=flags)
        ctx = agg.to_llm_context(intel, max_flags=50)
        assert "... and 10 more flags" in ctx

    def test_flags_not_truncated_at_max(self):
        agg = self._agg()
        flags = tuple(_make_flag(f"--flag{i:03d}", confidence=0.8, source="man") for i in range(50))
        intel = self._minimal_intel(all_flags=flags)
        ctx = agg.to_llm_context(intel, max_flags=50)
        assert "more flags" not in ctx

    def test_flags_absent_when_empty(self):
        agg = self._agg()
        intel = self._minimal_intel()
        ctx = agg.to_llm_context(intel)
        assert "## Flags/Options" not in ctx

    def test_systemd_units_section(self):
        agg = self._agg()
        units = (
            _make_systemd_unit(
                unit_name="nginx.service",
                description="Nginx Web Server",
                exec_start="/usr/sbin/nginx",
            ),
        )
        intel = self._minimal_intel(systemd_units=units)
        ctx = agg.to_llm_context(intel)
        assert "## Systemd Units" in ctx
        assert "- nginx.service: Nginx Web Server" in ctx
        assert "ExecStart: /usr/sbin/nginx" in ctx

    def test_systemd_units_no_description(self):
        agg = self._agg()
        units = (_make_systemd_unit(description=None, exec_start=None),)
        intel = self._minimal_intel(systemd_units=units)
        ctx = agg.to_llm_context(intel)
        assert "## Systemd Units" in ctx
        # No ": " suffix when description is None
        assert "- nginx.service\n" in ctx
        # No ExecStart line when exec_start is None
        assert "ExecStart:" not in ctx

    def test_systemd_units_absent_when_empty(self):
        agg = self._agg()
        intel = self._minimal_intel()
        ctx = agg.to_llm_context(intel)
        assert "## Systemd Units" not in ctx

    def test_config_files_section(self):
        agg = self._agg()
        intel = self._minimal_intel(
            manifest=_make_manifest(config_files=("/etc/nginx/nginx.conf", "/etc/nginx/mime.types"))
        )
        ctx = agg.to_llm_context(intel)
        assert "## Config Files" in ctx
        assert "- /etc/nginx/nginx.conf" in ctx

    def test_config_files_truncated_after_5(self):
        agg = self._agg()
        cfgs = tuple(f"/etc/app/conf{i}.cfg" for i in range(8))
        intel = self._minimal_intel(manifest=_make_manifest(config_files=cfgs))
        ctx = agg.to_llm_context(intel)
        assert "... and 3 more" in ctx

    def test_config_files_not_truncated_at_5(self):
        agg = self._agg()
        cfgs = tuple(f"/etc/app/conf{i}.cfg" for i in range(5))
        intel = self._minimal_intel(manifest=_make_manifest(config_files=cfgs))
        ctx = agg.to_llm_context(intel)
        assert "... and" not in ctx

    def test_config_files_absent_when_empty(self):
        agg = self._agg()
        intel = self._minimal_intel()
        ctx = agg.to_llm_context(intel)
        assert "## Config Files" not in ctx

    def test_sources_line(self):
        agg = self._agg()
        intel = self._minimal_intel(extraction_sources=("dpkg", "man_page", "help"))
        ctx = agg.to_llm_context(intel)
        assert "## Sources: dpkg, man_page, help" in ctx

    def test_sources_empty(self):
        agg = self._agg()
        intel = self._minimal_intel(extraction_sources=())
        ctx = agg.to_llm_context(intel)
        assert "## Sources: " in ctx

    def test_flag_takes_value_false_no_value_type_shown(self):
        """When takes_value is False, value_type should NOT appear even if set."""
        agg = self._agg()
        flags = (
            _make_flag(
                "--output",
                takes_value=False,
                value_type="file",
                confidence=0.9,
                source="man",
            ),
        )
        intel = self._minimal_intel(all_flags=flags)
        ctx = agg.to_llm_context(intel)
        assert "<file>" not in ctx

    def test_flag_takes_value_true_but_no_value_type(self):
        """When takes_value is True but value_type is None, no <type> shown."""
        agg = self._agg()
        flags = (
            _make_flag(
                "--output",
                takes_value=True,
                value_type=None,
                confidence=0.9,
                source="man",
            ),
        )
        intel = self._minimal_intel(all_flags=flags)
        ctx = agg.to_llm_context(intel)
        assert "`--output`" in ctx
        # No angle-bracket type should appear
        assert "<" not in ctx.split("--output")[1].split("\n")[0]

    def test_full_context_structure(self):
        """End-to-end format: a richly populated intelligence produces expected sections."""
        agg = self._agg()
        intel = self._minimal_intel(
            package_name="nginx",
            metadata=_make_metadata(
                name="nginx",
                version="1.22.1",
                description="High performance web server",
                section="httpd",
                homepage="https://nginx.org",
            ),
            manifest=_make_manifest(
                binaries=("/usr/sbin/nginx",),
                config_files=("/etc/nginx/nginx.conf",),
            ),
            primary_binary="nginx",
            all_flags=(_make_flag("--help", confidence=0.9, source="help"),),
            subcommands=(_make_subcommand(name="reload", description="Reload config"),),
            systemd_units=(_make_systemd_unit(),),
            extraction_sources=("dpkg", "manifest", "help_output"),
        )
        ctx = agg.to_llm_context(intel)

        # Every expected section header should be present
        assert "# Package: nginx" in ctx
        assert "## Metadata" in ctx
        assert "## Primary Binary: nginx" in ctx
        assert "## Binaries" in ctx
        assert "## Subcommands" in ctx
        assert "## Flags/Options" in ctx
        assert "## Systemd Units" in ctx
        assert "## Config Files" in ctx
        assert "## Sources:" in ctx


# ===================================================================
# Tests for get_aggregator singleton
# ===================================================================


class TestGetAggregator:
    """Tests for the module-level singleton accessor."""

    def test_returns_instance(self):
        from elle.capabilities.autogen.intelligence import aggregator as mod

        # Reset the singleton
        mod._aggregator = None
        agg = mod.get_aggregator()
        assert isinstance(agg, mod.IntelligenceAggregator)

    def test_returns_same_instance(self):
        from elle.capabilities.autogen.intelligence import aggregator as mod

        mod._aggregator = None
        first = mod.get_aggregator()
        second = mod.get_aggregator()
        assert first is second

    def test_creates_when_none(self):
        from elle.capabilities.autogen.intelligence import aggregator as mod

        mod._aggregator = None
        assert mod._aggregator is None
        mod.get_aggregator()
        assert mod._aggregator is not None

    def test_default_budget_on_singleton(self):
        from elle.capabilities.autogen.intelligence import aggregator as mod

        mod._aggregator = None
        agg = mod.get_aggregator()
        assert agg.token_budget == mod.IntelligenceAggregator.TOKEN_BUDGET_MAX


# ===================================================================
# Tests for edge-case interactions
# ===================================================================


class TestEdgeCases:
    """Miscellaneous edge-case tests."""

    def test_exec_start_truncated_in_systemd_output(self):
        """exec_start is sliced to 80 chars in to_llm_context."""
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        long_exec = "/usr/sbin/nginx " + "x" * 100
        unit = _make_systemd_unit(exec_start=long_exec)
        intel = PackageIntelligence(
            package_name="pkg",
            metadata=_make_metadata(),
            manifest=FileManifest(),
            systemd_units=(unit,),
            extraction_sources=("test",),
            extraction_timestamp=datetime(2024, 1, 1),
        )
        agg = IntelligenceAggregator()
        ctx = agg.to_llm_context(intel)
        # The exec_start line should be truncated to 80 chars
        for line in ctx.split("\n"):
            if "ExecStart:" in line:
                exec_value = line.split("ExecStart: ")[1]
                assert len(exec_value) == 80
                break
        else:
            pytest.fail("ExecStart line not found")

    @pytest.mark.asyncio
    @patch("elle.capabilities.autogen.intelligence.aggregator.get_extractors")
    async def test_gather_metadata_updated_by_second_extractor(self, mock_get_extractors):
        """If two extractors provide metadata, the last successful one wins for _aggregate."""
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        meta1 = _make_metadata(name="pkg", version="1.0")
        meta2 = _make_metadata(name="pkg", version="2.0")

        ext1 = MagicMock()
        ext1.name = "dpkg1"
        ext1.extract.return_value = _make_result(source_name="dpkg1", success=True, metadata=meta1)

        ext2 = MagicMock()
        ext2.name = "dpkg2"
        ext2.extract.return_value = _make_result(source_name="dpkg2", success=True, metadata=meta2)

        mock_get_extractors.return_value = [ext1, ext2]

        agg = IntelligenceAggregator()
        intel = await agg.gather("pkg")

        # The last successful metadata should be the one used
        assert intel.metadata.version == "2.0"

    def test_max_flags_zero_shows_no_flags(self):
        """Passing max_flags=0 should include the section header but no flags."""
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        flags = (_make_flag("--verbose", confidence=0.8, source="man"),)
        intel = PackageIntelligence(
            package_name="pkg",
            metadata=_make_metadata(),
            manifest=FileManifest(),
            all_flags=flags,
            extraction_sources=("test",),
            extraction_timestamp=datetime(2024, 1, 1),
        )
        agg = IntelligenceAggregator()
        ctx = agg.to_llm_context(intel, max_flags=0)
        assert "## Flags/Options" in ctx
        assert "`--verbose`" not in ctx
        assert "... and 1 more flags" in ctx

    def test_subcommand_without_description(self):
        """Subcommand with empty description should have no ': ' suffix."""
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        subs = (_make_subcommand(name="init", description=""),)
        intel = PackageIntelligence(
            package_name="pkg",
            metadata=_make_metadata(),
            manifest=FileManifest(),
            subcommands=subs,
            extraction_sources=("test",),
            extraction_timestamp=datetime(2024, 1, 1),
        )
        agg = IntelligenceAggregator()
        ctx = agg.to_llm_context(intel)
        # Should be "- init" without a trailing ":"
        assert "- init\n" in ctx
        assert "- init:" not in ctx

    def test_completions_first_non_bash_wins_if_no_bash(self):
        """When no bash completions exist, the first non-bash one is used."""
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        fish_comp = _make_completions(shell_type="fish")
        zsh_comp = _make_completions(shell_type="zsh")
        results = [
            _make_result(source_name="fish", success=True, completions=fish_comp),
            _make_result(source_name="zsh", success=True, completions=zsh_comp),
        ]
        agg = IntelligenceAggregator()
        intel = agg._aggregate("pkg", results, None, None)
        # fish was encountered first, so it should be selected
        assert intel.completions is not None
        assert intel.completions.shell_type == "fish"

    def test_completions_bash_breaks_loop_even_if_later(self):
        """Bash completion causes break; earlier non-bash completions are replaced."""
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        zsh_comp = _make_completions(shell_type="zsh")
        bash_comp = _make_completions(shell_type="bash")
        results = [
            _make_result(source_name="zsh", success=True, completions=zsh_comp),
            _make_result(source_name="bash", success=True, completions=bash_comp),
        ]
        agg = IntelligenceAggregator()
        intel = agg._aggregate("pkg", results, None, None)
        assert intel.completions.shell_type == "bash"

    def test_flag_description_empty_not_shown(self):
        """Flag with empty description should not have ': ' in output."""
        from elle.capabilities.autogen.intelligence.aggregator import IntelligenceAggregator

        flags = (_make_flag("--quiet", description="", confidence=0.8, source="man"),)
        intel = PackageIntelligence(
            package_name="pkg",
            metadata=_make_metadata(),
            manifest=FileManifest(),
            all_flags=flags,
            extraction_sources=("test",),
            extraction_timestamp=datetime(2024, 1, 1),
        )
        agg = IntelligenceAggregator()
        ctx = agg.to_llm_context(intel)
        for line in ctx.split("\n"):
            if "`--quiet`" in line:
                assert ": " not in line.split("`--quiet`")[1].split("(")[0]
                break
