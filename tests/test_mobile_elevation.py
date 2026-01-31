"""Tests for ELLE Mobile Gateway elevation management.

Covers ElevationManager methods (grant, revoke, effective role, status,
listing, cleanup), the parse_ttl / format_ttl helpers, and edge-case
error paths for 90%+ branch coverage of elle.mobile.elevation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from elle.mobile.config import MobileGatewayConfig
from elle.mobile.elevation import (
    ElevationError,
    ElevationManager,
    format_ttl,
    parse_ttl,
)
from elle.mobile.models import DeviceStatus, Elevation, MobileRole, PairedDevice
from elle.mobile.store import MobileStore

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def config():
    """Create test configuration with known TTL bounds."""
    return MobileGatewayConfig(
        enabled=True,
        default_elevation_ttl_seconds=600,
        max_elevation_ttl_seconds=3600,
    )


@pytest.fixture
def store(config):
    """Create a mock MobileStore with in-memory device/elevation dicts."""
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

    def _cleanup_expired():
        expired = [k for k, v in s._elevations.items() if not v.is_active()]
        for k in expired:
            del s._elevations[k]
        return len(expired)

    s.create_device = MagicMock(side_effect=_create_device)
    s.get_device = MagicMock(side_effect=_get_device)
    s.create_elevation = MagicMock(side_effect=_create_elevation)
    s.get_active_elevation = MagicMock(side_effect=_get_active_elevation)
    s.revoke_elevation = MagicMock(side_effect=_revoke_elevation)
    s.list_active_elevations = MagicMock(side_effect=_list_active_elevations)
    s.cleanup_expired_elevations = MagicMock(side_effect=_cleanup_expired)

    return s


@pytest.fixture
def elevation_manager(config, store):
    """Create an ElevationManager wired to the mock config and store."""
    return ElevationManager(config, store)


@pytest.fixture
def paired_device(store):
    """Create and register a PAIRED readonly device for testing."""
    device = PairedDevice(
        device_id="test-device-1",
        name="Test Phone",
        role=MobileRole.MOBILE_READONLY,
        status=DeviceStatus.PAIRED,
        cert_fingerprint="abc123",
        paired_at=datetime.utcnow(),
    )
    return store.create_device(device)


@pytest.fixture
def operator_device(store):
    """Create and register a PAIRED operator device."""
    device = PairedDevice(
        device_id="test-device-op",
        name="Operator Tablet",
        role=MobileRole.MOBILE_OPERATOR,
        status=DeviceStatus.PAIRED,
        cert_fingerprint="op456",
        paired_at=datetime.utcnow(),
    )
    return store.create_device(device)


# =============================================================================
# TestElevationManager -- grant_elevation
# =============================================================================


class TestGrantElevation:
    """Tests for ElevationManager.grant_elevation."""

    def test_grant_elevation_success(self, elevation_manager, paired_device):
        """Grant elevation to a readonly device returns an active Elevation."""
        elevation = elevation_manager.grant_elevation(
            paired_device.device_id,
            MobileRole.MOBILE_OPERATOR,
            ttl_seconds=600,
        )
        assert elevation.device_id == paired_device.device_id
        assert elevation.elevated_role == MobileRole.MOBILE_OPERATOR
        assert elevation.is_active()

    def test_grant_elevation_default_ttl(self, elevation_manager, paired_device):
        """When ttl_seconds is None the config default (600s) is used."""
        elevation = elevation_manager.grant_elevation(
            paired_device.device_id,
            MobileRole.MOBILE_OPERATOR,
        )
        assert elevation.is_active()
        remaining = (elevation.expires_at - datetime.utcnow()).total_seconds()
        assert 590 < remaining <= 600

    def test_grant_elevation_custom_granted_by(self, elevation_manager, paired_device):
        """The granted_by field is recorded on the Elevation."""
        elevation = elevation_manager.grant_elevation(
            paired_device.device_id,
            MobileRole.MOBILE_OPERATOR,
            ttl_seconds=300,
            granted_by="admin-panel",
        )
        assert elevation.granted_by == "admin-panel"

    def test_grant_elevation_zero_ttl_raises(self, elevation_manager, paired_device):
        """TTL of zero is rejected with ElevationError."""
        with pytest.raises(ElevationError, match="TTL must be positive"):
            elevation_manager.grant_elevation(
                paired_device.device_id,
                MobileRole.MOBILE_OPERATOR,
                ttl_seconds=0,
            )

    def test_grant_elevation_negative_ttl_raises(self, elevation_manager, paired_device):
        """Negative TTL is rejected with ElevationError."""
        with pytest.raises(ElevationError, match="TTL must be positive"):
            elevation_manager.grant_elevation(
                paired_device.device_id,
                MobileRole.MOBILE_OPERATOR,
                ttl_seconds=-10,
            )

    def test_grant_elevation_exceeds_max_ttl(self, elevation_manager, paired_device):
        """TTL exceeding config max raises ElevationError."""
        with pytest.raises(ElevationError, match="exceeds maximum"):
            elevation_manager.grant_elevation(
                paired_device.device_id,
                MobileRole.MOBILE_OPERATOR,
                ttl_seconds=7200,
            )

    def test_grant_elevation_nonexistent_device(self, elevation_manager):
        """Attempting to elevate a device not in the store raises ElevationError."""
        with pytest.raises(ElevationError, match="not found"):
            elevation_manager.grant_elevation(
                "nonexistent-id",
                MobileRole.MOBILE_OPERATOR,
            )

    def test_grant_elevation_unpaired_device_rejected(self, elevation_manager, store):
        """A device with status PENDING cannot be elevated."""
        pending = PairedDevice(
            device_id="pending-dev",
            name="Pending Phone",
            role=MobileRole.MOBILE_READONLY,
            status=DeviceStatus.PENDING,
            cert_fingerprint="pend999",
        )
        store.create_device(pending)

        with pytest.raises(ElevationError, match="status invalid"):
            elevation_manager.grant_elevation(
                "pending-dev",
                MobileRole.MOBILE_OPERATOR,
            )

    def test_grant_elevation_revoked_device_rejected(self, elevation_manager, store):
        """A device with status REVOKED cannot be elevated."""
        revoked = PairedDevice(
            device_id="revoked-dev",
            name="Revoked Phone",
            role=MobileRole.MOBILE_READONLY,
            status=DeviceStatus.REVOKED,
            cert_fingerprint="rev888",
        )
        store.create_device(revoked)

        with pytest.raises(ElevationError, match="status invalid"):
            elevation_manager.grant_elevation(
                "revoked-dev",
                MobileRole.MOBILE_OPERATOR,
            )

    def test_grant_elevation_same_role_raises(self, elevation_manager, paired_device):
        """Granting elevation to the same role the device already has fails."""
        with pytest.raises(ElevationError, match="already has role"):
            elevation_manager.grant_elevation(
                paired_device.device_id,
                MobileRole.MOBILE_READONLY,
            )

    def test_grant_elevation_demotion_raises(self, elevation_manager, operator_device):
        """Cannot 'elevate' from operator down to readonly (demotion blocked)."""
        with pytest.raises(ElevationError, match="Cannot elevate from"):
            elevation_manager.grant_elevation(
                operator_device.device_id,
                MobileRole.MOBILE_READONLY,
            )

    def test_grant_elevation_revokes_existing_first(self, elevation_manager, paired_device, store):
        """Granting a new elevation revokes the prior one first."""
        elevation_manager.grant_elevation(
            paired_device.device_id,
            MobileRole.MOBILE_OPERATOR,
            ttl_seconds=300,
        )
        # store.revoke_elevation should have been called during the first grant
        # and again during the second grant
        store.revoke_elevation.reset_mock()

        elevation_manager.grant_elevation(
            paired_device.device_id,
            MobileRole.MOBILE_OPERATOR,
            ttl_seconds=600,
        )
        store.revoke_elevation.assert_called_once_with(paired_device.device_id)


# =============================================================================
# TestElevationManager -- revoke_elevation
# =============================================================================


class TestRevokeElevation:
    """Tests for ElevationManager.revoke_elevation."""

    def test_revoke_active_elevation(self, elevation_manager, paired_device):
        """Revoking an active elevation returns True."""
        elevation_manager.grant_elevation(
            paired_device.device_id,
            MobileRole.MOBILE_OPERATOR,
        )
        result = elevation_manager.revoke_elevation(paired_device.device_id)
        assert result is True

    def test_revoke_no_elevation_returns_false(self, elevation_manager, paired_device):
        """Revoking when there is no elevation returns False."""
        result = elevation_manager.revoke_elevation(paired_device.device_id)
        assert result is False


# =============================================================================
# TestElevationManager -- get_effective_role
# =============================================================================


class TestGetEffectiveRole:
    """Tests for ElevationManager.get_effective_role."""

    def test_effective_role_no_elevation(self, elevation_manager, paired_device):
        """Without elevation, effective role equals the base device role."""
        effective = elevation_manager.get_effective_role(paired_device)
        assert effective == MobileRole.MOBILE_READONLY

    def test_effective_role_with_active_elevation(self, elevation_manager, paired_device):
        """With an active elevation, effective role is the elevated role."""
        elevation_manager.grant_elevation(
            paired_device.device_id,
            MobileRole.MOBILE_OPERATOR,
        )
        effective = elevation_manager.get_effective_role(paired_device)
        assert effective == MobileRole.MOBILE_OPERATOR

    def test_effective_role_after_revocation(self, elevation_manager, paired_device):
        """After revocation the device falls back to its base role."""
        elevation_manager.grant_elevation(
            paired_device.device_id,
            MobileRole.MOBILE_OPERATOR,
        )
        elevation_manager.revoke_elevation(paired_device.device_id)
        effective = elevation_manager.get_effective_role(paired_device)
        assert effective == MobileRole.MOBILE_READONLY

    def test_effective_role_expired_elevation_returns_base(self, elevation_manager, paired_device, store):
        """An expired elevation in the store does not affect the effective role."""
        expired_elevation = Elevation(
            device_id=paired_device.device_id,
            elevated_role=MobileRole.MOBILE_OPERATOR,
            expires_at=datetime.utcnow() - timedelta(seconds=60),
            granted_by="cli",
            created_at=datetime.utcnow() - timedelta(seconds=660),
        )
        store._elevations[paired_device.device_id] = expired_elevation
        effective = elevation_manager.get_effective_role(paired_device)
        assert effective == MobileRole.MOBILE_READONLY


# =============================================================================
# TestElevationManager -- get_elevation_status
# =============================================================================


class TestGetElevationStatus:
    """Tests for ElevationManager.get_elevation_status."""

    def test_status_device_not_found(self, elevation_manager):
        """Status for a nonexistent device returns error dict."""
        status = elevation_manager.get_elevation_status("no-such-device")
        assert status == {"error": "Device not found"}

    def test_status_no_elevation(self, elevation_manager, paired_device):
        """Status for a device with no elevation shows elevated=False."""
        status = elevation_manager.get_elevation_status(paired_device.device_id)
        assert status["device_id"] == paired_device.device_id
        assert status["device_name"] == "Test Phone"
        assert status["base_role"] == "mobile_readonly"
        assert status["effective_role"] == "mobile_readonly"
        assert status["elevated"] is False

    def test_status_with_active_elevation(self, elevation_manager, paired_device):
        """Status for an elevated device includes elevation details."""
        elevation_manager.grant_elevation(
            paired_device.device_id,
            MobileRole.MOBILE_OPERATOR,
            ttl_seconds=600,
        )
        status = elevation_manager.get_elevation_status(paired_device.device_id)
        assert status["elevated"] is True
        assert status["effective_role"] == "mobile_operator"
        assert status["elevated_role"] == "mobile_operator"
        assert "expires_at" in status
        assert "remaining_seconds" in status
        assert status["remaining_seconds"] > 0
        assert status["granted_by"] == "cli"

    def test_status_expired_elevation_shows_not_elevated(self, elevation_manager, paired_device, store):
        """An expired elevation does not mark the device as elevated."""
        expired = Elevation(
            device_id=paired_device.device_id,
            elevated_role=MobileRole.MOBILE_OPERATOR,
            expires_at=datetime.utcnow() - timedelta(seconds=10),
            granted_by="cli",
            created_at=datetime.utcnow() - timedelta(seconds=610),
        )
        store._elevations[paired_device.device_id] = expired
        status = elevation_manager.get_elevation_status(paired_device.device_id)
        assert status["elevated"] is False
        assert status["effective_role"] == "mobile_readonly"


# =============================================================================
# TestElevationManager -- list_elevated_devices
# =============================================================================


class TestListElevatedDevices:
    """Tests for ElevationManager.list_elevated_devices."""

    def test_list_empty_when_no_elevations(self, elevation_manager):
        """Returns empty list when no devices are elevated."""
        result = elevation_manager.list_elevated_devices()
        assert result == []

    def test_list_multiple_elevated_devices(self, elevation_manager, store):
        """All actively elevated devices appear in the listing."""
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

        elevation_manager.grant_elevation("device-0", MobileRole.MOBILE_OPERATOR)
        elevation_manager.grant_elevation("device-1", MobileRole.MOBILE_OPERATOR)

        elevated = elevation_manager.list_elevated_devices()
        assert len(elevated) == 2
        device_ids = {e["device_id"] for e in elevated}
        assert device_ids == {"device-0", "device-1"}
        for entry in elevated:
            assert entry["base_role"] == "mobile_readonly"
            assert entry["elevated_role"] == "mobile_operator"
            assert entry["remaining_seconds"] > 0
            assert "expires_at" in entry
            assert "granted_by" in entry

    def test_list_skips_missing_device_in_store(self, elevation_manager, store):
        """If store.get_device returns None during listing, that entry is skipped."""
        # Manually insert an elevation for a device id that is NOT in the devices dict
        orphan_elevation = Elevation(
            device_id="orphan-device",
            elevated_role=MobileRole.MOBILE_OPERATOR,
            expires_at=datetime.utcnow() + timedelta(seconds=600),
            granted_by="cli",
            created_at=datetime.utcnow(),
        )
        store._elevations["orphan-device"] = orphan_elevation

        result = elevation_manager.list_elevated_devices()
        assert len(result) == 0


# =============================================================================
# TestElevationManager -- cleanup_expired
# =============================================================================


class TestCleanupExpired:
    """Tests for ElevationManager.cleanup_expired."""

    def test_cleanup_returns_count(self, elevation_manager, store):
        """cleanup_expired delegates to store and returns the count."""
        store.cleanup_expired_elevations.reset_mock()
        store.cleanup_expired_elevations.side_effect = None
        store.cleanup_expired_elevations.return_value = 5
        count = elevation_manager.cleanup_expired()
        assert count == 5
        store.cleanup_expired_elevations.assert_called_once()

    def test_cleanup_zero_when_none_expired(self, elevation_manager, store):
        """cleanup_expired returns 0 when there are no expired elevations."""
        store.cleanup_expired_elevations.reset_mock()
        store.cleanup_expired_elevations.side_effect = None
        store.cleanup_expired_elevations.return_value = 0
        count = elevation_manager.cleanup_expired()
        assert count == 0


# =============================================================================
# TestElevationManager -- constructor defaults
# =============================================================================


class TestElevationManagerInit:
    """Tests for ElevationManager.__init__ default paths."""

    @patch("elle.mobile.elevation.MobileStore")
    @patch("elle.mobile.elevation.get_mobile_config")
    def test_defaults_used_when_no_args(self, mock_get_config, mock_store_cls):
        """When config and store are None, defaults are loaded."""
        mock_cfg = MagicMock(spec=MobileGatewayConfig)
        mock_get_config.return_value = mock_cfg

        manager = ElevationManager()

        mock_get_config.assert_called_once()
        mock_store_cls.assert_called_once_with(mock_cfg)
        assert manager.config is mock_cfg

    def test_explicit_config_and_store_used(self, config, store):
        """Explicitly supplied config and store are used directly."""
        manager = ElevationManager(config=config, store=store)
        assert manager.config is config
        assert manager.store is store


# =============================================================================
# TestTTLParsing
# =============================================================================


class TestParseTTL:
    """Tests for the parse_ttl helper."""

    def test_parse_seconds(self):
        """Suffix 's' is parsed as seconds."""
        assert parse_ttl("30s") == 30
        assert parse_ttl("60s") == 60

    def test_parse_minutes(self):
        """Suffix 'm' is parsed as minutes (x60)."""
        assert parse_ttl("10m") == 600
        assert parse_ttl("30m") == 1800

    def test_parse_hours(self):
        """Suffix 'h' is parsed as hours (x3600)."""
        assert parse_ttl("1h") == 3600
        assert parse_ttl("2h") == 7200

    def test_parse_raw_number(self):
        """A plain integer string is treated as raw seconds."""
        assert parse_ttl("600") == 600
        assert parse_ttl("3600") == 3600

    def test_parse_strips_whitespace(self):
        """Leading and trailing whitespace is stripped before parsing."""
        assert parse_ttl("  10m  ") == 600

    def test_parse_case_insensitive(self):
        """Uppercase suffixes are lowered before matching."""
        assert parse_ttl("10M") == 600
        assert parse_ttl("1H") == 3600
        assert parse_ttl("30S") == 30

    def test_parse_invalid_format_raises(self):
        """Non-numeric, non-suffixed strings raise ValueError."""
        with pytest.raises(ValueError):
            parse_ttl("abc")

    def test_parse_empty_suffix_raises(self):
        """Just a suffix letter with no digits raises ValueError."""
        with pytest.raises(ValueError):
            parse_ttl("m")


# =============================================================================
# TestFormatTTL
# =============================================================================


class TestFormatTTL:
    """Tests for the format_ttl helper."""

    def test_format_seconds(self):
        """Values under 60 are formatted as seconds."""
        assert format_ttl(30) == "30s"
        assert format_ttl(59) == "59s"
        assert format_ttl(0) == "0s"

    def test_format_minutes(self):
        """Values between 60 and 3599 are formatted as minutes (integer division)."""
        assert format_ttl(60) == "1m"
        assert format_ttl(600) == "10m"
        assert format_ttl(1800) == "30m"

    def test_format_exact_hours(self):
        """Exact multiples of 3600 are formatted as hours only."""
        assert format_ttl(3600) == "1h"
        assert format_ttl(7200) == "2h"

    def test_format_hours_and_minutes(self):
        """Non-exact hours include a minutes component."""
        assert format_ttl(5400) == "1h 30m"
        assert format_ttl(3660) == "1h 1m"
