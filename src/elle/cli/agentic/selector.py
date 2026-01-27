"""CapabilitySelector - Maps information needs to capability calls.

Determines which read-only capabilities should be executed to gather
the information needed to answer a question.
"""

from __future__ import annotations

import logging
from typing import Any

from elle.cli.agentic.models import (
    CapabilityCall,
    GatherPlan,
    InformationCategory,
    InformationNeed,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Capability Mappings
# =============================================================================

# Maps (category, aspect) to capability templates
# Template args use {target} as a placeholder for the target value
NEED_TO_CAPABILITY: dict[tuple[InformationCategory, str], list[dict[str, Any]]] = {
    # === Service capabilities ===
    ("service", "status"): [
        {
            "capability": "service.status",
            "args": {"service": "{target}"},
            "purpose": "Get service status from systemd",
        },
    ],
    ("service", "logs"): [
        {
            "capability": "service.logs",
            "args": {"service": "{target}", "lines": 50},
            "purpose": "Get recent log entries for service",
        },
    ],
    # === File capabilities ===
    ("file", "content"): [
        {
            "capability": "file.read",
            "args": {"path": "{target}"},
            "purpose": "Read file contents",
        },
    ],
    ("file", "stat"): [
        {
            "capability": "file.stat",
            "args": {"path": "{target}"},
            "purpose": "Get file metadata",
        },
    ],
    # === Package capabilities ===
    ("package", "info"): [
        {
            "capability": "package.info",
            "args": {"package": "{target}"},
            "purpose": "Get package information",
        },
    ],
    ("package", "list"): [
        {
            "capability": "package.list",
            "args": {"pattern": "{target}*"},
            "purpose": "List matching packages",
        },
    ],
    # === Docker capabilities ===
    ("docker", "list"): [
        {
            "capability": "docker.list",
            "args": {"all": True},
            "purpose": "List all containers",
        },
    ],
    ("docker", "status"): [
        {
            "capability": "docker.inspect",
            "args": {"name": "{target}"},
            "purpose": "Inspect container details",
        },
    ],
    ("docker", "logs"): [
        {
            "capability": "docker.logs",
            "args": {"name": "{target}", "lines": 50},
            "purpose": "Get container logs",
        },
    ],
    # === Network capabilities ===
    ("network", "listeners"): [
        {
            "capability": "network.listeners",
            "args": {},
            "purpose": "List listening ports",
        },
    ],
    ("network", "connections"): [
        {
            "capability": "network.connections",
            "args": {"process": "{target}"},
            "purpose": "List network connections",
        },
    ],
    # === Config capabilities ===
    ("config", "content"): [
        {
            "capability": "config.preview",
            "args": {"path": "{target}"},
            "purpose": "Preview configuration file",
        },
    ],
    # === System capabilities ===
    ("system", "info"): [
        {
            "capability": "system.info",
            "args": {},
            "purpose": "Get system information",
        },
    ],
    ("system", "resources"): [
        {
            "capability": "system.resources",
            "args": {},
            "purpose": "Get CPU/memory/disk usage",
        },
    ],
}

# Estimated execution time for each capability (milliseconds)
CAPABILITY_DURATION_ESTIMATES: dict[str, int] = {
    "service.status": 100,
    "service.logs": 200,
    "file.read": 50,
    "file.stat": 20,
    "package.info": 150,
    "package.list": 200,
    "docker.list": 150,
    "docker.inspect": 100,
    "docker.logs": 200,
    "network.listeners": 100,
    "network.connections": 100,
    "config.preview": 50,
    "system.info": 100,
    "system.resources": 150,
}


# =============================================================================
# CapabilitySelector
# =============================================================================


class CapabilitySelector:
    """Maps information needs to capability calls.

    Takes analyzed information needs and produces a plan of capability
    calls that will gather the required information.
    """

    def select(self, needs: tuple[InformationNeed, ...]) -> GatherPlan:
        """Select capabilities to satisfy information needs.

        Args:
            needs: Information needs to satisfy.

        Returns:
            GatherPlan with capability calls.
        """
        if not needs:
            return GatherPlan(
                needs=(),
                calls=(),
                estimated_duration_ms=0,
            )

        calls: list[CapabilityCall] = []
        seen_calls: set[str] = set()  # Deduplicate calls
        total_duration = 0

        for need in needs:
            for aspect in need.aspects:
                # Look up capability mapping
                key = (need.category, aspect)
                templates = NEED_TO_CAPABILITY.get(key, [])

                for template in templates:
                    # Build the capability call
                    capability = template["capability"]
                    args = self._substitute_args(template["args"], need.target)
                    purpose = template["purpose"]

                    # Deduplicate calls with same capability and args
                    call_key = f"{capability}:{args}"
                    if call_key in seen_calls:
                        continue
                    seen_calls.add(call_key)

                    call = CapabilityCall(
                        capability=capability,
                        args=args,
                        purpose=purpose,
                    )
                    calls.append(call)

                    # Add estimated duration
                    total_duration += CAPABILITY_DURATION_ESTIMATES.get(capability, 100)

        return GatherPlan(
            needs=needs,
            calls=tuple(calls),
            estimated_duration_ms=total_duration,
        )

    def _substitute_args(
        self,
        args_template: dict[str, Any],
        target: str,
    ) -> dict[str, Any]:
        """Substitute target placeholder in args.

        Args:
            args_template: Args dict with {target} placeholders.
            target: Value to substitute.

        Returns:
            Args dict with placeholders replaced.
        """
        result: dict[str, Any] = {}

        for key, value in args_template.items():
            if isinstance(value, str) and "{target}" in value:
                result[key] = value.replace("{target}", target)
            else:
                result[key] = value

        return result


# =============================================================================
# Module-level singleton
# =============================================================================

_selector: CapabilitySelector | None = None


def get_selector() -> CapabilitySelector:
    """Get the shared selector instance.

    Returns:
        The CapabilitySelector singleton.
    """
    global _selector
    if _selector is None:
        _selector = CapabilitySelector()
    return _selector


def reset_selector() -> None:
    """Reset the shared selector instance."""
    global _selector
    _selector = None
