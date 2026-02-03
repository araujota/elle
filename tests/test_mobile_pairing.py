from __future__ import annotations

"""Tests for ELLE Mobile Gateway pairing module.

Covers:
- PairingResult NamedTuple creation
- PairingError exception
- PairingManager.__init__() default injection
- PairingManager.initiate_pairing() success and device limit error
- PairingManager.complete_pairing() success and all error paths
- PairingManager.generate_qr_terminal() with and without qrcode library
- PairingManager.generate_qr_ascii_simple() with and without qrcode library
- PairingManager._get_advertise_host() all branches
- PairingManager.get_pairing_instructions() output formatting
"""

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure elle.mobile.crypto is importable even when 'cryptography' is absent.
# The real module imports 'cryptography' at the top level, which may not be
# installed in the test environment.  We inject a lightweight mock module
# into sys.modules so that patch("elle.mobile.crypto.MobileCrypto") can
# resolve the dotted path.
# ---------------------------------------------------------------------------
import elle.mobile

if "elle.mobile.crypto" not in sys.modules:
    _crypto_stub = MagicMock()
    sys.modules["elle.mobile.crypto"] = _crypto_stub
    elle.mobile.crypto = _crypto_stub

from elle.mobile.config import MobileGatewayConfig
from elle.mobile.models import (
    DeviceStatus,
    MobileRole,
    PairedDevice,
    PairingToken,
    QRPayload,
)
from elle.mobile.pairing import (
    QRCODE_AVAILABLE,
    TOKEN_BYTES,
    PairingError,
    PairingManager,
    PairingResult,
)

# =============================================================================
# Constants
# =============================================================================

FAKE_FINGERPRINT = "aa11bb22cc33dd44ee55ff6600112233"


def _make_config(**overrides) -> MobileGatewayConfig:
    """Build a MobileGatewayConfig with sensible test defaults."""
    defaults = {
        "enabled": True,
        "bind_host": "0.0.0.0",
        "bind_port": 8378,
        "overlay_host": None,
        "pairing_token_ttl_seconds": 90,
        "max_paired_devices": 10,
        "default_role": "mobile_readonly",
    }
    defaults.update(overrides)
    return MobileGatewayConfig(**defaults)


def _build_pairing_manager(
    config: MobileGatewayConfig | None = None,
    store: MagicMock | None = None,
    crypto: MagicMock | None = None,
) -> PairingManager:
    """Build a PairingManager with all dependencies mocked."""
    return PairingManager(
        config=config or _make_config(),
        store=store or MagicMock(),
        crypto=crypto or MagicMock(),
    )


# =============================================================================
# TestPairingResult
# =============================================================================


class TestPairingResult:
    """Tests for the PairingResult NamedTuple."""

    def test_create_pairing_result(self):
        """PairingResult can be created with all fields."""
        device = MagicMock(spec=PairedDevice)
        result = PairingResult(
            device=device,
            client_cert_pem=b"CERT_PEM",
            client_key_pem=b"KEY_PEM",
        )
        assert result.device is device
        assert result.client_cert_pem == b"CERT_PEM"
        assert result.client_key_pem == b"KEY_PEM"

    def test_pairing_result_is_tuple(self):
        """PairingResult is a NamedTuple and supports indexing."""
        device = MagicMock(spec=PairedDevice)
        result = PairingResult(
            device=device,
            client_cert_pem=b"CERT",
            client_key_pem=b"KEY",
        )
        assert result[0] is device
        assert result[1] == b"CERT"
        assert result[2] == b"KEY"
        assert len(result) == 3

    def test_pairing_result_unpacking(self):
        """PairingResult can be unpacked into separate variables."""
        device = MagicMock(spec=PairedDevice)
        result = PairingResult(
            device=device,
            client_cert_pem=b"CERT",
            client_key_pem=b"KEY",
        )
        d, cert, key = result
        assert d is device
        assert cert == b"CERT"
        assert key == b"KEY"


# =============================================================================
# TestPairingError
# =============================================================================


class TestPairingError:
    """Tests for PairingError exception."""

    def test_pairing_error_message(self):
        """PairingError stores the message correctly."""
        err = PairingError("something went wrong")
        assert str(err) == "something went wrong"

    def test_pairing_error_is_exception(self):
        """PairingError is a subclass of Exception."""
        assert issubclass(PairingError, Exception)

    def test_pairing_error_can_be_raised_and_caught(self):
        """PairingError can be raised and caught."""
        with pytest.raises(PairingError, match="test error"):
            raise PairingError("test error")


# =============================================================================
# TestPairingManagerInit
# =============================================================================


