"""Validation pipeline for auto-generated capabilities.

Stages:
1. Flag Check - Verify all referenced flags exist in man page
2. Dry-run Test - Execute dry_run() with test input
3. Sandbox Test - Run in isolated environment
4. Trust Assignment - Assign trust level based on results
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from elle.capabilities.autogen.models import (
    CORE_COMMANDS,
    FullValidationResult,
    GeneratedCapabilitySpec,
    ParsedManPage,
    TrustLevel,
    ValidationResult,
    ValidationStage,
)
from elle.capabilities.autogen.parser import flag_exists

logger = logging.getLogger(__name__)


# =============================================================================
# Flag Validation
# =============================================================================


def validate_flags(
    spec: GeneratedCapabilitySpec,
    man_page: ParsedManPage | None,
) -> ValidationResult:
    """Validate that all flags in command template exist in man page.

    Args:
        spec: Generated capability spec.
        man_page: Parsed man page for reference.

    Returns:
        ValidationResult for flag check stage.
    """
    errors: list[str] = []
    warnings: list[str] = []
    details: dict[str, any] = {}

    # Extract flags from command template
    template = spec.command_template
    flag_pattern = re.compile(r"(-{1,2}[\w-]+)")
    template_flags = set(flag_pattern.findall(template))

    details["template_flags"] = list(template_flags)

    if not man_page:
        warnings.append("No man page available for flag validation")
        return ValidationResult(
            stage=ValidationStage.FLAG_CHECK,
            passed=True,  # Pass if we can't validate
            errors=tuple(errors),
            warnings=tuple(warnings),
            details=details,
        )

    # Check each flag
    invalid_flags: list[str] = []
    for flag in template_flags:
        if not flag_exists(man_page, flag):
            invalid_flags.append(flag)

    details["invalid_flags"] = invalid_flags
    details["man_page_flags"] = len(man_page.flags)

    if invalid_flags:
        errors.append(f"Invalid flags: {', '.join(invalid_flags)}")

    return ValidationResult(
        stage=ValidationStage.FLAG_CHECK,
        passed=len(errors) == 0,
        errors=tuple(errors),
        warnings=tuple(warnings),
        details=details,
    )


# =============================================================================
# Dry Run Validation
# =============================================================================


def validate_dry_run(spec: GeneratedCapabilitySpec) -> ValidationResult:
    """Validate the capability's dry_run method.

    Args:
        spec: Generated capability spec.

    Returns:
        ValidationResult for dry-run stage.
    """
    errors: list[str] = []
    warnings: list[str] = []
    details: dict[str, any] = {}

    try:
        from elle.capabilities.autogen.factory import (
            compile_capability_class,
            generate_capability_class_code,
            generate_input_model_code,
            generate_output_model_code,
        )

        # Generate code
        input_code = generate_input_model_code(spec)
        output_code = generate_output_model_code(spec)
        cap_code = generate_capability_class_code(spec)

        # Compile
        cap_class = compile_capability_class(spec, input_code, output_code, cap_code)
        if not cap_class:
            errors.append("Failed to compile capability class")
            return ValidationResult(
                stage=ValidationStage.DRY_RUN,
                passed=False,
                errors=tuple(errors),
                warnings=tuple(warnings),
                details=details,
            )

        # Instantiate
        instance = cap_class()

        # Build test input
        test_input = {}
        for field in spec.input_fields:
            if field.required:
                if field.field_type in ("str", "string"):
                    test_input[field.name] = "test_value"
                elif field.field_type in ("int", "integer"):
                    test_input[field.name] = 1
                elif field.field_type in ("bool", "boolean"):
                    test_input[field.name] = True
                elif "path" in field.field_type.lower():
                    test_input[field.name] = "/tmp/test"

        # Call dry_run
        result = instance.dry_run(test_input)

        details["dry_run_result"] = {
            "would_execute": result.would_execute if hasattr(result, "would_execute") else None,
            "preview_text": result.preview_text if hasattr(result, "preview_text") else None,
        }

        # Validate result
        if not result:
            errors.append("dry_run returned empty result")
        elif hasattr(result, "would_execute") and not result.would_execute:
            warnings.append("dry_run.would_execute is empty")

    except Exception as e:
        errors.append(f"dry_run failed: {e}")
        details["exception"] = str(e)

    return ValidationResult(
        stage=ValidationStage.DRY_RUN,
        passed=len(errors) == 0,
        errors=tuple(errors),
        warnings=tuple(warnings),
        details=details,
    )


# =============================================================================
# Sandbox Validation
# =============================================================================


def validate_sandbox(spec: GeneratedCapabilitySpec) -> ValidationResult:
    """Validate capability in sandbox environment.

    Note: This is a placeholder - full sandbox validation requires
    container isolation or chroot.

    Args:
        spec: Generated capability spec.

    Returns:
        ValidationResult for sandbox stage.
    """
    warnings: list[str] = []
    details: dict[str, any] = {}

    # For now, just check for obviously dangerous patterns
    dangerous_patterns = [
        (r"rm\s+-rf\s+/[^/]", "Recursive delete from root"),
        (r"dd\s+.*of=/dev/sd", "Raw disk write"),
        (r"mkfs", "Filesystem format"),
        (r">\s*/etc/", "Overwrite system config"),
        (r"\|\s*sh", "Pipe to shell"),
        (r"\|\s*bash", "Pipe to bash"),
    ]

    cmd = spec.command_template
    found_dangerous: list[str] = []

    for pattern, description in dangerous_patterns:
        if re.search(pattern, cmd, re.IGNORECASE):
            found_dangerous.append(description)

    details["dangerous_patterns"] = found_dangerous

    if found_dangerous:
        warnings.append(f"Dangerous patterns found: {', '.join(found_dangerous)}")

    # Check risk level alignment
    if found_dangerous and spec.risk_level in ("none", "low"):
        warnings.append("Risk level may be too low given dangerous patterns")

    return ValidationResult(
        stage=ValidationStage.SANDBOX,
        passed=True,  # Sandbox is advisory only
        errors=(),
        warnings=tuple(warnings),
        details=details,
    )


# =============================================================================
# Trust Assignment
# =============================================================================


def assign_trust_level(
    spec: GeneratedCapabilitySpec,
    validation_results: list[ValidationResult],
) -> tuple[TrustLevel, ValidationResult]:
    """Assign trust level based on validation results.

    Args:
        spec: Generated capability spec.
        validation_results: Results from previous validation stages.

    Returns:
        Tuple of (TrustLevel, ValidationResult for trust stage).
    """
    errors: list[str] = []
    warnings: list[str] = []
    details: dict[str, any] = {}

    # Check if core command
    if spec.source_command in CORE_COMMANDS:
        trust = TrustLevel.CORE
        details["reason"] = "Well-known core command"
    else:
        # Check validation results
        all_passed = all(r.passed for r in validation_results)
        any_errors = any(len(r.errors) > 0 for r in validation_results)

        if all_passed and spec.risk_level in ("none", "low", "medium"):
            trust = TrustLevel.OFFICIAL
            details["reason"] = "All validations passed, acceptable risk"
        elif all_passed and spec.risk_level in ("high", "critical"):
            trust = TrustLevel.THIRD_PARTY
            details["reason"] = "High/critical risk requires approval"
            warnings.append("High risk capability requires manual approval")
        else:
            trust = TrustLevel.THIRD_PARTY
            details["reason"] = "Validation failures require approval"
            if any_errors:
                warnings.append("Validation errors detected")

    details["assigned_trust"] = trust.value
    details["risk_level"] = spec.risk_level

    return trust, ValidationResult(
        stage=ValidationStage.TRUST_ASSIGN,
        passed=True,
        errors=tuple(errors),
        warnings=tuple(warnings),
        details=details,
    )


# =============================================================================
# Full Validation Pipeline
# =============================================================================


def validate_capability(
    spec: GeneratedCapabilitySpec,
    man_page: ParsedManPage | None = None,
) -> FullValidationResult:
    """Run full validation pipeline on a capability spec.

    Args:
        spec: Generated capability spec.
        man_page: Optional parsed man page for flag validation.

    Returns:
        FullValidationResult with all stage results.
    """
    results: list[ValidationResult] = []

    # Stage 1: Flag validation
    flag_result = validate_flags(spec, man_page)
    results.append(flag_result)

    # Stage 2: Dry-run validation
    dry_run_result = validate_dry_run(spec)
    results.append(dry_run_result)

    # Stage 3: Sandbox validation
    sandbox_result = validate_sandbox(spec)
    results.append(sandbox_result)

    # Stage 4: Trust assignment
    trust_level, trust_result = assign_trust_level(spec, results)
    results.append(trust_result)

    # Overall pass/fail
    overall_passed = all(r.passed for r in results)

    return FullValidationResult(
        capability_name=spec.name,
        stages=tuple(results),
        overall_passed=overall_passed,
        trust_level=trust_level,
        timestamp=datetime.utcnow(),
    )
