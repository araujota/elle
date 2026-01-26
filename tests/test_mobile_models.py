"""Tests for ELLE Mobile Gateway models."""

from datetime import datetime, timedelta

import pytest

from elle.mobile.models import (
    DeviceStatus,
    Elevation,
    GatewayStatus,
    MobileAuditAction,
    MobileAuditEntry,
    MobileRole,
    PairedDevice,
    PairingToken,
    QRPayload,
)


class TestMobileRole:
    """Tests for MobileRole enum."""

    def test_role_values(self):
        """Test role values are correct."""
        assert MobileRole.MOBILE_READONLY.value == "mobile_readonly"
        assert MobileRole.MOBILE_OPERATOR.value == "mobile_operator"

    def test_role_from_string(self):
        """Test creating role from string."""
        assert MobileRole("mobile_readonly") == MobileRole.MOBILE_READONLY
        assert MobileRole("mobile_operator") == MobileRole.MOBILE_OPERATOR


class TestPairedDevice:
    """Tests for PairedDevice model."""

    def test_create_device(self):
        """Test creating a paired device."""
        device = PairedDevice(
            device_id="test-123",
            name="Test Phone",
            cert_fingerprint="abc123",
        )
        assert device.device_id == "test-123"
        assert device.name == "Test Phone"
        assert device.role == MobileRole.MOBILE_READONLY
        assert device.status == DeviceStatus.PENDING
        assert device.cert_fingerprint == "abc123"

    def test_device_is_frozen(self):
        """Test device model is immutable."""
        from pydantic import ValidationError

        device = PairedDevice(
            device_id="test-123",
            name="Test Phone",
            cert_fingerprint="abc123",
        )
        with pytest.raises(ValidationError):
            device.name = "New Name"


class TestElevation:
    """Tests for Elevation model."""

    def test_create_elevation(self):
        """Test creating an elevation."""
        future = datetime.utcnow() + timedelta(minutes=10)
        elevation = Elevation(
            device_id="test-123",
            elevated_role=MobileRole.MOBILE_OPERATOR,
            expires_at=future,
            granted_by="cli",
        )
        assert elevation.device_id == "test-123"
        assert elevation.elevated_role == MobileRole.MOBILE_OPERATOR
        assert elevation.is_active()

    def test_elevation_expired(self):
        """Test elevation expiration check."""
        past = datetime.utcnow() - timedelta(minutes=10)
        elevation = Elevation(
            device_id="test-123",
            elevated_role=MobileRole.MOBILE_OPERATOR,
            expires_at=past,
            granted_by="cli",
        )
        assert not elevation.is_active()


class TestPairingToken:
    """Tests for PairingToken model."""

    def test_create_token(self):
        """Test creating a pairing token."""
        future = datetime.utcnow() + timedelta(seconds=90)
        token = PairingToken(
            token="abc123def456",
            expires_at=future,
        )
        assert token.token == "abc123def456"
        assert not token.used
        assert token.is_valid()

    def test_token_expired(self):
        """Test token expiration check."""
        past = datetime.utcnow() - timedelta(seconds=10)
        token = PairingToken(
            token="abc123def456",
            expires_at=past,
        )
        assert not token.is_valid()

    def test_token_used(self):
        """Test used token is not valid."""
        future = datetime.utcnow() + timedelta(seconds=90)
        token = PairingToken(
            token="abc123def456",
            expires_at=future,
            used=True,
        )
        assert not token.is_valid()


class TestQRPayload:
    """Tests for QRPayload model."""

    def test_create_payload(self):
        """Test creating a QR payload."""
        payload = QRPayload(
            host="192.168.1.100",
            port=8378,
            token="abc123",
            server_fingerprint="sha256fingerprint",
        )
        assert payload.host == "192.168.1.100"
        assert payload.port == 8378
        assert payload.version == 1

    def test_payload_to_json(self):
        """Test serializing payload to JSON."""
        payload = QRPayload(
            host="192.168.1.100",
            port=8378,
            token="abc123",
            server_fingerprint="sha256fingerprint",
        )
        json_str = payload.to_json()
        assert "192.168.1.100" in json_str
        assert "8378" in json_str
        assert "abc123" in json_str


class TestMobileAuditEntry:
    """Tests for MobileAuditEntry model."""

    def test_create_audit_entry(self):
        """Test creating an audit entry."""
        entry = MobileAuditEntry(
            device_id="test-123",
            device_name="Test Phone",
            action=MobileAuditAction.REQUEST,
            endpoint="/v1/chat/completions",
            execution_mode="readonly",
            success=True,
            ip_address="192.168.1.50",
        )
        assert entry.device_id == "test-123"
        assert entry.action == MobileAuditAction.REQUEST
        assert entry.success


class TestGatewayStatus:
    """Tests for GatewayStatus model."""

    def test_gateway_not_running(self):
        """Test status when gateway is not running."""
        status = GatewayStatus(running=False)
        assert not status.running
        assert status.pid is None

    def test_gateway_running(self):
        """Test status when gateway is running."""
        status = GatewayStatus(
            running=True,
            pid=12345,
            bind_host="0.0.0.0",
            bind_port=8378,
            paired_devices=2,
        )
        assert status.running
        assert status.pid == 12345
        assert status.paired_devices == 2
