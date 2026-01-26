"""ELLE Mobile Gateway - Secure remote access for mobile devices.

This module provides a secure gateway enabling mobile devices to access
ELLE's OpenAI-compatible API via QR pairing, mTLS authentication, and
role-based access control.

Architecture:
    Mobile Device → mTLS + Device Credential → Mobile Gateway → Internal API

Key components:
    - PairedDevice: Represents an authenticated mobile device
    - MobileRole: Access level (readonly/operator)
    - MobileGateway: FastAPI application handling requests
    - MobileStore: SQLite storage for devices and elevations
    - ConfigManager: Remote configuration read/write
"""

from elle.mobile.config import MobileGatewayConfig
from elle.mobile.config_manager import ConfigError, ConfigManager, get_config_manager
from elle.mobile.models import (
    Elevation,
    MobileRole,
    PairedDevice,
    PairingToken,
    QRPayload,
)

__all__ = [
    "ConfigError",
    "ConfigManager",
    "Elevation",
    "MobileGatewayConfig",
    "MobileRole",
    "PairedDevice",
    "PairingToken",
    "QRPayload",
    "get_config_manager",
]
