"""Pydantic models for auto-generated capabilities.

Defines data structures for:
- Discovered binaries and man pages
- Parsed man page structure
- Generated capability specifications
- Validation results
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class TrustLevel(str, Enum):
    """Trust level for auto-generated capabilities."""

    CORE = "core"  # Well-known commands (systemctl, docker, etc.)
    OFFICIAL = "official"  # Low/medium risk, all validations passed
    THIRD_PARTY = "third_party"  # High risk or failed validation


class FlagType(str, Enum):
    """Type of command-line flag."""

    BOOL = "bool"  # --flag (no argument)
    STRING = "str"  # --flag=VALUE
    INT = "int"  # --count=N
    PATH = "path"  # --file=PATH
    LIST = "list"  # --flag can be repeated


# =============================================================================
# Discovery Models
# =============================================================================


class InstalledBinary(BaseModel, frozen=True):
    """Information about an installed binary."""

    name: str = Field(..., description="Binary name (e.g., 'systemctl')")
    path: str = Field(..., description="Full path to binary")
    package: str | None = Field(None, description="Package that provides this binary")
    version: str | None = Field(None, description="Package version")
    man_available: bool = Field(False, description="Whether man page exists")
    man_section: int | None = Field(None, description="Man page section (1, 8, etc.)")
    man_path: str | None = Field(None, description="Path to man page file")


class DiscoveryResult(BaseModel, frozen=True):
    """Result of binary discovery."""

    binaries: tuple[InstalledBinary, ...] = Field(
        default_factory=tuple,
        description="Discovered binaries with man pages",
    )
    total_scanned: int = Field(0, description="Total binaries scanned")
    with_man_pages: int = Field(0, description="Binaries with man pages")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# Man Page Parsing Models
# =============================================================================


class ParsedFlag(BaseModel, frozen=True):
    """A parsed command-line flag from man page."""

    name: str = Field(..., description="Flag name without dashes")
    short: str | None = Field(default=None, description="Short form (e.g., '-v')")
    long: str | None = Field(default=None, description="Long form (e.g., '--verbose')")
    flag_type: FlagType = Field(default=FlagType.BOOL, description="Inferred type")
    description: str = Field(default="", description="Flag description from man page")
    required: bool = Field(default=False, description="Whether flag is required")
    default: Any = Field(default=None, description="Default value if known")
    metavar: str | None = Field(default=None, description="Argument placeholder (e.g., 'FILE')")


class ParsedSynopsis(BaseModel, frozen=True):
    """Parsed SYNOPSIS section."""

    raw: str = Field(..., description="Raw synopsis text")
    command: str = Field(..., description="Base command name")
    subcommands: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Available subcommands",
    )
    positional_args: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Positional arguments",
    )
    has_options: bool = Field(True, description="Whether [OPTIONS] is present")


class ParsedExample(BaseModel, frozen=True):
    """A parsed example from man page."""

    command: str = Field(..., description="Example command")
    description: str = Field("", description="What the example does")


class ParsedManPage(BaseModel, frozen=True):
    """Fully parsed man page structure."""

    name: str = Field(..., description="Command name")
    section: int = Field(1, description="Man page section")
    synopsis: ParsedSynopsis | None = Field(None, description="Parsed SYNOPSIS")
    description: str = Field("", description="DESCRIPTION section summary")
    flags: tuple[ParsedFlag, ...] = Field(
        default_factory=tuple,
        description="Parsed OPTIONS/FLAGS",
    )
    examples: tuple[ParsedExample, ...] = Field(
        default_factory=tuple,
        description="Parsed EXAMPLES",
    )
    see_also: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Related commands from SEE ALSO",
    )
    raw_text: str = Field("", description="Full raw man page text")


# =============================================================================
# Capability Generation Models
# =============================================================================


class InputFieldSpec(BaseModel, frozen=True):
    """Specification for an input field."""

    name: str = Field(..., description="Field name (Python identifier)")
    field_type: str = Field(..., description="Type annotation string")
    description: str = Field("", description="Field description")
    required: bool = Field(True, description="Whether field is required")
    default: Any = Field(None, description="Default value")
    constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="Pydantic field constraints (min_length, etc.)",
    )


class OutputFieldSpec(BaseModel, frozen=True):
    """Specification for an output field."""

    name: str = Field(..., description="Field name")
    field_type: str = Field(..., description="Type annotation string")
    description: str = Field("", description="Field description")


class GeneratedCapabilitySpec(BaseModel, frozen=True):
    """LLM-generated capability specification."""

    # Identity
    name: str = Field(..., description="Capability name (e.g., 'systemctl.restart')")
    display_name: str = Field("", description="Human-readable name")
    description: str = Field("", description="What this capability does")

    # Classification
    domain: str = Field(..., description="Capability domain")
    risk_level: Literal["none", "low", "medium", "high", "critical"] = Field(
        "medium",
        description="Risk level",
    )
    side_effects: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Side effect kinds",
    )

    # Input/Output
    input_fields: tuple[InputFieldSpec, ...] = Field(
        default_factory=tuple,
        description="Input field specifications",
    )
    output_fields: tuple[OutputFieldSpec, ...] = Field(
        default_factory=tuple,
        description="Output field specifications",
    )

    # Command templates
    command_template: str = Field(
        ...,
        description="Command template with {field} placeholders",
    )
    verify_command: str | None = Field(
        None,
        description="Optional verification command template",
    )
    rollback_command: str | None = Field(
        None,
        description="Optional rollback command template",
    )

    # Source info
    source_command: str = Field(..., description="Original command name")
    source_man_section: int = Field(1, description="Man page section")

    # Confidence
    confidence_score: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="LLM confidence in this spec",
    )


# =============================================================================
# Validation Models
# =============================================================================


class ValidationStage(str, Enum):
    """Stages of capability validation."""

    FLAG_CHECK = "flag_check"  # Verify flags exist in man page
    DRY_RUN = "dry_run"  # Test dry_run() method
    SANDBOX = "sandbox"  # Run in isolated environment
    TRUST_ASSIGN = "trust_assign"  # Assign trust level
    PACKAGE_COHERENCE = "package_coherence"  # Verify against PackageIntelligence


class ValidationResult(BaseModel, frozen=True):
    """Result of a single validation stage."""

    stage: ValidationStage
    passed: bool
    errors: tuple[str, ...] = Field(default_factory=tuple)
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    details: dict[str, Any] = Field(default_factory=dict)


class FullValidationResult(BaseModel, frozen=True):
    """Complete validation result across all stages."""

    capability_name: str
    stages: tuple[ValidationResult, ...] = Field(default_factory=tuple)
    overall_passed: bool = Field(False)
    trust_level: TrustLevel = Field(TrustLevel.THIRD_PARTY)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# Storage Models
# =============================================================================


class StoredCapability(BaseModel, frozen=True):
    """A capability stored in the database."""

    id: str = Field(..., description="Unique capability ID")
    capability_name: str = Field(..., description="Capability name")
    spec_json: str = Field(..., description="JSON-serialized GeneratedCapabilitySpec")
    input_model_code: str = Field(..., description="Generated input model Python code")
    output_model_code: str = Field(..., description="Generated output model Python code")
    capability_class_code: str = Field(..., description="Generated capability class code")
    source_command: str = Field(..., description="Source command name")
    man_page_hash: str = Field(..., description="Hash of man page for versioning")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trust_level: TrustLevel = Field(TrustLevel.THIRD_PARTY)
    approved: bool = Field(False, description="Whether approved for use")
    enabled: bool = Field(True, description="Whether enabled")

    # Package versioning (copied from InstalledBinary during save)
    source_package: str | None = Field(
        default=None,
        description="dpkg package name providing the source binary",
    )
    package_version: str | None = Field(
        default=None,
        description="Package version at capability generation time",
    )
    binary_path: str | None = Field(
        default=None,
        description="Full path to source binary",
    )


# Well-known core commands that get CORE trust level
CORE_COMMANDS: frozenset[str] = frozenset(
    {
        # Systemd
        "systemctl",
        "journalctl",
        "hostnamectl",
        "timedatectl",
        "loginctl",
        # Network
        "ip",
        "ss",
        "netplan",
        "ufw",
        "iptables",
        "wg",
        "wg-quick",
        # Package management
        "apt",
        "apt-get",
        "dpkg",
        "snap",
        # Docker/containers
        "docker",
        "docker-compose",
        "podman",
        "crictl",
        # File operations
        "cp",
        "mv",
        "rm",
        "mkdir",
        "chmod",
        "chown",
        # Process management
        "ps",
        "top",
        "kill",
        "pkill",
        "nice",
        "renice",
        # Disk management
        "df",
        "du",
        "mount",
        "umount",
        "lsblk",
        "fdisk",
        "parted",
        # Text processing
        "grep",
        "sed",
        "awk",
        "sort",
        "uniq",
        # System info
        "uname",
        "lscpu",
        "lsmem",
        "free",
        "uptime",
        # Services
        "service",
        "update-rc.d",
        # Cron
        "crontab",
        # Users
        "useradd",
        "usermod",
        "userdel",
        "passwd",
        "groups",
    }
)
