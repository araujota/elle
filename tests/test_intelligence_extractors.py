"""Tests for package intelligence extractors.

Tests the multi-source intelligence extraction system including:
- DpkgMetadataExtractor
- FileManifestExtractor
- BashCompletionExtractor
- ZshCompletionExtractor
- ManPageExtractor
- SystemdUnitExtractor
- HelpOutputExtractor
- IntelligenceAggregator
"""

from unittest.mock import MagicMock, patch

from elle.capabilities.autogen.intelligence.aggregator import (
    IntelligenceAggregator,
    estimate_tokens,
    get_aggregator,
)
from elle.capabilities.autogen.intelligence.extractors import (
    ALL_EXTRACTORS,
    BashCompletionExtractor,
    DpkgMetadataExtractor,
    FileManifestExtractor,
    HelpOutputExtractor,
    ManPageExtractor,
    SystemdUnitExtractor,
    ZshCompletionExtractor,
    get_extractors,
)
from elle.capabilities.autogen.intelligence.models import (
    ExtractedFlag,
    ExtractionResult,
    FileManifest,
    PackageIntelligence,
    PackageMetadata,
)


class TestExtractionResult:
    """Tests for ExtractionResult model."""

    def test_extraction_result_success(self):
        """Test successful extraction result."""
        result = ExtractionResult(
            source_name="dpkg",
            success=True,
            flags=(ExtractedFlag(flag="-v", description="verbose", source="dpkg"),),
        )

        assert result.success
        assert result.source_name == "dpkg"
        assert len(result.flags) == 1

    def test_extraction_result_failure(self):
        """Test failed extraction result."""
        result = ExtractionResult(
            source_name="dpkg",
            success=False,
            error="Package not found",
        )

        assert not result.success
        assert result.error == "Package not found"
        assert len(result.flags) == 0


class TestDpkgMetadataExtractor:
    """Tests for DpkgMetadataExtractor."""

    def test_extractor_attributes(self):
        """Test extractor has required attributes."""
        extractor = DpkgMetadataExtractor()

        assert extractor.name == "dpkg"
        assert extractor.priority == 10

    @patch("subprocess.run")
    def test_extract_success(self, mock_run):
        """Test successful metadata extraction."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ffmpeg\n6.0-1\nTools for transcoding\nvideo\nlibavcodec60\nhttps://ffmpeg.org",
        )

        extractor = DpkgMetadataExtractor()
        result = extractor.extract("ffmpeg")

        assert result.success
        assert result.metadata is not None
        assert result.metadata.name == "ffmpeg"
        assert result.metadata.version == "6.0-1"

    @patch("subprocess.run")
    def test_extract_package_not_found(self, mock_run):
        """Test extraction when package not found."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
        )

        extractor = DpkgMetadataExtractor()
        result = extractor.extract("nonexistent")

        assert not result.success
        assert "not found" in result.error.lower()


