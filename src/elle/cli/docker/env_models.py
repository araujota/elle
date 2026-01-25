"""Docker Environment Variable Models.

Core data models for Docker environment variable handling and prompting.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# Type alias for environment variable source
EnvVarSource = Literal["profile", "image", "compose", "llm", "user"]


class EnvVarSpec(BaseModel):
    """Specification for a Docker environment variable.

    Describes a single environment variable that a Docker image
    may require or accept, including metadata for prompting.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Environment variable name (e.g., POSTGRES_PASSWORD)")
    required: bool = Field(
        default=False,
        description="Whether the variable is required for the container to start",
    )
    default: str | None = Field(
        default=None,
        description="Default value if not provided",
    )
    description: str = Field(
        default="",
        description="Human-readable description of the variable's purpose",
    )
    sensitive: bool = Field(
        default=False,
        description="Whether the value should be masked (passwords, secrets, API keys)",
    )
    example: str | None = Field(
        default=None,
        description="Example value to show as placeholder",
    )
    source: EnvVarSource = Field(
        default="profile",
        description="Where this spec was obtained from",
    )

    def display_name(self) -> str:
        """Get a display-friendly name for UI."""
        parts = [self.name]
        if self.required:
            parts.append("(required)")
        if self.sensitive:
            parts.append("(sensitive)")
        return " ".join(parts)


class EnvVarValue(BaseModel):
    """A collected environment variable value.

    Represents a single variable name-value pair that has been
    entered by the user or retrieved from storage.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Environment variable name")
    value: str = Field(description="Environment variable value")
    from_saved: bool = Field(
        default=False,
        description="Whether the value was retrieved from saved storage",
    )
    sensitive: bool = Field(
        default=False,
        description="Whether this is a sensitive value (for display masking)",
    )


class EnvVarPromptResult(BaseModel):
    """Result of an environment variable prompting session.

    Contains all collected values and metadata about the prompting outcome.
    """

    model_config = ConfigDict(frozen=True)

    values: tuple[EnvVarValue, ...] = Field(
        default_factory=tuple,
        description="Collected environment variable values",
    )
    skipped: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Variable names that were skipped by user",
    )
    cancelled: bool = Field(
        default=False,
        description="Whether the user cancelled the prompting session",
    )
    image: str = Field(
        default="",
        description="Docker image name this result applies to",
    )

    def to_env_dict(self) -> dict[str, str]:
        """Convert to a dict suitable for docker run -e flags."""
        return {v.name: v.value for v in self.values}

    def to_env_tuple(self) -> tuple[tuple[str, str], ...]:
        """Convert to tuple of (name, value) pairs."""
        return tuple((v.name, v.value) for v in self.values)

    @property
    def from_saved_count(self) -> int:
        """Count of values retrieved from saved storage."""
        return sum(1 for v in self.values if v.from_saved)


class SavedEnvVar(BaseModel):
    """A saved environment variable value from the store.

    Represents persisted env var data for reuse across sessions.
    """

    model_config = ConfigDict(frozen=True)

    image_family: str = Field(
        description="Image family name (e.g., 'postgres' without tag)",
    )
    name: str = Field(description="Environment variable name")
    value: str = Field(description="Stored value")
    sensitive: bool = Field(
        default=False,
        description="Whether this is a sensitive value",
    )
    updated_at: datetime = Field(description="When the value was last updated")


class ImageEnvProfile(BaseModel):
    """Profile of environment variables for a Docker image.

    Contains all known env vars for an image family, used for
    prompting users when they run containers.
    """

    model_config = ConfigDict(frozen=True)

    image_family: str = Field(
        description="Image family name (e.g., 'postgres', 'mysql')",
    )
    display_name: str = Field(
        description="Human-friendly display name",
    )
    description: str = Field(
        default="",
        description="Brief description of the image",
    )
    env_vars: tuple[EnvVarSpec, ...] = Field(
        default_factory=tuple,
        description="Known environment variables for this image",
    )
    documentation_url: str | None = Field(
        default=None,
        description="URL to official documentation",
    )

    @property
    def required_vars(self) -> tuple[EnvVarSpec, ...]:
        """Get only required environment variables."""
        return tuple(v for v in self.env_vars if v.required)

    @property
    def optional_vars(self) -> tuple[EnvVarSpec, ...]:
        """Get only optional environment variables."""
        return tuple(v for v in self.env_vars if not v.required)

    def has_required_vars(self) -> bool:
        """Check if this profile has any required variables."""
        return any(v.required for v in self.env_vars)
