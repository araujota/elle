"""Role-based access control for ELLE Mobile Gateway.

This module enforces role restrictions on mobile requests:
- mobile_readonly: Can only access read-only operations
- mobile_operator: Can execute safe operations

Roles control:
1. Which models can be used (elle.readonly vs elle)
2. Which capabilities can be executed
"""

import logging
from typing import Any

from elle.mobile.auth import MobileAuthContext
from elle.mobile.models import MobileRole

logger = logging.getLogger(__name__)


# Model restrictions by role
# Maps role -> set of allowed model names/patterns
ROLE_ALLOWED_MODELS: dict[MobileRole, set[str]] = {
    MobileRole.MOBILE_READONLY: {
        "elle.readonly",
        "elle-readonly",
    },
    MobileRole.MOBILE_OPERATOR: {
        "elle.readonly",
        "elle-readonly",
        "elle",
    },
}


# High-risk capabilities blocked for all mobile roles
# These require local CLI interaction for safety
HIGH_RISK_CAPABILITIES: set[str] = {
    "file.delete",
    "file.write",
    "config.edit",
    "service.stop",
    "service.disable",
    "package.remove",
    "auth.modify",
    "system.shutdown",
    "system.reboot",
}


# Capabilities allowed for operator role
# readonly role cannot execute any capabilities
OPERATOR_ALLOWED_CAPABILITIES: set[str] = {
    "service.status",
    "service.start",
    "service.restart",
    "package.info",
    "package.list",
    "network.status",
    "disk.status",
    "memory.status",
    "process.list",
    "docker.status",
    "docker.logs",
}


class RoleViolationError(Exception):
    """Role restrictions violated."""

    def __init__(self, message: str, role: MobileRole):
        super().__init__(message)
        self.role = role


class RoleEnforcer:
    """Enforces role-based access control on requests.

    Filters and validates requests based on the device's effective role.
    """

    def filter_request(
        self, auth: MobileAuthContext, request_body: dict[str, Any]
    ) -> dict[str, Any]:
        """Filter and validate a chat completion request.

        Modifies the request to enforce role restrictions, particularly
        downgrading the model if needed.

        Args:
            auth: Authentication context with effective role.
            request_body: Original request body.

        Returns:
            Modified request body with role restrictions applied.

        Raises:
            RoleViolationError: If request cannot be allowed.
        """
        role = auth.effective_role
        allowed_models = ROLE_ALLOWED_MODELS.get(role, set())

        # Check requested model
        requested_model = request_body.get("model", "")

        if requested_model not in allowed_models:
            # Downgrade to readonly model
            if role == MobileRole.MOBILE_READONLY:
                logger.info(
                    "Device %s requested model %s, downgrading to elle.readonly",
                    auth.device_id,
                    requested_model,
                )
                request_body = {**request_body, "model": "elle.readonly"}
            elif role == MobileRole.MOBILE_OPERATOR:
                # Operator can use 'elle' model, downgrade if not in allowed
                if requested_model not in allowed_models:
                    logger.info(
                        "Device %s requested model %s, downgrading to elle",
                        auth.device_id,
                        requested_model,
                    )
                    request_body = {**request_body, "model": "elle"}

        return request_body

    def can_execute_capability(
        self, auth: MobileAuthContext, capability_name: str
    ) -> bool:
        """Check if the effective role allows executing a capability.

        Args:
            auth: Authentication context.
            capability_name: Name of the capability to execute.

        Returns:
            True if capability can be executed.
        """
        role = auth.effective_role

        # High-risk capabilities blocked for everyone
        if capability_name in HIGH_RISK_CAPABILITIES:
            logger.warning(
                "Device %s attempted high-risk capability %s",
                auth.device_id,
                capability_name,
            )
            return False

        # Readonly cannot execute any capabilities
        if role == MobileRole.MOBILE_READONLY:
            return False

        # Operator can execute allowed capabilities
        if role == MobileRole.MOBILE_OPERATOR:
            return capability_name in OPERATOR_ALLOWED_CAPABILITIES

        return False

    def get_execution_mode(self, auth: MobileAuthContext) -> str:
        """Get the execution mode for a request.

        Args:
            auth: Authentication context.

        Returns:
            Execution mode string ("readonly" or "execute").
        """
        if auth.effective_role == MobileRole.MOBILE_READONLY:
            return "readonly"
        return "execute"

    def validate_tool_call(
        self, auth: MobileAuthContext, tool_name: str, tool_args: dict[str, Any]
    ) -> bool:
        """Validate a tool call is allowed for the role.

        Args:
            auth: Authentication context.
            tool_name: Name of the tool being called.
            tool_args: Arguments to the tool.

        Returns:
            True if tool call is allowed.
        """
        # Map tool names to capability checks
        if tool_name.startswith("capability."):
            capability_name = tool_name[11:]  # Remove "capability." prefix
            return self.can_execute_capability(auth, capability_name)

        # Readonly role cannot use any execution tools
        if auth.effective_role == MobileRole.MOBILE_READONLY:
            # Allow only read/query tools
            readonly_tools = {
                "man_search",
                "incident_search",
                "status",
                "logs",
                "events",
            }
            return tool_name in readonly_tools

        return True


def get_role_description(role: MobileRole) -> str:
    """Get a human-readable description of a role.

    Args:
        role: The role to describe.

    Returns:
        Description string.
    """
    descriptions = {
        MobileRole.MOBILE_READONLY: (
            "Read-only access. Can query status, search documentation, "
            "and view logs, but cannot execute any system changes."
        ),
        MobileRole.MOBILE_OPERATOR: (
            "Operator access. Can query status and execute safe operations "
            "like restarting services, but cannot perform high-risk actions "
            "like file deletion or config editing."
        ),
    }
    return descriptions.get(role, "Unknown role")


def get_allowed_operations(role: MobileRole) -> list[str]:
    """Get list of allowed operations for a role.

    Args:
        role: The role to query.

    Returns:
        List of operation descriptions.
    """
    if role == MobileRole.MOBILE_READONLY:
        return [
            "View system status",
            "Search man pages",
            "Search incidents",
            "View logs and events",
            "Ask questions about the system",
        ]
    elif role == MobileRole.MOBILE_OPERATOR:
        return [
            "All readonly operations",
            "Start/restart services",
            "View package information",
            "View Docker container status",
            "View process lists",
        ]
    return []


def get_blocked_operations(role: MobileRole) -> list[str]:
    """Get list of blocked operations for a role.

    Args:
        role: The role to query.

    Returns:
        List of blocked operation descriptions.
    """
    if role == MobileRole.MOBILE_READONLY:
        return [
            "Execute any system commands",
            "Modify files or configurations",
            "Start/stop services",
            "Install/remove packages",
        ]
    elif role == MobileRole.MOBILE_OPERATOR:
        return [
            "Delete files",
            "Edit configurations",
            "Stop/disable services",
            "Remove packages",
            "Shutdown/reboot system",
        ]
    return []
