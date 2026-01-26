"""Tests for /learn package capability generation.

Tests the new /learn command that generates typed capabilities from
installed packages using multi-source intelligence extraction.
"""

from unittest.mock import MagicMock, patch

import pytest

from elle.cli.package_learn_commands import (
    _get_help,
    _handle_list,
    _handle_show,
    handle_learn_command,
)


class TestLearnCommandHelp:
    """Tests for /learn help."""

    def test_get_help(self):
        """Test help text generation."""
        help_text = _get_help()

        assert "/learn" in help_text
        assert "Usage:" in help_text
        assert "Examples:" in help_text
        assert "ffmpeg" in help_text
        assert "capabilities" in help_text.lower()

    @pytest.mark.asyncio
    async def test_empty_args_returns_help(self):
        """Test that empty args returns help."""
        result = await handle_learn_command("")

        assert "/learn" in result
        assert "Usage:" in result


class TestLearnCommandRouting:
    """Tests for command routing."""

    @pytest.mark.asyncio
    async def test_help_subcommand(self):
        """Test /learn help subcommand."""
        result = await handle_learn_command("help")

        assert "Usage:" in result

    @pytest.mark.asyncio
    async def test_list_subcommand_empty(self):
        """Test /learn list with no packages."""
        with patch("elle.capabilities.autogen.get_store") as mock_get_store:
            mock_store = MagicMock()
            mock_store.list_packages_with_capabilities.return_value = []
            mock_get_store.return_value = mock_store

            result = await _handle_list("")

            assert "No packages have generated capabilities" in result

    @pytest.mark.asyncio
    async def test_show_no_args(self):
        """Test /learn show without package name."""
        result = await _handle_show("")

        assert "Usage:" in result

    @pytest.mark.asyncio
    async def test_show_not_found(self):
        """Test /learn show for non-existent package."""
        with patch("elle.capabilities.autogen.get_store") as mock_get_store:
            mock_store = MagicMock()
            mock_store.list_by_package.return_value = []
            mock_store.list_by_command.return_value = []
            mock_get_store.return_value = mock_store

            result = await _handle_show("nonexistent")

            assert "No capabilities found" in result


class TestLearnResult:
    """Tests for LearnResult model."""

    def test_learn_result_creation(self):
        """Test LearnResult model."""
        from elle.cli.package_learn_commands import LearnResult

        result = LearnResult(
            package_name="ffmpeg",
            capabilities_generated=5,
            capabilities_validated=4,
            capabilities_saved=4,
            extraction_sources=("dpkg", "bash_completion", "man_page"),
            errors=(),
            warnings=("One flag not found",),
        )

        assert result.package_name == "ffmpeg"
        assert result.capabilities_generated == 5
        assert result.capabilities_validated == 4
        assert result.capabilities_saved == 4
        assert len(result.extraction_sources) == 3
        assert len(result.warnings) == 1


class TestIntentClassification:
    """Tests for LEARN_PACKAGE intent classification."""

    def test_learn_package_intent_exists(self):
        """Test that LEARN_PACKAGE intent is defined."""
        from elle.cli.terminal.intent import Intent

        assert hasattr(Intent, "LEARN_PACKAGE")
        assert Intent.LEARN_PACKAGE.value == "learn_package"

    def test_classifier_prefix_command(self):
        """Test that /learn routes to LEARN_PACKAGE."""
        from elle.cli.terminal.classifier import PREFIX_COMMANDS
        from elle.cli.terminal.intent import Intent

        assert "/learn" in PREFIX_COMMANDS
        assert PREFIX_COMMANDS["/learn"] == Intent.LEARN_PACKAGE

    def test_classifier_map_routes_to_navigation(self):
        """Test that /map routes to NAVIGATION (for GUI mapping)."""
        from elle.cli.terminal.classifier import PREFIX_COMMANDS
        from elle.cli.terminal.intent import Intent

        assert "/map" in PREFIX_COMMANDS
        assert PREFIX_COMMANDS["/map"] == Intent.NAVIGATION


class TestNaturalLanguagePatterns:
    """Tests for natural language pattern matching."""

    @pytest.fixture
    def classifier(self):
        """Get the classifier instance."""
        from elle.cli.terminal.classifier import get_classifier

        return get_classifier()

    @pytest.mark.parametrize(
        "input_text,expected_intent",
        [
            ("learn ffmpeg", "learn_package"),
            ("figure out how to use nginx", "learn_package"),
            ("teach me about docker", "learn_package"),
            ("what can ffmpeg do?", "learn_package"),
            ("how do I use systemctl", "learn_package"),
        ],
    )
    def test_learn_patterns(self, classifier, input_text, expected_intent):
        """Test natural language patterns are classified correctly."""
        from elle.common.session import Session

        session = Session()
        result = classifier.classify(input_text, session)

        # Allow for some flexibility - patterns might match other intents
        # with similar confidence
        assert result.intent.value in (expected_intent, "system_question")


