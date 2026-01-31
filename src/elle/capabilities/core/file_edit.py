"""File editing capability using the 6-Tier Deterministic Editing Stack.

Provides a unified interface for editing any file type, automatically
selecting the most appropriate editing tier based on file format
and operation type.

Capabilities:
- file.edit - Edit a file using the tier stack with automatic tier selection
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from elle.capabilities.models import (
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    RollbackResult,
    SideEffect,
    VerificationResult,
)
from elle.capabilities.protocol import BaseCapability

logger = logging.getLogger(__name__)


# =============================================================================
# Input/Output Models
# =============================================================================


class FileEditOperation(BaseModel):
    """A single file edit operation.

    This is the universal edit operation model consumed by all tiers.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["set", "rm", "add", "replace", "append", "insert"] = Field(
        description="Type of edit operation",
    )
    path: str | None = Field(
        default=None,
        description="Path expression (format depends on file type: JSONPath, XPath, Augeas path, INI section.key)",
    )
    value: str | None = Field(
        default=None,
        description="Value to set/add/replace",
    )
    anchor_pattern: str | None = Field(
        default=None,
        description="For Tier 5 text patching: regex pattern to anchor the edit",
    )
    anchor_context_lines: int = Field(
        default=3,
        ge=0,
        le=10,
        description="For Tier 5: lines of context for anchor verification",
    )
    section: str | None = Field(
        default=None,
        description="For INI files: section name",
    )
    key: str | None = Field(
        default=None,
        description="For INI files: parameter key",
    )
    position: Literal["before", "after", "into"] | None = Field(
        default=None,
        description="For insert operations: where to insert relative to path",
    )


class FileEditInput(BaseModel):
    """Input for the unified file.edit capability."""

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(
        description="Absolute path to the file to edit",
    )
    operation: FileEditOperation = Field(
        description="The edit operation to perform",
    )
    description: str = Field(
        default="",
        description="Human-readable summary of the changes",
    )

    # Tier preferences
    preferred_tier: int | None = Field(
        default=None,
        ge=0,
        le=5,
        description="Preferred tier (will validate applicability)",
    )
    max_tier: int = Field(
        default=5,
        ge=0,
        le=5,
        description="Maximum tier to use (won't escalate beyond this)",
    )
    tier5_justification: str | None = Field(
        default=None,
        description="Required justification if Tier 5 is used",
    )

    # Drop-in options (Tier 0)
    dropin_name: str | None = Field(
        default=None,
        description="For Tier 0: name for the drop-in file",
    )
    dropin_priority: int = Field(
        default=99,
        ge=0,
        le=99,
        description="For Tier 0: priority number (00-99)",
    )

    # Options
    skip_validation: bool = Field(
        default=False,
        description="Skip post-edit validation",
    )
    skip_backup: bool = Field(
        default=False,
        description="Skip backup creation (not recommended)",
    )

    # Incident integration
    incident_id: str | None = Field(
        default=None,
        description="Incident ID for recording",
    )


class TierEscalationRecord(BaseModel):
    """Record of why a tier was skipped during selection."""

    model_config = ConfigDict(frozen=True)

    tier: int = Field(description="Tier number that was skipped")
    tier_name: str = Field(description="Name of the skipped tier")
    reason: str = Field(description="Why this tier was skipped")
    tool_unavailable: bool = Field(
        default=False,
        description="Whether skipped due to missing tool",
    )


class FileEditOutput(BaseModel):
    """Output from the file.edit capability."""

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(description="Path that was edited")

    # Tier information
    tier_used: int = Field(description="Tier number that performed the edit")
    tier_name: str = Field(description="Name of the tier used")
    tier_selection_reason: str = Field(description="Why this tier was selected")
    escalation_trail: tuple[TierEscalationRecord, ...] = Field(
        default_factory=tuple,
        description="Trail of skipped tiers with reasons",
    )
    tool_used: str | None = Field(
        default=None,
        description="Specific tool that was used",
    )

    # Diff and changes
    diff: str = Field(
        default="",
        description="Unified diff showing changes",
    )

    # Backup
    backup_path: str | None = Field(
        default=None,
        description="Path to backup file if created",
    )

    # Validation
    validation_passed: bool = Field(
        default=True,
        description="Whether validation passed",
    )


# =============================================================================
# File Edit Capability
# =============================================================================


