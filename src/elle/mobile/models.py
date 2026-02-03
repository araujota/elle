"""Pydantic models for ELLE Mobile Gateway.

This module defines the core data models used throughout the mobile gateway:
- PairedDevice: Authenticated mobile device
- Elevation: Temporary privilege promotion
- PairingToken: One-time pairing token
- QRPayload: Data encoded in pairing QR code
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from elle.common.pydantic_compat import safe_model_dump_json


class MobileRole(str, Enum):
    """Access roles for mobile devices.

    Roles determine what operations a mobile device can perform:
    - MOBILE_READONLY: Read-only access (status, logs, queries)
    - MOBILE_OPERATOR: Can execute safe operations (low-risk capabilities)
    """

    MOBILE_READONLY = "mobile_readonly"
    MOBILE_OPERATOR = "mobile_operator"


class DeviceStatus(str, Enum):
    """Status of a paired device."""

    PENDING = "pending"  # Pairing initiated but not complete
    PAIRED = "paired"  # Successfully paired and active
    REVOKED = "revoked"  # Access revoked


class PairedDevice(BaseModel):
    """A mobile device that has been paired with ELLE.

    Attributes:
        device_id: Unique identifier (UUID)
        name: Human-readable device name
        role: Base access role
        status: Current pairing status
        cert_fingerprint: SHA-256 fingerprint of client certificate
        paired_at: When the device was paired
        last_seen_at: Last successful request timestamp
        created_at: When the pairing was initiated
    """

    device_id: str = Field(..., description="Unique device identifier (UUID)")
    name: str = Field(..., description="Human-readable device name")
    role: MobileRole = Field(default=MobileRole.MOBILE_READONLY, description="Base access role")
    status: DeviceStatus = Field(default=DeviceStatus.PENDING, description="Pairing status")
    cert_fingerprint: str = Field(..., description="SHA-256 fingerprint of client certificate")
    paired_at: datetime | None = Field(default=None, description="Timestamp when pairing completed")
    last_seen_at: datetime | None = Field(default=None, description="Last successful request timestamp")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="When pairing was initiated"
    )

    model_config = {"frozen": True}


class Elevation(BaseModel):
    """Temporary privilege elevation for a device.

    Elevations allow a device to temporarily operate at a higher
    privilege level than its base role.

    Attributes:
        device_id: The device receiving elevation
        elevated_role: The elevated role level
        expires_at: When the elevation expires
        granted_by: How elevation was granted (e.g., "cli", "admin")
        created_at: When elevation was granted
    """

    device_id: str = Field(..., description="Device receiving elevation")
    elevated_role: MobileRole = Field(..., description="Elevated role level")
    expires_at: datetime = Field(..., description="Elevation expiration time")
    granted_by: str = Field(..., description="Source of elevation grant")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="When elevation was granted"
    )

    model_config = {"frozen": True}

    def is_active(self) -> bool:
        """Check if this elevation is still active."""
        return datetime.now(timezone.utc) < self.expires_at


class PairingToken(BaseModel):
    """One-time token for device pairing.

    Pairing tokens are short-lived (90s default) and single-use.
    They are embedded in QR codes for mobile device scanning.

    Attributes:
        token: 64-byte hex string
        expires_at: Token expiration (90s TTL)
        role: Role to assign to the paired device
        used: Whether token has been consumed
        created_at: When token was generated
    """

    token: str = Field(..., description="64-byte hex token")
    expires_at: datetime = Field(..., description="Token expiration time")
    role: MobileRole = Field(default=MobileRole.MOBILE_READONLY, description="Role for paired device")
    used: bool = Field(default=False, description="Whether token has been used")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="When token was created"
    )

    model_config = {"frozen": True}

    def is_valid(self) -> bool:
        """Check if this token is still valid (not expired or used)."""
        return not self.used and datetime.now(timezone.utc) < self.expires_at


class QRPayload(BaseModel):
    """Data encoded in the pairing QR code.

    This payload is JSON-encoded and displayed as a QR code.
    The mobile app scans this to initiate pairing.

    Attributes:
        host: Gateway hostname or IP
        port: Gateway port
        token: Pairing token
        server_fingerprint: SHA-256 of server certificate for pinning
        version: Protocol version for compatibility
    """

    host: str = Field(..., description="Gateway hostname or IP address")
    port: int = Field(..., description="Gateway port number")
    token: str = Field(..., description="Pairing token")
    server_fingerprint: str = Field(..., description="SHA-256 fingerprint of server certificate")
    version: int = Field(default=1, description="Protocol version")

    model_config = {"frozen": True}

    def to_json(self) -> str:
        """Serialize to JSON for QR encoding."""
        return safe_model_dump_json(self)


class MobileAuditAction(str, Enum):
    """Types of auditable mobile actions."""

    PAIR = "pair"
    REQUEST = "request"
    ELEVATE = "elevate"
    REVOKE = "revoke"
    GATEWAY_START = "gateway_start"
    GATEWAY_STOP = "gateway_stop"


class MobileAuditEntry(BaseModel):
    """Audit log entry for mobile gateway operations.

    All mobile gateway operations are logged for security auditing.

    Attributes:
        timestamp: When the action occurred
        device_id: Device that performed the action (if applicable)
        device_name: Human-readable device name
        action: Type of action
        endpoint: API endpoint accessed (for requests)
        execution_mode: Mode used (readonly/execute)
        success: Whether the action succeeded
        error: Error message if failed
        ip_address: Client IP address
    """

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="When action occurred")
    device_id: str | None = Field(default=None, description="Device identifier")
    device_name: str | None = Field(default=None, description="Device name")
    action: MobileAuditAction = Field(..., description="Type of action")
    endpoint: str | None = Field(default=None, description="API endpoint")
    execution_mode: str | None = Field(default=None, description="Execution mode")
    success: bool = Field(..., description="Whether action succeeded")
    error: str | None = Field(default=None, description="Error message if failed")
    ip_address: str = Field(..., description="Client IP address")

    model_config = {"frozen": True}


class GatewayStatus(BaseModel):
    """Current status of the mobile gateway.

    Attributes:
        running: Whether gateway is running
        pid: Process ID if running
        bind_host: Bound host address
        bind_port: Bound port number
        paired_devices: Count of paired devices
        active_elevations: Count of active elevations
        uptime_seconds: Seconds since gateway started
        started_at: When gateway was started
    """

    running: bool = Field(..., description="Whether gateway is running")
    pid: int | None = Field(default=None, description="Gateway process ID")
    bind_host: str | None = Field(default=None, description="Bound host")
    bind_port: int | None = Field(default=None, description="Bound port")
    paired_devices: int = Field(default=0, description="Count of paired devices")
    active_elevations: int = Field(default=0, description="Count of active elevations")
    uptime_seconds: float | None = Field(default=None, description="Gateway uptime")
    started_at: datetime | None = Field(default=None, description="Gateway start time")

    model_config = {"frozen": True}