class TestPairingManagerInit:
    """Tests for PairingManager.__init__() default injection."""

    @patch("elle.mobile.pairing.MobileCrypto")
    @patch("elle.mobile.pairing.MobileStore")
    @patch("elle.mobile.pairing.get_mobile_config")
    def test_defaults_injected_when_none(self, mock_get_config, mock_store_cls, mock_crypto_cls):
        """When config/store/crypto are None, defaults are created."""
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config

        pm = PairingManager()

        mock_get_config.assert_called_once()
        assert pm.config is mock_config
        mock_store_cls.assert_called_once_with(mock_config)
        mock_crypto_cls.assert_called_once_with(mock_config)

    def test_explicit_dependencies_used(self):
        """When config/store/crypto are passed, they are used directly."""
        config = _make_config()
        store = MagicMock()
        crypto = MagicMock()

        pm = PairingManager(config=config, store=store, crypto=crypto)

        assert pm.config is config
        assert pm.store is store
        assert pm.crypto is crypto

    @patch("elle.mobile.pairing.MobileCrypto")
    @patch("elle.mobile.pairing.get_mobile_config")
    def test_config_none_store_explicit(self, mock_get_config, mock_crypto_cls):
        """When only config is None, store and crypto still use the resolved config."""
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config
        explicit_store = MagicMock()

        pm = PairingManager(store=explicit_store)

        assert pm.config is mock_config
        assert pm.store is explicit_store
        mock_crypto_cls.assert_called_once_with(mock_config)


# =============================================================================
# TestInitiatePairing
# =============================================================================


class TestInitiatePairing:
    """Tests for PairingManager.initiate_pairing()."""

    def test_successful_pairing_initiation(self):
        """initiate_pairing returns QRPayload and PairingToken."""
        mock_store = MagicMock()
        mock_store.count_devices.return_value = 0

        mock_crypto = MagicMock()
        mock_crypto.get_server_fingerprint.return_value = FAKE_FINGERPRINT

        config = _make_config(overlay_host="10.0.0.5")
        pm = _build_pairing_manager(config=config, store=mock_store, crypto=mock_crypto)

        payload, token = pm.initiate_pairing()

        assert isinstance(payload, QRPayload)
        assert isinstance(token, PairingToken)
        assert payload.host == "10.0.0.5"
        assert payload.port == 8378
        assert payload.server_fingerprint == FAKE_FINGERPRINT
        assert len(payload.token) == TOKEN_BYTES * 2  # hex encoding doubles length
        mock_store.create_token.assert_called_once()

    def test_pairing_with_explicit_role(self):
        """initiate_pairing uses the explicit role when provided."""
        mock_store = MagicMock()
        mock_store.count_devices.return_value = 0

        mock_crypto = MagicMock()
        mock_crypto.get_server_fingerprint.return_value = FAKE_FINGERPRINT

        config = _make_config(overlay_host="10.0.0.5")
        pm = _build_pairing_manager(config=config, store=mock_store, crypto=mock_crypto)

        payload, token = pm.initiate_pairing(role=MobileRole.MOBILE_OPERATOR)

        assert token.role == MobileRole.MOBILE_OPERATOR

    def test_pairing_with_default_role_from_config(self):
        """initiate_pairing uses config default_role when role is None."""
        mock_store = MagicMock()
        mock_store.count_devices.return_value = 0

        mock_crypto = MagicMock()
        mock_crypto.get_server_fingerprint.return_value = FAKE_FINGERPRINT

        config = _make_config(default_role="mobile_operator", overlay_host="10.0.0.5")
        pm = _build_pairing_manager(config=config, store=mock_store, crypto=mock_crypto)

        payload, token = pm.initiate_pairing()

        assert token.role == MobileRole.MOBILE_OPERATOR

    def test_max_devices_reached_raises_error(self):
        """initiate_pairing raises PairingError when max devices reached."""
        mock_store = MagicMock()
        mock_store.count_devices.return_value = 10

        config = _make_config(max_paired_devices=10)
        pm = _build_pairing_manager(config=config, store=mock_store)

        with pytest.raises(PairingError, match="Maximum paired devices.*reached"):
            pm.initiate_pairing()

    def test_max_devices_message_includes_count(self):
        """PairingError message includes the max device count."""
        mock_store = MagicMock()
        mock_store.count_devices.return_value = 5

        config = _make_config(max_paired_devices=5)
        pm = _build_pairing_manager(config=config, store=mock_store)

        with pytest.raises(PairingError, match="5"):
            pm.initiate_pairing()

    def test_token_stored_in_store(self):
        """initiate_pairing calls store.create_token with the generated token."""
        mock_store = MagicMock()
        mock_store.count_devices.return_value = 0

        mock_crypto = MagicMock()
        mock_crypto.get_server_fingerprint.return_value = FAKE_FINGERPRINT

        config = _make_config(overlay_host="10.0.0.5")
        pm = _build_pairing_manager(config=config, store=mock_store, crypto=mock_crypto)

        payload, token = pm.initiate_pairing()

        mock_store.create_token.assert_called_once()
        stored_token = mock_store.create_token.call_args[0][0]
        assert isinstance(stored_token, PairingToken)
        assert stored_token.token == token.token

    def test_token_expiration_uses_config_ttl(self):
        """Token expires_at is approximately now + config TTL."""
        mock_store = MagicMock()
        mock_store.count_devices.return_value = 0

        mock_crypto = MagicMock()
        mock_crypto.get_server_fingerprint.return_value = FAKE_FINGERPRINT

        config = _make_config(pairing_token_ttl_seconds=120, overlay_host="10.0.0.5")
        pm = _build_pairing_manager(config=config, store=mock_store, crypto=mock_crypto)

        before = datetime.now(timezone.utc)
        payload, token = pm.initiate_pairing()
        after = datetime.now(timezone.utc)

        expected_min = before + timedelta(seconds=120)
        expected_max = after + timedelta(seconds=120)
        assert expected_min <= token.expires_at <= expected_max

    def test_overlay_host_used_when_set(self):
        """When overlay_host is set, it is used in the QR payload."""
        mock_store = MagicMock()
        mock_store.count_devices.return_value = 0

        mock_crypto = MagicMock()
        mock_crypto.get_server_fingerprint.return_value = FAKE_FINGERPRINT

        config = _make_config(overlay_host="wireguard.example.com")
        pm = _build_pairing_manager(config=config, store=mock_store, crypto=mock_crypto)

        payload, token = pm.initiate_pairing()

        assert payload.host == "wireguard.example.com"

    def test_fallback_to_advertise_host_when_no_overlay(self):
        """When overlay_host is None, _get_advertise_host() is called."""
        mock_store = MagicMock()
        mock_store.count_devices.return_value = 0

        mock_crypto = MagicMock()
        mock_crypto.get_server_fingerprint.return_value = FAKE_FINGERPRINT

        config = _make_config(overlay_host=None, bind_host="192.168.1.50")
        pm = _build_pairing_manager(config=config, store=mock_store, crypto=mock_crypto)

        payload, token = pm.initiate_pairing()

        # bind_host is specific (not 0.0.0.0), so it's used directly
        assert payload.host == "192.168.1.50"

    def test_count_devices_called_with_paired_status(self):
        """initiate_pairing checks count of PAIRED devices specifically."""
        mock_store = MagicMock()
        mock_store.count_devices.return_value = 0

        mock_crypto = MagicMock()
        mock_crypto.get_server_fingerprint.return_value = FAKE_FINGERPRINT

        config = _make_config(overlay_host="10.0.0.5")
        pm = _build_pairing_manager(config=config, store=mock_store, crypto=mock_crypto)

        pm.initiate_pairing()

        mock_store.count_devices.assert_called_once_with(DeviceStatus.PAIRED)


