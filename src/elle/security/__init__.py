"""Security - Polkit integration and privilege management.

Provides privileged operation support via Polkit/pkexec.

Public API:
    - PolkitHelper: Execute privileged operations
    - PolkitAction: Action identifiers
    - PolkitResult: Operation result
    - write_privileged(): Write to privileged file
    - run_validator(): Run validation command
    - is_authorized(): Check authorization status
"""

from elle.security.polkit_helper import (
    PolkitAction,
    PolkitHelper,
    PolkitResult,
    get_helper,
    is_authorized,
    run_validator,
    write_privileged,
)

__all__ = [
    "PolkitAction",
    "PolkitHelper",
    "PolkitResult",
    "get_helper",
    "is_authorized",
    "run_validator",
    "write_privileged",
]
