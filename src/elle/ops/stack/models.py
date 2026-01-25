"""Stack orchestration models.

Defines data structures for deployable stacks, their components,
guarantees, and execution results.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Stack Definition Models
# =============================================================================


StackLayer = Literal["package", "config", "service", "verify"]
GuaranteeAction = Literal["warn", "fail", "rollback"]


class StackVariable(BaseModel):
    """User-configurable variable for a stack.

    Variables allow customization of stack deployment
    without modifying the recipe itself.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Variable name (e.g., 'db_password')")
    description: str = Field(description="Human-readable description")
    default: str | None = Field(default=None, description="Default value")
    required: bool = Field(default=False, description="Must be provided")
    secret: bool = Field(default=False, description="Don't log this value")
    validator: str | None = Field(
        default=None,
        description="Regex pattern for validation",
    )


class StackGuarantee(BaseModel):
    """A verification check that ELLE guarantees.

    Guarantees are run after deployment to verify the stack
    is functioning correctly.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Guarantee name (e.g., 'localhost_only')")
    description: str = Field(
        default="",
        description="Human-readable description",
    )
    check_command: str = Field(description="Command to verify guarantee")
    expected_pattern: str | None = Field(
        default=None,
        description="Regex pattern to match in output",
    )
    failure_action: GuaranteeAction = Field(
        default="warn",
        description="Action on failure: warn, fail, or rollback",
    )
    remediation_hint: str = Field(
        default="",
        description="What to try if check fails",
    )


class StackStep(BaseModel):
    """A single step within a stack phase."""

    model_config = ConfigDict(frozen=True)

    command: str = Field(description="Command to execute")
    description: str = Field(default="", description="What this step does")
    can_fail: bool = Field(default=False, description="Acceptable to fail")
    timeout_sec: int = Field(default=300, description="Step timeout")


class StackPhase(BaseModel):
    """A phase in stack deployment.

    Phases group related steps and control rollback behavior.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Phase name")
    layer: StackLayer = Field(description="Deployment layer")
    steps: tuple[StackStep, ...] = Field(
        default_factory=tuple,
        description="Steps in this phase",
    )
    rollback_on_failure: bool = Field(
        default=True,
        description="Trigger rollback if phase fails",
    )


