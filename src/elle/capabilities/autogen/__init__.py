"""Auto-generating capability system.

Discovers installed binaries, parses man pages, and generates
capability specifications using LLM with Man Vault grounding.

Public API:
- discover_commands(): Find binaries with man pages
- generate_capability(): Generate spec from man page
- validate_capability(): Validate generated spec
- save_capability(): Save to database
- load_capabilities(): Load approved capabilities

Usage:
    # Discover available commands
    from elle.capabilities.autogen import discover_commands
    result = discover_commands(limit=10)

    # Generate capability from command
    from elle.capabilities.autogen import generate_and_save
    cap_id = await generate_and_save("systemctl", subcommand="restart")

    # Load capabilities on startup
    from elle.capabilities.autogen import load_capabilities
    count = load_capabilities(registry)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from elle.capabilities.autogen.discovery import (
    DiscoveryResult,
    InstalledBinary,
    discover_all_binaries,
    discover_binary,
    discover_specific_commands,
)
from elle.capabilities.autogen.factory import (
    create_capability_from_spec,
    generate_capability_class_code,
    generate_input_model_code,
    generate_output_model_code,
)
from elle.capabilities.autogen.generator import (
    CapabilityGenerator,
    generate_basic_spec,
    generate_capability_spec,
    get_generator,
)
from elle.capabilities.autogen.loader import (
    create_capability_instance,
    execute_autogen_capability,
    get_loaded_autogen_capabilities,
    load_all_approved,
    load_capability_by_name,
    load_capability_from_stored,
    register_autogen_capabilities,
)
from elle.capabilities.autogen.models import (
    CORE_COMMANDS,
    FlagType,
    FullValidationResult,
    GeneratedCapabilitySpec,
    InputFieldSpec,
    OutputFieldSpec,
    ParsedExample,
    ParsedFlag,
    ParsedManPage,
    ParsedSynopsis,
    StoredCapability,
    TrustLevel,
    ValidationResult,
    ValidationStage,
)
from elle.capabilities.autogen.parser import (
    flag_exists,
    parse_man_page,
    read_man_page,
)
from elle.capabilities.autogen.store import (
    AutogenStore,
    get_store,
    reset_store,
)
from elle.capabilities.autogen.validator import (
    assign_trust_level,
    validate_capability,
    validate_dry_run,
    validate_flags,
    validate_sandbox,
)

if TYPE_CHECKING:
    from elle.capabilities.registry import CapabilityRegistry


# =============================================================================
# High-Level API
# =============================================================================


def discover_commands(
    limit: int | None = None,
    require_man: bool = True,
) -> DiscoveryResult:
    """Discover commands with man pages.

    Args:
        limit: Maximum number of commands to return.
        require_man: Only return commands with man pages.

    Returns:
        DiscoveryResult with discovered binaries.
    """
    return discover_all_binaries(require_man=require_man, limit=limit)


async def generate_and_validate(
    command: str,
    subcommand: str | None = None,
    use_llm: bool = True,
) -> tuple[GeneratedCapabilitySpec | None, FullValidationResult | None]:
    """Generate and validate a capability spec.

    Args:
        command: Command name.
        subcommand: Optional subcommand.
        use_llm: Whether to use LLM for generation.

    Returns:
        Tuple of (spec, validation_result).
    """
    # Parse man page
    man_page = parse_man_page(command)
    if not man_page:
        return None, None

    # Generate spec
    spec = await generate_capability_spec(man_page, subcommand, use_llm=use_llm)
    if not spec:
        return None, None

    # Validate
    validation = validate_capability(spec, man_page)

    return spec, validation


async def generate_and_save(
    command: str,
    subcommand: str | None = None,
    use_llm: bool = True,
    auto_approve_core: bool = True,
) -> str | None:
    """Generate, validate, and save a capability.

    Args:
        command: Command name.
        subcommand: Optional subcommand.
        use_llm: Whether to use LLM for generation.
        auto_approve_core: Auto-approve core commands.

    Returns:
        Capability ID or None on failure.
    """
    # Parse man page
    man_page = parse_man_page(command)
    if not man_page:
        return None

    # Generate spec
    spec = await generate_capability_spec(man_page, subcommand, use_llm=use_llm)
    if not spec:
        return None

    # Validate
    validation = validate_capability(spec, man_page)

    # Generate code
    input_code, output_code, cap_code = create_capability_from_spec(spec)

    # Save
    store = get_store()
    cap_id = store.save(
        spec=spec,
        input_model_code=input_code,
        output_model_code=output_code,
        capability_class_code=cap_code,
        man_page_text=man_page.raw_text,
        trust_level=validation.trust_level,
        validation_json=validation.model_dump_json(),
    )

    # Auto-approve core commands
    if auto_approve_core and validation.trust_level == TrustLevel.CORE:
        store.approve(spec.name)

    return cap_id


def load_capabilities(registry: CapabilityRegistry | None = None) -> int:
    """Load all approved capabilities.

    Args:
        registry: Optional registry to register with.

    Returns:
        Number of capabilities loaded.
    """
    return load_all_approved(registry)


def list_generated_capabilities() -> list[dict[str, Any]]:
    """List all generated capabilities.

    Returns:
        List of capability info dicts.
    """
    store = get_store()
    capabilities = []

    for stored in store.list_all():
        try:
            spec = GeneratedCapabilitySpec.model_validate_json(stored.spec_json)
            capabilities.append({
                "id": stored.id,
                "name": stored.capability_name,
                "source_command": stored.source_command,
                "domain": spec.domain,
                "risk_level": spec.risk_level,
                "trust_level": stored.trust_level.value,
                "approved": stored.approved,
                "enabled": stored.enabled,
                "generated_at": stored.generated_at.isoformat(),
            })
        except Exception:
            pass

    return capabilities


def list_pending_capabilities() -> list[dict[str, Any]]:
    """List capabilities pending approval.

    Returns:
        List of capability info dicts.
    """
    store = get_store()
    capabilities = []

    for stored in store.list_pending_approval():
        try:
            spec = GeneratedCapabilitySpec.model_validate_json(stored.spec_json)
            capabilities.append({
                "id": stored.id,
                "name": stored.capability_name,
                "source_command": stored.source_command,
                "domain": spec.domain,
                "risk_level": spec.risk_level,
                "trust_level": stored.trust_level.value,
                "generated_at": stored.generated_at.isoformat(),
            })
        except Exception:
            pass

    return capabilities


def approve_capability(name: str) -> bool:
    """Approve a capability for use.

    Args:
        name: Capability name.

    Returns:
        True if approved.
    """
    store = get_store()
    return store.approve(name)


def disable_capability(name: str) -> bool:
    """Disable a capability.

    Args:
        name: Capability name.

    Returns:
        True if disabled.
    """
    store = get_store()
    return store.disable(name)


def delete_capability(name: str) -> bool:
    """Delete a capability.

    Args:
        name: Capability name.

    Returns:
        True if deleted.
    """
    store = get_store()
    return store.delete(name)


__all__ = [
    # Discovery
    "discover_commands",
    "discover_binary",
    "discover_specific_commands",
    "discover_all_binaries",
    "DiscoveryResult",
    "InstalledBinary",
    # Parsing
    "parse_man_page",
    "read_man_page",
    "flag_exists",
    "ParsedManPage",
    "ParsedSynopsis",
    "ParsedFlag",
    "ParsedExample",
    "FlagType",
    # Generation
    "generate_capability_spec",
    "generate_basic_spec",
    "generate_and_validate",
    "generate_and_save",
    "CapabilityGenerator",
    "get_generator",
    "GeneratedCapabilitySpec",
    "InputFieldSpec",
    "OutputFieldSpec",
    # Factory
    "create_capability_from_spec",
    "generate_input_model_code",
    "generate_output_model_code",
    "generate_capability_class_code",
    # Validation
    "validate_capability",
    "validate_flags",
    "validate_dry_run",
    "validate_sandbox",
    "assign_trust_level",
    "ValidationResult",
    "ValidationStage",
    "FullValidationResult",
    # Storage
    "get_store",
    "reset_store",
    "AutogenStore",
    "StoredCapability",
    "TrustLevel",
    "CORE_COMMANDS",
    # Loading
    "load_capabilities",
    "load_capability_by_name",
    "load_capability_from_stored",
    "load_all_approved",
    "register_autogen_capabilities",
    "create_capability_instance",
    "execute_autogen_capability",
    "get_loaded_autogen_capabilities",
    # High-level API
    "list_generated_capabilities",
    "list_pending_capabilities",
    "approve_capability",
    "disable_capability",
    "delete_capability",
]
