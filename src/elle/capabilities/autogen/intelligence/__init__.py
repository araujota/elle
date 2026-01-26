"""Package intelligence extraction system.

Multi-source intelligence extraction for package capability generation.
Supports:
- dpkg metadata
- Shell completions (bash, zsh)
- Man pages
- Systemd units
- --help output fallback

Usage:
    from elle.capabilities.autogen.intelligence import (
        IntelligenceAggregator,
        PackageIntelligence,
        get_aggregator,
    )

    aggregator = get_aggregator()
    intel = await aggregator.gather("ffmpeg")
    context = aggregator.to_llm_context(intel)
"""

from elle.capabilities.autogen.intelligence.aggregator import (
    IntelligenceAggregator,
    get_aggregator,
)
from elle.capabilities.autogen.intelligence.extractors import (
    ALL_EXTRACTORS,
    BaseExtractor,
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
    ExtractedSubcommand,
    ExtractionResult,
    FileManifest,
    PackageIntelligence,
    PackageMetadata,
    ShellCompletions,
    SystemdUnitInfo,
)

__all__ = [
    # Aggregator
    "IntelligenceAggregator",
    "get_aggregator",
    # Extractors
    "ALL_EXTRACTORS",
    "BaseExtractor",
    "BashCompletionExtractor",
    "DpkgMetadataExtractor",
    "FileManifestExtractor",
    "HelpOutputExtractor",
    "ManPageExtractor",
    "SystemdUnitExtractor",
    "ZshCompletionExtractor",
    "get_extractors",
    # Models
    "ExtractionResult",
    "ExtractedFlag",
    "ExtractedSubcommand",
    "FileManifest",
    "PackageIntelligence",
    "PackageMetadata",
    "ShellCompletions",
    "SystemdUnitInfo",
]
