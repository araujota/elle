"""Tests for ELLE Mobile Gateway storage."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from elle.mobile.config import MobileGatewayConfig
from elle.mobile.models import (
    DeviceStatus,
    Elevation,
    MobileRole,
    PairedDevice,
    PairingToken,
)
from elle.mobile.store import MobileStore


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "mobile.db"
        yield db_path


@pytest.fixture
def config(temp_db):
    """Create test configuration."""
    return MobileGatewayConfig(
        enabled=True,
        db_path=temp_db,
        audit_db_path=temp_db.parent / "mobile_audit.db",
        cert_dir=temp_db.parent / "certs",
    )


@pytest.fixture
def store(config):
    """Create test store."""
    return MobileStore(config)


class TestDeviceOperations:
    """Tests for device CRUD operations."""

    def test_create_device(self, store):
        """Test creating a device."""
        device = PairedDevice(
            device_id="test-device-1",
            name="Test Phone",
            cert_fingerprint="abc123",
        )
        created = store.create_device(device)
        assert created.device_id == "test-device-1"

    def test_create_duplicate_device(self, store):
        """Test creating a duplicate device fails."""
        device = PairedDevice(
            device_id="test-device-1",
            name="Test Phone",
            cert_fingerprint="abc123",
        )
        store.create_device(device)

        with pytest.raises(ValueError, match="already exists"):
            store.create_device(device)

    def test_get_device(self, store):
        """Test retrieving a device."""
        device = PairedDevice(
            device_id="test-device-1",
            name="Test Phone",
            cert_fingerprint="abc123",
        )
        store.create_device(device)

        retrieved = store.get_device("test-device-1")
        assert retrieved is not None
        assert retrieved.name == "Test Phone"

    def test_get_nonexistent_device(self, store):
        """Test retrieving a nonexistent device."""
        retrieved = store.get_device("nonexistent")
        assert retrieved is None

    def test_get_device_by_fingerprint(self, store):
        """Test retrieving a device by fingerprint."""
        device = PairedDevice(
            device_id="test-device-1",
            name="Test Phone",
            cert_fingerprint="unique-fingerprint",
        )
        store.create_device(device)

        retrieved = store.get_device_by_fingerprint("unique-fingerprint")
        assert retrieved is not None
        assert retrieved.device_id == "test-device-1"

    def test_list_devices(self, store):
        """Test listing devices."""
        for i in range(3):
            device = PairedDevice(
                device_id=f"test-device-{i}",
                name=f"Test Phone {i}",
                cert_fingerprint=f"fp-{i}",
            )
            store.create_device(device)

        devices = store.list_devices()
        assert len(devices) == 3

    def test_list_devices_by_status(self, store):
        """Test listing devices filtered by status."""
        device1 = PairedDevice(
            device_id="paired-device",
            name="Paired",
            cert_fingerprint="fp1",
            status=DeviceStatus.PAIRED,
        )
        device2 = PairedDevice(
            device_id="pending-device",
            name="Pending",
            cert_fingerprint="fp2",
            status=DeviceStatus.PENDING,
        )
        store.create_device(device1)
        store.create_device(device2)

        paired = store.list_devices(status=DeviceStatus.PAIRED)
        assert len(paired) == 1
        assert paired[0].device_id == "paired-device"

    def test_update_device(self, store):
        """Test updating a device."""
        device = PairedDevice(
            device_id="test-device-1",
            name="Old Name",
            cert_fingerprint="abc123",
        )
        store.create_device(device)

        updated = store.update_device(
            "test-device-1",
            name="New Name",
            status=DeviceStatus.PAIRED,
        )
        assert updated is not None
        assert updated.name == "New Name"
        assert updated.status == DeviceStatus.PAIRED

    def test_delete_device(self, store):
        """Test deleting a device."""
        device = PairedDevice(
            device_id="test-device-1",
            name="Test Phone",
            cert_fingerprint="abc123",
        )
        store.create_device(device)

        deleted = store.delete_device("test-device-1")
        assert deleted

        retrieved = store.get_device("test-device-1")
        assert retrieved is None

    def test_count_devices(self, store):
        """Test counting devices."""
        for i in range(5):
            device = PairedDevice(
                device_id=f"test-device-{i}",
                name=f"Test Phone {i}",
                cert_fingerprint=f"fp-{i}",
                status=DeviceStatus.PAIRED if i < 3 else DeviceStatus.PENDING,
            )
            store.create_device(device)

        assert store.count_devices() == 5
        assert store.count_devices(DeviceStatus.PAIRED) == 3
        assert store.count_devices(DeviceStatus.PENDING) == 2


class TestElevationOperations:
    """Tests for elevation CRUD operations."""

    def test_create_elevation(self, store):
        """Test creating an elevation."""
        # First create a device
        device = PairedDevice(
            device_id="test-device-1",
            name="Test Phone",
            cert_fingerprint="abc123",
        )
        store.create_device(device)

        # Create elevation
        elevation = Elevation(
            device_id="test-device-1",
            elevated_role=MobileRole.MOBILE_OPERATOR,
            expires_at=datetime.utcnow() + timedelta(minutes=10),
            granted_by="cli",
        )
        created = store.create_elevation(elevation)
        assert created.device_id == "test-device-1"

    def test_get_active_elevation(self, store):
        """Test getting active elevation."""
        device = PairedDevice(
            device_id="test-device-1",
            name="Test Phone",
            cert_fingerprint="abc123",
        )
        store.create_device(device)

        elevation = Elevation(
            device_id="test-device-1",
            elevated_role=MobileRole.MOBILE_OPERATOR,
            expires_at=datetime.utcnow() + timedelta(minutes=10),
            granted_by="cli",
        )
        store.create_elevation(elevation)

        active = store.get_active_elevation("test-device-1")
        assert active is not None
        assert active.elevated_role == MobileRole.MOBILE_OPERATOR

    def test_no_active_elevation_when_expired(self, store):
        """Test no active elevation returned when expired."""
        device = PairedDevice(
            device_id="test-device-1",
            name="Test Phone",
            cert_fingerprint="abc123",
        )
        store.create_device(device)

        elevation = Elevation(
            device_id="test-device-1",
            elevated_role=MobileRole.MOBILE_OPERATOR,
            expires_at=datetime.utcnow() - timedelta(minutes=10),
            granted_by="cli",
        )
        store.create_elevation(elevation)

        active = store.get_active_elevation("test-device-1")
        assert active is None

    def test_revoke_elevation(self, store):
        """Test revoking elevations."""
        device = PairedDevice(
            device_id="test-device-1",
            name="Test Phone",
            cert_fingerprint="abc123",
        )
        store.create_device(device)

        elevation = Elevation(
            device_id="test-device-1",
            elevated_role=MobileRole.MOBILE_OPERATOR,
            expires_at=datetime.utcnow() + timedelta(minutes=10),
            granted_by="cli",
        )
        store.create_elevation(elevation)

        revoked = store.revoke_elevation("test-device-1")
        assert revoked

        active = store.get_active_elevation("test-device-1")
        assert active is None


class TestTokenOperations:
    """Tests for pairing token operations."""

    def test_create_token(self, store):
        """Test creating a token."""
        token = PairingToken(
            token="test-token-123",
            expires_at=datetime.utcnow() + timedelta(seconds=90),
        )
        created = store.create_token(token)
        assert created.token == "test-token-123"

    def test_get_token(self, store):
        """Test retrieving a token."""
        token = PairingToken(
            token="test-token-123",
            expires_at=datetime.utcnow() + timedelta(seconds=90),
        )
        store.create_token(token)

        retrieved = store.get_token("test-token-123")
        assert retrieved is not None
        assert not retrieved.used

    def test_mark_token_used(self, store):
        """Test marking a token as used."""
        token = PairingToken(
            token="test-token-123",
            expires_at=datetime.utcnow() + timedelta(seconds=90),
        )
        store.create_token(token)

        store.mark_token_used("test-token-123")

        retrieved = store.get_token("test-token-123")
        assert retrieved.used

    def test_cleanup_expired_tokens(self, store):
        """Test cleaning up expired tokens."""
        # Create expired token
        expired = PairingToken(
            token="expired-token",
            expires_at=datetime.utcnow() - timedelta(seconds=10),
        )
        store.create_token(expired)

        # Create valid token
        valid = PairingToken(
            token="valid-token",
            expires_at=datetime.utcnow() + timedelta(seconds=90),
        )
        store.create_token(valid)

        cleaned = store.cleanup_expired_tokens()
        assert cleaned == 1

        assert store.get_token("expired-token") is None
        assert store.get_token("valid-token") is not None
