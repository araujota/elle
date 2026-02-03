"""Pydantic models for extracted package intelligence.

Defines data structures for multi-source package intelligence extraction:
- Package metadata from dpkg
- File manifests
- Shell completion data
- Systemd unit information
- Aggregated intelligence

These models support the /learn command's capability generation pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

# =============================================================================
# Extracted Flag/Option Models
# =============================================================================


class ExtractedFlag(BaseModel, frozen=True):
    """A single flag/option extracted from any source.

    Flags are deduplicated across sources, preferring higher-confidence sources.
    """

    flag: str = Field(..., description="Flag string (e.g., '-v', '--verbose')")
    long_form: str | None = Field(default=None, description="Long form if flag is short")
    description: str = Field(default="", description="Description of what the flag does")
    takes_value: bool = Field(default=False, description="Whether flag requires an argument")
    value_type: str | None = Field(
        default=None,
        description="Type of value: 'file', 'int', 'string', 'path', 'dir', etc.",
    )
    source: str = Field(..., description="Source: 'man', 'completion', 'help', etc.")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in this flag's accuracy",
    )


class ExtractedSubcommand(BaseModel, frozen=True):
    """A subcommand with its own flags.

    For commands like 'git commit', 'docker run', etc.
    """

    name: str = Field(..., description="Subcommand name (e.g., 'commit', 'run')")
    description: str = Field("", description="Subcommand description")
    flags: tuple[ExtractedFlag, ...] = Field(
        default_factory=tuple,
        description="Flags specific to this subcommand",
    )
    source: str = Field(..., description="Source where this subcommand was found")


# =============================================================================
# Package Metadata Models
# =============================================================================


class PackageMetadata(BaseModel, frozen=True):
    """Core package metadata from dpkg/apt.

    This is the authoritative source for package identity and versioning.
    """

    name: str = Field(..., description="Package name (e.g., 'ffmpeg')")
    version: str = Field(..., description="Installed version")
    description: str = Field("", description="Short description from dpkg")
    section: str | None = Field(None, description="Debian section (e.g., 'utils')")
    depends: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Direct dependencies",
    )
    homepage: str | None = Field(None, description="Upstream homepage URL")


class FileManifest(BaseModel, frozen=True):
    """Files installed by the package.

    Categorized for efficient extraction targeting.
    """

    binaries: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Executable binaries in PATH (/usr/bin, /bin, etc.)",
    )
    man_pages: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Man page files (.1, .5, .8, etc.)",
    )
    completions: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Shell completion files (bash, zsh, fish)",
    )
    systemd_units: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Systemd unit files (.service, .timer, etc.)",
    )
    config_files: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Configuration files (typically in /etc)",
    )
    polkit_actions: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Polkit action files for privilege escalation",
    )


# =============================================================================
# Shell Completion Models
# =============================================================================


class ShellCompletions(BaseModel, frozen=True):
    """Extracted shell completion data.

    Shell completions are HIGH SIGNAL sources because they're maintained
    by package authors and contain structured flag/subcommand information.
    """

    flags: tuple[ExtractedFlag, ...] = Field(
        default_factory=tuple,
        description="Flags extracted from completion scripts",
    )
    subcommands: tuple[ExtractedSubcommand, ...] = Field(
        default_factory=tuple,
        description="Subcommands with their flags",
    )
    source_file: str | None = Field(
        None,
        description="Path to the completion file parsed",
    )
    shell_type: Literal["bash", "zsh", "fish"] | None = Field(
        None,
        description="Type of shell completion",
    )


# =============================================================================
# Systemd Unit Models
# =============================================================================


class SystemdUnitInfo(BaseModel, frozen=True):
    """Information extracted from systemd unit files.

    Useful for service packages to understand runtime behavior.
    """

    unit_name: str = Field(..., description="Unit filename (e.g., 'nginx.service')")
    unit_type: str = Field(..., description="Type: service, socket, timer, etc.")
    description: str | None = Field(None, description="Unit description")
    exec_start: str | None = Field(
        None,
        description="ExecStart command (reveals common flags)",
    )
    environment: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Environment variables set in the unit",
    )
    requires: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Required units",
    )


# =============================================================================
# Aggregated Intelligence Model
# =============================================================================


class PackageIntelligence(BaseModel, frozen=True):
    """Aggregated intelligence from all sources.

    This is the final output of the intelligence gathering pipeline,
    ready to be formatted for LLM consumption.
    """

    package_name: str = Field(..., description="Package name")
    metadata: PackageMetadata = Field(..., description="Core package metadata")
    manifest: FileManifest = Field(..., description="Installed files by category")

    # Aggregated flag data (deduplicated across sources)
    all_flags: tuple[ExtractedFlag, ...] = Field(
        default_factory=tuple,
        description="All unique flags from all sources",
    )
    subcommands: tuple[ExtractedSubcommand, ...] = Field(
        default_factory=tuple,
        description="All subcommands with their flags",
    )

    # Source-specific data
    completions: ShellCompletions | None = Field(
        None,
        description="Shell completion data (if available)",
    )
    systemd_units: tuple[SystemdUnitInfo, ...] = Field(
        default_factory=tuple,
        description="Systemd unit information",
    )

    # Extraction metadata
    extraction_sources: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Sources that contributed data",
    )
    extraction_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When extraction occurred",
    )
    token_estimate: int = Field(
        0,
        description="Estimated token count for LLM context",
    )

    # Primary binary (for commands with multiple binaries)
    primary_binary: str | None = Field(
        None,
        description="Main binary to generate capabilities for",
    )


# =============================================================================
# Extractor Protocol
# =============================================================================


class ExtractionResult(BaseModel, frozen=True):
    """Result from a single extractor.

    Each extractor returns this, which the aggregator merges.
    """

    source_name: str = Field(..., description="Name of the extractor")
    success: bool = Field(default=True, description="Whether extraction succeeded")
    error: str | None = Field(default=None, description="Error message if failed")

    # Data from extraction
    flags: tuple[ExtractedFlag, ...] = Field(default_factory=tuple)
    subcommands: tuple[ExtractedSubcommand, ...] = Field(default_factory=tuple)
    completions: ShellCompletions | None = Field(default=None)
    systemd_units: tuple[SystemdUnitInfo, ...] = Field(default_factory=tuple)
    metadata: PackageMetadata | None = Field(default=None)
    manifest: FileManifest | None = Field(default=None)