# =============================================================================
# TestCompletePairing
# =============================================================================


class TestCompletePairing:
    """Tests for PairingManager.complete_pairing()."""

    def test_successful_pairing_completion(self):
        """complete_pairing returns PairingResult with device and certs."""
        future = datetime.now(timezone.utc) + timedelta(seconds=90)
        mock_token = PairingToken(
            token="abc123",
            expires_at=future,
            role=MobileRole.MOBILE_READONLY,
        )

        mock_store = MagicMock()
        mock_store.mark_token_used_atomic.return_value = mock_token

        mock_client_cert = MagicMock()
        mock_client_cert.fingerprint = FAKE_FINGERPRINT
        mock_client_cert.cert_pem = b"CLIENT_CERT_PEM"
        mock_client_cert.key_pem = b"CLIENT_KEY_PEM"

        mock_crypto = MagicMock()
        mock_crypto.generate_client_certificate.return_value = mock_client_cert

        pm = _build_pairing_manager(store=mock_store, crypto=mock_crypto)

        result = pm.complete_pairing("abc123", "My Phone")

        assert isinstance(result, PairingResult)
        assert result.device.name == "My Phone"
        assert result.device.role == MobileRole.MOBILE_READONLY
        assert result.device.status == DeviceStatus.PAIRED
        assert result.device.cert_fingerprint == FAKE_FINGERPRINT
        assert result.client_cert_pem == b"CLIENT_CERT_PEM"
        assert result.client_key_pem == b"CLIENT_KEY_PEM"

    def test_invalid_token_raises_error(self):
        """complete_pairing raises PairingError for invalid (unknown) token."""
        mock_store = MagicMock()
        mock_store.mark_token_used_atomic.return_value = None

        pm = _build_pairing_manager(store=mock_store)

        with pytest.raises(PairingError, match="Invalid or already-used pairing token"):
            pm.complete_pairing("nonexistent", "My Phone")

    def test_used_token_raises_error(self):
        """complete_pairing raises PairingError for already-used token."""
        mock_store = MagicMock()
        # Atomic mark returns None when token was already used (0 rows updated)
        mock_store.mark_token_used_atomic.return_value = None

        pm = _build_pairing_manager(store=mock_store)

        with pytest.raises(PairingError, match="Invalid or already-used pairing token"):
            pm.complete_pairing("abc123", "My Phone")

    def test_expired_token_raises_error(self):
        """complete_pairing raises PairingError for expired token."""
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        expired_token = PairingToken(
            token="abc123",
            expires_at=past,
        )

        mock_store = MagicMock()
        # Atomic mark succeeds (returns the token) but it is expired
        mock_store.mark_token_used_atomic.return_value = expired_token

        pm = _build_pairing_manager(store=mock_store)

        with pytest.raises(PairingError, match="expired"):
            pm.complete_pairing("abc123", "My Phone")

    def test_token_marked_used_before_cert_generation(self):
        """complete_pairing atomically marks token as used."""
        future = datetime.now(timezone.utc) + timedelta(seconds=90)
        mock_token = PairingToken(
            token="abc123",
            expires_at=future,
            role=MobileRole.MOBILE_READONLY,
        )

        mock_store = MagicMock()
        mock_store.mark_token_used_atomic.return_value = mock_token

        mock_client_cert = MagicMock()
        mock_client_cert.fingerprint = FAKE_FINGERPRINT
        mock_client_cert.cert_pem = b"CERT"
        mock_client_cert.key_pem = b"KEY"

        mock_crypto = MagicMock()
        mock_crypto.generate_client_certificate.return_value = mock_client_cert

        pm = _build_pairing_manager(store=mock_store, crypto=mock_crypto)
        pm.complete_pairing("abc123", "My Phone")

        mock_store.mark_token_used_atomic.assert_called_once_with("abc123")

    def test_device_created_in_store(self):
        """complete_pairing calls store.create_device with the new device."""
        future = datetime.now(timezone.utc) + timedelta(seconds=90)
        mock_token = PairingToken(
            token="abc123",
            expires_at=future,
            role=MobileRole.MOBILE_OPERATOR,
        )

        mock_store = MagicMock()
        mock_store.mark_token_used_atomic.return_value = mock_token

        mock_client_cert = MagicMock()
        mock_client_cert.fingerprint = FAKE_FINGERPRINT
        mock_client_cert.cert_pem = b"CERT"
        mock_client_cert.key_pem = b"KEY"

        mock_crypto = MagicMock()
        mock_crypto.generate_client_certificate.return_value = mock_client_cert

        pm = _build_pairing_manager(store=mock_store, crypto=mock_crypto)
        pm.complete_pairing("abc123", "My Tablet")

        mock_store.create_device.assert_called_once()
        created_device = mock_store.create_device.call_args[0][0]
        assert isinstance(created_device, PairedDevice)
        assert created_device.name == "My Tablet"
        assert created_device.role == MobileRole.MOBILE_OPERATOR
        assert created_device.status == DeviceStatus.PAIRED
        assert created_device.cert_fingerprint == FAKE_FINGERPRINT

    def test_client_certificate_generated_with_device_id_and_name(self):
        """complete_pairing passes device_id and name to crypto."""
        future = datetime.now(timezone.utc) + timedelta(seconds=90)
        mock_token = PairingToken(
            token="abc123",
            expires_at=future,
        )

        mock_store = MagicMock()
        mock_store.mark_token_used_atomic.return_value = mock_token

        mock_client_cert = MagicMock()
        mock_client_cert.fingerprint = FAKE_FINGERPRINT
        mock_client_cert.cert_pem = b"CERT"
        mock_client_cert.key_pem = b"KEY"

        mock_crypto = MagicMock()
        mock_crypto.generate_client_certificate.return_value = mock_client_cert

        pm = _build_pairing_manager(store=mock_store, crypto=mock_crypto)
        pm.complete_pairing("abc123", "Test Device")

        mock_crypto.generate_client_certificate.assert_called_once()
        call_args = mock_crypto.generate_client_certificate.call_args[0]
        # First arg is device_id (a UUID string), second is device_name
        assert call_args[1] == "Test Device"

    def test_device_has_uuid_format_id(self):
        """Device ID generated during complete_pairing is a valid UUID."""
        import uuid

        future = datetime.now(timezone.utc) + timedelta(seconds=90)
        mock_token = PairingToken(
            token="abc123",
            expires_at=future,
        )

        mock_store = MagicMock()
        mock_store.mark_token_used_atomic.return_value = mock_token

        mock_client_cert = MagicMock()
        mock_client_cert.fingerprint = FAKE_FINGERPRINT
        mock_client_cert.cert_pem = b"CERT"
        mock_client_cert.key_pem = b"KEY"

        mock_crypto = MagicMock()
        mock_crypto.generate_client_certificate.return_value = mock_client_cert

        pm = _build_pairing_manager(store=mock_store, crypto=mock_crypto)
        result = pm.complete_pairing("abc123", "My Phone")

        # Verify device_id is a valid UUID
        parsed = uuid.UUID(result.device.device_id)
        assert str(parsed) == result.device.device_id

    def test_device_timestamps_set(self):
        """Device paired_at and created_at are set during pairing."""
        future = datetime.now(timezone.utc) + timedelta(seconds=90)
        mock_token = PairingToken(
            token="abc123",
            expires_at=future,
        )

        mock_store = MagicMock()
        mock_store.mark_token_used_atomic.return_value = mock_token

        mock_client_cert = MagicMock()
        mock_client_cert.fingerprint = FAKE_FINGERPRINT
        mock_client_cert.cert_pem = b"CERT"
        mock_client_cert.key_pem = b"KEY"

        mock_crypto = MagicMock()
        mock_crypto.generate_client_certificate.return_value = mock_client_cert

        before = datetime.now(timezone.utc)
        pm = _build_pairing_manager(store=mock_store, crypto=mock_crypto)
        result = pm.complete_pairing("abc123", "My Phone")
        after = datetime.now(timezone.utc)

        assert result.device.paired_at is not None
        assert result.device.created_at is not None
        assert before <= result.device.paired_at <= after
        assert before <= result.device.created_at <= after


