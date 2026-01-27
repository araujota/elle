"""Command-to-Capability Mapper for the Planner.

Maps common shell commands to equivalent capability calls, enabling
the planner to execute operations through the capability system
rather than raw subprocess calls.

This module addresses the C5/C6 audit violations by providing a
translation layer between LLM-generated commands and capabilities.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapabilityMapping:
    """A mapping from command pattern to capability call."""

    capability: str
    """Capability name (e.g., 'service.restart')"""

    input_builder: dict[str, Any] | None
    """Static input or None if dynamic extraction needed"""

    extract_fn: str | None
    """Name of extraction function for dynamic input"""


# Command patterns and their capability mappings
COMMAND_PATTERNS: list[tuple[re.Pattern[str], CapabilityMapping]] = [
    # Service management
    (
        re.compile(r"^sudo\s+systemctl\s+restart\s+(\S+)$"),
        CapabilityMapping("service.restart", None, "extract_service"),
    ),
    (
        re.compile(r"^systemctl\s+restart\s+(\S+)$"),
        CapabilityMapping("service.restart", None, "extract_service"),
    ),
    (
        re.compile(r"^sudo\s+systemctl\s+start\s+(\S+)$"),
        CapabilityMapping("service.start", None, "extract_service"),
    ),
    (
        re.compile(r"^systemctl\s+start\s+(\S+)$"),
        CapabilityMapping("service.start", None, "extract_service"),
    ),
    (
        re.compile(r"^sudo\s+systemctl\s+stop\s+(\S+)$"),
        CapabilityMapping("service.stop", None, "extract_service"),
    ),
    (
        re.compile(r"^systemctl\s+stop\s+(\S+)$"),
        CapabilityMapping("service.stop", None, "extract_service"),
    ),
    (
        re.compile(r"^systemctl\s+status\s+(\S+)$"),
        CapabilityMapping("service.status", None, "extract_service"),
    ),
    (
        re.compile(r"^sudo\s+service\s+(\S+)\s+restart$"),
        CapabilityMapping("service.restart", None, "extract_service_legacy"),
    ),
    (
        re.compile(r"^sudo\s+service\s+(\S+)\s+start$"),
        CapabilityMapping("service.start", None, "extract_service_legacy"),
    ),
    (
        re.compile(r"^sudo\s+service\s+(\S+)\s+stop$"),
        CapabilityMapping("service.stop", None, "extract_service_legacy"),
    ),
    # Package management
    (
        re.compile(r"^sudo\s+apt(?:-get)?\s+install\s+(?:-y\s+)?(.+)$"),
        CapabilityMapping("package.install", None, "extract_packages"),
    ),
    (
        re.compile(r"^apt(?:-get)?\s+install\s+(?:-y\s+)?(.+)$"),
        CapabilityMapping("package.install", None, "extract_packages"),
    ),
    (
        re.compile(r"^sudo\s+apt(?:-get)?\s+remove\s+(?:-y\s+)?(.+)$"),
        CapabilityMapping("package.remove", None, "extract_packages"),
    ),
    (
        re.compile(r"^apt(?:-get)?\s+remove\s+(?:-y\s+)?(.+)$"),
        CapabilityMapping("package.remove", None, "extract_packages"),
    ),
    (
        re.compile(r"^sudo\s+apt(?:-get)?\s+update$"),
        CapabilityMapping("package.update", {"packages": []}, None),
    ),
    (
        re.compile(r"^apt(?:-get)?\s+update$"),
        CapabilityMapping("package.update", {"packages": []}, None),
    ),
    # Docker operations
    (
        re.compile(r"^docker\s+stop\s+(\S+)$"),
        CapabilityMapping("docker.stop", None, "extract_container"),
    ),
    (
        re.compile(r"^sudo\s+docker\s+stop\s+(\S+)$"),
        CapabilityMapping("docker.stop", None, "extract_container"),
    ),
    (
        re.compile(r"^docker\s+system\s+prune\s+(?:-a\s+)?(?:-f\s+)?$"),
        CapabilityMapping("docker.prune", {"all_images": True, "force": True}, None),
    ),
    (
        re.compile(r"^docker\s+image\s+prune\s+(?:-a\s+)?(?:-f\s+)?$"),
        CapabilityMapping("docker.prune", {"all_images": True, "force": True}, None),
    ),
    (
        re.compile(r"^docker\s+ps(?:\s+-a)?$"),
        CapabilityMapping("docker.list", {"all": True}, None),
    ),
    (
        re.compile(r"^docker\s+inspect\s+(\S+)$"),
        CapabilityMapping("docker.inspect", None, "extract_container"),
    ),
    # WireGuard
    (
        re.compile(r"^sudo\s+wg-quick\s+down\s+(\S+)\s*&&\s*sudo\s+wg-quick\s+up\s+\1$"),
        CapabilityMapping("wireguard.restart", None, "extract_interface"),
    ),
    (
        re.compile(r"^wg\s+show(?:\s+(\S+))?$"),
        CapabilityMapping("wireguard.status", None, "extract_interface_optional"),
    ),
    (
        re.compile(r"^wg\s+genkey$"),
        CapabilityMapping("wireguard.generate-key", {}, None),
    ),
]


def extract_service(match: re.Match[str]) -> dict[str, Any]:
    """Extract service name from match."""
    service = match.group(1)
    # Remove .service suffix if present
    if service.endswith(".service"):
        service = service[:-8]
    return {"service": service}


def extract_service_legacy(match: re.Match[str]) -> dict[str, Any]:
    """Extract service name from legacy service command."""
    return {"service": match.group(1)}


def extract_packages(match: re.Match[str]) -> dict[str, Any]:
    """Extract package list from match."""
    pkg_str = match.group(1).strip()
    # Split on whitespace, filter empty
    packages = [p for p in pkg_str.split() if p and not p.startswith("-")]
    return {"packages": packages}


def extract_container(match: re.Match[str]) -> dict[str, Any]:
    """Extract container ID/name from match."""
    return {"container": match.group(1)}


def extract_interface(match: re.Match[str]) -> dict[str, Any]:
    """Extract WireGuard interface from match."""
    return {"interface": match.group(1)}


def extract_interface_optional(match: re.Match[str]) -> dict[str, Any]:
    """Extract optional WireGuard interface from match."""
    interface = match.group(1) if match.lastindex and match.group(1) else None
    return {"interface": interface}


# Extraction function registry
EXTRACT_FUNCTIONS = {
    "extract_service": extract_service,
    "extract_service_legacy": extract_service_legacy,
    "extract_packages": extract_packages,
    "extract_container": extract_container,
    "extract_interface": extract_interface,
    "extract_interface_optional": extract_interface_optional,
}


@dataclass
class MappingResult:
    """Result of command-to-capability mapping."""

    success: bool
    """Whether mapping was successful"""

    capability: str | None
    """Capability name if mapped"""

    capability_input: dict[str, Any] | None
    """Input parameters for capability"""

    original_command: str
    """The original command"""

    unmapped_reason: str | None
    """Reason mapping failed, if applicable"""


def map_command_to_capability(command: str) -> MappingResult:
    """Map a shell command to a capability call.

    Args:
        command: The shell command to map.

    Returns:
        MappingResult indicating success/failure and capability details.
    """
    command = command.strip()

    for pattern, mapping in COMMAND_PATTERNS:
        match = pattern.match(command)
        if match:
            # Build input
            if mapping.input_builder is not None:
                cap_input = dict(mapping.input_builder)
            elif mapping.extract_fn and mapping.extract_fn in EXTRACT_FUNCTIONS:
                try:
                    cap_input = EXTRACT_FUNCTIONS[mapping.extract_fn](match)
                except Exception as e:
                    logger.debug(f"Failed to extract input for {command}: {e}")
                    return MappingResult(
                        success=False,
                        capability=None,
                        capability_input=None,
                        original_command=command,
                        unmapped_reason=f"Input extraction failed: {e}",
                    )
            else:
                cap_input = {}

            logger.debug(f"Mapped '{command}' to capability {mapping.capability}")
            return MappingResult(
                success=True,
                capability=mapping.capability,
                capability_input=cap_input,
                original_command=command,
                unmapped_reason=None,
            )

    # No mapping found
    return MappingResult(
        success=False,
        capability=None,
        capability_input=None,
        original_command=command,
        unmapped_reason="No matching capability pattern",
    )


def can_map_command(command: str) -> bool:
    """Check if a command can be mapped to a capability.

    Args:
        command: The command to check.

    Returns:
        True if command can be mapped.
    """
    return map_command_to_capability(command).success


def get_supported_patterns() -> list[str]:
    """Get list of supported command patterns (for documentation).

    Returns:
        List of pattern descriptions.
    """
    descriptions = []
    for pattern, mapping in COMMAND_PATTERNS:
        descriptions.append(f"{pattern.pattern} -> {mapping.capability}")
    return descriptions
