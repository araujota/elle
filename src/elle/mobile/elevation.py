"""Elevation management for ELLE Mobile Gateway.

Elevations allow temporary privilege promotion for mobile devices.
This enables a device with readonly access to temporarily gain
operator privileges when approved via CLI.

Elevations are:
- Time-limited (default 10min, max 1hr)
- Granted via local CLI only
- Logged for audit
- Auto-expire (no manual revocation needed)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from elle.mobile.config import MobileGatewayConfig, get_mobile_config
from elle.mobile.models import DeviceStatus, Elevation, MobileRole, PairedDevice
from elle.mobile.store import MobileStore

logger = logging.getLogger(__name__)


class ElevationError(Exception):
    """Elevation operation failed."""

    pass


class ElevationManager:
    """Manages temporary privilege elevations for devices.

    Elevations allow devices to temporarily operate at a higher
    privilege level than their base role.
    """

    def __init__(
        self,
        config: MobileGatewayConfig | None = None,
        store: MobileStore | None = None,
    ):
        """Initialize elevation manager.

        Args:
            config: Mobile gateway configuration.
            store: Mobile store instance.
        """
        self.config = config or get_mobile_config()
        self.store = store or MobileStore(self.config)

    def grant_elevation(
        self,
        device_id: str,
        target_role: MobileRole,
        ttl_seconds: int | None = None,
        granted_by: str = "cli",
    ) -> Elevation:
        """Grant temporary elevation to a device.

        Args:
            device_id: Device to elevate.
            target_role: Role to elevate to.
            ttl_seconds: How long elevation lasts. Uses config default if None.
            granted_by: Source of the grant (for audit).

        Returns:
            The created Elevation.

        Raises:
            ElevationError: If elevation cannot be granted.
        """
        # Validate TTL
        if ttl_seconds is None:
            ttl_seconds = self.config.default_elevation_ttl_seconds

        if ttl_seconds <= 0:
            raise ElevationError("TTL must be positive")

        if ttl_seconds > self.config.max_elevation_ttl_seconds:
            raise ElevationError(f"TTL exceeds maximum ({self.config.max_elevation_ttl_seconds}s)")

        # Get device
        device = self.store.get_device(device_id)
        if device is None:
            raise ElevationError(f"Device {device_id} not found")

        if device.status != DeviceStatus.PAIRED:
            raise ElevationError(f"Device status invalid: {device.status}")

        # Validate role elevation makes sense
        if target_role == device.role:
            raise ElevationError(f"Device already has role {target_role.value}")

        # Only allow elevation, not demotion
        role_order = [MobileRole.MOBILE_READONLY, MobileRole.MOBILE_OPERATOR]
        if role_order.index(target_role) <= role_order.index(device.role):
            raise ElevationError(f"Cannot elevate from {device.role.value} to {target_role.value}")

        # Revoke any existing elevation
        self.store.revoke_elevation(device_id)

        # Create new elevation
        now = datetime.now(timezone.utc)
        elevation = Elevation(
            device_id=device_id,
            elevated_role=target_role,
            expires_at=now + timedelta(seconds=ttl_seconds),
            granted_by=granted_by,
            created_at=now,
        )

        self.store.create_elevation(elevation)

        logger.info(
            "Granted %s elevation to device %s (%s) for %ds by %s",
            target_role.value,
            device_id,
            device.name,
            ttl_seconds,
            granted_by,
        )

        return elevation

    def revoke_elevation(self, device_id: str) -> bool:
        """Revoke all elevations for a device.

        Args:
            device_id: Device whose elevations to revoke.

        Returns:
            True if any elevations were revoked.
        """
        revoked = self.store.revoke_elevation(device_id)
        if revoked:
            logger.info("Revoked elevation for device %s", device_id)
        return revoked

    def get_effective_role(self, device: PairedDevice) -> MobileRole:
        """Get the effective role for a device.

        Returns elevated role if active, otherwise base role.

        Args:
            device: Device to check.

        Returns:
            Effective role (elevated if active, else base).
        """
        elevation = self.store.get_active_elevation(device.device_id)
        if elevation and elevation.is_active():
            return elevation.elevated_role
        return device.role

    def get_elevation_status(self, device_id: str) -> dict[str, Any]:
        """Get elevation status for a device.

        Args:
            device_id: Device to check.

        Returns:
            Dictionary with elevation details.
        """
        device = self.store.get_device(device_id)
        if device is None:
            return {"error": "Device not found"}

        elevation = self.store.get_active_elevation(device_id)

        result: dict[str, Any] = {
            "device_id": device_id,
            "device_name": device.name,
            "base_role": device.role.value,
            "effective_role": device.role.value,
            "elevated": False,
        }

        if elevation and elevation.is_active():
            remaining = (elevation.expires_at - datetime.now(timezone.utc)).total_seconds()
            result.update(
                {
                    "elevated": True,
                    "effective_role": elevation.elevated_role.value,
                    "elevated_role": elevation.elevated_role.value,
                    "expires_at": elevation.expires_at.isoformat(),
                    "remaining_seconds": max(0, int(remaining)),
                    "granted_by": elevation.granted_by,
                }
            )

        return result

    def list_elevated_devices(self) -> list[dict[str, Any]]:
        """List all devices with active elevations.

        Returns:
            List of elevation status dictionaries.
        """
        elevations = self.store.list_active_elevations()
        result = []

        for elevation in elevations:
            device = self.store.get_device(elevation.device_id)
            if device:
                remaining = (elevation.expires_at - datetime.now(timezone.utc)).total_seconds()
                result.append(
                    {
                        "device_id": device.device_id,
                        "device_name": device.name,
                        "base_role": device.role.value,
                        "elevated_role": elevation.elevated_role.value,
                        "expires_at": elevation.expires_at.isoformat(),
                        "remaining_seconds": max(0, int(remaining)),
                        "granted_by": elevation.granted_by,
                    }
                )

        return result

    def cleanup_expired(self) -> int:
        """Clean up expired elevations.

        Returns:
            Number of elevations cleaned up.
        """
        return self.store.cleanup_expired_elevations()


def parse_ttl(ttl_str: str) -> int:
    """Parse a TTL string into seconds.

    Supports formats like:
    - "10m" -> 600 seconds
    - "1h" -> 3600 seconds
    - "30s" -> 30 seconds
    - "600" -> 600 seconds (raw number)

    Args:
        ttl_str: TTL string to parse.

    Returns:
        TTL in seconds.

    Raises:
        ValueError: If format is invalid.
    """
    ttl_str = ttl_str.strip().lower()

    if ttl_str.endswith("s"):
        return int(ttl_str[:-1])
    elif ttl_str.endswith("m"):
        return int(ttl_str[:-1]) * 60
    elif ttl_str.endswith("h"):
        return int(ttl_str[:-1]) * 3600
    else:
        return int(ttl_str)


def format_ttl(seconds: int) -> str:
    """Format seconds as a human-readable TTL.

    Args:
        seconds: TTL in seconds.

    Returns:
        Formatted string (e.g., "10m", "1h 30m").
    """
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if minutes:
            return f"{hours}h {minutes}m"
        return f"{hours}h"