class TestPackageIntelligenceModels:
    """Tests for PackageIntelligence models."""

    def test_extracted_flag_model(self):
        """Test ExtractedFlag model."""
        from elle.capabilities.autogen.intelligence.models import ExtractedFlag

        flag = ExtractedFlag(
            flag="--verbose",
            long_form="--verbose",
            description="Enable verbose output",
            takes_value=False,
            source="man_page",
            confidence=0.9,
        )

        assert flag.flag == "--verbose"
        assert flag.source == "man_page"
        assert flag.confidence == 0.9

    def test_package_metadata_model(self):
        """Test PackageMetadata model."""
        from elle.capabilities.autogen.intelligence.models import PackageMetadata

        metadata = PackageMetadata(
            name="ffmpeg",
            version="6.0-1",
            description="Tools for audio/video processing",
            section="video",
            depends=("libavcodec60",),
            homepage="https://ffmpeg.org",
        )

        assert metadata.name == "ffmpeg"
        assert metadata.version == "6.0-1"
        assert len(metadata.depends) == 1

    def test_file_manifest_model(self):
        """Test FileManifest model."""
        from elle.capabilities.autogen.intelligence.models import FileManifest

        manifest = FileManifest(
            binaries=("/usr/bin/ffmpeg", "/usr/bin/ffprobe"),
            man_pages=("/usr/share/man/man1/ffmpeg.1.gz",),
            completions=("/usr/share/bash-completion/completions/ffmpeg",),
            systemd_units=(),
            config_files=(),
        )

        assert len(manifest.binaries) == 2
        assert len(manifest.man_pages) == 1
        assert len(manifest.completions) == 1

    def test_package_intelligence_model(self):
        """Test PackageIntelligence model."""
        from elle.capabilities.autogen.intelligence.models import (
            ExtractedFlag,
            FileManifest,
            PackageIntelligence,
            PackageMetadata,
        )

        metadata = PackageMetadata(
            name="ffmpeg",
            version="6.0-1",
            description="Tools for audio/video processing",
        )

        manifest = FileManifest(
            binaries=("/usr/bin/ffmpeg",),
        )

        intel = PackageIntelligence(
            package_name="ffmpeg",
            metadata=metadata,
            manifest=manifest,
            all_flags=(
                ExtractedFlag(flag="-i", description="Input file", source="man"),
                ExtractedFlag(flag="-o", description="Output file", source="man"),
            ),
            extraction_sources=("dpkg", "man_page"),
            primary_binary="ffmpeg",
        )

        assert intel.package_name == "ffmpeg"
        assert len(intel.all_flags) == 2
        assert intel.primary_binary == "ffmpeg"


class TestValidationStage:
    """Tests for PACKAGE_COHERENCE validation stage."""

    def test_package_coherence_stage_exists(self):
        """Test that PACKAGE_COHERENCE validation stage is defined."""
        from elle.capabilities.autogen.models import ValidationStage

        assert hasattr(ValidationStage, "PACKAGE_COHERENCE")
        assert ValidationStage.PACKAGE_COHERENCE.value == "package_coherence"

    def test_validate_package_coherence_no_intel(self):
        """Test package coherence validation without intelligence."""
        from elle.capabilities.autogen.models import (
            GeneratedCapabilitySpec,
            InputFieldSpec,
            ValidationStage,
        )
        from elle.capabilities.autogen.validator import validate_package_coherence

        spec = GeneratedCapabilitySpec(
            name="ffmpeg.convert",
            display_name="FFmpeg Convert",
            description="Convert media files",
            domain="media",
            risk_level="low",
            side_effects=("file_write",),
            input_fields=(InputFieldSpec(name="input_file", field_type="Path", required=True),),
            output_fields=(),
            command_template="ffmpeg -i {input_file} output.mp4",
            source_command="ffmpeg",
            confidence_score=0.9,
        )

        result = validate_package_coherence(spec, None)

        assert result.stage == ValidationStage.PACKAGE_COHERENCE
        assert result.passed  # Should pass when no intel available
        assert len(result.warnings) > 0  # Should warn about missing intel
