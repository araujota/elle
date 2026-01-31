"""Tests for ELLE Mobile Gateway elevation management."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from elle.mobile.config import MobileGatewayConfig
from elle.mobile.elevation import (
    ElevationError,
    ElevationManager,
    format_ttl,
    parse_ttl,
)
from elle.mobile.models import DeviceStatus, MobileRole, PairedDevice
from elle.mobile.store import MobileStore


@pytest.fixture
def config():
    """Create test configuration."""
    return MobileGatewayConfig(
        enabled=True,
        default_elevation_ttl_seconds=600,
        max_elevation_ttl_seconds=3600,
    )


@pytest.fixture
def store(config):
    """Create test store with in-memory mock backend."""
    s = MagicMock(spec=MobileStore)
    s.config = config
    s._devices = {}
    s._elevations = {}

    def _create_device(device):
        s._devices[device.device_id] = device
        return device

    def _get_device(device_id):
        return s._devices.get(device_id)

    def _create_elevation(elevation):
        s._elevations[elevation.device_id] = elevation

    def _get_active_elevation(device_id):
        elev = s._elevations.get(device_id)
        if elev and elev.is_active():
            return elev
        return None

    def _revoke_elevation(device_id):
        if device_id in s._elevations:
            del s._elevations[device_id]
            return True
        return False

    def _list_active_elevations():
        return [e for e in s._elevations.values() if e.is_active()]

    s.create_device = MagicMock(side_effect=_create_device)
    s.get_device = MagicMock(side_effect=_get_device)
    s.create_elevation = MagicMock(side_effect=_create_elevation)
    s.get_active_elevation = MagicMock(side_effect=_get_active_elevation)
    s.revoke_elevation = MagicMock(side_effect=_revoke_elevation)
    s.list_active_elevations = MagicMock(side_effect=_list_active_elevations)

    return s


@pytest.fixture
def elevation_manager(config, store):
    """Create test elevation manager."""
    return ElevationManager(config, store)


@pytest.fixture
def paired_device(store):
    """Create a paired device for testing."""
    device = PairedDevice(
        device_id="test-device-1",
        name="Test Phone",
        role=MobileRole.MOBILE_READONLY,
        status=DeviceStatus.PAIRED,
        cert_fingerprint="abc123",
        paired_at=datetime.utcnow(),
    )
    return store.create_device(device)


class TestElevationManager:
    """Tests for ElevationManager."""

    def test_grant_elevation(self, elevation_manager, paired_device):
        """Test granting elevation."""
        elevation = elevation_manager.grant_elevation(
            paired_device.device_id,
            MobileRole.MOBILE_OPERATOR,
            ttl_seconds=600,
        )
        assert elevation.device_id == paired_device.device_id
        assert elevation.elevated_role == MobileRole.MOBILE_OPERATOR
        assert elevation.is_active()

    def test_grant_elevation_default_ttl(self, elevation_manager, paired_device):
        """Test granting elevation with default TTL."""
        elevation = elevation_manager.grant_elevation(
            paired_device.device_id,
            MobileRole.MOBILE_OPERATOR,
        )
        assert elevation.is_active()
        # Should use default TTL from config (600s)
        time_remaining = (elevation.expires_at - datetime.utcnow()).total_seconds()
        assert 590 < time_remaining <= 600

    def test_grant_elevation_exceeds_max_ttl(self, elevation_manager, paired_device):
        """Test granting elevation with TTL exceeding max."""
        with pytest.raises(ElevationError, match="exceeds maximum"):
            elevation_manager.grant_elevation(
                paired_device.device_id,
                MobileRole.MOBILE_OPERATOR,
                ttl_seconds=7200,  # 2 hours, max is 1 hour
            )

    def test_grant_elevation_nonexistent_device(self, elevation_manager):
        """Test granting elevation to nonexistent device."""
        with pytest.raises(ElevationError, match="not found"):
            elevation_manager.grant_elevation(
                "nonexistent",
                MobileRole.MOBILE_OPERATOR,
            )

    def test_grant_elevation_same_role(self, elevation_manager, paired_device):
        """Test granting elevation to same role fails."""
        with pytest.raises(ElevationError, match="already has role"):
            elevation_manager.grant_elevation(
                paired_device.device_id,
                MobileRole.MOBILE_READONLY,  # Same as base role
            )

    def test_revoke_elevation(self, elevation_manager, paired_device):
        """Test revoking elevation."""
        elevation_manager.grant_elevation(
            paired_device.device_id,
            MobileRole.MOBILE_OPERATOR,
        )

        revoked = elevation_manager.revoke_elevation(paired_device.device_id)
        assert revoked

        # Verify no active elevation
        effective = elevation_manager.get_effective_role(paired_device)
        assert effective == MobileRole.MOBILE_READONLY

    def test_get_effective_role_no_elevation(self, elevation_manager, paired_device):
        """Test effective role with no elevation."""
        effective = elevation_manager.get_effective_role(paired_device)
        assert effective == paired_device.role

    def test_get_effective_role_with_elevation(self, elevation_manager, paired_device):
        """Test effective role with active elevation."""
        elevation_manager.grant_elevation(
            paired_device.device_id,
            MobileRole.MOBILE_OPERATOR,
        )

        effective = elevation_manager.get_effective_role(paired_device)
        assert effective == MobileRole.MOBILE_OPERATOR

    def test_get_elevation_status(self, elevation_manager, paired_device):
        """Test getting elevation status."""
        status = elevation_manager.get_elevation_status(paired_device.device_id)
        assert status["device_id"] == paired_device.device_id
        assert not status["elevated"]
        assert status["base_role"] == "mobile_readonly"

        # Grant elevation
        elevation_manager.grant_elevation(
            paired_device.device_id,
            MobileRole.MOBILE_OPERATOR,
        )

        status = elevation_manager.get_elevation_status(paired_device.device_id)
        assert status["elevated"]
        assert status["effective_role"] == "mobile_operator"
        assert "remaining_seconds" in status

    def test_list_elevated_devices(self, elevation_manager, store):
        """Test listing elevated devices."""
        # Create multiple devices
        for i in range(3):
            device = PairedDevice(
                device_id=f"device-{i}",
                name=f"Phone {i}",
                role=MobileRole.MOBILE_READONLY,
                status=DeviceStatus.PAIRED,
                cert_fingerprint=f"fp-{i}",
                paired_at=datetime.utcnow(),
            )
            store.create_device(device)

        # Elevate two of them
        elevation_manager.grant_elevation("device-0", MobileRole.MOBILE_OPERATOR)
        elevation_manager.grant_elevation("device-1", MobileRole.MOBILE_OPERATOR)

        elevated = elevation_manager.list_elevated_devices()
        assert len(elevated) == 2


class TestTTLParsing:
    """Tests for TTL parsing and formatting."""

    def test_parse_seconds(self):
        """Test parsing seconds."""
        assert parse_ttl("30s") == 30
        assert parse_ttl("60s") == 60

    def test_parse_minutes(self):
        """Test parsing minutes."""
        assert parse_ttl("10m") == 600
        assert parse_ttl("30m") == 1800

    def test_parse_hours(self):
        """Test parsing hours."""
        assert parse_ttl("1h") == 3600
        assert parse_ttl("2h") == 7200

    def test_parse_raw_number(self):
        """Test parsing raw number as seconds."""
        assert parse_ttl("600") == 600
        assert parse_ttl("3600") == 3600

    def test_format_seconds(self):
        """Test formatting seconds."""
        assert format_ttl(30) == "30s"
        assert format_ttl(59) == "59s"

    def test_format_minutes(self):
        """Test formatting minutes."""
        assert format_ttl(60) == "1m"
        assert format_ttl(600) == "10m"
        assert format_ttl(1800) == "30m"

    def test_format_hours(self):
        """Test formatting hours."""
        assert format_ttl(3600) == "1h"
        assert format_ttl(7200) == "2h"

    def test_format_hours_and_minutes(self):
        """Test formatting hours and minutes."""
        assert format_ttl(5400) == "1h 30m"  # 1.5 hours
