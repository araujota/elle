"""ELLE operations module.

DEPRECATION NOTICE:
    Direct use of ops modules is deprecated. These modules are internal
    implementations that should be accessed through the Capabilities system.

    Instead of:
        from elle.ops.augeas import AugeasController
        controller = AugeasController()
        controller.edit(...)

    Use:
        from elle.capabilities import execute_capability
        from elle.capabilities.core import ConfigEditInput
        result = await execute_capability(
            "config.edit",
            ConfigEditInput(file_path=..., operations=...),
        )

    Capabilities provide:
    - Policy enforcement (confirmation, risk assessment)
    - Audit trails (incident integration)
    - Dry-run previews
    - Automatic rollback on failure

Provides subsystems for:
- augeas: Structured config file editing via Augeas -> use config.edit capability
- files: General file operations -> use file.* capabilities
- wireguard: WireGuard key operations -> use wireguard.* capabilities
- stack: Stack deployment -> use stack.* capabilities (in development)
"""

import warnings

# Issue deprecation warning when ops is imported directly
warnings.warn(
    "Direct import from elle.ops is deprecated. "
    "Use elle.capabilities for policy-governed operations. "
    "See elle.ops.__doc__ for migration guidance.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["augeas", "files", "wireguard", "stack"]
