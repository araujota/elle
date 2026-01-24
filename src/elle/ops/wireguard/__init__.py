"""WireGuard operations module for ELLE.

Provides safe WireGuard key management and configuration operations.

Usage:
    from elle.ops.wireguard import (
        generate_keypair,
        rotate_keys_safely,
        get_interface_status,
    )

    # Generate a new keypair
    private_key, public_key = generate_keypair()

    # Rotate keys safely without dropping connections
    result = rotate_keys_safely("wg0")
"""

from elle.ops.wireguard.keys import (
    generate_keypair,
    generate_preshared_key,
    derive_public_key,
    rotate_keys_safely,
    KeyRotationResult,
    WireGuardKeyError,
)

__all__ = [
    "generate_keypair",
    "generate_preshared_key",
    "derive_public_key",
    "rotate_keys_safely",
    "KeyRotationResult",
    "WireGuardKeyError",
]
