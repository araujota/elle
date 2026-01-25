"""Core capabilities for ELLE.

Provides built-in, fully-trusted capabilities for common system operations.

Capabilities are organized by domain:
- service: Systemd service management
- file: File operations
- config: Configuration editing
- package: Package management
- docker: Container operations
- notification: Desktop/push notifications
- network: Network and WireGuard operations
- incident: Incident management
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

    # Docker capabilities
    try:
        from elle.capabilities.core.docker import DOCKER_CAPABILITIES

        capabilities.extend(DOCKER_CAPABILITIES)
    except ImportError:
        pass

    # Notification capabilities
    try:
        from elle.capabilities.core.notify import NOTIFY_CAPABILITIES

        capabilities.extend(NOTIFY_CAPABILITIES)
    except ImportError:
        pass

    # Network capabilities
    try:
        from elle.capabilities.core.network import NETWORK_CAPABILITIES

        capabilities.extend(NETWORK_CAPABILITIES)
    except ImportError:
        pass

    # Incident capabilities
    try:
        from elle.capabilities.core.incident import INCIDENT_CAPABILITIES

        capabilities.extend(INCIDENT_CAPABILITIES)
    except ImportError:
        pass

    # GUI capabilities
    try:
        from elle.capabilities.core.gui import GUI_CAPABILITIES

        capabilities.extend(GUI_CAPABILITIES)
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

# Docker capabilities
from elle.capabilities.core.docker import (
    DOCKER_CAPABILITIES,
    DockerInspectCapability,
    DockerInspectInput,
    DockerInspectOutput,
    DockerListCapability,
    DockerListInput,
    DockerListOutput,
    DockerPruneCapability,
    DockerPruneInput,
    DockerPruneOutput,
    DockerRollbackCapability,
    DockerRollbackInput,
    DockerRollbackOutput,
    DockerStopCapability,
    DockerStopInput,
    DockerStopOutput,
)

# Notification capabilities
from elle.capabilities.core.notify import (
    NOTIFY_CAPABILITIES,
    NotifyAlertCapability,
    NotifyAlertInput,
    NotifyAlertOutput,
    NotifySendCapability,
    NotifySendInput,
    NotifySendOutput,
)

# Network capabilities
from elle.capabilities.core.network import (
    NETWORK_CAPABILITIES,
    NetworkListenersCapability,
    NetworkListenersInput,
    NetworkListenersOutput,
    WireGuardRestartCapability,
    WireGuardRestartInput,
    WireGuardRestartOutput,
    WireGuardStatusCapability,
    WireGuardStatusInput,
    WireGuardStatusOutput,
)

# Incident capabilities
from elle.capabilities.core.incident import (
    INCIDENT_CAPABILITIES,
    IncidentAttachCapability,
    IncidentAttachInput,
    IncidentAttachOutput,
    IncidentCreateCapability,
    IncidentCreateInput,
    IncidentCreateOutput,
)

# GUI capabilities
from elle.capabilities.core.gui import (
    GUI_CAPABILITIES,
    GuiLearnCapability,
    GuiLearnInput,
    GuiLearnOutput,
    GuiClickCapability,
    GuiClickInput,
    GuiClickOutput,
    GuiTypeCapability,
    GuiTypeInput,
    GuiTypeOutput,
    GuiNavigateCapability,
    GuiNavigateInput,
    GuiNavigateOutput,
    GuiExecuteTaskCapability,
    GuiExecuteTaskInput,
    GuiExecuteTaskOutput,
)

# File diff/snapshot capabilities
from elle.capabilities.core.file import (
    FileDiffCapability,
    FileDiffInput,
    FileDiffOutput,
    FileWatchSnapshotCapability,
    FileWatchSnapshotInput,
    FileWatchSnapshotOutput,
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
    "FileDiffCapability",
    "FileDiffInput",
    "FileDiffOutput",
    "FileWatchSnapshotCapability",
    "FileWatchSnapshotInput",
    "FileWatchSnapshotOutput",
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
    # Docker
    "DOCKER_CAPABILITIES",
    "DockerPruneCapability",
    "DockerPruneInput",
    "DockerPruneOutput",
    "DockerStopCapability",
    "DockerStopInput",
    "DockerStopOutput",
    "DockerInspectCapability",
    "DockerInspectInput",
    "DockerInspectOutput",
    "DockerRollbackCapability",
    "DockerRollbackInput",
    "DockerRollbackOutput",
    "DockerListCapability",
    "DockerListInput",
    "DockerListOutput",
    # Notification
    "NOTIFY_CAPABILITIES",
    "NotifySendCapability",
    "NotifySendInput",
    "NotifySendOutput",
    "NotifyAlertCapability",
    "NotifyAlertInput",
    "NotifyAlertOutput",
    # Network
    "NETWORK_CAPABILITIES",
    "WireGuardRestartCapability",
    "WireGuardRestartInput",
    "WireGuardRestartOutput",
    "WireGuardStatusCapability",
    "WireGuardStatusInput",
    "WireGuardStatusOutput",
    "NetworkListenersCapability",
    "NetworkListenersInput",
    "NetworkListenersOutput",
    # Incident
    "INCIDENT_CAPABILITIES",
    "IncidentCreateCapability",
    "IncidentCreateInput",
    "IncidentCreateOutput",
    "IncidentAttachCapability",
    "IncidentAttachInput",
    "IncidentAttachOutput",
    # GUI
    "GUI_CAPABILITIES",
    "GuiLearnCapability",
    "GuiLearnInput",
    "GuiLearnOutput",
    "GuiClickCapability",
    "GuiClickInput",
    "GuiClickOutput",
    "GuiTypeCapability",
    "GuiTypeInput",
    "GuiTypeOutput",
    "GuiNavigateCapability",
    "GuiNavigateInput",
    "GuiNavigateOutput",
    "GuiExecuteTaskCapability",
    "GuiExecuteTaskInput",
    "GuiExecuteTaskOutput",
]
