"""ELLE Capabilities System.

First-class, typed, policy-governed operations that replace raw shell commands.

Usage:
    from elle.capabilities import get_registry, execute_capability
    from elle.capabilities.core import ServiceInput

    # Execute via executor (recommended)
    result = await execute_capability(
        "service.restart",
        ServiceInput(service="nginx"),
    )

    # Or get capability directly from registry
    registry = get_registry()
    cap = registry.get("service.restart")
    if cap:
        preview = cap.dry_run(ServiceInput(service="nginx"))
        result = cap.run(ServiceInput(service="nginx"))

Capability Lifecycle:
    1. dry_run() - Preview what would happen
    2. Policy check - Verify operation is allowed
    3. User confirmation - If required by policy or risk level
    4. run() - Execute the operation
    5. verify() - Confirm state change occurred
    6. rollback() - Undo changes if needed

Domains:
    - service: Systemd service management
    - file: File operations
    - config: Configuration editing
    - package: Package management
    - network: Network configuration
    - docker: Container operations
    - auth: Authentication/authorization
"""

# Models
# Exceptions
from elle.capabilities.exceptions import (
    CapabilityCancelledError,
    CapabilityDeniedError,
    CapabilityError,
    CapabilityExecutionError,
    CapabilityInputError,
    CapabilityNotFoundError,
    CapabilityRegistrationError,
)

# Executor
from elle.capabilities.executor import (
    CapabilityExecutor,
    execute_capability,
    get_executor,
    reset_executor,
)
from elle.capabilities.models import (
    CapabilityDomain,
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    RiskLevel,
    RollbackResult,
    SideEffect,
    SideEffectKind,
    TrustLevel,
    VerificationResult,
)

# Policy integration
from elle.capabilities.policy import (
    CapabilityPolicyResult,
    capability_requires_confirmation,
    evaluate_capability,
)

# Protocol
from elle.capabilities.protocol import (
    BaseCapability,
    Capability,
)

# Registry
from elle.capabilities.registry import (
    CapabilityRegistry,
    get_registry,
    reset_registry,
)

__all__ = [
    # Models
    "CapabilityDomain",
    "CapabilityEvidence",
    "CapabilityResult",
    "CapabilitySpec",
    "DryRunResult",
    "RiskLevel",
    "RollbackResult",
    "SideEffect",
    "SideEffectKind",
    "TrustLevel",
    "VerificationResult",
    # Protocol
    "BaseCapability",
    "Capability",
    # Exceptions
    "CapabilityCancelledError",
    "CapabilityDeniedError",
    "CapabilityError",
    "CapabilityExecutionError",
    "CapabilityInputError",
    "CapabilityNotFoundError",
    "CapabilityRegistrationError",
    # Registry
    "CapabilityRegistry",
    "get_registry",
    "reset_registry",
    # Executor
    "CapabilityExecutor",
    "execute_capability",
    "get_executor",
    "reset_executor",
    # Policy
    "CapabilityPolicyResult",
    "capability_requires_confirmation",
    "evaluate_capability",
]