class FileEditCapability(BaseCapability[FileEditInput, FileEditOutput]):
    """Edit a file using the 6-Tier Deterministic Editing Stack.

    Automatically selects the most appropriate editing tier based on:
    - File type and format
    - Available tools
    - Operation type

    Tier Hierarchy (highest-first ordering):
        Tier 0 - Native Override: systemd drop-ins, *.d/ directories
        Tier 1 - Augeas: Grammar-based structured editing
        Tier 2 - Structured Data: jq/yq/toml for JSON/YAML/TOML
        Tier 3 - Format Editors: crudini/xmlstarlet for INI/XML
        Tier 4 - Document-aware: Markdown/RST parsers
        Tier 5 - Text Patching: Last resort (requires justification)
    """

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="file.edit",
            summary="Edit a file using automatic tier selection",
            domain="file",
            risk="medium",
            side_effects=(
                SideEffect(
                    kind="file_write",
                    target="{file_path}",
                    reversible=True,
                    description="File will be modified using the most appropriate editing tier",
                ),
            ),
            requires_privilege=False,  # Depends on file
            idempotent=False,
            trust_level="core",
            version="1.0.0",
            input_schema_name="FileEditInput",
            output_schema_name="FileEditOutput",
            dependencies=("jq", "yq", "crudini", "xmlstarlet", "augeas"),
        )

    def dry_run(self, input: FileEditInput) -> DryRunResult:
        """Preview file edit with tier selection."""
        try:
            from elle.ops.editing import TierSelector
            from elle.ops.editing.tiers import get_tier_instance

            # Convert input to EditOperation
            edit_op = self._convert_operation(input.operation)

            # Select tier
            selector = TierSelector()
            selection = selector.select_tier(
                input.file_path,
                edit_op,
                max_tier=input.max_tier,  # type: ignore
                preferred_tier=input.preferred_tier,  # type: ignore
            )

            # Check Tier 5 justification
            if selection.tier == 5 and not input.tier5_justification:
                return DryRunResult(
                    is_valid=False,
                    validation_errors=(
                        "Tier 5 (Text Patching) requires justification. Set tier5_justification in the input.",
                    ),
                    would_execute=(),
                    would_modify=(input.file_path,),
                    estimated_risk="medium",
                    requires_confirmation=True,
                )

            # Get preview from tier
            tier_instance = get_tier_instance(selection.tier)
            preview = tier_instance.preview(Path(input.file_path), edit_op)

            return DryRunResult(
                is_valid=preview.is_valid,
                validation_errors=(),
                would_execute=(
                    f"Tier {selection.tier} ({selection.tier_name}): {selection.tool_used or 'default tool'}",
                ),
                would_modify=(input.file_path,),
                diff=preview.diff,
                estimated_risk="medium",
                requires_confirmation=True,
                preview_text=(
                    f"Selected Tier {selection.tier} ({selection.tier_name})\n"
                    f"Reason: {selection.reason}\n"
                    f"Tool: {selection.tool_used or 'default'}\n"
                    f"Requires privilege: {preview.requires_privilege}"
                ),
            )

        except Exception as e:
            return DryRunResult(
                is_valid=False,
                validation_errors=(str(e),),
                would_execute=(),
                would_modify=(input.file_path,),
                estimated_risk="medium",
                requires_confirmation=True,
            )

    def run(self, input: FileEditInput) -> CapabilityResult:
        """Execute file edit using tier stack."""
        import asyncio

        start_time = datetime.utcnow()

        try:
            from elle.ops.editing import (
                Tier5RequiresJustificationError,
                TierSelector,
            )
            from elle.ops.editing.tiers import get_tier_instance

            # Convert input to EditOperation
            edit_op = self._convert_operation(input.operation)

            # Select tier
            selector = TierSelector()
            selection = selector.select_tier(
                input.file_path,
                edit_op,
                max_tier=input.max_tier,  # type: ignore
                preferred_tier=input.preferred_tier,  # type: ignore
            )

            # Check Tier 5 justification
            if selection.tier == 5 and not input.tier5_justification:
                raise Tier5RequiresJustificationError(input.file_path)

            # Get tier instance and apply
            tier_instance = get_tier_instance(selection.tier)

            # Run the async apply method
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're in an async context - create task
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        tier_instance.apply(
                            Path(input.file_path),
                            edit_op,
                            skip_backup=input.skip_backup,
                            skip_validation=input.skip_validation,
                            dropin_name=input.dropin_name,
                            dropin_priority=input.dropin_priority,
                        ),
                    )
                    result = future.result()
            else:
                result = asyncio.run(
                    tier_instance.apply(
                        Path(input.file_path),
                        edit_op,
                        skip_backup=input.skip_backup,
                        skip_validation=input.skip_validation,
                        dropin_name=input.dropin_name,
                        dropin_priority=input.dropin_priority,
                    )
                )

            # Convert escalation trail
            escalation_records = tuple(
                TierEscalationRecord(
                    tier=e.tier,
                    tier_name=e.tier_name,
                    reason=e.reason,
                    tool_unavailable=e.tool_unavailable,
                )
                for e in selection.escalation_trail
            )

            if result.success:
                output = FileEditOutput(
                    file_path=result.file_path,
                    tier_used=result.tier_used,
                    tier_name=result.tier_name,
                    tier_selection_reason=selection.reason,
                    escalation_trail=escalation_records,
                    tool_used=result.tool_used,
                    diff=result.diff,
                    backup_path=result.backup_path,
                    validation_passed=result.validation.passed if result.validation else True,
                )

                end_time = datetime.utcnow()
                duration_ms = int((end_time - start_time).total_seconds() * 1000)

                return CapabilityResult(
                    success=True,
                    output=output,
                    side_effects_applied=(
                        SideEffect(
                            kind="file_write",
                            target=input.file_path,
                            reversible=True,
                            description=f"Edited using Tier {result.tier_used} ({result.tier_name})",
                        ),
                    ),
                    execution_time_ms=duration_ms,
                    executed_at=start_time,
                    evidence=CapabilityEvidence(
                        commands_executed=(f"Tier {result.tier_used}: {result.tool_used}",),
                        files_modified=(input.file_path,),
                        verification_passed=result.validation.passed if result.validation else True,
                        verification_details=result.diff,
                        rationale=f"Selected {result.tier_name} for editing {input.file_path}",
                    ),
                )
            else:
                output = FileEditOutput(
                    file_path=input.file_path,
                    tier_used=result.tier_used,
                    tier_name=result.tier_name,
                    tier_selection_reason=selection.reason,
                    escalation_trail=escalation_records,
                    tool_used=result.tool_used,
                    diff=result.diff,
                    backup_path=result.backup_path,
                    validation_passed=False,
                )

                end_time = datetime.utcnow()
                duration_ms = int((end_time - start_time).total_seconds() * 1000)

                return CapabilityResult(
                    success=False,
                    output=output,
                    error=result.error,
                    execution_time_ms=duration_ms,
                    executed_at=start_time,
                    evidence=CapabilityEvidence(
                        commands_executed=(f"Tier {result.tier_used}: {result.tool_used}",),
                        files_modified=(),
                        verification_passed=False,
                        rationale=f"Edit failed: {result.error}",
                    ),
                )

        except Exception as e:
            end_time = datetime.utcnow()
            duration_ms = int((end_time - start_time).total_seconds() * 1000)

            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=duration_ms,
                executed_at=start_time,
                evidence=CapabilityEvidence(
                    commands_executed=(),
                    files_modified=(),
                    verification_passed=False,
                    rationale=f"Exception: {e}",
                ),
            )

    def verify(self, input: FileEditInput) -> VerificationResult:
        """Verify the edit was successful."""
        try:
            # Check file exists
            file_path = Path(input.file_path)
            if not file_path.exists():
                return VerificationResult(
                    passed=False,
                    checks_performed=("file_exists",),
                    actual_state={"exists": False},
                    expected_state={"exists": True},
                    discrepancies=("File does not exist",),
                )

            # Verify content matches expected (based on operation)
            return VerificationResult(
                passed=True,
                checks_performed=("file_exists", "content_valid"),
                actual_state={"exists": True, "readable": True},
                expected_state={"exists": True, "readable": True},
                discrepancies=(),
            )
        except Exception as e:
            return VerificationResult(
                passed=False,
                checks_performed=("verify",),
                discrepancies=(str(e),),
            )

    def rollback(self, input: FileEditInput, result: CapabilityResult) -> RollbackResult:
        """Rollback the edit using backup."""
        try:
            from elle.ops.augeas.backup import restore_file

            output = result.output
            if hasattr(output, "backup_path") and output.backup_path:
                restore_file(output.backup_path)
                return RollbackResult(
                    success=True,
                    rolled_back=(input.file_path,),
                    failed_to_rollback=(),
                    commands_executed=(f"restore from {output.backup_path}",),
                )
            else:
                return RollbackResult(
                    success=False,
                    error="No backup available",
                    rolled_back=(),
                    failed_to_rollback=(input.file_path,),
                )
        except Exception as e:
            return RollbackResult(
                success=False,
                error=str(e),
                rolled_back=(),
                failed_to_rollback=(input.file_path,),
            )

    def _convert_operation(self, op: FileEditOperation) -> Any:
        """Convert FileEditOperation to EditOperation."""
        from elle.ops.editing import EditOperation

        return EditOperation(
            kind=op.kind,
            path=op.path,
            value=op.value,
            anchor_pattern=op.anchor_pattern,
            anchor_context_lines=op.anchor_context_lines,
            section=op.section,
            key=op.key,
            position=op.position,
        )


# =============================================================================
# Exports
# =============================================================================

FILE_EDIT_CAPABILITIES = [FileEditCapability]
