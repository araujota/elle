"""Pydantic models for LLM-driven configuration generation.

Defines data structures for configuration generation requests,
operations, results, and validation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Type Aliases
# =============================================================================

ConfigOpMode = Literal["edit", "create", "patch"]
ConfigFileType = Literal["augeas", "yaml", "json", "toml", "text", "markdown", "systemd"]
ConfigDomain = Literal[
    "network",
    "filesystem",
    "ssh",
    "firewall",
    "service",
    "cron",
    "package",
    "hosts",
    "docker",
    "wireguard",
    "general",
]


# =============================================================================
# Request Models
# =============================================================================


class ConfigGenRequest(BaseModel):
    """Request for configuration generation.

    Encapsulates a natural language request to generate or edit
    configuration files.
    """

    model_config = ConfigDict(frozen=True)

    request: str = Field(description="Natural language request describing the configuration change")
    mode: ConfigOpMode = Field(
        default="edit",
        description="Operation mode: edit (modify existing), create (new file), or patch",
    )
    target_path: Path | None = Field(
        default=None,
        description="Target configuration file path",
    )
    domain: ConfigDomain | None = Field(
        default=None,
        description="Configuration domain (auto-detected if None)",
    )
    file_type: ConfigFileType | None = Field(
        default=None,
        description="File type (auto-detected if None)",
    )
    entities: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Extracted entities (IPs, interfaces, service names)",
    )
    current_content: str | None = Field(
        default=None,
        description="Current file content (for edit mode)",
    )


# =============================================================================
# Operation Models
# =============================================================================


class ConfigOp(BaseModel):
    """A single configuration operation.

    Represents an atomic change to a configuration file,
    using Augeas paths or dot-notation for structured files.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["set", "rm", "ins", "mv", "append", "replace"] = Field(description="Operation type")
    path: str = Field(description="Augeas path or dot-notation path to the setting")
    value: str | None = Field(
        default=None,
        description="New value (for set, ins, append operations)",
    )
    position: str | None = Field(
        default=None,
        description="Position for ins operations (before/after path)",
    )
    explanation: str = Field(
        default="",
        description="Human-readable explanation of this operation",
    )


# =============================================================================
# Result Models
# =============================================================================


class ConfigGenResult(BaseModel):
    """Result of configuration generation.

    Contains the generated operations or content, along with
    metadata for validation and rollback.
    """

    model_config = ConfigDict(frozen=True)

    title: str = Field(description="Short title describing the configuration change")
    explanation: str = Field(description="Detailed explanation of what the configuration does")
    operations: tuple[ConfigOp, ...] = Field(
        default_factory=tuple,
        description="Configuration operations (for edit/patch mode)",
    )
    generated_content: str | None = Field(
        default=None,
        description="Generated file content (for create mode)",
    )
    target_path: str = Field(description="Target file path for the configuration")
    file_type: ConfigFileType = Field(
        default="text",
        description="Type of configuration file",
    )
    domain: ConfigDomain = Field(
        default="general",
        description="Configuration domain",
    )
    requires_privilege: bool = Field(
        default=False,
        description="Whether the operation requires elevated privileges",
    )
    validation_command: str | None = Field(
        default=None,
        description="Command to validate the configuration after applying",
    )
    risks: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Potential risks or side effects",
    )
    rollback_hint: str | None = Field(
        default=None,
        description="Hint for rolling back this change",
    )
    grounded_in: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Man pages or documentation used to generate this config",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the result was generated",
    )


# =============================================================================
# Validation Models
# =============================================================================


class ValidationIssue(BaseModel):
    """A validation issue found in generated configuration."""

    model_config = ConfigDict(frozen=True)

    severity: Literal["error", "warning", "info"] = Field(description="Issue severity")
    message: str = Field(description="Description of the issue")
    path: str | None = Field(
        default=None,
        description="Path in the configuration where the issue was found",
    )
    suggestion: str | None = Field(
        default=None,
        description="Suggested fix for the issue",
    )


class ConfigGenValidation(BaseModel):
    """Validation result for generated configuration."""

    model_config = ConfigDict(frozen=True)

    valid: bool = Field(description="Whether the configuration is valid")
    issues: tuple[ValidationIssue, ...] = Field(
        default_factory=tuple,
        description="Validation issues found",
    )
    syntax_valid: bool = Field(
        default=True,
        description="Whether the syntax is valid",
    )
    path_valid: bool = Field(
        default=True,
        description="Whether the target path is safe",
    )
    values_valid: bool = Field(
        default=True,
        description="Whether the values are safe",
    )

    @property
    def error_count(self) -> int:
        """Count of error-level issues."""
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        """Count of warning-level issues."""
        return sum(1 for i in self.issues if i.severity == "warning")


# =============================================================================
# Context Models
# =============================================================================


class ConfigGenContext(BaseModel):
    """Context for configuration generation.

    Contains all information needed to generate configuration,
    including request details, documentation, and prior art.
    """

    model_config = ConfigDict(frozen=True)

    request: ConfigGenRequest = Field(description="The original request")
    current_content: str | None = Field(
        default=None,
        description="Current file content (for edit mode)",
    )
    man_docs: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Relevant man page snippets for grounding",
    )
    prior_art: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Similar past incidents and their solutions",
    )
    schema_hint: dict[str, Any] | None = Field(
        default=None,
        description="Schema hint for structured files",
    )
    detected_domain: ConfigDomain = Field(
        default="general",
        description="Auto-detected configuration domain",
    )
    detected_file_type: ConfigFileType = Field(
        default="text",
        description="Auto-detected file type",
    )


# =============================================================================
# Outcome Models
# =============================================================================


class ConfigGenOutcome(BaseModel):
    """Complete outcome of a configuration generation workflow.

    Combines the generation result with validation and application status.
    """

    model_config = ConfigDict(frozen=True)

    request: ConfigGenRequest = Field(description="The original request")
    result: ConfigGenResult | None = Field(
        default=None,
        description="Generation result (None if generation failed)",
    )
    validation: ConfigGenValidation | None = Field(
        default=None,
        description="Validation result",
    )
    applied: bool = Field(
        default=False,
        description="Whether the configuration was applied",
    )
    apply_error: str | None = Field(
        default=None,
        description="Error message if apply failed",
    )
    backup_path: str | None = Field(
        default=None,
        description="Path to backup of original configuration",
    )
    generation_error: str | None = Field(
        default=None,
        description="Error message if generation failed",
    )

    @property
    def success(self) -> bool:
        """Whether generation and validation succeeded."""
        return (
            self.result is not None
            and self.validation is not None
            and self.validation.valid
            and self.generation_error is None
        )


# =============================================================================
# Exceptions
# =============================================================================


class ConfigGenError(Exception):
    """Base exception for configuration generation errors."""

    pass


class ConfigGenValidationError(ConfigGenError):
    """Configuration validation failed."""

    def __init__(self, message: str, validation: ConfigGenValidation) -> None:
        super().__init__(message)
        self.validation = validation


class ConfigGenLLMError(ConfigGenError):
    """LLM generation failed."""

    def __init__(self, message: str, raw_response: str | None = None) -> None:
        super().__init__(message)
        self.raw_response = raw_response
