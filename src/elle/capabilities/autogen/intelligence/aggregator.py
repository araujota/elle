"""Intelligence aggregator for package capability generation.

Combines results from all extractors into a unified PackageIntelligence
model, handling deduplication, confidence weighting, and token budget
enforcement.

Usage:
    aggregator = IntelligenceAggregator()
    intel = await aggregator.gather("ffmpeg")
    context = aggregator.to_llm_context(intel)
"""

from __future__ import annotations

import logging
from datetime import datetime

from elle.capabilities.autogen.intelligence.extractors import get_extractors
from elle.capabilities.autogen.intelligence.models import (
    ExtractedFlag,
    ExtractedSubcommand,
    ExtractionResult,
    FileManifest,
    PackageIntelligence,
    PackageMetadata,
    ShellCompletions,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Token Estimation
# =============================================================================


def estimate_tokens(text: str) -> int:
    """Estimate token count for a string.

    Uses rough approximation: ~4 characters per token.
    """
    return len(text) // 4


# =============================================================================
# Intelligence Aggregator
# =============================================================================


class IntelligenceAggregator:
    """Combines intelligence from multiple sources.

    Runs extractors in priority order, deduplicates flags,
    and enforces token budget for LLM consumption.
    """

    # Target token budget for LLM context
    TOKEN_BUDGET_MIN = 1700
    TOKEN_BUDGET_MAX = 3400

    def __init__(self, token_budget: int | None = None) -> None:
        """Initialize the aggregator.

        Args:
            token_budget: Maximum tokens for output. Defaults to TOKEN_BUDGET_MAX.
        """
        self.token_budget = token_budget or self.TOKEN_BUDGET_MAX

    async def gather(self, package_name: str) -> PackageIntelligence:
        """Gather intelligence from all sources.

        Runs extractors in priority order. Failed extractors don't block others.

        Args:
            package_name: Package name to gather intelligence for.

        Returns:
            PackageIntelligence with aggregated data.
        """
        extractors = get_extractors()
        results: list[ExtractionResult] = []

        # Run extractors in priority order
        manifest: FileManifest | None = None
        metadata: PackageMetadata | None = None

        for extractor in extractors:
            try:
                logger.debug(f"Running {extractor.name} extractor...")
                result = extractor.extract(package_name, manifest)
                results.append(result)

                # Update manifest and metadata for subsequent extractors
                if result.success:
                    if result.manifest:
                        manifest = result.manifest
                    if result.metadata:
                        metadata = result.metadata

            except Exception as e:
                logger.warning(f"Extractor {extractor.name} failed: {e}")
                results.append(
                    ExtractionResult(
                        source_name=extractor.name,
                        success=False,
                        error=str(e),
                    )
                )

        # Aggregate results
        return self._aggregate(package_name, results, manifest, metadata)

    def _aggregate(
        self,
        package_name: str,
        results: list[ExtractionResult],
        manifest: FileManifest | None,
        metadata: PackageMetadata | None,
    ) -> PackageIntelligence:
        """Aggregate extraction results into PackageIntelligence."""
        # Collect successful sources
        sources: list[str] = [r.source_name for r in results if r.success]

        # Deduplicate flags
        all_flags = self._deduplicate_flags(results)

        # Collect subcommands
        subcommands: list[ExtractedSubcommand] = []
        for result in results:
            if result.success and result.subcommands:
                subcommands.extend(result.subcommands)

        # Get completions (prefer bash, then zsh)
        completions: ShellCompletions | None = None
        for result in results:
            if result.success and result.completions:
                if result.completions.shell_type == "bash":
                    completions = result.completions
                    break
                elif completions is None:
                    completions = result.completions

        # Collect systemd units
        systemd_units = []
        for result in results:
            if result.success and result.systemd_units:
                systemd_units.extend(result.systemd_units)

        # Ensure we have metadata and manifest
        if metadata is None:
            metadata = PackageMetadata(
                name=package_name,
                version="unknown",
                description="",
            )

        if manifest is None:
            manifest = FileManifest()

        # Determine primary binary
        primary_binary = None
        if manifest.binaries:
            # Prefer binary matching package name
            for binary_path in manifest.binaries:
                from pathlib import Path

                name = Path(binary_path).name
                if name == package_name:
                    primary_binary = name
                    break
            if primary_binary is None:
                from pathlib import Path

                primary_binary = Path(manifest.binaries[0]).name

        # Estimate tokens
        intel = PackageIntelligence(
            package_name=package_name,
            metadata=metadata,
            manifest=manifest,
            all_flags=tuple(all_flags),
            subcommands=tuple(subcommands),
            completions=completions,
            systemd_units=tuple(systemd_units),
            extraction_sources=tuple(sources),
            extraction_timestamp=datetime.utcnow(),
            primary_binary=primary_binary,
            token_estimate=0,  # Calculate below
        )

        # Calculate token estimate
        context = self.to_llm_context(intel)
        token_estimate = estimate_tokens(context)

        # Recreate with accurate token estimate
        return PackageIntelligence(
            package_name=intel.package_name,
            metadata=intel.metadata,
            manifest=intel.manifest,
            all_flags=intel.all_flags,
            subcommands=intel.subcommands,
            completions=intel.completions,
            systemd_units=intel.systemd_units,
            extraction_sources=intel.extraction_sources,
            extraction_timestamp=intel.extraction_timestamp,
            primary_binary=intel.primary_binary,
            token_estimate=token_estimate,
        )

    def _deduplicate_flags(
        self,
        results: list[ExtractionResult],
    ) -> list[ExtractedFlag]:
        """Deduplicate flags across sources, preferring higher confidence.

        Args:
            results: Extraction results to deduplicate.

        Returns:
            List of unique flags, highest confidence per flag retained.
        """
        flag_map: dict[str, ExtractedFlag] = {}

        for result in results:
            if not result.success or not result.flags:
                continue

            for flag in result.flags:
                key = flag.flag.lower()  # Normalize

                if key not in flag_map:
                    flag_map[key] = flag
                elif flag.confidence > flag_map[key].confidence:
                    # Prefer higher confidence
                    flag_map[key] = flag
                elif flag.confidence == flag_map[key].confidence and flag.description and not flag_map[key].description:
                    # Same confidence but this one has description
                    flag_map[key] = flag

        # Sort by flag name
        return sorted(flag_map.values(), key=lambda f: f.flag)

    def to_llm_context(
        self,
        intel: PackageIntelligence,
        max_flags: int = 50,
    ) -> str:
        """Convert PackageIntelligence to structured text for LLM.

        Formats the intelligence as a structured prompt context,
        respecting the token budget.

        Args:
            intel: Package intelligence to format.
            max_flags: Maximum number of flags to include.

        Returns:
            Formatted string for LLM context.
        """
        lines: list[str] = []

        # Header
        lines.append(f"# Package: {intel.package_name}")
        lines.append("")

        # Metadata
        lines.append("## Metadata")
        lines.append(f"- Name: {intel.metadata.name}")
        lines.append(f"- Version: {intel.metadata.version}")
        if intel.metadata.description:
            lines.append(f"- Description: {intel.metadata.description}")
        if intel.metadata.section:
            lines.append(f"- Section: {intel.metadata.section}")
        if intel.metadata.homepage:
            lines.append(f"- Homepage: {intel.metadata.homepage}")
        lines.append("")

        # Primary binary
        if intel.primary_binary:
            lines.append(f"## Primary Binary: {intel.primary_binary}")
            lines.append("")

        # Binaries
        if intel.manifest.binaries:
            lines.append("## Binaries")
            for binary in intel.manifest.binaries[:10]:
                from pathlib import Path

                lines.append(f"- {Path(binary).name}")
            if len(intel.manifest.binaries) > 10:
                lines.append(f"- ... and {len(intel.manifest.binaries) - 10} more")
            lines.append("")

        # Subcommands
        if intel.subcommands:
            lines.append("## Subcommands")
            for sub in intel.subcommands[:20]:
                desc = f": {sub.description}" if sub.description else ""
                lines.append(f"- {sub.name}{desc}")
            if len(intel.subcommands) > 20:
                lines.append(f"- ... and {len(intel.subcommands) - 20} more")
            lines.append("")

        # Flags (sorted by confidence, limited)
        if intel.all_flags:
            # Sort by confidence descending
            sorted_flags = sorted(
                intel.all_flags,
                key=lambda f: (-f.confidence, f.flag),
            )[:max_flags]

            lines.append("## Flags/Options")
            for flag in sorted_flags:
                parts = [f"- `{flag.flag}`"]
                if flag.takes_value and flag.value_type:
                    parts.append(f"<{flag.value_type}>")
                if flag.description:
                    # Truncate long descriptions
                    desc = flag.description[:100]
                    if len(flag.description) > 100:
                        desc += "..."
                    parts.append(f": {desc}")
                parts.append(f"({flag.source}, {flag.confidence:.0%})")
                lines.append(" ".join(parts))
            if len(intel.all_flags) > max_flags:
                lines.append(f"- ... and {len(intel.all_flags) - max_flags} more flags")
            lines.append("")

        # Systemd units
        if intel.systemd_units:
            lines.append("## Systemd Units")
            for unit in intel.systemd_units:
                desc = f": {unit.description}" if unit.description else ""
                lines.append(f"- {unit.unit_name}{desc}")
                if unit.exec_start:
                    lines.append(f"  ExecStart: {unit.exec_start[:80]}")
            lines.append("")

        # Config files
        if intel.manifest.config_files:
            lines.append("## Config Files")
            for cfg in intel.manifest.config_files[:5]:
                lines.append(f"- {cfg}")
            if len(intel.manifest.config_files) > 5:
                lines.append(f"- ... and {len(intel.manifest.config_files) - 5} more")
            lines.append("")

        # Extraction sources
        lines.append(f"## Sources: {', '.join(intel.extraction_sources)}")

        return "\n".join(lines)


# =============================================================================
# Module-level singleton
# =============================================================================


_aggregator: IntelligenceAggregator | None = None


def get_aggregator() -> IntelligenceAggregator:
    """Get the shared aggregator instance.

    Returns:
        IntelligenceAggregator singleton.
    """
    global _aggregator
    if _aggregator is None:
        _aggregator = IntelligenceAggregator()
    return _aggregator
