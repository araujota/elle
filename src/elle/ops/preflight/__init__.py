"""Pre-flight safety testing for package operations.

Provides multi-tier validation before applying changes:
- Tier 1: apt --dry-run (always)
- Tier 2: systemd-nspawn ephemeral container (high-risk packages)
- Tier 3: LXD snapshot (critical changes only)
"""

from elle.ops.preflight.models import (
    PreflightIssue,
    PreflightResult,
    PreflightStatus,
    PreflightTest,
)
from elle.ops.preflight.validator import (
    PreflightValidator,
    format_result_for_display,
    get_validator,
    validate_packages,
)

__all__ = [
    "PreflightIssue",
    "PreflightResult",
    "PreflightStatus",
    "PreflightTest",
    "PreflightValidator",
    "format_result_for_display",
    "get_validator",
    "validate_packages",
]