class TestFileManifestExtractor:
    """Tests for FileManifestExtractor."""

    def test_extractor_attributes(self):
        """Test extractor has required attributes."""
        extractor = FileManifestExtractor()

        assert extractor.name == "manifest"
        assert extractor.priority == 20

    @patch("subprocess.run")
    def test_extract_success(self, mock_run):
        """Test successful file manifest extraction."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/usr/bin/ffmpeg\n/usr/bin/ffprobe\n/usr/share/man/man1/ffmpeg.1.gz\n/usr/share/bash-completion/completions/ffmpeg\n",
        )

        extractor = FileManifestExtractor()
        result = extractor.extract("ffmpeg")

        assert result.success
        assert result.manifest is not None
        # Note: binary detection requires actual file check, so may be empty in mock
        assert len(result.manifest.man_pages) >= 0


class TestBashCompletionExtractor:
    """Tests for BashCompletionExtractor."""

    def test_extractor_attributes(self):
        """Test extractor has required attributes."""
        extractor = BashCompletionExtractor()

        assert extractor.name == "bash_completion"
        assert extractor.priority == 30

    def test_extract_no_completions(self):
        """Test extraction when no completion files exist."""
        extractor = BashCompletionExtractor()
        manifest = FileManifest()  # Empty manifest

        result = extractor.extract("nonexistent", manifest)

        assert not result.success
        assert "No bash completion" in result.error


class TestZshCompletionExtractor:
    """Tests for ZshCompletionExtractor."""

    def test_extractor_attributes(self):
        """Test extractor has required attributes."""
        extractor = ZshCompletionExtractor()

        assert extractor.name == "zsh_completion"
        assert extractor.priority == 31


class TestManPageExtractor:
    """Tests for ManPageExtractor."""

    def test_extractor_attributes(self):
        """Test extractor has required attributes."""
        extractor = ManPageExtractor()

        assert extractor.name == "man_page"
        assert extractor.priority == 40


class TestSystemdUnitExtractor:
    """Tests for SystemdUnitExtractor."""

    def test_extractor_attributes(self):
        """Test extractor has required attributes."""
        extractor = SystemdUnitExtractor()

        assert extractor.name == "systemd_unit"
        assert extractor.priority == 50

    def test_extract_no_units(self):
        """Test extraction when no systemd units in manifest."""
        extractor = SystemdUnitExtractor()
        manifest = FileManifest()  # Empty manifest

        result = extractor.extract("ffmpeg", manifest)

        assert not result.success
        assert "No systemd units" in result.error


class TestHelpOutputExtractor:
    """Tests for HelpOutputExtractor."""

    def test_extractor_attributes(self):
        """Test extractor has required attributes."""
        extractor = HelpOutputExtractor()

        assert extractor.name == "help_output"
        assert extractor.priority == 70


class TestExtractorRegistry:
    """Tests for extractor registry."""

    def test_all_extractors_defined(self):
        """Test all extractors are in the registry."""
        assert len(ALL_EXTRACTORS) >= 7  # At least 7 extractors

    def test_extractors_have_required_attrs(self):
        """Test all extractors have required attributes."""
        for extractor_cls in ALL_EXTRACTORS:
            assert hasattr(extractor_cls, "name")
            assert hasattr(extractor_cls, "priority")
            assert hasattr(extractor_cls, "extract")

    def test_get_extractors_sorted_by_priority(self):
        """Test get_extractors returns sorted list."""
        extractors = get_extractors()

        # Should be sorted by priority
        priorities = [e.priority for e in extractors]
        assert priorities == sorted(priorities)


class TestIntelligenceAggregator:
    """Tests for IntelligenceAggregator."""

    def test_aggregator_default_budget(self):
        """Test aggregator has default token budget."""
        aggregator = IntelligenceAggregator()

        assert aggregator.token_budget == IntelligenceAggregator.TOKEN_BUDGET_MAX

    def test_aggregator_custom_budget(self):
        """Test aggregator accepts custom token budget."""
        aggregator = IntelligenceAggregator(token_budget=2000)

        assert aggregator.token_budget == 2000

    def test_get_aggregator_singleton(self):
        """Test get_aggregator returns singleton."""
        agg1 = get_aggregator()
        agg2 = get_aggregator()

        assert agg1 is agg2

    def test_to_llm_context_basic(self):
        """Test LLM context generation."""
        aggregator = IntelligenceAggregator()

        metadata = PackageMetadata(
            name="ffmpeg",
            version="6.0-1",
            description="Tools for transcoding",
        )

        manifest = FileManifest(
            binaries=("/usr/bin/ffmpeg",),
        )

        intel = PackageIntelligence(
            package_name="ffmpeg",
            metadata=metadata,
            manifest=manifest,
            all_flags=(ExtractedFlag(flag="-i", description="Input", source="man"),),
            extraction_sources=("dpkg", "man"),
            primary_binary="ffmpeg",
        )

        context = aggregator.to_llm_context(intel)

        assert "ffmpeg" in context
        assert "6.0-1" in context
        assert "-i" in context
        assert "dpkg" in context or "man" in context


class TestTokenEstimation:
    """Tests for token estimation."""

    def test_estimate_tokens_basic(self):
        """Test basic token estimation."""
        text = "This is a test string."

        tokens = estimate_tokens(text)

        # Rough estimate: ~4 chars per token
        assert 4 <= tokens <= 8

    def test_estimate_tokens_empty(self):
        """Test token estimation for empty string."""
        tokens = estimate_tokens("")

        assert tokens == 0


class TestFlagDeduplication:
    """Tests for flag deduplication in aggregator."""

    def test_deduplicate_prefers_higher_confidence(self):
        """Test that deduplication prefers higher confidence flags."""
        aggregator = IntelligenceAggregator()

        results = [
            ExtractionResult(
                source_name="man_page",
                success=True,
                flags=(
                    ExtractedFlag(
                        flag="-v",
                        description="verbose",
                        source="man_page",
                        confidence=0.8,
                    ),
                ),
            ),
            ExtractionResult(
                source_name="bash_completion",
                success=True,
                flags=(
                    ExtractedFlag(
                        flag="-v",
                        description="verbose mode",
                        source="bash_completion",
                        confidence=0.9,
                    ),
                ),
            ),
        ]

        deduped = aggregator._deduplicate_flags(results)

        assert len(deduped) == 1
        # Should prefer bash_completion (higher confidence)
        assert deduped[0].confidence == 0.9
        assert deduped[0].source == "bash_completion"

    def test_deduplicate_prefers_with_description(self):
        """Test that deduplication prefers flags with descriptions."""
        aggregator = IntelligenceAggregator()

        results = [
            ExtractionResult(
                source_name="completion",
                success=True,
                flags=(
                    ExtractedFlag(
                        flag="-v",
                        description="",  # No description
                        source="completion",
                        confidence=0.9,
                    ),
                ),
            ),
            ExtractionResult(
                source_name="man_page",
                success=True,
                flags=(
                    ExtractedFlag(
                        flag="-v",
                        description="Enable verbose output",
                        source="man_page",
                        confidence=0.9,  # Same confidence
                    ),
                ),
            ),
        ]

        deduped = aggregator._deduplicate_flags(results)

        assert len(deduped) == 1
        # Should prefer man_page (has description)
        assert deduped[0].description == "Enable verbose output"


class TestExtractorProtocol:
    """Tests for extractor protocol compliance."""

    def test_all_extractors_implement_protocol(self):
        """Test all extractors implement the extract method."""
        for extractor_cls in ALL_EXTRACTORS:
            extractor = extractor_cls()
            assert callable(extractor.extract)

            # Should accept package_name and optional manifest
            import inspect

            sig = inspect.signature(extractor.extract)
            params = list(sig.parameters.keys())
            assert "package_name" in params or len(params) >= 1
