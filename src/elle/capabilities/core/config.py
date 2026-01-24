"""Configuration editing capabilities.

Provides typed, policy-governed operations for config file editing:
- config.edit - Edit a config file using Augeas
- config.preview - Preview config changes without applying

Wraps the existing Augeas controller for config editing.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from elle.capabilities.models import (
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    RiskLevel,
    RollbackResult,
    SideEffect,
    VerificationResult,
)
from elle.capabilities.protocol import BaseCapability

logger = logging.getLogger(__name__)


# =============================================================================
# Input/Output Models
# =============================================================================


class ConfigOperation(BaseModel):
    """A single configuration operation."""

    model_config = ConfigDict(frozen=True)

    kind: str = Field(
        description="Operation type: 'set' or 'rm'",
    )
    path: str = Field(
        description="Augeas path within the file",
    )
    value: str | None = Field(
        default=None,
        description="Value to set (for 'set' operations)",
    )


class ConfigEditInput(BaseModel):
    """Input for config edit operation."""

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(
        description="Path to the config file",
    )
    operations: tuple[ConfigOperation, ...] = Field(
        description="Operations to perform",
    )
    lens: str | None = Field(
        default=None,
        description="Augeas lens to use (auto-detected if None)",
    )
    description: str = Field(
        default="",
        description="Description of the changes",
    )
    skip_validation: bool = Field(
        default=False,
        description="Skip post-edit validation",
    )
    incident_id: str | None = Field(
        default=None,
        description="Incident ID for recording",
    )


class ConfigEditOutput(BaseModel):
    """Output from config edit operation."""

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(description="Path that was edited")
    changes: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Changes that were made",
    )
    diff: str = Field(
        default="",
        description="Unified diff of changes",
    )
    backup_path: str | None = Field(
        default=None,
        description="Path to backup if created",
    )
    validation_passed: bool = Field(
        default=True,
        description="Whether validation passed",
    )
    validation_output: str = Field(
        default="",
        description="Validation command output",
    )


class ConfigPreviewInput(BaseModel):
    """Input for config preview operation."""

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(
        description="Path to the config file",
    )
    operations: tuple[ConfigOperation, ...] = Field(
        description="Operations to preview",
    )
    lens: str | None = Field(
        default=None,
        description="Augeas lens to use",
    )


class ConfigPreviewOutput(BaseModel):
    """Output from config preview operation."""

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(description="Path that would be edited")
    diff: str = Field(description="Unified diff of proposed changes")
    diff_colored: str = Field(
        default="",
        description="Colored diff for display",
    )
    changes: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Proposed changes",
    )
    requires_privilege: bool = Field(
        default=False,
        description="Whether edit requires privileges",
    )


# =============================================================================
# Config Edit Capability
# =============================================================================


class ConfigEditCapability(BaseCapability):
    """Edit a configuration file using Augeas."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="config.edit",
            summary="Edit a configuration file with preview/rollback",
            domain="config",
            risk="medium",
            side_effects=(
                SideEffect(
                    kind="file_write",
                    target="{file_path}",
                    reversible=True,
                    description="Config file will be modified",
                ),
            ),
            requires_privilege=False,  # Depends on file
            idempotent=False,
            trust_level="core",
            version="1.0.0",
            input_schema_name="ConfigEditInput",
            output_schema_name="ConfigEditOutput",
        )

    def dry_run(self, input: ConfigEditInput) -> DryRunResult:
        """Preview config edit."""
        try:
            from elle.ops.augeas.controller import get_controller
            from elle.ops.augeas.models import AugeasEditRequest, AugeasOperation

            # Convert operations
            ops = []
            for op in input.operations:
                ops.append(AugeasOperation(
                    kind=op.kind,
                    path=op.path,
                    value=op.value,
                ))

            request = AugeasEditRequest(
                file_path=input.file_path,
                lens=input.lens,
                operations=tuple(ops),
                description=input.description,
            )

            controller = get_controller()
            preview = controller.preview(request)

            return DryRunResult(
                would_execute=(),
                would_modify=(input.file_path,),
                estimated_risk="medium" if preview.requires_privilege else "low",
                requires_confirmation=True,
                preview_text=f"Would modify {input.file_path}",
                diff=preview.diff,
                is_valid=True,
            )

        except ImportError:
            return DryRunResult(
                is_valid=False,
                validation_errors=("Augeas controller not available",),
                preview_text="Cannot preview: Augeas not available",
            )
        except Exception as e:
            return DryRunResult(
                is_valid=False,
                validation_errors=(str(e),),
                preview_text=f"Preview failed: {e}",
            )

    def run(self, input: ConfigEditInput) -> CapabilityResult:
        """Execute config edit."""
        import asyncio
        start_time = time.time()

        try:
            from elle.ops.augeas.controller import get_controller
            from elle.ops.augeas.models import AugeasEditRequest, AugeasOperation

            # Convert operations
            ops = []
            for op in input.operations:
                ops.append(AugeasOperation(
                    kind=op.kind,
                    path=op.path,
                    value=op.value,
                ))

            request = AugeasEditRequest(
                file_path=input.file_path,
                lens=input.lens,
                operations=tuple(ops),
                description=input.description,
                incident_id=input.incident_id,
                skip_validation=input.skip_validation,
            )

            controller = get_controller()

            # Execute (may need to run async)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Create a new task for async execution
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        result = pool.submit(
                            lambda: asyncio.run(controller.execute(request))
                        ).result()
                else:
                    result = loop.run_until_complete(controller.execute(request))
            except RuntimeError:
                result = asyncio.run(controller.execute(request))

            execution_time = int((time.time() - start_time) * 1000)

            if result.success:
                changes = tuple(
                    {
                        "path": c.path,
                        "old_value": c.old_value,
                        "new_value": c.new_value,
                        "operation": c.operation,
                    }
                    for c in result.changes
                )

                return CapabilityResult(
                    success=True,
                    output=ConfigEditOutput(
                        file_path=input.file_path,
                        changes=changes,
                        diff=result.diff or "",
                        backup_path=result.backup_path,
                        validation_passed=result.validation_passed,
                        validation_output=result.validation_output or "",
                    ),
                    side_effects_applied=self.spec.side_effects,
                    execution_time_ms=execution_time,
                    evidence=CapabilityEvidence(
                        files_modified=(input.file_path,),
                        verification_passed=result.validation_passed,
                        verification_details=result.validation_output or "",
                        rationale=f"Edited {input.file_path}: {input.description}",
                    ),
                )
            else:
                return CapabilityResult(
                    success=False,
                    error=result.error,
                    execution_time_ms=execution_time,
                    evidence=CapabilityEvidence(
                        files_modified=(input.file_path,) if result.rollback_applied else (),
                        rationale=f"Failed to edit {input.file_path}",
                    ),
                )

        except ImportError:
            return CapabilityResult(
                success=False,
                error="Augeas controller not available",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )
        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Config edit failed: {e}",
                ),
            )

    def verify(self, input: ConfigEditInput) -> VerificationResult:
        """Verify config edit was applied."""
        from pathlib import Path

        path = Path(input.file_path)
        if not path.exists():
            return VerificationResult(
                passed=False,
                discrepancies=("File does not exist after edit",),
            )

        # Run validation if available
        try:
            import asyncio

            from elle.ops.augeas.validators import validate_config

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        result = pool.submit(
                            lambda: asyncio.run(validate_config(path))
                        ).result()
                else:
                    result = loop.run_until_complete(validate_config(path))
            except RuntimeError:
                result = asyncio.run(validate_config(path))

            return VerificationResult(
                passed=result.valid,
                checks_performed=(result.command or "validation",),
                actual_state={"valid": result.valid},
                expected_state={"valid": True},
                discrepancies=()
                if result.valid
                else (result.error or "Validation failed",),
            )

        except ImportError:
            return VerificationResult(
                passed=True,
                checks_performed=("file exists",),
            )
        except Exception as e:
            return VerificationResult(
                passed=False,
                discrepancies=(str(e),),
            )

    def rollback(
        self,
        input: ConfigEditInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback config edit."""
        if not result.output:
            return RollbackResult(
                success=False,
                error="No output to determine backup path",
            )

        backup_path = result.output.backup_path
        if not backup_path:
            return RollbackResult(
                success=False,
                error="No backup available for rollback",
            )

        try:
            from elle.ops.augeas.controller import rollback_edit

            success = rollback_edit(input.file_path, backup_path)

            return RollbackResult(
                success=success,
                error=None if success else "Rollback failed",
                rolled_back=(input.file_path,) if success else (),
                failed_to_rollback=() if success else (input.file_path,),
            )

        except ImportError:
            return RollbackResult(
                success=False,
                error="Augeas controller not available",
            )
        except Exception as e:
            return RollbackResult(
                success=False,
                error=str(e),
                failed_to_rollback=(input.file_path,),
            )


# =============================================================================
# Config Preview Capability
# =============================================================================


class ConfigPreviewCapability(BaseCapability):
    """Preview changes to a configuration file."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="config.preview",
            summary="Preview changes to a config file without applying",
            domain="config",
            risk="none",
            side_effects=(),  # Read-only
            requires_privilege=False,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="ConfigPreviewInput",
            output_schema_name="ConfigPreviewOutput",
        )

    def dry_run(self, input: ConfigPreviewInput) -> DryRunResult:
        """Preview is itself a dry-run operation."""
        return DryRunResult(
            would_execute=(),
            would_modify=(),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text=f"Would preview changes to {input.file_path}",
            is_valid=True,
        )

    def run(self, input: ConfigPreviewInput) -> CapabilityResult:
        """Generate preview of config changes."""
        start_time = time.time()

        try:
            from elle.ops.augeas.controller import get_controller
            from elle.ops.augeas.models import AugeasEditRequest, AugeasOperation

            # Convert operations
            ops = []
            for op in input.operations:
                ops.append(AugeasOperation(
                    kind=op.kind,
                    path=op.path,
                    value=op.value,
                ))

            request = AugeasEditRequest(
                file_path=input.file_path,
                lens=input.lens,
                operations=tuple(ops),
            )

            controller = get_controller()
            preview = controller.preview(request)

            changes = tuple(
                {
                    "path": c.path,
                    "old_value": c.old_value,
                    "new_value": c.new_value,
                    "operation": c.operation,
                }
                for c in preview.changes
            )

            return CapabilityResult(
                success=True,
                output=ConfigPreviewOutput(
                    file_path=input.file_path,
                    diff=preview.diff,
                    diff_colored=preview.diff_colored,
                    changes=changes,
                    requires_privilege=preview.requires_privilege,
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Generated preview for {input.file_path}",
                ),
            )

        except ImportError:
            return CapabilityResult(
                success=False,
                error="Augeas controller not available",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )
        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )


# =============================================================================
# Exports
# =============================================================================

CONFIG_CAPABILITIES = [
    ConfigEditCapability,
    ConfigPreviewCapability,
]
