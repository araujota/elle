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
- auth: Authentication (session tokens, mobile certificates)
"""

# ruff: noqa: E402  # Imports after function definitions for re-exporting

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from elle.capabilities.protocol import Capability


def get_core_capabilities() -> list[type[Capability[Any, Any]]]:
    """Get all core capability classes.

    Returns:
        List of capability classes to register.
    """
    capabilities: list[type[Capability[Any, Any]]] = []

    # Service capabilities
    try:
        from elle.capabilities.core.service import SERVICE_CAPABILITIES

        capabilities.extend(SERVICE_CAPABILITIES)
    except ImportError:
        pass

    # File capabilities
    try:
        from elle.capabilities.core.file import FILE_CAPABILITIES

        capabilities.extend(FILE_CAPABILITIES)  # type: ignore[arg-type]
    except ImportError:
        pass

    # Config capabilities
    try:
        from elle.capabilities.core.config import CONFIG_CAPABILITIES

        capabilities.extend(CONFIG_CAPABILITIES)  # type: ignore[arg-type]
    except ImportError:
        pass

    # Package capabilities
    try:
        from elle.capabilities.core.package import PACKAGE_CAPABILITIES

        capabilities.extend(PACKAGE_CAPABILITIES)  # type: ignore[arg-type]
    except ImportError:
        pass

    # Docker capabilities
    try:
        from elle.capabilities.core.docker import DOCKER_CAPABILITIES

        capabilities.extend(DOCKER_CAPABILITIES)  # type: ignore[arg-type]
    except ImportError:
        pass

    # Notification capabilities
    try:
        from elle.capabilities.core.notify import NOTIFY_CAPABILITIES

        capabilities.extend(NOTIFY_CAPABILITIES)  # type: ignore[arg-type]
    except ImportError:
        pass

    # Network capabilities
    try:
        from elle.capabilities.core.network import NETWORK_CAPABILITIES

        capabilities.extend(NETWORK_CAPABILITIES)  # type: ignore[arg-type]
    except ImportError:
        pass

    # Incident capabilities
    try:
        from elle.capabilities.core.incident import INCIDENT_CAPABILITIES

        capabilities.extend(INCIDENT_CAPABILITIES)  # type: ignore[arg-type]
    except ImportError:
        pass

    # Auth capabilities
    try:
        from elle.capabilities.core.auth import AUTH_CAPABILITIES

        capabilities.extend(AUTH_CAPABILITIES)
    except ImportError:
        pass

    # File edit capability (tier stack)
    try:
        from elle.capabilities.core.file_edit import FILE_EDIT_CAPABILITIES

        capabilities.extend(FILE_EDIT_CAPABILITIES)
    except ImportError:
        pass

    return capabilities


# Re-export commonly used classes
# File edit capability (tier stack)
# Auth capabilities
from elle.capabilities.core.auth import (
    AUTH_CAPABILITIES,
    MobileCertsCapability,
    MobileCertsInput,
    MobileCertsOutput,
    SessionTokenCapability,
    SessionTokenInput,
    SessionTokenOutput,
)
from elle.capabilities.core.config import (
    ConfigEditCapability,
    ConfigEditInput,
    ConfigEditOutput,
    ConfigOperation,
    ConfigPreviewCapability,
    ConfigPreviewInput,
    ConfigPreviewOutput,
)

# Docker capabilities
from elle.capabilities.core.docker import (
    DOCKER_CAPABILITIES,
    DockerConfigureEnvCapability,
    DockerConfigureEnvInput,
    DockerConfigureEnvOutput,
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
    EnvVarConfig,
)

# File diff/snapshot capabilities
from elle.capabilities.core.file import (
    FileCopyCapability,
    FileCopyInput,
    FileCopyOutput,
    FileDeleteCapability,
    FileDeleteInput,
    FileDeleteOutput,
    FileDiffCapability,
    FileDiffInput,
    FileDiffOutput,
    FileReadCapability,
    FileReadInput,
    FileReadOutput,
    FileWatchSnapshotCapability,
    FileWatchSnapshotInput,
    FileWatchSnapshotOutput,
    FileWriteCapability,
    FileWriteInput,
    FileWriteOutput,
)
from elle.capabilities.core.file_edit import (
    FILE_EDIT_CAPABILITIES,
    FileEditCapability,
    FileEditInput,
    FileEditOperation,
    FileEditOutput,
    TierEscalationRecord,
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

# Network capabilities
from elle.capabilities.core.network import (
    NETWORK_CAPABILITIES,
    NetworkDiagnoseCapability,
    NetworkDiagnoseInput,
    NetworkDiagnoseOutput,
    NetworkListenersCapability,
    NetworkListenersInput,
    NetworkListenersOutput,
    WireGuardGenerateKeyCapability,
    WireGuardGenerateKeyInput,
    WireGuardGenerateKeyOutput,
    WireGuardRestartCapability,
    WireGuardRestartInput,
    WireGuardRestartOutput,
    WireGuardRotateKeysCapability,
    WireGuardRotateKeysInput,
    WireGuardRotateKeysOutput,
    WireGuardStatusCapability,
    WireGuardStatusInput,
    WireGuardStatusOutput,
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
    # File Edit (Tier Stack)
    "FILE_EDIT_CAPABILITIES",
    "FileEditCapability",
    "FileEditInput",
    "FileEditOperation",
    "FileEditOutput",
    "TierEscalationRecord",
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
    "DockerConfigureEnvCapability",
    "DockerConfigureEnvInput",
    "DockerConfigureEnvOutput",
    "EnvVarConfig",
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
    "WireGuardGenerateKeyCapability",
    "WireGuardGenerateKeyInput",
    "WireGuardGenerateKeyOutput",
    "WireGuardRotateKeysCapability",
    "WireGuardRotateKeysInput",
    "WireGuardRotateKeysOutput",
    "NetworkListenersCapability",
    "NetworkListenersInput",
    "NetworkListenersOutput",
    "NetworkDiagnoseCapability",
    "NetworkDiagnoseInput",
    "NetworkDiagnoseOutput",
    # Incident
    "INCIDENT_CAPABILITIES",
    "IncidentCreateCapability",
    "IncidentCreateInput",
    "IncidentCreateOutput",
    "IncidentAttachCapability",
    "IncidentAttachInput",
    "IncidentAttachOutput",
    # Auth
    "AUTH_CAPABILITIES",
    "SessionTokenCapability",
    "SessionTokenInput",
    "SessionTokenOutput",
    "MobileCertsCapability",
    "MobileCertsInput",
    "MobileCertsOutput",
]