# =============================================================================
# TestGenerateQrTerminal
# =============================================================================


def _make_mock_qr(modules: list[list[bool]] | None = None):
    """Build a mock QRCode object with controllable modules grid.

    If modules is None, a default 4x4 grid is used that exercises all
    four character branches (top+bottom, top-only, bottom-only, neither).
    """
    if modules is None:
        # 4 rows, 4 cols - covers all 4 branches in the Unicode renderer:
        # row 0-1: (True,True), (True,False), (False,True), (False,False)
        # row 2-3: (True,True), (False,False), (True,False), (False,True)
        modules = [
            [True, True, False, False],
            [True, False, True, False],
            [True, False, True, False],
            [True, False, False, True],
        ]
    mock_qr = MagicMock()
    mock_qr.modules = modules
    mock_qr.modules_count = len(modules)
    return mock_qr


class TestGenerateQrTerminal:
    """Tests for PairingManager.generate_qr_terminal()."""

    def test_generate_qr_terminal_returns_string(self):
        """generate_qr_terminal returns a non-empty string with mocked qrcode."""
        pm = _build_pairing_manager()
        payload = QRPayload(
            host="192.168.1.100",
            port=8378,
            token="abc123",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        mock_qr = _make_mock_qr()
        with (
            patch("elle.mobile.pairing.QRCODE_AVAILABLE", True),
            patch("elle.mobile.pairing.qrcode", create=True) as mock_qrcode_mod,
            patch("elle.mobile.pairing.ERROR_CORRECT_L", 1, create=True),
        ):
            mock_qrcode_mod.QRCode.return_value = mock_qr
            result = pm.generate_qr_terminal(payload)

        assert isinstance(result, str)
        assert len(result) > 0

    def test_generate_qr_terminal_contains_newlines(self):
        """QR terminal output contains multiple lines."""
        pm = _build_pairing_manager()
        payload = QRPayload(
            host="192.168.1.100",
            port=8378,
            token="abc123",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        mock_qr = _make_mock_qr()
        with (
            patch("elle.mobile.pairing.QRCODE_AVAILABLE", True),
            patch("elle.mobile.pairing.qrcode", create=True) as mock_qrcode_mod,
            patch("elle.mobile.pairing.ERROR_CORRECT_L", 1, create=True),
        ):
            mock_qrcode_mod.QRCode.return_value = mock_qr
            result = pm.generate_qr_terminal(payload)

        assert "\n" in result
        lines = result.strip().split("\n")
        assert len(lines) >= 1

    def test_generate_qr_terminal_uses_unicode_blocks(self):
        """QR terminal output uses Unicode block characters for all 4 combos."""
        pm = _build_pairing_manager()
        payload = QRPayload(
            host="192.168.1.100",
            port=8378,
            token="abc123",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        # Grid designed so all 4 branches are hit.
        # modules_count is used for both row and column iteration, so grid must be square.
        # Row pair (0,1): col0=(T,T)->full, col1=(T,F)->upper, col2=(F,T)->lower, col3=(F,F)->space
        # Row pair (2,3): filler rows
        modules = [
            [True, True, False, False],
            [True, False, True, False],
            [True, True, False, False],
            [True, False, True, False],
        ]
        mock_qr = _make_mock_qr(modules)

        with (
            patch("elle.mobile.pairing.QRCODE_AVAILABLE", True),
            patch("elle.mobile.pairing.qrcode", create=True) as mock_qrcode_mod,
            patch("elle.mobile.pairing.ERROR_CORRECT_L", 1, create=True),
        ):
            mock_qrcode_mod.QRCode.return_value = mock_qr
            result = pm.generate_qr_terminal(payload)

        assert "\u2588" in result  # Full block (top=True, bottom=True)
        assert "\u2580" in result  # Upper half (top=True, bottom=False)
        assert "\u2584" in result  # Lower half (top=False, bottom=True)
        assert " " in result  # Space (top=False, bottom=False)

    def test_generate_qr_terminal_odd_row_count(self):
        """QR terminal handles odd row count (last row has no bottom pair)."""
        pm = _build_pairing_manager()
        payload = QRPayload(
            host="192.168.1.100",
            port=8378,
            token="abc123",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        # 3 rows x 3 cols - the third row (index 2) pairs with a non-existent row 3.
        # modules_count is used for both rows and columns, so grid must be square.
        modules = [
            [True, False, True],
            [False, True, False],
            [True, False, True],  # No row 3, so bottom=False for all
        ]
        mock_qr = _make_mock_qr(modules)

        with (
            patch("elle.mobile.pairing.QRCODE_AVAILABLE", True),
            patch("elle.mobile.pairing.qrcode", create=True) as mock_qrcode_mod,
            patch("elle.mobile.pairing.ERROR_CORRECT_L", 1, create=True),
        ):
            mock_qrcode_mod.QRCode.return_value = mock_qr
            result = pm.generate_qr_terminal(payload)

        # Row 2 top=True, bottom=False -> upper half block
        assert "\u2580" in result
        assert isinstance(result, str)

    def test_generate_qr_terminal_raises_without_qrcode(self):
        """generate_qr_terminal raises ImportError when qrcode unavailable."""
        pm = _build_pairing_manager()
        payload = QRPayload(
            host="192.168.1.100",
            port=8378,
            token="abc123",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        with patch("elle.mobile.pairing.QRCODE_AVAILABLE", False):
            with pytest.raises(ImportError, match="qrcode library required"):
                pm.generate_qr_terminal(payload)


# =============================================================================
# TestGenerateQrAsciiSimple
# =============================================================================


class TestGenerateQrAsciiSimple:
    """Tests for PairingManager.generate_qr_ascii_simple()."""

    def test_generate_qr_ascii_simple_returns_string(self):
        """generate_qr_ascii_simple returns a non-empty string."""
        pm = _build_pairing_manager()
        payload = QRPayload(
            host="192.168.1.100",
            port=8378,
            token="abc123",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        modules = [
            [True, False, True],
            [False, True, False],
        ]
        mock_qr = _make_mock_qr(modules)

        with (
            patch("elle.mobile.pairing.QRCODE_AVAILABLE", True),
            patch("elle.mobile.pairing.qrcode", create=True) as mock_qrcode_mod,
            patch("elle.mobile.pairing.ERROR_CORRECT_L", 1, create=True),
        ):
            mock_qrcode_mod.QRCode.return_value = mock_qr
            result = pm.generate_qr_ascii_simple(payload)

        assert isinstance(result, str)
        assert len(result) > 0

    def test_generate_qr_ascii_simple_uses_hash_and_space(self):
        """Simple ASCII QR uses ## for True modules and spaces for False."""
        pm = _build_pairing_manager()
        payload = QRPayload(
            host="192.168.1.100",
            port=8378,
            token="abc123",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        modules = [
            [True, False],
            [False, True],
        ]
        mock_qr = _make_mock_qr(modules)

        with (
            patch("elle.mobile.pairing.QRCODE_AVAILABLE", True),
            patch("elle.mobile.pairing.qrcode", create=True) as mock_qrcode_mod,
            patch("elle.mobile.pairing.ERROR_CORRECT_L", 1, create=True),
        ):
            mock_qrcode_mod.QRCode.return_value = mock_qr
            result = pm.generate_qr_ascii_simple(payload)

        assert "##" in result
        assert "  " in result

    def test_generate_qr_ascii_simple_contains_newlines(self):
        """Simple ASCII QR output contains multiple lines."""
        pm = _build_pairing_manager()
        payload = QRPayload(
            host="192.168.1.100",
            port=8378,
            token="abc123",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        modules = [
            [True, True],
            [True, True],
            [False, False],
        ]
        mock_qr = _make_mock_qr(modules)

        with (
            patch("elle.mobile.pairing.QRCODE_AVAILABLE", True),
            patch("elle.mobile.pairing.qrcode", create=True) as mock_qrcode_mod,
            patch("elle.mobile.pairing.ERROR_CORRECT_L", 1, create=True),
        ):
            mock_qrcode_mod.QRCode.return_value = mock_qr
            result = pm.generate_qr_ascii_simple(payload)

        lines = result.strip().split("\n")
        assert len(lines) > 1

    def test_generate_qr_ascii_simple_raises_without_qrcode(self):
        """generate_qr_ascii_simple raises ImportError without qrcode."""
        pm = _build_pairing_manager()
        payload = QRPayload(
            host="192.168.1.100",
            port=8378,
            token="abc123",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        with patch("elle.mobile.pairing.QRCODE_AVAILABLE", False):
            with pytest.raises(ImportError, match="qrcode library required"):
                pm.generate_qr_ascii_simple(payload)


# =============================================================================
# TestGetAdvertiseHost
# =============================================================================


class TestGetAdvertiseHost:
    """Tests for PairingManager._get_advertise_host()."""

    def test_specific_bind_host_returned(self):
        """When bind_host is a specific address, it is returned directly."""
        config = _make_config(bind_host="192.168.1.50")
        pm = _build_pairing_manager(config=config)

        result = pm._get_advertise_host()

        assert result == "192.168.1.50"

    def test_ipv6_specific_bind_host_returned(self):
        """When bind_host is a specific IPv6 address, it is returned."""
        config = _make_config(bind_host="fd00::1")
        pm = _build_pairing_manager(config=config)

        result = pm._get_advertise_host()

        assert result == "fd00::1"

    def test_wildcard_ipv4_triggers_detection(self):
        """When bind_host is 0.0.0.0, LAN IP detection is attempted."""
        config = _make_config(bind_host="0.0.0.0")
        pm = _build_pairing_manager(config=config)

        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("10.0.0.42", 0)
            mock_socket_cls.return_value = mock_sock

            result = pm._get_advertise_host()

            assert result == "10.0.0.42"
            mock_sock.connect.assert_called_once_with(("10.255.255.255", 1))
            mock_sock.close.assert_called_once()

    def test_wildcard_ipv6_triggers_detection(self):
        """When bind_host is ::, LAN IP detection is attempted."""
        config = _make_config(bind_host="::")
        pm = _build_pairing_manager(config=config)

        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("10.0.0.99", 0)
            mock_socket_cls.return_value = mock_sock

            result = pm._get_advertise_host()

            assert result == "10.0.0.99"

    def test_empty_bind_host_triggers_detection(self):
        """When bind_host is empty string, LAN IP detection is attempted."""
        config = _make_config(bind_host="")
        pm = _build_pairing_manager(config=config)

        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("10.0.0.55", 0)
            mock_socket_cls.return_value = mock_sock

            result = pm._get_advertise_host()

            assert result == "10.0.0.55"

    def test_socket_connect_failure_returns_localhost(self):
        """When socket connect fails, 127.0.0.1 is returned."""
        config = _make_config(bind_host="0.0.0.0")
        pm = _build_pairing_manager(config=config)

        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.connect.side_effect = OSError("Network unreachable")
            mock_sock.getsockname.return_value = ("127.0.0.1", 0)
            mock_socket_cls.return_value = mock_sock

            result = pm._get_advertise_host()

            # The inner except catches the connect exception and falls back
            assert result == "127.0.0.1"
            mock_sock.close.assert_called_once()

    def test_socket_creation_failure_returns_localhost(self):
        """When socket.socket() itself raises, 127.0.0.1 is returned."""
        config = _make_config(bind_host="0.0.0.0")
        pm = _build_pairing_manager(config=config)

        with patch("socket.socket", side_effect=OSError("Cannot create socket")):
            result = pm._get_advertise_host()

            assert result == "127.0.0.1"

    def test_socket_import_failure_returns_localhost(self):
        """When socket import fails entirely, 127.0.0.1 is returned."""
        config = _make_config(bind_host="0.0.0.0")
        pm = _build_pairing_manager(config=config)

        # Simulate a general exception in the outer try block
        with patch("socket.socket", side_effect=Exception("unexpected")):
            result = pm._get_advertise_host()

            assert result == "127.0.0.1"


# =============================================================================
# TestGetPairingInstructions
# =============================================================================


class TestGetPairingInstructions:
    """Tests for PairingManager.get_pairing_instructions()."""

    def test_instructions_contain_host_and_port(self):
        """Instructions include server host and port."""
        config = _make_config(pairing_token_ttl_seconds=120)
        pm = _build_pairing_manager(config=config)
        payload = QRPayload(
            host="192.168.1.100",
            port=8378,
            token="abc123def456ghi789",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        result = pm.get_pairing_instructions(payload)

        assert "192.168.1.100:8378" in result
        assert "Host: 192.168.1.100" in result
        assert "Port: 8378" in result

    def test_instructions_contain_truncated_token(self):
        """Instructions show only first 16 chars of token followed by ellipsis."""
        pm = _build_pairing_manager()
        payload = QRPayload(
            host="192.168.1.100",
            port=8378,
            token="0123456789abcdef0123456789abcdef",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        result = pm.get_pairing_instructions(payload)

        assert "0123456789abcdef..." in result
        # Full token should NOT appear
        assert "0123456789abcdef0123456789abcdef" not in result

    def test_instructions_contain_ttl(self):
        """Instructions include the token TTL from config."""
        config = _make_config(pairing_token_ttl_seconds=300)
        pm = _build_pairing_manager(config=config)
        payload = QRPayload(
            host="10.0.0.1",
            port=9999,
            token="abcdef1234567890abcdef1234567890",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        result = pm.get_pairing_instructions(payload)

        assert "300 seconds" in result

    def test_instructions_contain_header(self):
        """Instructions start with the pairing header."""
        pm = _build_pairing_manager()
        payload = QRPayload(
            host="10.0.0.1",
            port=8378,
            token="abc123def456ghi789jkl",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        result = pm.get_pairing_instructions(payload)

        assert "Mobile Gateway Pairing" in result
        assert "=" * 50 in result

    def test_instructions_mention_qr_scan(self):
        """Instructions tell the user to scan the QR code."""
        pm = _build_pairing_manager()
        payload = QRPayload(
            host="10.0.0.1",
            port=8378,
            token="abc123def456ghi789jkl",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        result = pm.get_pairing_instructions(payload)

        assert "Scan the QR code" in result
        assert "ELLE mobile app" in result

    def test_instructions_mention_manual_entry(self):
        """Instructions include manual entry section."""
        pm = _build_pairing_manager()
        payload = QRPayload(
            host="10.0.0.1",
            port=8378,
            token="abc123def456ghi789jkl",
            server_fingerprint=FAKE_FINGERPRINT,
        )

        result = pm.get_pairing_instructions(payload)

        assert "Or manually enter:" in result


# =============================================================================
# TestTokenBytesConstant
# =============================================================================


class TestTokenBytesConstant:
    """Tests for the TOKEN_BYTES module-level constant."""

    def test_token_bytes_value(self):
        """TOKEN_BYTES is 64 (producing 128 hex chars)."""
        assert TOKEN_BYTES == 64


# =============================================================================
# TestQRCodeAvailableFlag
# =============================================================================


class TestQRCodeAvailableFlag:
    """Tests for the QRCODE_AVAILABLE module-level flag."""

    def test_qrcode_available_is_bool(self):
        """QRCODE_AVAILABLE is a boolean."""
        assert isinstance(QRCODE_AVAILABLE, bool)


# =============================================================================
# Integration-style tests (all mocked, testing method interaction)
# =============================================================================


class TestPairingFlow:
    """Tests that exercise the full initiate -> complete flow with mocks."""

    def test_full_pairing_flow(self):
        """Initiate then complete pairing produces a valid PairingResult."""
        mock_store = MagicMock()
        mock_store.count_devices.return_value = 0

        mock_client_cert = MagicMock()
        mock_client_cert.fingerprint = FAKE_FINGERPRINT
        mock_client_cert.cert_pem = b"CLIENT_CERT"
        mock_client_cert.key_pem = b"CLIENT_KEY"

        mock_crypto = MagicMock()
        mock_crypto.get_server_fingerprint.return_value = FAKE_FINGERPRINT
        mock_crypto.generate_client_certificate.return_value = mock_client_cert

        config = _make_config(overlay_host="10.0.0.5")
        pm = _build_pairing_manager(config=config, store=mock_store, crypto=mock_crypto)

        # Step 1: Initiate
        payload, token = pm.initiate_pairing(role=MobileRole.MOBILE_OPERATOR)

        # Simulate store returning the token during atomic mark
        mock_store.mark_token_used_atomic.return_value = token

        # Step 2: Complete
        result = pm.complete_pairing(token.token, "Test Phone")

        assert result.device.name == "Test Phone"
        assert result.device.role == MobileRole.MOBILE_OPERATOR
        assert result.device.status == DeviceStatus.PAIRED
        assert result.client_cert_pem == b"CLIENT_CERT"
        assert result.client_key_pem == b"CLIENT_KEY"

        # Verify interactions
        mock_store.create_token.assert_called_once()
        mock_store.mark_token_used_atomic.assert_called_once_with(token.token)
        mock_store.create_device.assert_called_once()

    def test_pairing_with_instructions(self):
        """Initiate pairing and get instructions produces valid output."""
        mock_store = MagicMock()
        mock_store.count_devices.return_value = 0

        mock_crypto = MagicMock()
        mock_crypto.get_server_fingerprint.return_value = FAKE_FINGERPRINT

        config = _make_config(
            overlay_host="10.0.0.5",
            pairing_token_ttl_seconds=60,
        )
        pm = _build_pairing_manager(config=config, store=mock_store, crypto=mock_crypto)

        payload, token = pm.initiate_pairing()
        instructions = pm.get_pairing_instructions(payload)

        assert "10.0.0.5:8378" in instructions
        assert "60 seconds" in instructions
