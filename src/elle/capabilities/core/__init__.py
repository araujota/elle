"""Core capabilities for ELLE.

Provides built-in, fully-trusted capabilities for common system operations.

Capabilities are organized by domain:
- service: Systemd service management
- file: File operations
- config: Configuration editing
- package: Package management
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from elle.capabilities.protocol import Capability


def get_core_capabilities() -> list[type["Capability"]]:
    """Get all core capability classes.

    Returns:
        List of capability classes to register.
    """
    capabilities: list[type["Capability"]] = []

    # Service capabilities
    try:
        from elle.capabilities.core.service import SERVICE_CAPABILITIES

        capabilities.extend(SERVICE_CAPABILITIES)
    except ImportError:
        pass

    # File capabilities
    try:
        from elle.capabilities.core.file import FILE_CAPABILITIES

        capabilities.extend(FILE_CAPABILITIES)
    except ImportError:
        pass

    # Config capabilities
    try:
        from elle.capabilities.core.config import CONFIG_CAPABILITIES

        capabilities.extend(CONFIG_CAPABILITIES)
    except ImportError:
        pass

    # Package capabilities
    try:
        from elle.capabilities.core.package import PACKAGE_CAPABILITIES

        capabilities.extend(PACKAGE_CAPABILITIES)
    except ImportError:
        pass

    return capabilities


# Re-export commonly used classes
from elle.capabilities.core.config import (
    ConfigEditCapability,
    ConfigEditInput,
    ConfigEditOutput,
    ConfigOperation,
    ConfigPreviewCapability,
    ConfigPreviewInput,
    ConfigPreviewOutput,
)
from elle.capabilities.core.file import (
    FileCopyCapability,
    FileCopyInput,
    FileCopyOutput,
    FileDeleteCapability,
    FileDeleteInput,
    FileDeleteOutput,
    FileReadCapability,
    FileReadInput,
    FileReadOutput,
    FileWriteCapability,
    FileWriteInput,
    FileWriteOutput,
)
from elle.capabilities.core.package import (
    PackageInfoCapability,
    PackageInfoOutput,
    PackageInput,
    PackageInstallCapability,
    PackageInstallOutput,
    PackageRemoveCapability,
    PackageRemoveOutput,
    PackageUpdateCapability,
    PackageUpdateOutput,
)
from elle.capabilities.core.service import (
    ServiceInput,
    ServiceOutput,
    ServiceRestartCapability,
    ServiceStartCapability,
    ServiceStatusCapability,
    ServiceStatusOutput,
    ServiceStopCapability,
)

__all__ = [
    # Factory
    "get_core_capabilities",
    # Service
    "ServiceInput",
    "ServiceOutput",
    "ServiceStatusOutput",
    "ServiceRestartCapability",
    "ServiceStartCapability",
    "ServiceStopCapability",
    "ServiceStatusCapability",
    # File
    "FileReadInput",
    "FileReadOutput",
    "FileWriteInput",
    "FileWriteOutput",
    "FileDeleteInput",
    "FileDeleteOutput",
    "FileCopyInput",
    "FileCopyOutput",
    "FileReadCapability",
    "FileWriteCapability",
    "FileDeleteCapability",
    "FileCopyCapability",
    # Config
    "ConfigOperation",
    "ConfigEditInput",
    "ConfigEditOutput",
    "ConfigPreviewInput",
    "ConfigPreviewOutput",
    "ConfigEditCapability",
    "ConfigPreviewCapability",
    # Package
    "PackageInput",
    "PackageInstallOutput",
    "PackageRemoveOutput",
    "PackageUpdateOutput",
    "PackageInfoOutput",
    "PackageInstallCapability",
    "PackageRemoveCapability",
    "PackageUpdateCapability",
    "PackageInfoCapability",
]