class StackRecipe(BaseModel):
    """Definition of a deployable stack.

    Recipes are templates that define what packages to install,
    how to configure them, and what guarantees to verify.
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    name: str = Field(description="Recipe name (e.g., 'postgres', 'lemp')")
    display_name: str = Field(description="Human-readable name")
    description: str = Field(description="What this stack provides")
    category: str = Field(
        default="general",
        description="Category: web, data, observability, etc.",
    )

    # Components
    packages: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Apt packages to install",
    )
    services: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Systemd services to manage",
    )
    config_templates: dict[str, str] = Field(
        default_factory=dict,
        description="Config path -> template content",
    )

    # Guarantees
    guarantees: tuple[StackGuarantee, ...] = Field(
        default_factory=tuple,
        description="Verification checks after deployment",
    )

    # Customization
    variables: tuple[StackVariable, ...] = Field(
        default_factory=tuple,
        description="User-configurable values",
    )
    presets: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description="Named preset configurations (e.g., 'dev', 'prod')",
    )

    # Requirements
    min_ram_mb: int = Field(default=0, description="Minimum RAM in MB")
    min_disk_gb: int = Field(default=0, description="Minimum disk in GB")
    ports_required: tuple[int, ...] = Field(
        default_factory=tuple,
        description="Ports that must be available",
    )

    # Metadata
    version: str = Field(default="1.0.0", description="Recipe version")
    tags: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Tags for categorization",
    )


# =============================================================================
# Deployment Models
# =============================================================================


DeploymentStatus = Literal[
    "pending",
    "executing",
    "completed",
    "failed",
    "rolled_back",
]


class StackDeployment(BaseModel):
    """Complete stack deployment specification.

    Represents a specific deployment of a recipe with
    user-provided variable values.
    """

    model_config = ConfigDict(frozen=True)

    deployment_id: str = Field(description="Unique deployment ID")
    recipe: StackRecipe = Field(description="Recipe being deployed")
    variables: dict[str, str] = Field(
        default_factory=dict,
        description="User-provided variable values",
    )
    preset: str | None = Field(
        default=None,
        description="Named preset to apply",
    )
    phases: tuple[StackPhase, ...] = Field(
        default_factory=tuple,
        description="Deployment phases",
    )
    status: DeploymentStatus = Field(default="pending")

    # Cross-layer coordination
    requires_reboot: bool = Field(default=False)
    config_changes: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Config files that will be changed",
    )
    services_affected: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Services that will be affected",
    )

    # Timing
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)

    def with_status(self, status: DeploymentStatus) -> "StackDeployment":
        """Return a copy with updated status."""
        return StackDeployment(
            deployment_id=self.deployment_id,
            recipe=self.recipe,
            variables=self.variables,
            preset=self.preset,
            phases=self.phases,
            status=status,
            requires_reboot=self.requires_reboot,
            config_changes=self.config_changes,
            services_affected=self.services_affected,
            started_at=self.started_at,
            completed_at=self.completed_at if status != "completed" else datetime.utcnow(),
        )


# =============================================================================
# Result Models
# =============================================================================


class StepResult(BaseModel):
    """Result of executing a single step."""

    model_config = ConfigDict(frozen=True)

    step: StackStep = Field(description="The step that was executed")
    success: bool = Field(description="Whether step succeeded")
    exit_code: int | None = Field(default=None)
    stdout: str = Field(default="")
    stderr: str = Field(default="")
    duration_ms: int = Field(default=0)
    executed_at: datetime = Field(default_factory=datetime.utcnow)


class PhaseResult(BaseModel):
    """Result of executing a deployment phase."""

    model_config = ConfigDict(frozen=True)

    phase: StackPhase = Field(description="The phase that was executed")
    success: bool = Field(description="Whether phase succeeded overall")
    step_results: tuple[StepResult, ...] = Field(
        default_factory=tuple,
        description="Results for each step",
    )
    backup_path: str | None = Field(
        default=None,
        description="Backup path for rollback (config phase)",
    )
    duration_ms: int = Field(default=0)


class GuaranteeResult(BaseModel):
    """Result of verifying a guarantee."""

    model_config = ConfigDict(frozen=True)

    guarantee: StackGuarantee = Field(description="The guarantee checked")
    passed: bool = Field(description="Whether guarantee passed")
    actual_output: str = Field(default="", description="Command output")
    match_found: bool = Field(
        default=False,
        description="Whether pattern matched (if applicable)",
    )
    error: str | None = Field(default=None)


class RollbackResult(BaseModel):
    """Result of rollback operation."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether rollback succeeded")
    phases_rolled_back: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Names of phases that were rolled back",
    )
    errors: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Errors encountered during rollback",
    )


class StackResult(BaseModel):
    """Complete result of stack deployment.

    Captures all phase results, guarantee verifications,
    and any rollback that occurred.
    """

    model_config = ConfigDict(frozen=True)

    deployment: StackDeployment = Field(description="The deployment spec")
    phase_results: tuple[PhaseResult, ...] = Field(
        default_factory=tuple,
        description="Results for each phase",
    )
    guarantees_verified: tuple[GuaranteeResult, ...] = Field(
        default_factory=tuple,
        description="Guarantee verification results",
    )
    rollback_performed: bool = Field(
        default=False,
        description="Whether rollback was triggered",
    )
    rollback_result: RollbackResult | None = Field(
        default=None,
        description="Rollback result if performed",
    )
    incident_id: str | None = Field(
        default=None,
        description="Associated incident ID for tracking",
    )
    credentials_generated: dict[str, str] = Field(
        default_factory=dict,
        description="Generated credentials to surface to user",
    )

    # Overall status
    success: bool = Field(description="Whether deployment succeeded overall")
    error_message: str | None = Field(default=None)

    # Timing
    duration_ms: int = Field(default=0)
    completed_at: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# Prerequisite Check Models
# =============================================================================


class PrerequisiteCheck(BaseModel):
    """Result of checking deployment prerequisites."""

    model_config = ConfigDict(frozen=True)

    passed: bool = Field(description="Whether all prerequisites passed")
    warnings: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Warning messages",
    )
    errors: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Error messages (blocking)",
    )

    # Checks performed
    ram_sufficient: bool = Field(default=True)
    disk_sufficient: bool = Field(default=True)
    ports_available: bool = Field(default=True)
    packages_available: bool = Field(default=True)


# =============================================================================
# Recipe Matching
# =============================================================================


class RecipeMatch(BaseModel):
    """Result of matching a request to a recipe."""

    model_config = ConfigDict(frozen=True)

    recipe: StackRecipe = Field(description="Matched recipe")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Match confidence",
    )
    matched_keywords: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Keywords that matched",
    )
